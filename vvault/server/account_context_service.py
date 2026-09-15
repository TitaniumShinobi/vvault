"""Authenticated account assertion verification and owner-scoped projection.

Auth supplies a short-lived, audience-bound assertion.  VVAULT verifies it,
matches it to the authenticated canonical owner, and persists only the small
construct-visible allowlist.  Stored projections are append-only and signed by
VVAULT; transcript tables are never touched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vvault.server import (
    canonical_projection_signing,
    chatty_body_service,
    offline_snapshot_service,
)


AUTH_ASSERTION_VERSION = "life-auth-account-context-assertion/v1"
PROJECTION_VERSION = "life-account-context-projection/v1"
PUBLICATION_RECEIPT_VERSION = "life-account-context-publication-receipt/v1"
AUTHORITY = "ovvaults.vault_files"
DOCUMENT_ID = "life.vvault.account-context"
_ALLOWED_PROFILE_FIELDS = {"displayName", "ageAssurance"}
_ALLOWED_ASSERTION_HEADERS = {"alg", "typ", "kid"}
_ALLOWED_ASSERTION_CLAIMS = {
    "iss", "aud", "sub", "jti", "iat", "exp", "version",
    "vvault_owner_id", "sid", "key_id", "account_context",
}
_AGE_ASSURANCE = {"adult", "minor"}
_KID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("account assertion is not valid base64url") from exc


def _json_segment(value: str, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url_decode(value).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"account assertion {label} is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"account assertion {label} must be an object")
    return decoded


def _safe_profile(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("account assertion account_context must be an object")
    unknown = sorted(set(value) - _ALLOWED_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"account assertion contains disallowed account fields: {', '.join(unknown)}")
    display_name = str(value.get("displayName") or "").strip()
    if not display_name or len(display_name) > 160:
        raise ValueError("account assertion displayName is required and must be at most 160 characters")
    profile = {"displayName": display_name}
    if value.get("ageAssurance") is not None:
        age_assurance = str(value.get("ageAssurance") or "").strip().lower()
        if age_assurance not in _AGE_ASSURANCE:
            raise ValueError("account assertion ageAssurance must be adult or minor")
        profile["ageAssurance"] = age_assurance
    return profile


def verify_auth_assertion(
    assertion: str,
    *,
    owner_user_id: str,
    public_key_pem: str | None = None,
    issuer: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify Auth's Ed25519 JWT without trusting assertion-provided keys."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("authenticated owner_user_id is required")
    encoded = str(assertion or "").strip()
    parts = encoded.split(".")
    if len(parts) != 3:
        raise ValueError("account assertion must be a compact JWT")
    header = _json_segment(parts[0], "header")
    claims = _json_segment(parts[1], "claims")
    unknown_headers = sorted(set(header) - _ALLOWED_ASSERTION_HEADERS)
    unknown_claims = sorted(set(claims) - _ALLOWED_ASSERTION_CLAIMS)
    if unknown_headers:
        raise ValueError(f"account assertion contains unsupported header fields: {', '.join(unknown_headers)}")
    if unknown_claims:
        raise ValueError(f"account assertion contains disallowed claims: {', '.join(unknown_claims)}")
    if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
        raise ValueError("account assertion algorithm is not allowed")
    kid = str(header.get("kid") or "").strip()
    encoded_key = str(
        public_key_pem
        or os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_PEM")
        or ""
    ).strip().replace("\\n", "\n")
    if not encoded_key:
        raise RuntimeError("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_UNAVAILABLE")
    try:
        public_key = serialization.load_pem_public_key(encoded_key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_INVALID") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise RuntimeError("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_INVALID")
    canonical_public_pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    derived_kid = hashlib.sha256(canonical_public_pem).hexdigest()
    configured_kid = str(os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_KEY_ID") or "").strip()
    expected_kid = configured_kid or derived_kid
    if configured_kid and configured_kid != derived_kid:
        raise RuntimeError("AUTH_VVAULT_ACCOUNT_ASSERTION_KEY_ID_MISMATCH")
    if not _KID.fullmatch(kid) or kid != expected_kid:
        raise ValueError("account assertion key id is not trusted")
    supplied_signature = _b64url_decode(parts[2])
    try:
        public_key.verify(supplied_signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except InvalidSignature as exc:
        raise ValueError("account assertion signature is invalid")

    expected_issuer = str(issuer or os.environ.get("AUTH_JWT_ISSUER") or "quantum-auth")
    if claims.get("version") != AUTH_ASSERTION_VERSION:
        raise ValueError("account assertion version is unsupported")
    if claims.get("iss") != expected_issuer or claims.get("aud") != "vvault":
        raise ValueError("account assertion issuer or audience is invalid")
    if claims.get("key_id") != kid:
        raise ValueError("account assertion key id claim does not match header")
    assertion_subject = str(claims.get("sub") or "")
    assertion_owner = str(claims.get("vvault_owner_id") or "")
    if assertion_owner != owner:
        raise ValueError("account assertion owner does not match authenticated owner")
    for required in ("sub", "sid", "jti"):
        if not str(claims.get(required) or "").strip():
            raise ValueError(f"account assertion {required} claim is required")
    instant = int((now or datetime.now(timezone.utc)).timestamp())
    try:
        issued_at = int(claims.get("iat"))
        expires_at = int(claims.get("exp"))
    except (TypeError, ValueError) as exc:
        raise ValueError("account assertion iat and exp claims are required") from exc
    if issued_at > instant + 30 or expires_at <= instant or expires_at - issued_at > 300:
        raise ValueError("account assertion is expired or outside the allowed lifetime")
    profile = _safe_profile(claims.get("account_context"))
    return {
        "issuer": expected_issuer,
        "subject": str(claims["sub"]),
        "sessionId": str(claims["sid"]),
        "assertionId": str(claims["jti"]),
        "ownerUserId": owner,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "keyId": kid,
        "profile": profile,
        "attestationHash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _sign_projection(payload: dict[str, Any], private_key_pem: str | None) -> dict[str, Any]:
    key = offline_snapshot_service._load_private_key(private_key_pem)
    key_document = offline_snapshot_service.public_key_document(private_key_pem=private_key_pem)
    return {
        **payload,
        "algorithm": "Ed25519",
        "keyId": key_document["keyId"],
        "signature": base64.b64encode(key.sign(_canonical_json(payload))).decode("ascii"),
    }


def _verify_projection(projection: Any, *, owner_user_id: str, private_key_pem: str | None) -> None:
    if not isinstance(projection, dict) or projection.get("contract") != PROJECTION_VERSION:
        raise ValueError("stored account context projection contract is invalid")
    if projection.get("authority") != "ovvaults" or projection.get("ownerUserId") != owner_user_id:
        raise ValueError("stored account context projection owner or authority is invalid")
    signed = {key: value for key, value in projection.items() if key not in {"algorithm", "keyId", "signature"}}
    try:
        canonical_projection_signing.verify_canonical_payload(
            signed,
            {
                "algorithm": projection.get("algorithm"),
                "keyId": projection.get("keyId"),
                "signature": projection.get("signature"),
            },
            private_key_pem=private_key_pem,
        )
    except RuntimeError:
        raise
    except ValueError as exc:
        if str(exc) == "CANONICAL_PROJECTION_KEY_ID_MISMATCH":
            raise ValueError("stored account context projection key id is invalid") from exc
        raise ValueError("stored account context projection signature is invalid") from exc
    profile = _safe_profile(projection.get("profile"))
    content = {
        "ownerUserId": owner_user_id,
        "documentId": projection.get("documentId"),
        "revision": projection.get("revision"),
        "provenance": projection.get("provenance"),
        "profile": profile,
    }
    if projection.get("contentHash") != hashlib.sha256(_canonical_json(content)).hexdigest():
        raise ValueError("stored account context projection content hash is invalid")


def _projection_payload(
    *, owner_user_id: str, revision: str, verified: dict[str, Any], issued_at: str
) -> dict[str, Any]:
    content = {
        "ownerUserId": owner_user_id,
        "documentId": DOCUMENT_ID,
        "revision": revision,
        "provenance": {
            "authority": "auth",
            "issuer": verified["issuer"],
            "subject": verified["subject"],
            "attestationHash": verified["attestationHash"],
        },
        "profile": verified["profile"],
    }
    return {
        "contract": PROJECTION_VERSION,
        "authority": "ovvaults",
        **content,
        "contentHash": hashlib.sha256(_canonical_json(content)).hexdigest(),
        "verification": {
            "signatureVerified": True,
            "authAssertionVerified": True,
            "ownerMatched": True,
        },
        "issuedAt": issued_at,
    }


def publish(
    *,
    owner_user_id: str,
    actor: str,
    assertion: str,
    assertion_public_key_pem: str | None = None,
    private_key_pem: str | None = None,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    verified = verify_auth_assertion(
        assertion,
        owner_user_id=owner_user_id,
        public_key_pem=assertion_public_key_pem,
        now=published_at,
    )
    timestamp = (published_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    profile_hash = hashlib.sha256(_canonical_json(verified["profile"])).hexdigest()
    with chatty_body_service._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text AS id,content,sha256,metadata
                   FROM ovvaults.vault_files
                   WHERE user_id=%s AND metadata->>'contract_version'=%s
                   ORDER BY (metadata->>'revision')::int DESC, updated_at DESC LIMIT 1""",
                (owner_user_id, PROJECTION_VERSION),
            )
            existing = cur.fetchone()
            existing_row = dict(existing) if existing else None
            metadata = (existing_row or {}).get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if existing_row and metadata.get("profile_hash") == profile_hash:
                projection = json.loads(str(existing_row.get("content") or "{}"))
                _verify_projection(projection, owner_user_id=owner_user_id, private_key_pem=private_key_pem)
                return _receipt(
                    owner_user_id, actor, str(existing_row["id"]), projection,
                    str(existing_row.get("sha256") or ""), timestamp, True,
                )
            revision = str(int(metadata.get("revision") or 0) + 1)
            projection = _sign_projection(
                _projection_payload(
                    owner_user_id=owner_user_id, revision=revision, verified=verified, issued_at=timestamp
                ),
                private_key_pem,
            )
            content = _canonical_json(projection).decode("utf-8")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            path = f"account/context/{DOCUMENT_ID}/r{revision}.json"
            stored_metadata = {
                "artifact_id": DOCUMENT_ID,
                "contract_version": PROJECTION_VERSION,
                "revision": revision,
                "profile_hash": profile_hash,
                "owner_fingerprint": hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest(),
                "auth_attestation_hash": verified["attestationHash"],
                "publication_status": "approved",
            }
            cur.execute(
                """INSERT INTO ovvaults.vault_files
                   (user_id,bucket,object_key,filename,storage_path,content_type,file_type,
                    size_bytes,sha256,content,metadata,construct_id,is_system,created_at,updated_at)
                   VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',%s,%s,%s,%s::jsonb,NULL,false,%s,%s)
                   RETURNING id::text AS id""",
                (
                    owner_user_id, f"users/{owner_user_id}/{path}", path, path,
                    len(content.encode("utf-8")), digest, content,
                    json.dumps(stored_metadata), timestamp, timestamp,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return _receipt(owner_user_id, actor, row["id"], projection, digest, timestamp, False)


def _receipt(
    owner_user_id: str,
    actor: str,
    record_id: str,
    projection: dict[str, Any],
    digest: str,
    published_at: str,
    idempotent: bool,
) -> dict[str, Any]:
    return {
        "version": PUBLICATION_RECEIPT_VERSION,
        "ownerUserId": owner_user_id,
        "actor": actor,
        "recordId": str(record_id),
        "documentId": projection["documentId"],
        "revision": projection["revision"],
        "sha256": digest,
        "contentHash": projection["contentHash"],
        "projectionKeyId": projection["keyId"],
        "publishedAt": published_at,
        "idempotent": idempotent,
        "transcriptsMutated": False,
    }


def read_projection(
    *,
    owner_user_id: str,
    private_key_pem: str | None = None,
    statement_timeout_ms: int | None = None,
    connection_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    timeout_kwargs = {}
    if statement_timeout_ms is not None:
        timeout_kwargs["statement_timeout_ms"] = statement_timeout_ms
    if connection_timeout_seconds is not None:
        timeout_kwargs["connection_timeout_seconds"] = connection_timeout_seconds
    rows = chatty_body_service._rows(
        """SELECT id::text AS id,content,sha256,metadata,updated_at
           FROM ovvaults.vault_files
           WHERE user_id=%s AND metadata->>'contract_version'=%s
             AND metadata->>'publication_status'='approved'
           ORDER BY (metadata->>'revision')::int DESC, updated_at DESC LIMIT 1""",
        (owner_user_id, PROJECTION_VERSION),
        **timeout_kwargs,
    )
    if not rows:
        raise ValueError("required canonical account context is not published")
    return read_projection_row(
        rows[0],
        owner_user_id=owner_user_id,
        private_key_pem=private_key_pem,
    )


def read_projection_row(
    row: dict[str, Any],
    *,
    owner_user_id: str,
    private_key_pem: str | None = None,
) -> dict[str, Any]:
    """Verify one exact owner-qualified canonical row without another query."""
    if not isinstance(row, dict):
        raise ValueError("stored account context projection row is malformed")
    raw = str(row.get("content") or "")
    if str(row.get("sha256") or "") != hashlib.sha256(raw.encode("utf-8")).hexdigest():
        raise ValueError("stored account context projection hash is invalid")
    try:
        projection = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("stored account context projection is malformed") from exc
    _verify_projection(projection, owner_user_id=owner_user_id, private_key_pem=private_key_pem)
    return projection


def context_candidate(
    projection: dict[str, Any],
    *,
    owner_user_id: str,
    respondent_principal_id: str,
    purpose: str,
    private_key_pem: str | None = None,
) -> dict[str, Any]:
    """Return the minimum account fields eligible for one context purpose.

    Age assurance is intentionally not general conversational context. It is
    disclosed only to the explicit ``age-assurance`` purpose; display name is
    the sole default account candidate.
    """
    _verify_projection(
        projection,
        owner_user_id=owner_user_id,
        private_key_pem=private_key_pem,
    )
    profile = _safe_profile(projection.get("profile"))
    minimized = {"displayName": profile["displayName"]}
    if purpose == "age-assurance" and "ageAssurance" in profile:
        minimized["ageAssurance"] = profile["ageAssurance"]
    return {
        "content": minimized,
        "privacy": {
            "classification": "account-private",
            "audience": ["construct"],
            "purposes": [purpose],
            "recipientPrincipalIds": [respondent_principal_id],
        },
        "provenance": {
            "authority": AUTHORITY,
            # Bind the context unit to the stable signed document identifier
            # without exposing the physical OVVAULTS row identifier.
            "recordId": str(projection.get("documentId") or ""),
            "revision": str(projection.get("revision") or ""),
            "sha256": str(projection.get("contentHash") or ""),
            "projectionEvidence": {
                "contract": PROJECTION_VERSION,
                "keyId": projection.get("keyId"),
                "signatureVerified": True,
                "authAssertionVerified": True,
                "ownerMatched": True,
            },
        },
    }
