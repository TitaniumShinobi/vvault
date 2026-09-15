"""Append-only activation of exact canonical knowledge references."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from vvault.server import (
    canonical_projection_signing,
    chatty_body_service,
    knowledge_contract,
    offline_snapshot_service,
)


RECEIPT_VERSION = "life-vvault-knowledge-activation-receipt/v1"
OWNER_SHARED_ACTIVATION_VERSION = "life-vvault-owner-shared-activation/v1"


def _reference_identity(value: Any) -> tuple[str, str | None, str | None, bool]:
    if not isinstance(value, dict):
        raise ValueError("knowledgeReference must be an object")
    artifact_id = str(value.get("artifact_id") or value.get("artifactId") or "").strip()
    revision = str(value.get("revision") or "").strip() or None
    digest = str(value.get("sha256") or value.get("contentHash") or "").strip().lower() or None
    required = value.get("required") is not False
    if not artifact_id or not revision or not digest:
        raise ValueError("knowledgeReference requires artifact_id, revision, and sha256")
    return artifact_id, revision, digest, required


def _same_reference(left: Any, right: dict[str, Any]) -> bool:
    try:
        artifact_id, revision, digest, required = _reference_identity(left)
    except ValueError:
        return False
    return (
        artifact_id == right["artifact_id"]
        and revision == right["revision"]
        and digest == right["sha256"]
        and required == right["required"]
    )


def _artifact_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("artifact_id") or value.get("artifactId") or "").strip()
    return ""


def _signed_receipt(receipt: dict[str, Any], private_key_pem: str | None) -> dict[str, Any]:
    key = offline_snapshot_service._load_private_key(private_key_pem)
    key_document = offline_snapshot_service.public_key_document(private_key_pem=private_key_pem)
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        **receipt,
        "receiptAlgorithm": "Ed25519",
        "receiptKeyId": key_document["keyId"],
        "receiptSignature": base64.b64encode(key.sign(canonical)).decode("ascii"),
    }


def owner_shared_references(
    *,
    owner_user_id: str,
    private_key_pem: str | None = None,
    statement_timeout_ms: int | None = None,
    connection_timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Read the exact latest owner-wide shared activation set."""
    timeout_kwargs = {}
    if statement_timeout_ms is not None:
        timeout_kwargs["statement_timeout_ms"] = statement_timeout_ms
    if connection_timeout_seconds is not None:
        timeout_kwargs["connection_timeout_seconds"] = connection_timeout_seconds
    rows = chatty_body_service._rows(
        """SELECT DISTINCT ON (metadata->>'artifact_id') content,sha256,metadata,updated_at
           FROM ovvaults.vault_files
           WHERE user_id=%s AND metadata->>'contract_version'=%s
             AND metadata->>'activation_status'='active'
           ORDER BY metadata->>'artifact_id', updated_at DESC""",
        (owner_user_id, OWNER_SHARED_ACTIVATION_VERSION),
        **timeout_kwargs,
    )
    return owner_shared_references_from_rows(
        rows,
        owner_user_id=owner_user_id,
        private_key_pem=private_key_pem,
    )


def owner_shared_references_from_rows(
    rows: list[dict[str, Any]],
    *,
    owner_user_id: str,
    private_key_pem: str | None = None,
) -> list[dict[str, Any]]:
    """Verify a transactionally supplied activation set without another read.

    The caller remains responsible for selecting the exact owner-scoped latest
    rows.  This function applies the same content, signature, owner, and scope
    checks as the ordinary database-backed loader.
    """
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("authenticated owner_user_id is required")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("stored owner shared activation rows are malformed")
    references: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row.get("content") or "")
        if str(row.get("sha256") or "") != hashlib.sha256(raw.encode("utf-8")).hexdigest():
            raise ValueError("stored owner shared activation receipt hash is invalid")
        try:
            receipt = json.loads(raw)
            reference = receipt["knowledgeReference"]
            artifact_id, revision, digest, required = _reference_identity(reference)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stored owner shared activation receipt is malformed") from exc
        signed = {
            key: value for key, value in receipt.items()
            if key not in {"receiptAlgorithm", "receiptKeyId", "receiptSignature"}
        }
        try:
            canonical_projection_signing.verify_canonical_payload(
                signed,
                {
                    "algorithm": receipt.get("receiptAlgorithm"),
                    "keyId": receipt.get("receiptKeyId"),
                    "signature": receipt.get("receiptSignature"),
                },
                private_key_pem=private_key_pem,
            )
        except RuntimeError:
            raise
        except ValueError as exc:
            raise ValueError("stored owner shared activation receipt signature is invalid") from exc
        if receipt.get("ownerUserId") != owner or receipt.get("scope") != "shared":
            raise ValueError("stored owner shared activation receipt owner or scope is invalid")
        references.append({
            "artifact_id": artifact_id,
            "revision": revision,
            "sha256": digest,
            "required": required,
        })
    return references


def activate_shared(
    *,
    owner_user_id: str,
    actor: str,
    knowledge_reference: dict[str, Any],
    activated_at: datetime | None = None,
    private_key_pem: str | None = None,
) -> dict[str, Any]:
    """Append an owner-level shared activation; never mutate construct profiles."""
    artifact_id, revision, digest, required = _reference_identity(knowledge_reference)
    reference = {
        "artifact_id": artifact_id,
        "revision": revision,
        "sha256": digest,
        "required": required,
    }
    projection, status = knowledge_contract.resolve_knowledge_references(
        owner_user_id=owner_user_id,
        instance_id="owner-shared-000",
        references=[reference],
        private_key_pem=private_key_pem,
    )
    if status != 200 or projection.get("success") is not True or len(projection.get("artifacts") or []) != 1:
        raise ValueError("shared knowledge reference could not be resolved exactly for activation")
    artifact = projection["artifacts"][0]
    if artifact.get("scope") != "shared" or artifact.get("construct_id") is not None:
        raise ValueError("owner-level activation accepts shared knowledge only")

    current = owner_shared_references(
        owner_user_id=owner_user_id, private_key_pem=private_key_pem
    )
    same_artifact = [item for item in current if _artifact_id(item) == artifact_id]
    idempotent = any(_same_reference(item, reference) for item in same_artifact)
    if idempotent:
        return _signed_receipt({
            "version": OWNER_SHARED_ACTIVATION_VERSION,
            "ownerUserId": owner_user_id,
            "actor": actor,
            "targetField": "ownerSharedRefs",
            "knowledgeReference": reference,
            "documentId": artifact.get("document_id"),
            "documentKind": artifact.get("document_kind"),
            "scope": "shared",
            "activatedAt": (activated_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
            "idempotent": True,
            "readbackVerified": True,
            "transcriptsMutated": False,
        }, private_key_pem)

    timestamp = (activated_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    receipt = _signed_receipt({
        "version": OWNER_SHARED_ACTIVATION_VERSION,
        "ownerUserId": owner_user_id,
        "actor": actor,
        "targetField": "ownerSharedRefs",
        "knowledgeReference": reference,
        "documentId": artifact.get("document_id"),
        "documentKind": artifact.get("document_kind"),
        "scope": "shared",
        "activatedAt": timestamp,
        "idempotent": False,
        "readbackVerified": True,
        "supersedes": same_artifact[0] if same_artifact else None,
        "transcriptsMutated": False,
    }, private_key_pem)
    content = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    path = f"account/training/activations/{artifact_id}/{revision}-{content_hash[:12]}.json"
    metadata = {
        "artifact_id": artifact_id,
        "contract_version": OWNER_SHARED_ACTIVATION_VERSION,
        "revision": revision,
        "sha256": digest,
        "activation_status": "active",
        "scope": "shared",
    }
    with chatty_body_service._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ovvaults.vault_files
                   (user_id,bucket,object_key,filename,storage_path,content_type,file_type,
                    size_bytes,sha256,content,metadata,construct_id,is_system,created_at,updated_at)
                   VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',%s,%s,%s,%s::jsonb,NULL,false,%s,%s)
                   RETURNING id::text AS id""",
                (
                    owner_user_id, f"users/{owner_user_id}/{path}", path, path,
                    len(content.encode("utf-8")), content_hash, content,
                    json.dumps(metadata), timestamp, timestamp,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return {**receipt, "recordId": row["id"], "storagePath": path, "receiptSha256": content_hash}


def activate(
    *,
    owner_user_id: str,
    actor: str,
    construct_id: str,
    knowledge_reference: dict[str, Any],
    activated_at: datetime | None = None,
) -> dict[str, Any]:
    callsign = chatty_body_service.normalize_callsign(construct_id)
    artifact_id, revision, digest, required = _reference_identity(knowledge_reference)
    reference = {
        "artifact_id": artifact_id,
        "revision": revision,
        "sha256": digest,
        "required": required,
    }
    projection, status = knowledge_contract.resolve_knowledge_references(
        owner_user_id=owner_user_id,
        instance_id=callsign,
        references=[reference],
    )
    if status != 200 or projection.get("success") is not True or len(projection.get("artifacts") or []) != 1:
        raise ValueError("knowledge reference could not be resolved exactly for activation")
    artifact = projection["artifacts"][0]
    target_field = "canonRefs" if artifact.get("scope") == "shared" else "knowledgeRefs"
    if artifact.get("scope") == "construct-unique" and artifact.get("construct_id") != callsign:
        raise ValueError("construct-unique knowledge cannot be activated for another construct")

    profile_result = chatty_body_service.construct_profile(callsign)
    if profile_result.status != "body_native":
        raise RuntimeError("canonical construct profile is unavailable for knowledge activation")
    profile = profile_result.payload.get("profile") or {}
    current = list(profile.get(target_field) or [])
    same_artifact = [item for item in current if _artifact_id(item) == artifact_id]
    if same_artifact and not any(_same_reference(item, reference) for item in same_artifact):
        raise ValueError("knowledge artifact is already activated with a different revision or hash")
    idempotent = any(_same_reference(item, reference) for item in current)
    if not idempotent:
        current.append(reference)
        updated = chatty_body_service.update_construct_profile(
            callsign,
            {target_field: current},
            user_id=owner_user_id,
        )
        if updated.status != "body_native" or updated.payload.get("success") is not True:
            raise RuntimeError("canonical construct profile rejected knowledge activation")

    readback = chatty_body_service.construct_profile(callsign)
    if readback.status != "body_native":
        raise RuntimeError("canonical knowledge activation readback is unavailable")
    readback_profile = readback.payload.get("profile") or {}
    if not any(_same_reference(item, reference) for item in readback_profile.get(target_field) or []):
        raise RuntimeError("canonical knowledge activation readback did not contain the exact reference")
    combined = list(readback_profile.get("canonRefs") or []) + list(readback_profile.get("knowledgeRefs") or [])
    verified, verified_status = knowledge_contract.resolve_knowledge_references(
        owner_user_id=owner_user_id,
        instance_id=callsign,
        references=combined,
    )
    if verified_status != 200 or verified.get("success") is not True:
        raise RuntimeError("activated canonical knowledge projection failed readback verification")

    timestamp = (activated_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    receipt_body = {
        "version": RECEIPT_VERSION,
        "ownerUserId": owner_user_id,
        "actor": actor,
        "constructId": callsign,
        "targetField": target_field,
        "knowledgeReference": reference,
        "documentId": artifact.get("document_id"),
        "scope": artifact.get("scope"),
        "activatedAt": timestamp,
        "idempotent": idempotent,
        "readbackVerified": True,
        "transcriptsMutated": False,
    }
    receipt_body["receiptHash"] = hashlib.sha256(
        json.dumps(receipt_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return receipt_body
