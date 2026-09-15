"""Owner-qualified, signed candidate context projection for Chatty Core.

VVAULT resolves authority, privacy, chronology, principals, conflicts, and
provenance. It deliberately returns candidates rather than assembling a model
prompt; selection and inference remain Chatty Core responsibilities.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from vvault.server import (
    account_context_service,
    canonical_data_contract,
    canonical_projection_signing,
    chatty_body_service,
    knowledge_activation_service,
    knowledge_contract,
)


REQUEST_CONTRACT = "life-vvault-canonical-context-candidate-request/v1"
ENVELOPE_VERSION = "life-vvault-canonical-context-manifest-envelope/v1"
MANIFEST_SCHEMA_ID = "life.vvault.canonical-context-manifest"
MANIFEST_SCHEMA_VERSION = "1.0.0"
UNIT_SCHEMA_ID = "life.vvault.context-unit"
UNIT_SCHEMA_VERSION = "1.0.0"
PRINCIPAL_BINDING_CONTRACT = "life-vvault-context-principal-binding/v1"
PRIVACY_DECISION_CONTRACT = "life-vvault-context-privacy-decision/v1"
POLICY_CONTRACT = "life-vvault-context-candidate-policy/v1"
AUTHORITY = "vvault/ovvaults"
DEFAULT_TTL_SECONDS = 60
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_PURPOSE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_ALLOWED_REQUEST_FIELDS = frozenset({
    "contract", "requestId", "threadId", "turnId", "speakerPrincipalId",
    "respondentPrincipalId", "relationshipSubjectPrincipalIds", "purpose",
    "query", "queryHash", "contextPolicyVersion", "participantFrameHash",
    "providerBudgetClass", "surface", "onBehalfOf", "requiredEvidenceIds",
    "limits",
})
_ALLOWED_LIMIT_FIELDS = frozenset({"maxUnits", "maxChars"})
_CONTEXT_POLICY_VERSION = "chatty-context-sea-policy/v1"
_PROVIDER_BUDGET_CLASSES = frozenset({"compact", "standard", "extended"})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CONTENT_FIELDS_BY_KIND = {
    "identity_expression": frozenset({
        "construct_id", "display_name", "description", "instructions",
        "system_prompt", "definition", "conditioning", "voice",
        "expression_projection",
    }),
    "capsule_context": frozenset({"capsule"}),
    "account_context": frozenset({"displayName", "ageAssurance"}),
    "knowledge_claim": frozenset({
        "claim_id", "subject", "subject_principal_id", "predicate", "object",
        "evidence_class", "status", "effective_from", "effective_until",
        "supersedes_claim_ids", "preferred_wording", "forbidden_implications",
        "audience", "retrieval_scope", "claim_kind", "capability_metadata",
        "document_scope",
    }),
    "transcript_exchange": frozenset({"user", "construct", "source", "tag", "index"}),
}
_REQUIRED_CONTENT_FIELDS_BY_KIND = {
    "identity_expression": frozenset({"construct_id"}),
    "capsule_context": frozenset({"capsule"}),
    "account_context": frozenset({"displayName"}),
    "knowledge_claim": frozenset({
        "claim_id", "subject", "predicate", "object", "evidence_class", "status",
        "audience", "retrieval_scope", "preferred_wording", "document_scope",
    }),
    "transcript_exchange": frozenset({"user", "construct", "source", "tag", "index"}),
}
_KNOWLEDGE_EVIDENCE_FIELDS = frozenset({
    "contract", "originalClaimSha256", "documentArtifactId", "documentId",
    "documentRevision", "documentSha256", "approved", "signedPublication",
    "trustedPrivilegedFields",
})
_ACCOUNT_EVIDENCE_FIELDS = frozenset({
    "contract", "keyId", "signatureVerified", "authAssertionVerified", "ownerMatched",
})
_PRIVILEGED_KNOWLEDGE_FIELDS = frozenset({
    "subject_principal_id", "claim_kind", "capability_metadata",
})


class CanonicalContextError(ValueError):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _validate_unit_semantics(
    *, kind: str, content: dict[str, Any], provenance: dict[str, Any]
) -> None:
    """Reject untyped context surfaces before they enter a signed manifest.

    JSON Schema bounds the common envelope. This check binds each unit kind to
    its exact content and projection-evidence vocabulary, preventing a newly
    added source field from silently becoming provider-facing context.
    """
    allowed = _CONTENT_FIELDS_BY_KIND.get(kind)
    required = _REQUIRED_CONTENT_FIELDS_BY_KIND.get(kind)
    if allowed is None or required is None:
        raise CanonicalContextError(
            "CONTEXT_PROJECTION_INVALID", "context unit kind is unsupported", status=503
        )
    unknown = sorted(set(content) - allowed)
    missing = sorted(required - set(content))
    if unknown or missing:
        detail = []
        if unknown:
            detail.append(f"unknown {kind} content fields: {', '.join(unknown)}")
        if missing:
            detail.append(f"missing {kind} content fields: {', '.join(missing)}")
        raise CanonicalContextError(
            "CONTEXT_PROJECTION_INVALID", "; ".join(detail), status=503
        )

    evidence = provenance.get("projection_evidence")
    if kind == "knowledge_claim":
        if not isinstance(evidence, dict) or set(evidence) != _KNOWLEDGE_EVIDENCE_FIELDS:
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "knowledge projection evidence fields are invalid",
                status=503,
            )
        if (
            evidence.get("contract") != knowledge_contract.ATOMIC_CLAIM_EVIDENCE_CONTRACT
            or evidence.get("approved") is not True
            or evidence.get("signedPublication") is not True
        ):
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "knowledge candidate is not an approved signed publication",
                status=503,
            )
        trusted = evidence.get("trustedPrivilegedFields")
        if (
            not isinstance(trusted, list)
            or any(not isinstance(item, str) for item in trusted)
            or len(set(trusted)) != len(trusted)
            or not set(trusted).issubset(_PRIVILEGED_KNOWLEDGE_FIELDS)
        ):
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "knowledge trusted privileged fields are invalid",
                status=503,
            )
        content_privileged = set(content).intersection(_PRIVILEGED_KNOWLEDGE_FIELDS)
        if content_privileged != set(trusted):
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "knowledge privileged fields do not match signed projection evidence",
                status=503,
            )
    elif kind == "account_context":
        if not isinstance(evidence, dict) or set(evidence) != _ACCOUNT_EVIDENCE_FIELDS:
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "account projection evidence fields are invalid",
                status=503,
            )
        if (
            evidence.get("contract") != account_context_service.PROJECTION_VERSION
            or evidence.get("signatureVerified") is not True
            or evidence.get("authAssertionVerified") is not True
            or evidence.get("ownerMatched") is not True
        ):
            raise CanonicalContextError(
                "CONTEXT_PROJECTION_INVALID",
                "account projection evidence is not verified",
                status=503,
            )
    elif evidence is not None:
        raise CanonicalContextError(
            "CONTEXT_PROJECTION_INVALID",
            f"{kind} does not accept projection evidence",
            status=503,
        )


def _canonical_bytes(value: Any) -> bytes:
    return canonical_projection_signing.canonical_json_bytes(value)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_principal(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _PRINCIPAL.fullmatch(text):
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", f"{label} is invalid")
    return text


def validate_request(request: Any, *, construct_id: str) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != _ALLOWED_REQUEST_FIELDS:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "request fields are invalid")
    if request.get("contract") != REQUEST_CONTRACT:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "request contract is invalid")
    request_id = str(request.get("requestId") or "").strip()
    thread_id = str(request.get("threadId") or "").strip()
    turn_id = str(request.get("turnId") or "").strip()
    if not all(_IDENTIFIER.fullmatch(value) for value in (request_id, thread_id, turn_id)):
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "request/thread/turn identifier is invalid")
    respondent = _safe_principal(request.get("respondentPrincipalId"), "respondentPrincipalId")
    if respondent != chatty_body_service.normalize_callsign(construct_id):
        raise CanonicalContextError("CONTEXT_SCOPE_MISMATCH", "respondent does not match route construct")
    speaker = _safe_principal(request.get("speakerPrincipalId"), "speakerPrincipalId")
    subjects = request.get("relationshipSubjectPrincipalIds")
    if not isinstance(subjects, list) or len(subjects) > 32:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "relationship subjects are invalid")
    normalized_subjects: list[str] = []
    for value in subjects:
        principal = _safe_principal(value, "relationshipSubjectPrincipalIds")
        if principal not in normalized_subjects:
            normalized_subjects.append(principal)
    purpose = str(request.get("purpose") or "").strip().lower()
    if not _PURPOSE.fullmatch(purpose):
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "purpose is invalid")
    query = request.get("query")
    surface = str(request.get("surface") or "").strip().lower()
    if not isinstance(query, str) or len(query) > 16_384 or "\x00" in query:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "query is invalid")
    query_hash = str(request.get("queryHash") or "").strip()
    if not _SHA256.fullmatch(query_hash):
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "queryHash is invalid")
    context_policy_version = str(request.get("contextPolicyVersion") or "").strip()
    if context_policy_version != _CONTEXT_POLICY_VERSION:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "contextPolicyVersion is invalid")
    participant_frame_hash = request.get("participantFrameHash")
    if participant_frame_hash is not None:
        participant_frame_hash = str(participant_frame_hash).strip()
        if not _SHA256.fullmatch(participant_frame_hash):
            raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "participantFrameHash is invalid")
    provider_budget_class = str(request.get("providerBudgetClass") or "").strip()
    if provider_budget_class not in _PROVIDER_BUDGET_CLASSES:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "providerBudgetClass is invalid")
    required_evidence_ids = request.get("requiredEvidenceIds")
    if not isinstance(required_evidence_ids, list) or len(required_evidence_ids) > 50:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "requiredEvidenceIds is invalid")
    normalized_required_evidence_ids: list[str] = []
    for value in required_evidence_ids:
        evidence_id = str(value or "").strip()
        if not _IDENTIFIER.fullmatch(evidence_id):
            raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "requiredEvidenceIds is invalid")
        normalized_required_evidence_ids.append(evidence_id)
    if (
        len(set(normalized_required_evidence_ids)) != len(normalized_required_evidence_ids)
        or normalized_required_evidence_ids != sorted(normalized_required_evidence_ids)
    ):
        raise CanonicalContextError(
            "CONTEXT_REQUEST_INVALID", "requiredEvidenceIds must be sorted and unique"
        )
    if not _PURPOSE.fullmatch(surface):
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "surface is invalid")
    if request.get("onBehalfOf") is not None:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "onBehalfOf must be null")
    limits = request.get("limits")
    if not isinstance(limits, dict) or set(limits) != _ALLOWED_LIMIT_FIELDS:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "limits are invalid")
    max_units = limits.get("maxUnits")
    max_chars = limits.get("maxChars")
    if isinstance(max_units, bool) or not isinstance(max_units, int) or not 1 <= max_units <= 50:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "maxUnits must be between 1 and 50")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1024 <= max_chars <= 131_072:
        raise CanonicalContextError("CONTEXT_REQUEST_INVALID", "maxChars must be between 1024 and 131072")
    if len(normalized_required_evidence_ids) > max_units:
        raise CanonicalContextError(
            "CONTEXT_REQUIRED_EVIDENCE_BUDGET_EXCEEDED",
            "requiredEvidenceIds exceed maxUnits",
        )
    return {
        "contract": REQUEST_CONTRACT,
        "requestId": request_id,
        "threadId": thread_id,
        "turnId": turn_id,
        "speakerPrincipalId": speaker,
        "respondentPrincipalId": respondent,
        "relationshipSubjectPrincipalIds": normalized_subjects,
        "purpose": purpose,
        "query": query,
        "queryHash": query_hash,
        "contextPolicyVersion": context_policy_version,
        "participantFrameHash": participant_frame_hash,
        "providerBudgetClass": provider_budget_class,
        "surface": surface,
        "onBehalfOf": None,
        "requiredEvidenceIds": normalized_required_evidence_ids,
        "limits": {"maxUnits": max_units, "maxChars": max_chars},
    }


def _default_source_loader(
    owner_user_id: str,
    construct_id: str,
    request: dict[str, Any],
    *,
    private_key_pem: str | None,
) -> dict[str, Any]:
    profile_result = chatty_body_service.construct_profile(
        construct_id, owner_user_id=owner_user_id
    )
    profile_payload, profile_status = profile_result.to_response()
    if profile_status != 200 or profile_result.status != "body_native":
        raise CanonicalContextError("CONTEXT_PROFILE_UNAVAILABLE", "owner-qualified construct profile is unavailable", status=503)
    profile = profile_payload.get("profile") or {}
    owner_shared = knowledge_activation_service.owner_shared_references(
        owner_user_id=owner_user_id, private_key_pem=private_key_pem
    )
    references = [
        *owner_shared,
        *list(profile.get("canonRefs") or []),
        *list(profile.get("knowledgeRefs") or []),
    ]
    deduped: list[Any] = []
    seen: set[str] = set()
    for reference in references:
        key = _sha256(reference)
        if key not in seen:
            seen.add(key)
            deduped.append(reference)
    knowledge, status = knowledge_contract.resolve_knowledge_references(
        owner_user_id=owner_user_id,
        instance_id=construct_id,
        references=deduped,
        owner_shared_references=owner_shared,
        require_shared=bool((profile.get("contextRequirements") or {}).get("sharedTraining")),
        private_key_pem=private_key_pem,
    )
    if status != 200 or knowledge.get("success") is not True:
        raise CanonicalContextError("CONTEXT_KNOWLEDGE_UNAVAILABLE", "required canonical knowledge is unavailable", status=503)
    memory_payload, memory_status = chatty_body_service.memories(
        construct_id,
        owner_user_id=owner_user_id,
        max_chars=request["limits"]["maxChars"],
        query=request["query"],
        limit=request["limits"]["maxUnits"],
        required_event_ids=request["requiredEvidenceIds"],
    ).to_response()
    if memory_status != 200:
        raise CanonicalContextError("CONTEXT_TRANSCRIPT_UNAVAILABLE", "owner-qualified transcript projection is unavailable", status=503)
    try:
        account = account_context_service.read_projection(
            owner_user_id=owner_user_id, private_key_pem=private_key_pem
        )
    except ValueError as exc:
        if str(exc) != "required canonical account context is not published":
            raise
        account = None
    capsule_payload, capsule_status = chatty_body_service.canonical_capsule(
        construct_id, user_id=owner_user_id
    ).to_response()
    capsule = capsule_payload if capsule_status == 200 else None
    return {
        "profile": profile_payload,
        "capsule": capsule,
        "account": account,
        "knowledge": knowledge,
        "memories": memory_payload,
    }


def _revision_vector(bundle: dict[str, Any]) -> dict[str, str]:
    profile = bundle.get("profile") or {}
    capsule = bundle.get("capsule") or {}
    account = bundle.get("account") or {}
    knowledge = bundle.get("knowledge") or {}
    memories = bundle.get("memories") or {}
    components = {
        "profile": _sha256({
            "profile": profile.get("profile") or {},
            "definition": profile.get("definition"),
            "conditioning": profile.get("conditioning"),
            "voice": profile.get("voice"),
            "expressionProjection": profile.get("expressionProjection") or profile.get("expression_projection"),
            "sourceFiles": [
                {
                    "storage_path": item.get("storage_path") or item.get("storagePath"),
                    "sha256": item.get("sha256"),
                }
                for item in profile.get("source_files") or []
            ],
            "taxonomySha256": profile.get("taxonomy_sha256"),
            "capsuleSha256": capsule.get("sha256"),
        }),
        "account_context": _sha256({
            "revision": account.get("revision"),
            "contentHash": account.get("contentHash"),
            "keyId": account.get("keyId"),
        }) if account else _EMPTY_SHA256,
        "knowledge": _sha256({
            "artifacts": [
                {
                    "artifact_id": item.get("artifact_id"),
                    "revision": item.get("revision"),
                    "sha256": item.get("sha256"),
                }
                for item in knowledge.get("artifacts") or []
            ],
            "sharedSetHash": (knowledge.get("shared_corpus") or {}).get("setHash"),
        }),
        "transcript": _sha256({
            "sources": [
                {
                    "artifact_id": item.get("artifact_id"),
                    "sha256": item.get("sha256"),
                    "authority": item.get("authority"),
                    "source_type": item.get("source_type"),
                    "participantBindingStatus": item.get("participantBindingStatus"),
                }
                for item in memories.get("transcript_sources") or []
            ],
            "memories": [
                {
                    "user": item.get("user"),
                    "construct": item.get("construct"),
                    "source_artifact_id": item.get("source_artifact_id"),
                    "source_hash": item.get("source_hash"),
                    "index": item.get("index"),
                    "principalBinding": item.get("principalBinding"),
                }
                for item in memories.get("memories") or []
            ],
        }),
    }
    return {**components, "combined_sha256": _sha256(components)}


def _principal_binding(
    *,
    status: str,
    authority: str | None,
    author: str | None,
    respondent: str | None,
    addressees: list[str],
    relationship_subjects: list[str],
    participants: list[str],
) -> dict[str, Any]:
    verified = status == "verified"
    return {
        "contract": PRINCIPAL_BINDING_CONTRACT,
        "status": "verified" if verified else "unresolved",
        "authority": authority if verified else None,
        "author_principal_id": author if verified else None,
        "respondent_principal_id": respondent if verified else None,
        "addressee_principal_ids": addressees if verified else [],
        "relationship_subject_principal_ids": relationship_subjects if verified else [],
        "participant_principal_ids": participants if verified else [],
        "pronoun_scope": {
            "source_first_person_principal_id": author if verified else None,
            "source_second_person_principal_id": respondent if verified else None,
        },
    }


def _chronology(
    *,
    status: str = "unresolved",
    authority: str | None = None,
    occurred_at: str | None = None,
    asserted_at: str | None = None,
    ingested_at: str | None = None,
    published_at: str | None = None,
    superseded_at: str | None = None,
    precision: str = "unknown",
    confidence: float = 0.0,
) -> dict[str, Any]:
    verified = status == "verified"
    # JSON has a single number type, but Python preserves the lexical
    # distinction between integral floats (1.0) and integers (1) while
    # JavaScript does not.  Context manifests are independently re-hashed and
    # signature-verified by Chatty after JSON transport, so emit integral
    # confidence values in their transport-stable integer form.
    normalized_confidence = 0
    if verified:
        numeric_confidence = float(confidence)
        if not numeric_confidence.is_integer() or int(numeric_confidence) not in (0, 1):
            raise CanonicalContextError(
                "CONTEXT_CHRONOLOGY_CONFIDENCE_INVALID",
                "canonical chronology confidence must be exactly 0 or 1",
                status=503,
            )
        normalized_confidence = int(numeric_confidence)
    return {
        "status": "verified" if verified else "unresolved",
        "authority": authority if verified else None,
        "occurred_at": occurred_at if verified else None,
        "asserted_at": asserted_at if verified else None,
        "ingested_at": ingested_at if verified else None,
        "published_at": published_at if verified else None,
        "superseded_at": superseded_at if verified else None,
        "precision": precision if verified else "unknown",
        "confidence": normalized_confidence,
    }


def _privacy(classification: str, request: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": PRIVACY_DECISION_CONTRACT,
        "decision": "included",
        "classification": classification,
        "audience": ["construct"],
        "purposes": [request["purpose"]],
        "recipient_principal_ids": [request["respondentPrincipalId"]],
        "owner_authorized": True,
        "minimum_necessary": True,
        "expires_at": None,
        "revoked_at": None,
    }


def _unit(
    *,
    owner_user_id: str,
    respondent: str,
    kind: str,
    source_class: str,
    identity: str,
    content: dict[str, Any],
    principal_binding: dict[str, Any],
    chronology: dict[str, Any],
    privacy: dict[str, Any],
    conflict: dict[str, Any],
    provenance: dict[str, Any],
    temporal_state: str = "current",
    artifact_id: str | None = None,
    event_id: str | None = None,
    turn_id: str | None = None,
    thread_id: str | None = None,
    document_id: str | None = None,
    claim_id: str | None = None,
) -> dict[str, Any]:
    _validate_unit_semantics(kind=kind, content=content, provenance=provenance)
    source_references = {
        "artifact_id": artifact_id,
        "event_id": event_id,
        "turn_id": turn_id,
        "thread_id": thread_id,
        "document_id": document_id,
        "claim_id": claim_id,
    }
    invalid_reference = next(
        (
            name for name, value in source_references.items()
            if value is not None and (
                not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)
            )
        ),
        None,
    )
    if invalid_reference:
        raise CanonicalContextError(
            "CONTEXT_PROJECTION_INVALID",
            f"context unit {invalid_reference} is invalid",
            status=503,
        )
    content_bytes = _canonical_bytes(content)
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    unit_id = f"context:{kind}:{hashlib.sha256(f'{owner_user_id}:{respondent}:{identity}:{content_hash}'.encode()).hexdigest()}"
    return {
        "schema_id": UNIT_SCHEMA_ID,
        "schema_version": UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "kind": kind,
        "source_class": source_class,
        "owner_user_id": owner_user_id,
        "respondent_principal_id": respondent,
        "artifact_id": artifact_id,
        "event_id": event_id,
        "turn_id": turn_id,
        "thread_id": thread_id,
        "document_id": document_id,
        "claim_id": claim_id,
        "content": content,
        "content_sha256": content_hash,
        "verification_state": "verified",
        "temporal_state": temporal_state,
        "instruction_authority": (
            "canonical_instructions" if kind == "identity_expression" else "none"
        ),
        "inference_eligible": True,
        "token_cost": {
            "contract": "life-vvault-context-unit-cost/v1",
            "utf8_bytes": len(content_bytes),
            "conservative_tokens": max(1, (len(content_bytes) + 2) // 3),
            "chars_per_token": 3,
        },
        "principal_binding": principal_binding,
        "chronology": chronology,
        "privacy": privacy,
        "conflict": conflict,
        "provenance": provenance,
    }


def _candidate_units(
    owner_user_id: str,
    construct_id: str,
    request: dict[str, Any],
    bundle: dict[str, Any],
    *,
    private_key_pem: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    units: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    profile_payload = bundle.get("profile") or {}
    profile = profile_payload.get("profile") or {}
    identity_content = {
        key: copy.deepcopy(value)
        for key, value in {
            "construct_id": construct_id,
            "display_name": profile.get("displayName") or profile_payload.get("displayName"),
            "description": profile.get("description") or profile_payload.get("description"),
            "instructions": profile.get("instructions") or profile_payload.get("instructions"),
            "system_prompt": profile.get("system_prompt") or profile_payload.get("system_prompt"),
            "definition": profile_payload.get("definition"),
            "conditioning": profile_payload.get("conditioning"),
            "voice": profile_payload.get("voice"),
            "expression_projection": profile_payload.get("expressionProjection") or profile_payload.get("expression_projection"),
        }.items()
        if value not in (None, "", [], {})
    }
    if identity_content:
        profile_hash = _sha256(profile_payload)
        units.append(_unit(
            owner_user_id=owner_user_id, respondent=construct_id,
            kind="identity_expression", source_class="canonical_identity_expression",
            identity=f"profile:{profile_hash}", content=identity_content,
            principal_binding=_principal_binding(
                status="verified", authority="ovvaults.vault_files",
                author=construct_id, respondent=construct_id,
                addressees=[], relationship_subjects=[], participants=[construct_id],
            ),
            chronology=_chronology(),
            privacy=_privacy("construct-private", request),
            conflict={"status": "clear", "group_id": None, "supersedes_unit_ids": [], "conflicting_unit_ids": []},
            provenance={
                "authority": "ovvaults.vault_files", "source_type": "canonical_identity_expression",
                "record_id": None, "revision": profile_hash, "sha256": profile_hash,
                "projection_evidence": None, "parser_contract": "life-vvault-construct-profile/v1",
                "parser_version": "1.0.0",
            },
        ))
    else:
        omissions.append({"source": "canonical_identity_expression", "reason": "not_available", "count": 0})

    capsule = bundle.get("capsule")
    if isinstance(capsule, dict) and capsule.get("content"):
        capsule_hash = str(capsule.get("sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", capsule_hash):
            capsule_hash = _sha256(capsule.get("content"))
        units.append(_unit(
            owner_user_id=owner_user_id, respondent=construct_id,
            kind="capsule_context", source_class="capsule",
            identity=f"capsule:{capsule_hash}",
            content={"capsule": capsule.get("content")},
            principal_binding=_principal_binding(
                status="verified", authority="ovvaults.vault_files",
                author=construct_id, respondent=construct_id,
                addressees=[], relationship_subjects=[], participants=[construct_id],
            ),
            chronology=_chronology(), privacy=_privacy("construct-private", request),
            conflict={"status": "clear", "group_id": None, "supersedes_unit_ids": [], "conflicting_unit_ids": []},
            provenance={
                "authority": "ovvaults.vault_files", "source_type": "canonical_capsule",
                "record_id": str(capsule.get("record_id") or "") or None,
                "revision": capsule_hash, "sha256": capsule_hash,
                "projection_evidence": None, "parser_contract": "life-vvault-capsule/v1",
                "parser_version": "1.0.0",
            },
            artifact_id=str(capsule.get("record_id") or "") or None,
        ))
    else:
        omissions.append({"source": "capsule", "reason": "not_available", "count": 0})
    account = bundle.get("account")
    if isinstance(account, dict):
        account_candidate = account_context_service.context_candidate(
            account,
            owner_user_id=owner_user_id,
            respondent_principal_id=construct_id,
            purpose=request["purpose"],
            private_key_pem=private_key_pem,
        )
        provenance = account_candidate["provenance"]
        account_subject = str((account.get("provenance") or {}).get("subject") or owner_user_id)
        if not _PRINCIPAL.fullmatch(account_subject):
            account_subject = owner_user_id
        units.append(_unit(
            owner_user_id=owner_user_id,
            respondent=construct_id,
            kind="account_context",
            source_class="authenticated_owner_context",
            identity=f"account:{provenance['revision']}",
            content=account_candidate["content"],
            principal_binding=_principal_binding(
                status="verified", authority="auth+ovvaults", author=account_subject,
                respondent=construct_id, addressees=[construct_id],
                relationship_subjects=request["relationshipSubjectPrincipalIds"],
                participants=[account_subject, construct_id, *request["relationshipSubjectPrincipalIds"]],
            ),
            chronology=_chronology(
                status="verified", authority="ovvaults.signed-account-context",
                published_at=account.get("issuedAt"), precision="exact", confidence=1.0,
            ),
            privacy=_privacy("account-private", request),
            conflict={"status": "clear", "group_id": None, "supersedes_unit_ids": [], "conflicting_unit_ids": []},
            provenance={
                "authority": str(provenance["authority"]), "source_type": "signed_account_projection",
                "record_id": provenance.get("recordId"), "revision": str(provenance["revision"]),
                "sha256": str(provenance["sha256"]), "projection_evidence": provenance.get("projectionEvidence"),
                "parser_contract": account_context_service.PROJECTION_VERSION, "parser_version": "1.0.0",
            },
            document_id=str(account.get("documentId") or "") or None,
        ))
    else:
        omissions.append({"source": "account_context", "reason": "not_published", "count": 0})

    knowledge = bundle.get("knowledge") or {}
    claims = knowledge_contract.context_claim_candidates(
        list(knowledge.get("artifacts") or []),
        request["query"],
        limit=request["limits"]["maxUnits"],
        required_evidence_ids=request["requiredEvidenceIds"],
    )
    artifacts = {str(item.get("artifact_id")): item for item in knowledge.get("artifacts") or []}
    for claim in claims:
        artifact = artifacts.get(str(claim.get("document_artifact_id"))) or {}
        eligibility = str(claim.get("context_eligibility") or "contextual")
        conflict_evidence = claim.get("context_conflict") or {}
        conflict_status = str(conflict_evidence.get("status") or "clear")
        status = conflict_status if conflict_status == "disputed" else (
            eligibility if eligibility in {"superseded", "expired"} else "clear"
        )
        if eligibility == "not_yet_effective":
            omissions.append({"source": "knowledge", "reason": "not_yet_effective", "count": 1})
            continue
        content = {
            key: copy.deepcopy(claim.get(key))
            for key in (
                "claim_id", "subject", "subject_principal_id", "predicate", "object",
                "evidence_class", "status", "effective_from", "effective_until",
                "supersedes_claim_ids", "preferred_wording", "forbidden_implications",
                "audience", "retrieval_scope", "claim_kind", "capability_metadata",
            ) if claim.get(key) is not None
        }
        content["document_scope"] = artifact.get("scope")
        subject_principal = claim.get("subject_principal_id")
        unit = _unit(
            owner_user_id=owner_user_id, respondent=construct_id, kind="knowledge_claim",
            source_class=("shared_ecosystem_training" if artifact.get("scope") == "shared" else "construct_knowledge"),
            identity=f"knowledge:{claim.get('document_artifact_id')}:{claim.get('claim_id')}",
            content=content,
            principal_binding=_principal_binding(
                status="verified" if subject_principal else "unresolved",
                authority="ovvaults.signed-knowledge-publication" if subject_principal else None,
                author=str(subject_principal) if subject_principal else None,
                respondent=construct_id if subject_principal else None,
                addressees=[construct_id], relationship_subjects=[str(subject_principal)] if subject_principal else [],
                participants=[str(subject_principal), construct_id] if subject_principal else [],
            ),
            chronology=_chronology(
                status="verified", authority="ovvaults.signed-knowledge-publication",
                occurred_at=claim.get("effective_from"), asserted_at=artifact.get("updated_at"),
                published_at=artifact.get("updated_at"),
                superseded_at=claim.get("effective_until") if status in {"superseded", "expired"} else None,
                precision="exact" if claim.get("effective_from") else "unknown", confidence=1.0,
            ),
            privacy=_privacy("shared" if artifact.get("scope") == "shared" else "construct-private", request),
            conflict={
                "status": status, "group_id": conflict_evidence.get("group_id"),
                "supersedes_unit_ids": [f"claim:{value}" for value in claim.get("supersedes_claim_ids", [])],
                "conflicting_unit_ids": [f"claim:{value}" for value in conflict_evidence.get("conflicting_claim_ids", [])],
            },
            provenance={
                "authority": "ovvaults.vault_files", "source_type": "signed_knowledge_claim",
                "record_id": str(claim.get("document_artifact_id") or "") or None,
                "revision": str(claim.get("document_revision") or "unknown"),
                "sha256": str(claim.get("document_sha256") or _EMPTY_SHA256),
                "projection_evidence": copy.deepcopy(claim.get("projection_evidence")),
                "parser_contract": knowledge_contract.CONTRACT_ID,
                "parser_version": knowledge_contract.CONTRACT_VERSION,
            },
            temporal_state=(
                "current" if eligibility == "eligible"
                else eligibility if eligibility in {
                    "superseded", "expired", "not_yet_effective"
                }
                else str(claim.get("status") or "unresolved")
                if str(claim.get("status") or "") in {"planned", "historical"}
                else "unresolved"
            ),
            artifact_id=str(claim.get("document_artifact_id") or "") or None,
            document_id=str(claim.get("document_id") or "") or None,
            claim_id=str(claim.get("claim_id") or "") or None,
        )
        units.append(unit)
        if status != "clear":
            conflicts.append({
                "conflict_id": str(conflict_evidence.get("group_id") or f"{status}:{claim.get('claim_id')}"),
                "status": status, "unit_ids": [unit["unit_id"]], "reason": f"knowledge_{status}",
            })

    memories = bundle.get("memories") or {}
    unresolved_count = 0
    for memory in memories.get("memories") or []:
        binding = memory.get("principalBinding") or {}
        if binding.get("bindingStatus") != "verified":
            unresolved_count += 1
            continue
        author = binding.get("userAuthorPrincipalId")
        respondent = binding.get("respondentPrincipalId")
        content = {
            "user": str(memory.get("user") or ""),
            "construct": str(memory.get("construct") or ""),
            "source": str(memory.get("source") or "Transcript"),
            "tag": str(memory.get("tag") or "historical_exchange"),
            "index": int(memory.get("index") or 0),
        }
        source_hash = str(memory.get("source_hash") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", source_hash):
            source_hash = _sha256({"source": content["source"], "content": content})
        provider = str(memory.get("provider") or "").strip().lower()
        source_type = str(memory.get("source_type") or "transcript")
        if source_type == "canonical_singleton":
            source_class = "current_conversation"
        elif provider == "chatgpt":
            source_class = "chatgpt_transcript"
        elif provider == "character.ai":
            source_class = "character_ai_transcript"
        elif provider in {"github", "github-copilot", "codex"}:
            source_class = "github_codex_agent_transcript"
        else:
            source_class = "canonical_document"
        units.append(_unit(
            owner_user_id=owner_user_id, respondent=construct_id, kind="transcript_exchange",
            source_class=source_class,
            identity=f"transcript:{memory.get('source_artifact_id')}:{content['index']}", content=content,
            principal_binding=_principal_binding(
                status="verified", authority=str(binding.get("bindingAuthority") or "ovvaults"),
                author=str(author) if author else None, respondent=str(respondent) if respondent else None,
                addressees=list(binding.get("addresseePrincipalIds") or []),
                relationship_subjects=list(binding.get("relationshipSubjectPrincipalIds") or []),
                participants=list(binding.get("participantPrincipalIds") or []),
            ),
            chronology=_chronology(),
            privacy=_privacy("owner-private", request),
            conflict={"status": "clear", "group_id": None, "supersedes_unit_ids": [], "conflicting_unit_ids": []},
            provenance={
                "authority": str(memory.get("source_authority") or "ovvaults"),
                "source_type": str(memory.get("source_type") or "transcript"),
                "record_id": str(memory.get("source_artifact_id") or "") or None,
                "revision": source_hash, "sha256": source_hash, "projection_evidence": None,
                "parser_contract": "life-vvault-principal-bound-history/v1", "parser_version": "1.0.0",
            },
            temporal_state="current" if source_class == "current_conversation" else "historical",
            artifact_id=str(memory.get("source_artifact_id") or "") or None,
            event_id=(
                str(memory.get("responseEventId") or "") or None
                if source_class == "current_conversation"
                else None
            ),
            turn_id=str(memory.get("turnId") or "") or None,
            thread_id=request["threadId"] if source_class == "current_conversation" else None,
        ))
    if unresolved_count:
        omissions.append({"source": "transcript", "reason": "principal_binding_unresolved", "count": unresolved_count})
    merged_conflicts: dict[str, dict[str, Any]] = {}
    for item in conflicts:
        conflict_id = item["conflict_id"]
        current = merged_conflicts.setdefault(
            conflict_id,
            {**item, "unit_ids": []},
        )
        for unit_id in item["unit_ids"]:
            if unit_id not in current["unit_ids"]:
                current["unit_ids"].append(unit_id)
    return units, omissions, list(merged_conflicts.values())


def project_candidates(
    *,
    owner_user_id: str,
    construct_id: str,
    request: dict[str, Any],
    private_key_pem: str | None = None,
    now: datetime | None = None,
    source_loader: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise CanonicalContextError("CONTEXT_OWNER_REQUIRED", "authenticated owner is required", status=403)
    callsign = chatty_body_service.normalize_callsign(construct_id)
    normalized = validate_request(request, construct_id=callsign)
    loader = source_loader
    if loader is None:
        def loader(owner_id: str, target: str, scoped_request: dict[str, Any]) -> dict[str, Any]:
            return _default_source_loader(
                owner_id, target, scoped_request, private_key_pem=private_key_pem
            )
    first = loader(owner, callsign, copy.deepcopy(normalized))
    first_revision = _revision_vector(first)
    second = loader(owner, callsign, copy.deepcopy(normalized))
    second_revision = _revision_vector(second)
    if first_revision != second_revision:
        raise CanonicalContextError(
            "CONTEXT_SOURCES_CHANGED",
            "canonical context changed while the candidate projection was built",
            status=409,
        )
    units, omissions, conflicts = _candidate_units(
        owner, callsign, normalized, second, private_key_pem=private_key_pem
    )
    max_units = normalized["limits"]["maxUnits"]
    max_chars = normalized["limits"]["maxChars"]
    required_evidence_ids = set(normalized["requiredEvidenceIds"])

    def unit_evidence_ids(unit: dict[str, Any]) -> set[str]:
        evidence_ids = {
            str(unit.get("event_id") or "").strip(),
            str((unit.get("content") or {}).get("claim_id") or "").strip(),
        }
        return {value for value in evidence_ids if value}

    # VVAULT remains a candidate authority, not the semantic selector, but an
    # exact server-bound requirement must survive the bounded read projection
    # so Core can independently validate and select it. Preserve source order
    # within required and optional partitions.
    ordered_units = sorted(
        enumerate(units),
        key=lambda item: (
            0 if unit_evidence_ids(item[1]) & required_evidence_ids else 1,
            item[0],
        ),
    )
    included: list[dict[str, Any]] = []
    used_chars = 0
    for _, unit in ordered_units:
        unit_chars = len(_canonical_bytes(unit))
        if len(included) >= max_units or used_chars + unit_chars > max_chars:
            omissions.append({"source": unit["kind"], "reason": "request_budget", "count": 1})
            continue
        included.append(unit)
        used_chars += unit_chars
    included_evidence_ids = set().union(*(unit_evidence_ids(unit) for unit in included)) if included else set()
    missing_required = sorted(required_evidence_ids - included_evidence_ids)
    if missing_required:
        raise CanonicalContextError(
            "CONTEXT_REQUIRED_EVIDENCE_MISSING",
            f"required canonical evidence was not projected: {', '.join(missing_required)}",
            status=409,
        )
    policy_unsigned = {
        "contract": POLICY_CONTRACT,
        "candidate_only": True,
        "selector_authority": "chatty-core",
        "privacy_minimized": True,
        "legacy_human_context_included": False,
    }
    policy = {**policy_unsigned, "policy_sha256": _sha256(policy_unsigned)}
    issued = now or datetime.now(timezone.utc)
    payload = {
        "schema_id": MANIFEST_SCHEMA_ID,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "authority": AUTHORITY,
        "owner_user_id": owner,
        "construct_id": callsign,
        "request": normalized,
        "revision_vector": second_revision,
        "units": included,
        "omissions": omissions,
        "conflicts": conflicts,
        "selection_policy": policy,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(seconds=DEFAULT_TTL_SECONDS)).isoformat().replace("+00:00", "Z"),
    }
    schemas = canonical_data_contract.load_schemas()
    unit_schema = schemas.get(UNIT_SCHEMA_ID)
    manifest_schema = schemas.get(MANIFEST_SCHEMA_ID)
    errors: list[str] = []
    if not unit_schema or not manifest_schema:
        raise RuntimeError("CONTEXT_SCHEMA_UNAVAILABLE")
    for index, unit in enumerate(included):
        errors.extend(
            f"$.units[{index}]{error[1:]}" if error.startswith("$") else error
            for error in canonical_data_contract.validate_json_document(unit, unit_schema)
        )
    errors.extend(canonical_data_contract.validate_json_document(payload, manifest_schema))
    if errors:
        raise CanonicalContextError("CONTEXT_PROJECTION_INVALID", "; ".join(errors), status=503)
    payload_sha256 = _sha256(payload)
    signature = canonical_projection_signing.sign_canonical_payload(
        payload, private_key_pem=private_key_pem
    )
    return {
        "version": ENVELOPE_VERSION,
        "algorithm": signature["algorithm"],
        "keyId": signature["keyId"],
        "payload": payload,
        "payloadSha256": payload_sha256,
        "signature": signature["signature"],
    }
