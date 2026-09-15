"""Owner-scoped publication of approved shared and construct knowledge."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from vvault.server import chatty_body_service, knowledge_contract, offline_snapshot_service


RECEIPT_VERSION = "life-vvault-knowledge-publication-receipt/v1"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CALLSIGN = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9]{3}$")
_SHARED_KINDS = {"shared_ecosystem", "shared_person_profile"}
_FORBIDDEN_RUNTIME_FIELDS = {
    "expectedanswer", "expectedanswers", "expectedsignals", "answerkey",
    "rubric", "grader", "gradermetadata", "evaluationanswer",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_grading_material(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in _FORBIDDEN_RUNTIME_FIELDS:
                raise ValueError(f"construct-visible publication contains forbidden grading field: {path}.{key}")
            _reject_grading_material(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_grading_material(child, path=f"{path}[{index}]")


def _sign(value: dict[str, Any], private_key_pem: str | None) -> tuple[str, str]:
    key = offline_snapshot_service._load_private_key(private_key_pem)
    key_document = offline_snapshot_service.public_key_document(private_key_pem=private_key_pem)
    signature = base64.b64encode(key.sign(_canonical_json(value).encode("utf-8"))).decode("ascii")
    return key_document["keyId"], signature


def _document_id(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-.")
    if not normalized:
        raise ValueError("documentId is required")
    return f"life.vvault.knowledge.{normalized}"


def _source_row(owner_user_id: str, artifact_id: str) -> dict[str, Any]:
    try:
        record_id = str(uuid.UUID(str(artifact_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"source artifact_id must be a canonical OVVAULTS UUID: {artifact_id}") from exc
    rows = chatty_body_service._rows(
        """
        SELECT id::text AS id,user_id::text AS user_id,construct_id,
               filename,storage_path,sha256,content,metadata,created_at,updated_at
        FROM ovvaults.vault_files
        WHERE user_id=%s AND id=%s::uuid
        """,
        (owner_user_id, record_id),
    )
    if len(rows) != 1:
        raise ValueError(f"source artifact not found for authenticated owner: {artifact_id}")
    return rows[0]


def _content_digest(row: dict[str, Any]) -> str:
    stored = str(row.get("sha256") or "").strip().lower()
    content = row.get("content")
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        raw = content.encode("utf-8")
    elif content is not None:
        raw = _canonical_json(content).encode("utf-8")
    else:
        raw = b""
    computed = hashlib.sha256(raw).hexdigest() if raw else ""
    if _SHA256.fullmatch(stored):
        if computed and computed != stored:
            raise ValueError(f"source artifact stored hash does not match content: {row.get('id')}")
        return stored
    if computed:
        return computed
    raise ValueError(f"source artifact has no verifiable canonical content hash: {row.get('id')}")


def _verify_sources(
    document: dict[str, Any], owner_user_id: str, construct_id: str | None
) -> dict[str, dict[str, Any]]:
    verified: dict[str, dict[str, Any]] = {}
    for source in document.get("sources", []):
        if not isinstance(source, dict):
            raise ValueError("knowledge sources must be objects")
        source_id = str(source.get("sourceId") or "").strip()
        if not source_id or source_id in verified:
            raise ValueError("knowledge sourceId values must be present and unique")
        row = _source_row(owner_user_id, source_id)
        actual_hash = _content_digest(row)
        expected_hash = str(source.get("sha256") or "").strip().lower()
        if expected_hash != actual_hash:
            raise ValueError(f"source sha256 does not match canonical OVVAULTS content: {source_id}")
        path = str(row.get("storage_path") or row.get("filename") or "").strip()
        locator = str(source.get("locator") or "").strip()
        if locator and locator != path:
            raise ValueError(f"source locator does not match canonical OVVAULTS path: {source_id}")
        row_construct = str(row.get("construct_id") or "").strip().lower()
        if construct_id and row_construct != construct_id:
            raise ValueError(f"construct-unique source belongs to another construct: {source_id}")
        source_kind = str(source.get("sourceKind") or "").strip()
        if source_kind == "verified_record":
            provider = "document"
            quality = "verified-record"
        elif source_kind in {"construct_history", "historical"}:
            classification = knowledge_contract.transcript_source(path)
            provider = classification["provider"]
            quality = classification["source_quality"]
        else:
            raise ValueError(f"runtime publication sourceKind is not independently verifiable: {source_kind}")
        verified[source_id] = {
            "artifact_id": str(row["id"]),
            "sha256": actual_hash,
            "provider": provider,
            "source_quality": quality,
            "path": path,
        }
    if not verified:
        raise ValueError("at least one verified canonical source is required")
    return verified


def _claim_document(
    document: dict[str, Any],
    owner_user_id: str,
    *,
    construct_id: str | None = None,
    verified_sources: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sources = verified_sources or {
        str(source.get("sourceId")): source
        for source in document.get("sources", []) if isinstance(source, dict)
    }
    kind = str(document.get("documentKind") or "")
    scope = "construct-unique" if kind == "construct_unique" else "shared"
    callsign = str(construct_id or "").strip().lower() or None
    claims = []
    for claim in document.get("claims", []):
        source_rows = []
        for source_id in claim.get("sourceIds", []):
            source = sources.get(str(source_id))
            if not source:
                raise ValueError(f"claim references an unverified source: {source_id}")
            if "artifact_id" in source:
                source_rows.append(dict(source))
                continue
            source_kind = str(source.get("sourceKind") or "")
            source_rows.append({
                "artifact_id": str(source_id),
                "sha256": str(source.get("sha256") or ""),
                "provider": "user-declared" if source_kind == "user_declared" else "document",
                "source_quality": "verified-record" if source_kind == "verified_record" else "approved-construct-history",
                "path": source.get("locator"),
            })
        effective_from = claim.get("effectiveFrom")
        if isinstance(effective_from, str) and len(effective_from) == 10:
            effective_from = f"{effective_from}T00:00:00Z"
        effective_until = claim.get("effectiveUntil")
        if isinstance(effective_until, str) and len(effective_until) == 10:
            effective_until = f"{effective_until}T00:00:00Z"
        projected_claim = {
            "claim_id": claim.get("claimId"),
            "subject": claim.get("subject"),
            "predicate": claim.get("predicate"),
            "object": claim.get("object"),
            "evidence_class": str(claim.get("evidenceClass") or "").replace("_", "-"),
            "status": claim.get("status"),
            "effective_from": effective_from,
            "effective_until": effective_until,
            "supersedes_claim_ids": claim.get("supersedes") or [],
            "audience": ["construct"],
            "retrieval_scope": claim.get("retrievalScope") or [],
            "preferred_wording": claim.get("preferredWording"),
            "forbidden_implications": claim.get("forbiddenImplications") or [],
            "sources": source_rows,
        }
        # These authority-bearing fields must be declared by the publishing
        # client and become trusted only after the complete canonical document
        # is signed below. Never derive them from subject or capability prose.
        if claim.get("subjectPrincipalId") is not None:
            projected_claim["subject_principal_id"] = claim.get("subjectPrincipalId")
        if claim.get("claimKind") is not None:
            projected_claim["claim_kind"] = claim.get("claimKind")
        if claim.get("capabilityMetadata") is not None:
            projected_claim["capability_metadata"] = claim.get("capabilityMetadata")
        claims.append(projected_claim)
    return {
        "schema_id": knowledge_contract.SCHEMA_ID,
        "schema_version": knowledge_contract.SCHEMA_VERSION,
        "document_id": _document_id(document.get("documentId")),
        "document_kind": kind,
        "revision": str(document.get("revision")),
        "scope": scope,
        "construct_id": callsign,
        "publication_status": "approved",
        "owner_user_id": owner_user_id,
        "approved_by": (document.get("approval") or {}).get("approvedBy"),
        "approved_at": (document.get("approval") or {}).get("approvedAt"),
        "supersedes_revision": None,
        "claims": claims,
    }


def _carry_forward_claims(
    claim_document: dict[str, Any],
    existing_document: dict[str, Any],
    requested_revision: Any,
    current_revision: int,
) -> None:
    if requested_revision is None:
        return
    if str(requested_revision) != str(current_revision):
        raise ValueError("carryForwardRevision must match the current canonical revision")
    if (
        existing_document.get("document_id") != claim_document.get("document_id")
        or existing_document.get("owner_user_id") != claim_document.get("owner_user_id")
        or existing_document.get("scope") != claim_document.get("scope")
        or existing_document.get("publication_status") != "approved"
    ):
        raise ValueError("canonical carry-forward source is incompatible")
    existing_claims = existing_document.get("claims")
    if not isinstance(existing_claims, list):
        raise ValueError("canonical carry-forward source has invalid claims")
    replacement_ids = {
        str(claim.get("claim_id") or "")
        for claim in claim_document.get("claims", [])
        if isinstance(claim, dict)
    }
    carried = []
    for claim in existing_claims:
        if not isinstance(claim, dict) or str(claim.get("claim_id") or "") in replacement_ids:
            continue
        # Revision 1 shared documents predate the exact runtime-audience
        # contract.  Carry their approved facts forward without preserving the
        # legacy broad-audience marker: resolution remains owner scoped and
        # Core decides which construct receives each selected claim.
        carried.append({**claim, "audience": ["construct"]})
    claim_document["claims"] = [*carried, *claim_document.get("claims", [])]


def publish(
    *,
    owner_user_id: str,
    actor: str,
    document: dict[str, Any],
    construct_id: str | None = None,
    published_at: datetime | None = None,
    private_key_pem: str | None = None,
) -> dict[str, Any]:
    _reject_grading_material(document)
    if document.get("contractVersion") != "life-training-document/v1":
        raise ValueError("unsupported training document contractVersion")
    kind = str(document.get("documentKind") or "")
    if kind not in {*_SHARED_KINDS, "construct_unique"}:
        raise ValueError("documentKind must be shared_ecosystem, shared_person_profile, or construct_unique")
    callsign = str(construct_id or "").strip().lower() or None
    declared_callsign = str((document.get("scope") or {}).get("constructCallsign") or "").strip().lower() or None
    if kind == "construct_unique":
        if not callsign or not _CALLSIGN.fullmatch(callsign):
            raise ValueError("construct_id is required for construct_unique publication")
        if declared_callsign != callsign:
            raise ValueError("document construct scope does not match route construct_id")
    elif callsign or declared_callsign:
        raise ValueError("shared publication must not declare a construct scope")
    if document.get("publicationState") != "approved" or document.get("runtimeActive") is not True:
        raise ValueError("publication requires approved and runtimeActive=true")
    approval = document.get("approval") or {}
    if not approval.get("approvedBy") or not approval.get("approvedAt"):
        raise ValueError("approval evidence is required")
    verified_sources = _verify_sources(document, owner_user_id, callsign)
    claim_document = _claim_document(
        document,
        owner_user_id,
        construct_id=callsign,
        verified_sources=verified_sources,
    )
    errors = knowledge_contract.validate_claim_document(claim_document)
    if errors:
        raise ValueError("; ".join(errors))
    document_id = claim_document["document_id"]
    revision = claim_document["revision"]
    folder = f"instances/{callsign}/documents/Knowledge" if callsign else "account/training/approved"
    path = f"{folder}/{document_id}/r{revision}.json"
    timestamp = (published_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    with chatty_body_service._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text AS id,sha256,metadata,content FROM ovvaults.vault_files
                   WHERE user_id=%s AND metadata->>'document_id'=%s
                     AND coalesce(construct_id::text,'')=%s
                   ORDER BY (metadata->>'revision')::int DESC LIMIT 1""",
                (owner_user_id, document_id, callsign or ""),
            )
            existing = cur.fetchone()
            supersedes_revision = None
            if existing:
                row = dict(existing)
                current_revision = int((row.get("metadata") or {}).get("revision") or 0)
                if int(revision) <= current_revision:
                    if int(revision) < current_revision:
                        raise ValueError("publication revision must increase monotonically")
                    try:
                        existing_document = json.loads(str(row.get("content") or "{}"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        existing_document = {}
                    claim_document["supersedes_revision"] = existing_document.get("supersedes_revision")
                    idempotent_content = _canonical_json(claim_document)
                    idempotent_digest = hashlib.sha256(idempotent_content.encode("utf-8")).hexdigest()
                    if str(row.get("sha256")) == idempotent_digest:
                        return _receipt(
                            owner_user_id, actor, document_id, revision, row["id"], path,
                            idempotent_digest, timestamp, True, claim_document["scope"], callsign,
                            private_key_pem=private_key_pem,
                        )
                    raise ValueError("publication revision must increase monotonically")
                try:
                    previous_document = json.loads(str(row.get("content") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError("canonical carry-forward source is malformed") from exc
                _carry_forward_claims(
                    claim_document,
                    previous_document,
                    document.get("carryForwardRevision"),
                    current_revision,
                )
                carry_forward_errors = knowledge_contract.validate_claim_document(claim_document)
                if carry_forward_errors:
                    raise ValueError("; ".join(carry_forward_errors))
                supersedes_revision = str(current_revision)
            claim_document["supersedes_revision"] = supersedes_revision
            content = _canonical_json(claim_document)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            signature_payload = {
                "contract": "life-vvault-knowledge-publication/v1",
                "authority": "ovvaults.vault_files",
                "ownerUserId": owner_user_id,
                "documentId": document_id,
                "documentKind": kind,
                "revision": revision,
                "scope": claim_document["scope"],
                "constructId": callsign,
                "sha256": digest,
                "sourceArtifacts": sorted(
                    {
                        source["artifact_id"]: source["sha256"]
                        for claim in claim_document["claims"]
                        for source in claim.get("sources", [])
                    }.items()
                ),
            }
            key_id, signature = _sign(signature_payload, private_key_pem)
            metadata = {
                "artifact_id": document_id,
                "contract_version": knowledge_contract.CONTRACT_VERSION,
                "document_id": document_id,
                "document_kind": kind,
                "revision": revision,
                "publication_status": "approved",
                "scope": claim_document["scope"],
                "signature_algorithm": "Ed25519",
                "signature_key_id": key_id,
                "signature": signature,
                "signature_payload": signature_payload,
            }
            cur.execute(
                """INSERT INTO ovvaults.vault_files
                  (user_id,bucket,object_key,filename,storage_path,content_type,file_type,size_bytes,sha256,content,metadata,construct_id,is_system,created_at,updated_at)
                  VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',%s,%s,%s,%s::jsonb,%s,false,%s,%s)
                  RETURNING id::text AS id""",
                (
                    owner_user_id,
                    f"users/{owner_user_id}/{path}",
                    path,
                    path,
                    len(content.encode("utf-8")),
                    digest,
                    content,
                    json.dumps(metadata),
                    callsign,
                    timestamp,
                    timestamp,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return _receipt(
        owner_user_id, actor, document_id, revision, row["id"], path,
        digest, timestamp, False, claim_document["scope"], callsign,
        private_key_pem=private_key_pem,
    )


def _receipt(
    owner: str,
    actor: str,
    document_id: str,
    revision: str,
    record_id: str,
    path: str,
    digest: str,
    published_at: str,
    idempotent: bool,
    scope: str,
    construct_id: str | None,
    *,
    private_key_pem: str | None,
) -> dict[str, Any]:
    receipt = {
        "version": RECEIPT_VERSION,
        "ownerUserId": owner,
        "actor": actor,
        "documentId": document_id,
        "revision": revision,
        "recordId": str(record_id),
        "storagePath": path,
        "sha256": digest,
        "publishedAt": published_at,
        "scope": scope,
        "constructId": construct_id,
        "idempotent": idempotent,
        "transcriptsMutated": False,
        "knowledgeReference": {
            "artifact_id": str(record_id),
            "revision": revision,
            "sha256": digest,
            "required": True,
        },
    }
    key_id, signature = _sign(receipt, private_key_pem)
    return {**receipt, "receiptAlgorithm": "Ed25519", "receiptKeyId": key_id, "receiptSignature": signature}
