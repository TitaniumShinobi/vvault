"""Verification for short-lived Auth-issued VVAULT access assertions.

The signed owner claim is the sole owner authority for assertion-authenticated
requests. Public keys are configured locally; assertion-provided key material
is never accepted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ASSERTION_VERSION = "life-auth-vvault-access-assertion/v2"
ALLOWED_RELYING_PARTIES = frozenset({"chatty", "chatty-cli"})
ALLOWED_SCOPES = frozenset({
    "constructs:read",
    "identity:read",
    "knowledge:read",
    "memory:read",
    "transcripts:read",
    "transcripts:append",
    "work:read",
    "work:append",
})
_ALLOWED_HEADERS = {"alg", "typ", "kid"}
_ALLOWED_CLAIMS = {
    "iss", "aud", "sub", "jti", "iat", "exp", "version",
    "vvault_owner_id", "sid", "scopes", "relying_party_id",
}
_KID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


class AccessAssertionRejected(ValueError):
    """The supplied assertion is malformed, untrusted, or unauthorized."""


class AccessAssertionUnavailable(RuntimeError):
    """The verifier has no valid trusted key configuration."""


def _decode_segment(value: str, label: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessAssertionRejected(f"access assertion {label} is invalid") from exc
    if not isinstance(decoded, dict):
        raise AccessAssertionRejected(f"access assertion {label} must be an object")
    return decoded


def _load_public_key(encoded: str) -> tuple[Ed25519PublicKey, str]:
    try:
        key = serialization.load_pem_public_key(encoded.strip().replace("\\n", "\n").encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
    canonical = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, hashlib.sha256(canonical).hexdigest()


def resolve_public_key_ring(
    configured: Mapping[str, str] | None = None,
) -> dict[str, Ed25519PublicKey]:
    """Load the current/next Ed25519 verification ring and validate every kid."""
    entries: dict[str, str] = dict(configured or {})
    if not entries:
        raw_ring = str(os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_PUBLIC_KEYS_JSON") or "").strip()
        if raw_ring:
            try:
                parsed = json.loads(raw_ring)
            except json.JSONDecodeError as exc:
                raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID") from exc
            if isinstance(parsed, dict) and isinstance(parsed.get("keys"), list):
                for item in parsed["keys"]:
                    if isinstance(item, dict):
                        entries[str(item.get("kid") or "").strip()] = str(item.get("publicKeyPem") or "")
            elif isinstance(parsed, dict):
                entries = {str(k): str(v) for k, v in parsed.items()}
            else:
                raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
        else:
            pem = str(
                os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_PUBLIC_KEY_PEM")
                or os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_PEM")
                or ""
            ).strip()
            if pem:
                _, derived = _load_public_key(pem)
                configured_kid = str(
                    os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_KEY_ID")
                    or os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_KEY_ID")
                    or derived
                ).strip()
                entries[configured_kid] = pem
    if not entries:
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_UNAVAILABLE")
    ring: dict[str, Ed25519PublicKey] = {}
    for kid, pem in entries.items():
        if not _KID.fullmatch(kid):
            raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
        key, derived = _load_public_key(pem)
        if kid != derived:
            raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_ID_MISMATCH")
        ring[kid] = key
    return ring


def verify_access_assertion(
    assertion: str,
    *,
    public_keys: Mapping[str, str] | None = None,
    issuer: str | None = None,
    now_seconds: int | None = None,
) -> dict[str, Any]:
    encoded = str(assertion or "").strip()
    parts = encoded.split(".")
    if len(parts) != 3:
        raise AccessAssertionRejected("access assertion must be a compact JWT")
    header = _decode_segment(parts[0], "header")
    claims = _decode_segment(parts[1], "claims")
    if set(header) - _ALLOWED_HEADERS or set(claims) - _ALLOWED_CLAIMS:
        raise AccessAssertionRejected("access assertion contains unsupported fields")
    if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
        raise AccessAssertionRejected("access assertion algorithm is not allowed")
    kid = str(header.get("kid") or "").strip()
    ring = resolve_public_key_ring(public_keys)
    public_key = ring.get(kid)
    if public_key is None:
        raise AccessAssertionRejected("access assertion key id is not trusted")
    try:
        signature_padding = "=" * (-len(parts[2]) % 4)
        signature = base64.urlsafe_b64decode(parts[2] + signature_padding)
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except (InvalidSignature, ValueError, UnicodeEncodeError) as exc:
        raise AccessAssertionRejected("access assertion signature is invalid") from exc

    expected_issuer = str(issuer or os.environ.get("AUTH_JWT_ISSUER") or "quantum-auth")
    if claims.get("version") != ASSERTION_VERSION:
        raise AccessAssertionRejected("access assertion version is unsupported")
    if claims.get("iss") != expected_issuer or claims.get("aud") != "vvault":
        raise AccessAssertionRejected("access assertion issuer or audience is invalid")
    owner = str(claims.get("vvault_owner_id") or "").strip()
    if not _UUID.fullmatch(owner):
        raise AccessAssertionRejected("access assertion owner is invalid")
    relying_party_id = str(claims.get("relying_party_id") or "").strip()
    if relying_party_id not in ALLOWED_RELYING_PARTIES:
        raise AccessAssertionRejected("access assertion relying party is invalid")
    for required in ("sub", "sid", "jti"):
        if not str(claims.get(required) or "").strip():
            raise AccessAssertionRejected(f"access assertion {required} claim is required")
    scopes = claims.get("scopes")
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(not isinstance(scope, str) or scope not in ALLOWED_SCOPES for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        raise AccessAssertionRejected("access assertion scopes are invalid")
    try:
        issued_at = int(claims.get("iat"))
        expires_at = int(claims.get("exp"))
    except (TypeError, ValueError) as exc:
        raise AccessAssertionRejected("access assertion iat and exp are required") from exc
    instant = int(time.time() if now_seconds is None else now_seconds)
    if issued_at > instant + 30 or expires_at <= instant or expires_at - issued_at > 60:
        raise AccessAssertionRejected("access assertion is expired or outside the allowed lifetime")
    return {
        "subject": str(claims["sub"]).strip(),
        "ownerUserId": owner,
        "sessionId": str(claims["sid"]).strip(),
        "assertionId": str(claims["jti"]).strip(),
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "keyId": kid,
        "scopes": frozenset(scopes),
        "relyingPartyId": relying_party_id,
        "ownerFingerprint": hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16],
    }


def required_scope(method: str, path: str) -> str | None:
    """Return the least privilege required by supported assertion routes."""
    scopes = required_scopes(method, path)
    return sorted(scopes)[0] if scopes else None


def required_scopes(method: str, path: str) -> frozenset[str]:
    """Return every scope required by a supported assertion route.

    Most legacy routes require one scope. AUTO context is deliberately
    stricter because one projection joins profile, transcript, and approved
    knowledge authorities; all three read grants must be present.
    """
    normalized_method = str(method or "GET").upper()
    normalized_path = str(path or "")
    if normalized_method == "POST" and normalized_path == "/api/chatty/work-programs/active-resolve":
        # Owner/thread-bound lookup only; no program or event is written.
        return frozenset({"work:read"})
    if normalized_path.startswith("/api/chatty/work-programs"):
        if normalized_method in {"GET", "HEAD"} or normalized_path.endswith(
            ("/context", "/preflight-inspect", "/scope-resolve", "/evidence/resolve")
        ):
            return frozenset({"work:read"})
        if normalized_method == "POST":
            return frozenset({"work:append"})
        return frozenset()
    if normalized_method == "POST":
        if re.fullmatch(r"/api/chatty/threads/[^/]+/participant-frame", normalized_path):
            # POST carries addressing inputs for an owner/member-checked read
            # projection. It does not create threads, memberships, or events.
            return frozenset({"transcripts:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/context":
            return frozenset({"identity:read", "transcripts:read", "knowledge:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/exchanges":
            return frozenset({"transcripts:append"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/hydro/events":
            return frozenset({"transcripts:append"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/threads/index":
            return frozenset({"transcripts:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/hydro/catalog":
            return frozenset({"identity:read", "constructs:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/hydro/recovery/index":
            # Service-token authority is enforced by the route. User access
            # assertions can never authorize cross-owner recovery discovery.
            return frozenset()
        if normalized_path == "/api/chatty/system-runtimes/auto-001/actions/grants":
            return frozenset({"transcripts:read", "transcripts:append"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/actions/events":
            return frozenset({"transcripts:append"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/code/projects/binding":
            return frozenset({"work:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/code/threads/history":
            return frozenset({"work:read", "transcripts:read"})
        if normalized_path == "/api/chatty/system-runtimes/auto-001/code/proposal-contexts":
            return frozenset({"work:append"})
        if normalized_path in {
            "/api/chatty/system-runtimes/auto-001/hydro/dispatches",
            "/api/chatty/system-runtimes/auto-001/hydro/cancellations",
            "/api/chatty/system-runtimes/auto-001/hydro/worker-receipts",
        }:
            return frozenset({"transcripts:append"})
        # Canonical system-runtime registration is never authorized by a
        # user access assertion. The route enforces service/admin authority.
        if normalized_path in {
            "/api/chatty/system-runtimes/auto-001/register",
            "/api/chatty/system-runtimes/auto-001/registration/preflight",
        }:
            return frozenset()
    if normalized_method in {"GET", "HEAD"}:
        if "/transcript/" in normalized_path or "/threads" in normalized_path:
            return frozenset({"transcripts:read"})
        if "/memories" in normalized_path or "/ledger" in normalized_path:
            return frozenset({"memory:read"})
        if "/identity" in normalized_path or "/capsule" in normalized_path or "/account-context" in normalized_path or "/human-context" in normalized_path:
            return frozenset({"identity:read"})
        if "/knowledge" in normalized_path:
            return frozenset({"knowledge:read"})
        return frozenset({"constructs:read"})
    if normalized_method == "POST" and (
        "/transcript/" in normalized_path
        or normalized_path == "/api/chatty/message"
        or normalized_path.endswith("/events")
    ):
        return frozenset({"transcripts:append"})
    return frozenset()
