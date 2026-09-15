"""Owner-bound publication and signed read projection for human/shared context."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature

from vvault.server import chatty_body_service, offline_snapshot_service


CONTRACT_VERSION = "life-human-context/v1"
PROJECTION_VERSION = "life-human-context-projection/v1"
CONTEXT_SEA_ELIGIBLE = False
LEGACY_ONLY_REASON = "retired-token-filtered-human-context"
ALLOWED_DOCUMENTS = {
    "life-ecosystem-shared": "shared_ecosystem",
    "devon-woodson-shared-profile": "shared_person_profile",
}
PRIVATE_TOKENS = {
    "email", "phone", "address", "street", "postal", "ssn", "social-security",
    "date-of-birth", "birth-date", "dob", "exact-age", "family-private", "contact",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _owner_fingerprint(owner_user_id: str) -> str:
    return hashlib.sha256(str(owner_user_id).encode("utf-8")).hexdigest()


def _claim_is_public(claim: dict[str, Any]) -> bool:
    descriptor = " ".join(
        str(claim.get(key) or "")
        for key in ("claimId", "subject", "predicate", "object", "preferredWording")
    ).lower().replace("_", "-")
    return not any(token in descriptor for token in PRIVATE_TOKENS)


def validate_publication(document: Any, *, owner_user_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["document must be an object"]
    if document.get("contractVersion") != "life-training-document/v1":
        errors.append("unsupported training document contractVersion")
    expected_kind = ALLOWED_DOCUMENTS.get(str(document.get("documentId") or ""))
    if not expected_kind or document.get("documentKind") != expected_kind:
        errors.append("documentId/documentKind is not approved for shared publication")
    if document.get("publicationState") != "approved" or document.get("runtimeActive") is not True:
        errors.append("publication requires approved and runtimeActive=true")
    if not isinstance(document.get("revision"), int) or document.get("revision", 0) < 1:
        errors.append("positive integer revision is required")
    approval = document.get("approval")
    if not isinstance(approval, dict) or not approval.get("approvedBy") or not approval.get("approvedAt"):
        errors.append("approval evidence is required")
    if not owner_user_id:
        errors.append("authenticated owner is required")
    claims = document.get("claims")
    if not isinstance(claims, list):
        errors.append("claims must be an array")
    else:
        ids = [str(item.get("claimId") or "") for item in claims if isinstance(item, dict)]
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            errors.append("claims require unique non-empty claimId values")
        if any(isinstance(item, dict) and not _claim_is_public(item) for item in claims):
            errors.append("document contains fields excluded from shared human context")
    return errors


def _public_document(document: dict[str, Any]) -> dict[str, Any]:
    source_hashes = {
        str(source.get("sourceId")): str(source.get("sha256"))
        for source in document.get("sources", [])
        if isinstance(source, dict) and source.get("sourceId") and source.get("sha256")
    }
    claims = []
    for claim in document.get("claims", []):
        if not isinstance(claim, dict) or not _claim_is_public(claim):
            continue
        claims.append({
            key: claim.get(key)
            for key in (
                "claimId", "subject", "predicate", "object", "evidenceClass", "status",
                "effectiveFrom", "supersedes", "sourceIds", "audience", "retrievalScope",
                "preferredWording", "forbiddenImplications",
            )
            if key in claim
        })
    return {
        "documentId": document["documentId"],
        "documentKind": document["documentKind"],
        "revision": document["revision"],
        "scope": document.get("scope") or {},
        "claims": claims,
        "sourceHashes": source_hashes,
    }


def issue_projection(
    *, owner_user_id: str, documents: list[dict[str, Any]], private_key_pem: str | None = None,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    if not owner_user_id:
        raise ValueError("authenticated owner is required")
    now = issued_at or datetime.now(timezone.utc)
    payload = {
        "ownerUserId": owner_user_id,
        "ownerFingerprint": _owner_fingerprint(owner_user_id),
        "documents": documents,
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "authority": "ovvaults.vault_files",
    }
    key = offline_snapshot_service._load_private_key(private_key_pem)
    key_document = offline_snapshot_service.public_key_document(private_key_pem=private_key_pem)
    return {
        "version": PROJECTION_VERSION,
        "algorithm": "Ed25519",
        "keyId": key_document["keyId"],
        "payload": payload,
        "signature": base64.b64encode(key.sign(_canonical_json(payload))).decode("ascii"),
    }


def _verify_projection(envelope: dict[str, Any], *, owner_user_id: str, private_key_pem: str | None = None) -> None:
    if envelope.get("version") != PROJECTION_VERSION or envelope.get("algorithm") != "Ed25519":
        raise ValueError("stored human context projection contract is invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("ownerUserId") != owner_user_id:
        raise ValueError("stored human context projection owner mismatch")
    key = offline_snapshot_service._load_private_key(private_key_pem)
    try:
        key.public_key().verify(base64.b64decode(str(envelope.get("signature") or ""), validate=True), _canonical_json(payload))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("stored human context projection signature is invalid") from exc


def publish(
    *, owner_user_id: str, actor: str, document: dict[str, Any], private_key_pem: str | None = None,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    errors = validate_publication(document, owner_user_id=owner_user_id)
    if errors:
        raise ValueError("; ".join(errors))
    public_document = _public_document(document)
    envelope = issue_projection(
        owner_user_id=owner_user_id,
        documents=[public_document],
        private_key_pem=private_key_pem,
        issued_at=published_at,
    )
    content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = public_document["documentId"]
    revision = public_document["revision"]
    path = f"account/training/approved/{document_id}/r{revision}.json"
    timestamp = (published_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    metadata = {
        "artifact_id": f"life.vvault.human-context.{document_id}",
        "contract_version": CONTRACT_VERSION,
        "document_id": document_id,
        "revision": revision,
        "publication_status": "approved",
        "owner_fingerprint": _owner_fingerprint(owner_user_id),
    }
    with chatty_body_service._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovvaults.vault_files (
                  user_id,bucket,object_key,filename,storage_path,content_type,file_type,
                  size_bytes,sha256,content,metadata,construct_id,is_system,created_at,updated_at
                ) VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',%s,%s,%s,%s::jsonb,NULL,false,%s,%s)
                ON CONFLICT (bucket,object_key) DO NOTHING
                RETURNING id::text AS id
                """,
                (owner_user_id, f"users/{owner_user_id}/{path}", path, path, len(content.encode("utf-8")), sha256, content, json.dumps(metadata), timestamp, timestamp),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("publication revision already exists")
        conn.commit()
    return {
        "version": "life-human-context-publication-receipt/v1",
        "ownerUserId": owner_user_id,
        "ownerFingerprint": _owner_fingerprint(owner_user_id),
        "actor": actor,
        "documentId": document_id,
        "revision": revision,
        "recordId": str(dict(row)["id"]),
        "storagePath": path,
        "sha256": sha256,
        "projectionKeyId": envelope["keyId"],
        "projectionSignature": envelope["signature"],
        "publishedAt": timestamp,
        "transcriptsMutated": False,
    }


def read_projection(*, owner_user_id: str, private_key_pem: str | None = None) -> dict[str, Any]:
    rows = chatty_body_service._rows(
        """
        SELECT DISTINCT ON (metadata->>'document_id') id::text AS id,content,sha256,metadata,updated_at
        FROM ovvaults.vault_files
        WHERE user_id=%s AND metadata->>'contract_version'=%s
          AND metadata->>'publication_status'='approved'
        ORDER BY metadata->>'document_id', (metadata->>'revision')::int DESC, updated_at DESC
        """,
        (owner_user_id, CONTRACT_VERSION),
    )
    documents = []
    evidence = []
    for row in rows:
        raw_content = str(row.get("content") or "")
        if str(row.get("sha256") or "") != hashlib.sha256(raw_content.encode("utf-8")).hexdigest():
            raise ValueError("stored human context projection hash is invalid")
        try:
            envelope = json.loads(raw_content)
            _verify_projection(envelope, owner_user_id=owner_user_id, private_key_pem=private_key_pem)
            document = envelope["payload"]["documents"][0]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("stored human context projection is malformed")
        documents.append(document)
        evidence.append({
            "recordId": str(row.get("id")),
            "documentId": document["documentId"],
            "revision": document["revision"],
            "sha256": str(row.get("sha256") or ""),
        })
    projection = issue_projection(owner_user_id=owner_user_id, documents=documents, private_key_pem=private_key_pem)
    projection["evidence"] = evidence
    return projection


def context_sea_eligibility() -> dict[str, Any]:
    """Declare that the retired heuristic projection is never context authority."""
    return {
        "eligible": CONTEXT_SEA_ELIGIBLE,
        "reason": LEGACY_ONLY_REASON,
        "replacementAuthorities": [
            "life-account-context-projection/v1",
            "life.vvault.knowledge-resolution",
        ],
    }
