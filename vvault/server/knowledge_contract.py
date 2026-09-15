"""Read-only, owner-scoped claim-document resolution for Chatty Core.

The resolver deliberately does not discover knowledge by scanning folders.
Callers must provide canonical OVVAULTS record or document identifiers plus
the revision/hash they expect.  VVAULT validates authority and provenance;
it does not choose which claims should enter an inference context.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from time import monotonic

from vvault.server import canonical_data_contract, canonical_projection_signing, chatty_body_service
from vvault.server import offline_snapshot_service


SCHEMA_ID = "life.vvault.knowledge.claim-document"
SCHEMA_VERSION = "1.0.0"
CONTRACT_ID = "life.vvault.knowledge-resolution"
CONTRACT_VERSION = "1.0.0"
ATOMIC_CLAIM_EVIDENCE_CONTRACT = "life-vvault-atomic-claim-evidence/v1"
CAPABILITY_METADATA_CONTRACT = "life.vvault.knowledge.capability/v1"
_PRIVILEGED_CLAIM_FIELDS = frozenset({
    "subject_principal_id", "claim_kind", "capability_metadata",
})
_PRIVILEGED_PROJECTED_FIELDS = frozenset({
    *_PRIVILEGED_CLAIM_FIELDS,
    "claim_type", "capability_name", "capability_state",
})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_INSTANCE = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9]{3}$")


@dataclass(frozen=True)
class KnowledgeReference:
    artifact_id: str
    revision: str | None = None
    sha256: str | None = None
    required: bool = True


def transcript_source(path: str) -> dict[str, Any]:
    """Classify transcript provenance without turning it into factual truth."""
    normalized = str(path or "").strip().replace("\\", "/").strip("/")
    parts = normalized.split("/")
    lowered = [part.lower() for part in parts]
    provider = None
    if "instances" in lowered:
        index = lowered.index("instances")
        if len(parts) > index + 2:
            provider = lowered[index + 2]
    elif parts:
        provider = lowered[0]

    if provider == "codex":
        quality = "canonical-provider-history"
    elif provider == "chatty" and parts and re.fullmatch(r"chat_with_[a-z0-9-]+\.md", lowered[-1]):
        quality = "under-development-singleton"
    elif provider in {"chatgpt", "character.ai", "github-copilot"}:
        quality = "unreviewed-archive"
    else:
        quality = "unreviewed-archive"
    return {
        "provider": provider or "unknown",
        "source_quality": quality,
        "authoritative_without_review": False,
        "eligible_as_evidence": True,
    }


def validate_claim_document(document: Any) -> list[str]:
    schema = canonical_data_contract.load_schemas().get(SCHEMA_ID)
    if not schema:
        return ["$: claim-document schema is unavailable"]
    errors = canonical_data_contract.validate_json_document(document, schema)
    if not isinstance(document, dict):
        return errors
    scope = document.get("scope")
    construct_id = document.get("construct_id")
    if scope == "construct-unique" and not construct_id:
        errors.append("$.construct_id: required for construct-unique scope")
    if scope == "shared" and construct_id is not None:
        errors.append("$.construct_id: must be null or omitted for shared scope")
    document_kind = document.get("document_kind")
    if document_kind:
        if scope == "shared" and document_kind not in {"shared_ecosystem", "shared_person_profile"}:
            errors.append("$.document_kind: shared scope requires a shared document kind")
        if scope == "construct-unique" and document_kind != "construct_unique":
            errors.append("$.document_kind: construct-unique scope requires construct_unique")
        for index, claim in enumerate(document.get("claims", [])):
            if isinstance(claim, dict) and claim.get("audience") != ["construct"]:
                errors.append(f"$.claims[{index}].audience: runtime publications require exact construct audience")
    if document.get("publication_status") == "approved":
        if not document.get("approved_by"):
            errors.append("$.approved_by: required for approved publication")
        if not document.get("approved_at"):
            errors.append("$.approved_at: required for approved publication")
    for index, claim in enumerate(document.get("claims", [])):
        if not isinstance(claim, dict):
            continue
        claim_kind = claim.get("claim_kind")
        capability_metadata = claim.get("capability_metadata")
        if capability_metadata is not None and claim_kind != "capability":
            errors.append(
                f"$.claims[{index}].claim_kind: must be capability when capability_metadata is present"
            )
        if claim_kind == "capability" and capability_metadata is None:
            errors.append(
                f"$.claims[{index}].capability_metadata: required for capability claims"
            )
    claim_ids = [claim.get("claim_id") for claim in document.get("claims", []) if isinstance(claim, dict)]
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("$.claims: claim_id values must be unique")
    return errors


def _reference(value: Any) -> KnowledgeReference:
    if isinstance(value, str):
        return KnowledgeReference(artifact_id=value.strip())
    if not isinstance(value, dict):
        raise ValueError("knowledge reference must be an artifact id or object")
    artifact_id = str(value.get("artifact_id") or "").strip()
    revision = str(value.get("revision") or "").strip() or None
    sha256 = str(value.get("sha256") or "").strip().lower() or None
    if not artifact_id:
        raise ValueError("knowledge reference artifact_id is required")
    if sha256 and not _SHA256.fullmatch(sha256):
        raise ValueError("knowledge reference sha256 is invalid")
    return KnowledgeReference(artifact_id, revision, sha256, bool(value.get("required", True)))


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _document(value: Any) -> tuple[dict[str, Any] | None, bytes]:
    if isinstance(value, dict):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return value, encoded
    raw = str(value or "").encode("utf-8")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None, raw
    return (parsed if isinstance(parsed, dict) else None), raw


def _artifact_rows(
    owner_user_id: str,
    artifact_id: str,
    *,
    statement_timeout_ms: int | None = None,
    connection_timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Resolve one canonical reference without forcing a vault-wide JSON scan.

    Published knowledge references use the OVVAULTS record UUID.  Keep the
    metadata lookup only for legacy symbolic references; combining both paths
    with OR made PostgreSQL scan owner vault contents and routinely hit the
    canonical 20-second statement timeout.
    """
    timeout_kwargs = {}
    if statement_timeout_ms is not None:
        timeout_kwargs["statement_timeout_ms"] = statement_timeout_ms
    if connection_timeout_seconds is not None:
        timeout_kwargs["connection_timeout_seconds"] = connection_timeout_seconds
    try:
        record_id = str(uuid.UUID(str(artifact_id)))
    except (TypeError, ValueError, AttributeError):
        return chatty_body_service._rows(
            """
            SELECT id::text AS id,user_id::text AS user_id,construct_id,
                   filename,storage_path,sha256,created_at,updated_at,metadata,content
            FROM ovvaults.vault_files
            WHERE user_id=%s
              AND metadata->>'artifact_id'=%s
            ORDER BY coalesce(updated_at,created_at) DESC
                """,
                (owner_user_id, artifact_id),
                **timeout_kwargs,
            )
    return chatty_body_service._rows(
        """
        SELECT id::text AS id,user_id::text AS user_id,construct_id,
               filename,storage_path,sha256,created_at,updated_at,metadata,content
        FROM ovvaults.vault_files
        WHERE user_id=%s
          AND id=%s::uuid
        ORDER BY coalesce(updated_at,created_at) DESC
        """,
        (owner_user_id, record_id),
        **timeout_kwargs,
    )


def _verify_publication_signature(
    *,
    row: dict[str, Any],
    metadata: dict[str, Any],
    document: dict[str, Any],
    digest: str,
    owner_user_id: str,
    private_key_pem: str | None,
) -> list[str]:
    """Require signatures on new typed publications while reading legacy r1."""
    if not document.get("document_kind"):
        return []
    payload = metadata.get("signature_payload")
    signature = str(metadata.get("signature") or "")
    if (
        metadata.get("signature_algorithm") != "Ed25519"
        or not isinstance(payload, dict)
        or not signature
    ):
        return ["$: signed publication evidence is required"]
    expected = {
        "ownerUserId": owner_user_id,
        "documentId": document.get("document_id"),
        "documentKind": document.get("document_kind"),
        "revision": document.get("revision"),
        "scope": document.get("scope"),
        "constructId": document.get("construct_id"),
        "sha256": digest,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            return [f"$: signed publication {field} does not match artifact"]
    try:
        canonical_projection_signing.verify_canonical_payload(
            payload,
            {
                "algorithm": metadata.get("signature_algorithm"),
                "keyId": metadata.get("signature_key_id"),
                "signature": signature,
            },
            private_key_pem=private_key_pem,
        )
    except RuntimeError:
        raise
    except ValueError as exc:
        return [f"$: signed publication signature is invalid ({type(exc).__name__})"]
    return []


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _project_claim(
    claim: dict[str, Any],
    *,
    artifact_id: str,
    document_id: str,
    revision: str,
    document_sha256: str,
    approved: bool,
    signed_publication: bool,
) -> dict[str, Any]:
    """Project one atomic claim without inventing principal or capability data.

    Principal bindings and capability declarations have authority only when
    they were part of an approved typed document whose publication signature
    VVAULT verified. Legacy approved documents remain readable, but cannot
    acquire privileged semantics merely by containing similarly named fields.
    """
    original = dict(claim)
    projected = dict(original)
    trusted_fields: list[str] = []
    if signed_publication:
        trusted_fields = sorted(
            field for field in _PRIVILEGED_CLAIM_FIELDS if field in original
        )
        capability_metadata = original.get("capability_metadata")
        if (
            original.get("claim_kind") == "capability"
            and isinstance(capability_metadata, dict)
            and capability_metadata.get("contract") == CAPABILITY_METADATA_CONTRACT
        ):
            capabilities = capability_metadata.get("capabilities")
            # Compatibility for existing Core consumers is derived only from
            # the signed, explicitly typed canonical metadata. Atomic claims
            # with more than one capability retain the canonical list only.
            if isinstance(capabilities, list) and len(capabilities) == 1:
                capability = capabilities[0]
                if isinstance(capability, dict):
                    projected.update({
                        "claim_type": "operational_capability",
                        "capability_name": capability.get("name"),
                        "capability_state": capability.get("state"),
                    })
    else:
        for field in _PRIVILEGED_CLAIM_FIELDS:
            projected.pop(field, None)

    projected["projection_evidence"] = {
        "contract": ATOMIC_CLAIM_EVIDENCE_CONTRACT,
        "originalClaimSha256": _canonical_sha256(original),
        "documentArtifactId": artifact_id,
        "documentId": document_id,
        "documentRevision": revision,
        "documentSha256": document_sha256,
        "approved": bool(approved),
        "signedPublication": bool(signed_publication),
        "trustedPrivilegedFields": trusted_fields,
    }
    return projected


def _sign_shared_corpus(
    corpus: dict[str, Any], *, private_key_pem: str | None
) -> dict[str, Any]:
    issued = {
        **corpus,
        "issuedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    key = offline_snapshot_service._load_private_key(private_key_pem)
    key_document = offline_snapshot_service.public_key_document(private_key_pem=private_key_pem)
    canonical = json.dumps(issued, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = key.sign(canonical)
    key.public_key().verify(signature, canonical)
    return {
        **issued,
        "algorithm": "Ed25519",
        "keyId": key_document["keyId"],
        "signature": base64.b64encode(signature).decode("ascii"),
        "signatureVerified": True,
    }


def resolve_knowledge_references(
    *, owner_user_id: str, instance_id: str, references: list[Any],
    require_shared: bool = False, private_key_pem: str | None = None,
    owner_shared_references: list[Any] | None = None,
    statement_timeout_ms: int | None = None,
    connection_timeout_seconds: float | None = None,
    deadline_monotonic: float | None = None,
    canonical_rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    owner = str(owner_user_id or "").strip()
    callsign = str(instance_id or "").strip().lower().replace("_", "-")
    if not owner:
        return {"success": False, "error": "authenticated owner_user_id is required"}, 403
    if not _INSTANCE.fullmatch(callsign):
        return {"success": False, "error": "invalid canonical instance_id"}, 400
    try:
        normalized = [_reference(item) for item in list(references or [])]
    except ValueError as exc:
        return {"success": False, "error": str(exc)}, 400
    if canonical_rows is not None and (
        not isinstance(canonical_rows, list)
        or any(not isinstance(row, dict) for row in canonical_rows)
    ):
        return {"success": False, "error": "canonical knowledge rows are malformed"}, 400

    resolved: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for ref in normalized:
        bounded_statement_timeout = statement_timeout_ms
        bounded_connection_timeout = connection_timeout_seconds
        if deadline_monotonic is not None:
            remaining_ms = int((deadline_monotonic - monotonic()) * 1000)
            if remaining_ms <= 0:
                return {
                    "success": False,
                    "error": "canonical knowledge resolution deadline exceeded",
                    "failures": failures,
                }, 503
            bounded_statement_timeout = (
                remaining_ms
                if statement_timeout_ms is None
                else min(statement_timeout_ms, remaining_ms)
            )
            bounded_connection_timeout = (
                remaining_ms / 1000
                if connection_timeout_seconds is None
                else min(connection_timeout_seconds, remaining_ms / 1000)
            )
        if canonical_rows is None:
            rows = _artifact_rows(
                owner,
                ref.artifact_id,
                statement_timeout_ms=bounded_statement_timeout,
                connection_timeout_seconds=bounded_connection_timeout,
            )
        else:
            rows = [
                row for row in canonical_rows
                if str(row.get("id") or "") == ref.artifact_id
                or str(_metadata(row.get("metadata")).get("artifact_id") or "")
                == ref.artifact_id
            ]
        candidates = []
        for row in rows:
            metadata = _metadata(row.get("metadata"))
            revision = str(metadata.get("revision") or "")
            if ref.revision and revision != ref.revision:
                continue
            candidates.append((row, metadata, revision))
        if len(candidates) != 1:
            failure = {
                "artifact_id": ref.artifact_id,
                "required": ref.required,
                "error": "artifact not found" if not candidates else "artifact revision is ambiguous",
            }
            failures.append(failure)
            continue
        row, metadata, revision = candidates[0]
        document, raw = _document(row.get("content"))
        digest = hashlib.sha256(raw).hexdigest()
        stored_hash = str(row.get("sha256") or "").lower()
        errors = validate_claim_document(document)
        if stored_hash and stored_hash != digest:
            errors.append("$: stored sha256 does not match document content")
        if ref.sha256 and ref.sha256 != (stored_hash or digest):
            errors.append("$: requested sha256 does not match canonical artifact")
        if str(row.get("user_id") or "") != owner:
            errors.append("$: canonical artifact row does not belong to authenticated owner")
        if document and document.get("owner_user_id") != owner:
            errors.append("$.owner_user_id: does not match authenticated owner")
        if document and document.get("scope") == "construct-unique" and document.get("construct_id") != callsign:
            errors.append("$.construct_id: does not match requested instance")
        if document and document.get("publication_status") != "approved":
            errors.append("$.publication_status: only approved documents are runtime eligible")
        signature_errors: list[str] = []
        if document:
            signature_errors = _verify_publication_signature(
                row=row,
                metadata=metadata,
                document=document,
                digest=stored_hash or digest,
                owner_user_id=owner,
                private_key_pem=private_key_pem,
            )
            errors.extend(signature_errors)
        if errors:
            failures.append({"artifact_id": ref.artifact_id, "required": ref.required, "error": "knowledge artifact rejected", "validation_errors": errors})
            continue
        path = str(row.get("storage_path") or row.get("filename") or "")
        approved = document.get("publication_status") == "approved"
        signed_publication = bool(document.get("document_kind")) and not signature_errors
        projected_claims = [
            _project_claim(
                claim,
                artifact_id=str(row["id"]),
                document_id=document["document_id"],
                revision=document["revision"],
                document_sha256=stored_hash or digest,
                approved=approved,
                signed_publication=signed_publication,
            )
            for claim in document["claims"]
        ]
        resolved.append({
            "artifact_id": str(row["id"]),
            "document_id": document["document_id"],
            "document_kind": document.get("document_kind"),
            "revision": document["revision"],
            "sha256": stored_hash or digest,
            "scope": document["scope"],
            "construct_id": document.get("construct_id"),
            "publication_status": document.get("publication_status"),
            "publication_evidence": {
                "contract": "life-vvault-knowledge-publication-evidence/v1",
                "approved": approved,
                "signedPublication": signed_publication,
                "signatureAlgorithm": (
                    metadata.get("signature_algorithm") if signed_publication else None
                ),
                "signatureKeyId": (
                    metadata.get("signature_key_id") if signed_publication else None
                ),
            },
            "claims": projected_claims,
            "source": transcript_source(path),
            "storage_path": path,
            "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
        })

    shared_artifacts = [item for item in resolved if item.get("scope") == "shared"]
    try:
        normalized_owner_shared = [
            _reference(item) for item in list(owner_shared_references or [])
        ]
    except ValueError as exc:
        return {"success": False, "error": str(exc)}, 400
    resolved_identities = {
        (item["artifact_id"], str(item["revision"]), item["sha256"])
        for item in shared_artifacts
    }
    activated_identities = [
        (item.artifact_id, str(item.revision or ""), str(item.sha256 or ""))
        for item in normalized_owner_shared
    ]
    activation_complete = bool(activated_identities) and all(
        identity in resolved_identities for identity in activated_identities
    )
    required_failures = [item for item in failures if item["required"]]
    if require_shared and not activation_complete:
        missing_shared = {
            "artifact_id": "owner-shared-activation-set",
            "required": True,
            "error": "required shared training set is empty or unresolved",
        }
        failures.append(missing_shared)
        required_failures.append(missing_shared)
    shared_corpus = {
        "contract": "life-vvault-owner-shared-corpus-resolution/v1",
        "activatedReferenceCount": len(activated_identities),
        "resolvedDocumentCount": sum(
            1 for identity in activated_identities if identity in resolved_identities
        ),
        "setHash": hashlib.sha256(json.dumps(
            sorted(activated_identities), separators=(",", ":")
        ).encode("utf-8")).hexdigest(),
        "complete": activation_complete,
        "activatedReferences": [
            {"artifact_id": item.artifact_id, "revision": item.revision, "sha256": item.sha256, "required": item.required}
            for item in normalized_owner_shared
        ],
        "resolvedDocuments": [
            {
                "artifact_id": item["artifact_id"],
                "document_id": item["document_id"],
                "document_kind": item.get("document_kind"),
                "revision": item["revision"],
                "sha256": item["sha256"],
                "scope": item["scope"],
            }
            for item in shared_artifacts
        ],
    }
    if owner_shared_references is not None:
        shared_corpus = _sign_shared_corpus(
            shared_corpus, private_key_pem=private_key_pem
        )
    payload = {
        "success": not required_failures,
        "canonical": not required_failures,
        "authority": "vvault_body",
        "storage_owner": "ovvaults.vault_files",
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "owner_user_id": owner,
        "instance_id": callsign,
        "artifacts": resolved,
        "failures": failures,
        "required_context": {
            "sharedTrainingRequired": bool(require_shared),
            "sharedTrainingResolved": activation_complete if require_shared else True,
            "resolvedSharedDocumentCount": len(shared_artifacts),
            "allRequiredReferencesResolved": not required_failures,
        },
        "shared_corpus": shared_corpus,
    }
    return payload, 409 if required_failures else 200


def select_claims(
    artifacts: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 12,
    include_zero_overlap: bool = False,
    required_claim_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Deterministically select relevant approved claims without generating prose."""
    query_terms = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
    required = {str(claim_id).strip() for claim_id in required_claim_ids if str(claim_id).strip()}
    include_planned = bool(query_terms.intersection({"plan", "planned", "future", "initiative", "aspiration"}))
    include_history = bool(query_terms.intersection({
        "history", "historical", "previously", "formerly", "originally", "past",
        "earlier", "origin", "remember", "remembered", "recall", "recalled",
        "before", "start", "started",
    })) or bool(re.search(r"\bback then\b|\blast time\b|\bwhere\s+(?:did|does)\b[^?!.]{0,80}\bstart(?:ed)?\b", str(query or ""), re.I))
    ranked: list[tuple[int, int, int, str, dict[str, Any]]] = []
    for artifact in artifacts:
        for claim in artifact.get("claims", []):
            status = claim.get("status")
            claim_id = str(claim.get("claim_id") or "")
            required_claim = claim_id in required
            if not required_claim and status != "current" and not (include_planned and status == "planned") and not (
                include_history and status in {"historical", "superseded"}
            ):
                continue
            searchable = " ".join(
                (
                    " ".join(str(item) for item in claim.get(field, []))
                    if isinstance(claim.get(field), list)
                    else str(claim.get(field) or "")
                )
                for field in ("subject", "predicate", "object", "preferred_wording", "retrieval_scope")
            ).lower()
            claim_terms = set(re.findall(r"[a-z0-9]+", searchable))
            score = len(query_terms.intersection(claim_terms))
            retrieval_scope = {
                str(value).strip().lower()
                for value in claim.get("retrieval_scope", [])
                if str(value).strip()
            }
            always_candidate = "always" in retrieval_scope
            if query_terms and score == 0 and not (
                include_zero_overlap or required_claim or always_candidate
            ):
                continue
            selected_claim = dict(claim)
            projection_evidence = selected_claim.get("projection_evidence")
            privileged_evidence_verified = bool(
                isinstance(projection_evidence, dict)
                and projection_evidence.get("contract") == ATOMIC_CLAIM_EVIDENCE_CONTRACT
                and projection_evidence.get("approved") is True
                and projection_evidence.get("signedPublication") is True
            )
            if not privileged_evidence_verified:
                for field in _PRIVILEGED_PROJECTED_FIELDS:
                    selected_claim.pop(field, None)
            else:
                trusted_fields = projection_evidence.get("trustedPrivilegedFields")
                trusted = (
                    set(trusted_fields)
                    if isinstance(trusted_fields, list)
                    and all(isinstance(field, str) for field in trusted_fields)
                    else set()
                )
                for field in _PRIVILEGED_CLAIM_FIELDS - trusted:
                    selected_claim.pop(field, None)
                # Compatibility fields are only meaningful when their
                # canonical typed capability source remains trusted.
                if not {"claim_kind", "capability_metadata"}.issubset(trusted):
                    for field in {"claim_type", "capability_name", "capability_state"}:
                        selected_claim.pop(field, None)
            selected = {
                **selected_claim,
                "document_id": artifact["document_id"],
                "document_artifact_id": artifact["artifact_id"],
                "document_revision": artifact["revision"],
                "document_sha256": artifact["sha256"],
            }
            ranked.append((
                0 if required_claim else 1,
                0 if always_candidate else 1,
                -score,
                claim_id,
                selected,
            ))
    ranked.sort(key=lambda item: item[:4])
    return [item[4] for item in ranked[: max(1, min(int(limit), 50))]]


def context_claim_candidates(
    artifacts: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 12,
    now: datetime | None = None,
    required_evidence_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Add temporal and conflict evidence without choosing a semantic winner.

    The canonical claim bytes remain untouched. This projection only records
    whether a signed claim is currently eligible and whether another selected
    claim occupies the same subject/predicate slot with a different object.
    """
    instant = now or datetime.now(timezone.utc)
    selected = select_claims(
        artifacts,
        query,
        limit=limit,
        include_zero_overlap=True,
        required_claim_ids=required_evidence_ids,
    )
    all_claims = [
        claim
        for artifact in artifacts
        for claim in artifact.get("claims", [])
        if isinstance(claim, dict)
    ]
    superseded_ids = {
        str(claim_id)
        for claim in all_claims
        for claim_id in claim.get("supersedes_claim_ids", [])
        if str(claim_id)
    }

    def parsed(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    projected: list[dict[str, Any]] = []
    slots: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for claim in selected:
        claim_id = str(claim.get("claim_id") or "")
        effective_from = parsed(claim.get("effective_from"))
        effective_until = parsed(claim.get("effective_until"))
        if claim_id in superseded_ids or claim.get("status") == "superseded":
            eligibility = "superseded"
        elif effective_from and instant < effective_from:
            eligibility = "not_yet_effective"
        elif effective_until and instant >= effective_until:
            eligibility = "expired"
        elif claim.get("status") == "current":
            eligibility = "eligible"
        else:
            eligibility = "contextual"
        item = {
            **claim,
            "context_eligibility": eligibility,
            "context_conflict": {
                "status": "clear",
                "group_id": None,
                "conflicting_claim_ids": [],
            },
        }
        projected.append(item)
        subject = str(claim.get("subject_principal_id") or claim.get("subject") or "").strip().lower()
        predicate = str(claim.get("predicate") or "").strip().lower()
        if subject and predicate and eligibility == "eligible":
            slots.setdefault((subject, predicate), []).append(item)

    for (subject, predicate), group in slots.items():
        objects = {str(item.get("object") or "").strip() for item in group}
        if len(group) < 2 or len(objects) < 2:
            continue
        group_id = hashlib.sha256(
            json.dumps([subject, predicate], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        ids = sorted(str(item.get("claim_id") or "") for item in group)
        for item in group:
            item["context_conflict"] = {
                "status": "disputed",
                "group_id": group_id,
                "conflicting_claim_ids": [
                    claim_id for claim_id in ids if claim_id != item.get("claim_id")
                ],
            }
    return projected
