"""Owner-qualified durable work-program evidence for Chatty Core.

VVAULT does not reason, select work, execute tools, or advance the semantic
state machine.  It verifies a short-lived Core-host authorization, canonical
owner/thread/construct scope, compare-and-swap head, idempotency, evidence
references, and one-use handoffs before signing one immutable event.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vvault.server import (
    canonical_projection_signing,
    chatty_body_service,
    conversation_thread_service,
    knowledge_contract,
)


WORK_PROGRAM_CONTRACT = "chatty-work-program/v1"
WORK_HANDOFF_CONTRACT = "chatty-work-handoff/v1"
WORK_EVIDENCE_REFERENCE_CONTRACT = "chatty-work-evidence-reference/v1"
WORK_EVENT_AUTHORIZATION_CONTRACT = "chatty-work-event-authorization/v1"
WORK_PROGRAM_CREATE_AUTHORIZATION_CONTRACT = "chatty-work-program-create-authorization/v1"
WORK_HANDOFF_AUTHORIZATION_CONTRACT = "chatty-work-handoff-authorization/v1"
WORK_EVENT_ENVELOPE_CONTRACT = "life-vvault-work-event-envelope/v1"
WORK_PROJECTION_CONTRACT = "life-vvault-work-projection/v1"
WORK_CONTEXT_PROJECTION_CONTRACT = "life-vvault-work-context-projection/v1"
WORK_PREFLIGHT_CONTRACT = "life-vvault-work-preflight-inspection/v1"
WORK_SCOPE_REQUEST_CONTRACT = "chatty-work-scope-resolution-request/v1"
WORK_SCOPE_RESOLUTION_CONTRACT = "life-vvault-work-scope-resolution/v1"
WORK_ACTIVE_SCOPE_REQUEST_CONTRACT = "chatty-work-active-scope-resolution-request/v1"
WORK_ACTIVE_SCOPE_RESOLUTION_CONTRACT = "life-vvault-work-active-scope-resolution/v1"
WORK_EVIDENCE_LOCATOR_CONTRACT = "chatty-work-evidence-locator/v1"
WORK_EVIDENCE_RESOLUTION_CONTRACT = "life-vvault-work-evidence-resolution/v1"
WORK_HANDOFF_ENVELOPE_CONTRACT = "life-vvault-work-handoff-envelope/v1"
WORK_EVENT_BATCH_CONTRACT = "chatty-work-event-batch/v1"
WORK_EVENT_BATCH_AUTHORIZATION_CONTRACT = "chatty-work-event-batch-authorization/v1"
WORK_TRANSCRIPT_BINDING_CONTRACT = "chatty-work-transcript-binding/v1"
WORK_TRANSCRIPT_EXCHANGE_CORE_CONTRACT = "chatty-work-transcript-exchange-core/v1"
WORK_ATOMIC_EXCHANGE_RECEIPT_CONTRACT = "life-vvault-atomic-transcript-work-receipt/v1"
AUTHORITY = "vvault/ovvaults"

EVENT_TYPES = frozenset({
    "program_requested", "scope_approved", "reasoning_started",
    "next_action_proposed", "reasoning_interrupted", "owner_choice_recorded",
    "handoff_accepted", "evidence_attached", "work_item_completed",
    "attempt_failed", "recovery_selected", "blocker_declared",
    "completion_verified", "paused", "resumed", "cancelled", "superseded",
})
TERMINAL_EVENT_TYPES = frozenset({"completion_verified", "cancelled", "superseded"})
OWNER_EVENT_TYPES = frozenset({
    "program_requested", "scope_approved", "owner_choice_recorded",
    "completion_verified", "paused", "resumed", "cancelled", "superseded",
})
_AUTH_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "keyId", "algorithm",
    "ownerPrincipalId", "programId", "constructId", "threadId",
    "expectedSequence", "expectedHeadEventId", "expectedHeadSha256",
    "goalRevision", "resultingGoalRevision", "eventType", "eventPayloadSha256", "idempotencyKey",
    "issuedAt", "expiresAt", "payloadSha256", "signature",
})
_CREATE_AUTH_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "keyId", "algorithm",
    "ownerPrincipalId", "programId", "constructId", "threadId", "sessionId",
    "branchId", "requestedDefinitionHash", "requestedGoalRevision",
    "programPayloadSha256", "scopeApprovalEvidenceSha256", "eventTypes",
    "idempotencyKey", "issuedAt", "expiresAt", "payloadSha256", "signature",
})
_HANDOFF_AUTH_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "keyId", "algorithm",
    "ownerPrincipalId", "programId", "constructId", "threadId", "sessionId",
    "branchId", "expectedHeadEventId", "expectedHeadSha256", "goalRevision",
    "fromPrincipalId", "toPrincipalId", "delegatedItemIds", "evidenceReferenceIds",
    "prerequisiteEvidenceReferenceIds", "issuedAt", "expiresAt", "idempotencyKey",
    "handoffPayloadSha256", "payloadSha256", "signature",
})
_PROGRAM_FIELDS = frozenset({
    "contract", "programId", "ownerPrincipalId", "constructId", "threadId",
    "sessionId", "goal", "approvedScope", "priority", "budgets",
    "parentProgramId", "supersedesProgramId", "items", "dependencies",
    "createdAt", "definitionHash",
})
_EVENT_FIELDS = frozenset({
    "eventId", "programId", "ownerPrincipalId", "constructId", "threadId",
    "sessionId", "branchId", "eventType", "sequence", "parentEventId",
    "parentEventSha256", "idempotencyKey", "requestDigest",
    "coreAuthorizationHash", "evidenceDigest", "occurredAt", "actor",
    "goalRevision", "resultingGoalRevision", "payload", "payloadSha256", "eventSha256",
})
_ACTOR_FIELDS = frozenset({"principalId", "principalType", "authority"})
_HANDOFF_FIELDS = frozenset({
    "contract", "handoffId", "programId", "sourceConstructId",
    "destinationConstructId", "delegatedItemIds", "allowedActionClasses",
    "responsibilityCode", "evidenceReferenceIds",
    "prerequisiteEvidenceReferenceIds", "checkpointHash", "scopeHash",
    "oneUseCapability", "issuedAt", "expiresAt", "acceptedAt",
    "acceptanceEvidenceReferenceId", "handoffHash",
})
_HANDOFF_CAPABILITY_FIELDS = frozenset({
    "contract", "capabilityId", "handoffId", "programId", "issuedToPrincipalId",
    "allowedEventType", "oneUse", "consumedAt", "capabilityHash",
})
_EVIDENCE_FIELDS = frozenset({
    "contract", "evidenceId", "evidenceType", "authority", "scope",
    "payloadSha256", "receiptSha256", "issuedAt", "cryptographicallyVerified",
    "advancementAuthority", "verifiedFactKinds",
})
_EVIDENCE_SCOPE_FIELDS = frozenset({
    "ownerPrincipalId", "programId", "constructId", "itemId", "threadId", "sessionId",
})
_SCOPE_RESOLUTION_FIELDS = frozenset({
    "contract", "ownerPrincipalId", "programId", "constructId",
    "constructIncarnationId", "threadId", "sessionId", "branchId",
    "membershipRevision", "activeProgramId", "sourceRevision",
    "requestedDefinitionHash", "requestedGoalRevision",
    "scopeApprovalEvidenceReference", "issuedAt", "expiresAt", "payloadSha256",
    "algorithm", "keyId", "signature",
})
_CONTEXT_FIELDS = frozenset({
    "contract", "derivationAuthority", "persistenceAuthority",
    "requiresVvaultSignature", "contextPolicyVersion", "programId", "ownerPrincipalId",
    "constructId", "activeConstructId", "threadId", "sessionId", "branchId", "goal", "approvedScope",
    "programPriority", "goalRevision", "status", "completionCriteria",
    "currentDependencies", "currentObstacle", "currentItem", "acceptedDecisions",
    "nextAction", "evidenceReferences", "stateReceipt", "containsPrivateReasoning",
    "containsEventPayloads", "createdAt", "budgetEvidence", "projectionSha256",
})
_STATE_RECEIPT_FIELDS = frozenset({
    "contract", "programId", "status", "headEventId", "headEventSha256",
    "sequence", "eventCount", "stateHash", "receiptSha256",
})
_WORK_EVENT_BATCH_FIELDS = frozenset({
    "contract", "transcriptBinding", "expectedStateReceiptSha256",
    "expectedHeadEventId", "expectedHeadSha256", "expectedSequence",
    "authorization", "events", "eventCount", "atomicWithTranscriptExchange", "noDelta",
    "batchSha256",
})
_WORK_EVENT_BATCH_AUTHORIZATION_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "keyId", "algorithm",
    "ownerPrincipalId", "programId", "constructId", "actorPrincipalId",
    "actorPrincipalType", "actorAuthority", "threadId", "sessionId",
    "branchId", "expectedSequence", "expectedHeadEventId", "expectedHeadSha256",
    "expectedStateReceiptSha256", "goalRevision", "resultingGoalRevision",
    "transcriptBindingSha256", "eventTypes", "eventPayloadSha256s",
    "idempotencyKey", "issuedAt", "expiresAt", "payloadSha256", "signature",
})
_WORK_EVENT_DESCRIPTOR_FIELDS = frozenset({
    "ordinal", "eventType", "payload", "eventPayloadSha256",
})
_WORK_TRANSCRIPT_BINDING_FIELDS = frozenset({
    "contract", "turnId", "threadId", "promptEventId", "responseEventId",
    "promptContentSha256", "responseContentSha256", "exchangePayloadSha256",
})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FORBIDDEN_REASONING_KEYS = frozenset({
    "chainofthought", "reasoningtrace", "scratchpad", "hiddenprompt",
    "internalreasoning", "privatereasoning", "deliberation",
    "thoughtprocess", "cot",
})
_FORBIDDEN_DURABLE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|"
        r"api[_-]?token|refresh[_-]?token|session[_-]?token|client[_-]?secret|"
        r"private[_-]?key|secret[_-]?key)\s*[:=]\s*[^\s,;]{6,}",
        re.IGNORECASE,
    ),
    re.compile(r"(?:/Users/|/home/|/root/|[A-Za-z]:\\Users\\)"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    re.compile(
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:BEGIN\s+)?(?:SYSTEM|DEVELOPER)\s+PROMPT(?:\s+DUMP)?\s*[:\-]",
        re.IGNORECASE,
    ),
    re.compile(r"<\|im_start\|>\s*(?:system|developer)\b", re.IGNORECASE),
    re.compile(r'"role"\s*:\s*"(?:system|developer)"', re.IGNORECASE),
)
_MAX_PROGRAM_BYTES = 512 * 1024
_MAX_EVENT_BYTES = 64 * 1024
_MAX_PROJECTION_EVENTS = 10_000
_MAX_AUTHORIZATION_LIFETIME_SECONDS = 300
_PROJECTION_TTL_SECONDS = 60


class ConstructWorkLoopError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _canonical_bytes(value: Any) -> bytes:
    return canonical_projection_signing.canonical_json_bytes(value)


def _canonical_json(value: Any) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _branch_id(*, owner_user_id: str, program_id: str, construct_id: str, thread_id: str) -> str:
    scope_hash = _sha256({
        "ownerPrincipalId": str(owner_user_id),
        "programId": program_id,
        "constructId": construct_id,
        "threadId": thread_id,
    })
    return f"work-branch-{scope_hash[:32]}"


def _event_id(*, program_id: str, branch_id: str, sequence: int) -> str:
    return f"work-event-{_sha256({'programId': program_id, 'branchId': branch_id, 'sequence': sequence})[:40]}"


def _row(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_TIMESTAMP_INVALID", f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ConstructWorkLoopError("WORK_TIMESTAMP_INVALID", f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _safe_id(value: Any, field: str, *, principal: bool = False) -> str:
    text = str(value or "").strip()
    pattern = _PRINCIPAL if principal else _ID
    if not pattern.fullmatch(text):
        raise ConstructWorkLoopError("WORK_ID_INVALID", f"{field} is invalid")
    return text


def _digest(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA256.fullmatch(text):
        raise ConstructWorkLoopError("WORK_SHA256_INVALID", f"{field} is invalid")
    return text


def _exact(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ConstructWorkLoopError("WORK_UNKNOWN_FIELD", f"{label} fields are invalid")
    return value


def _assert_no_private_reasoning(value: Any, path: str = "payload") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_private_reasoning(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _FORBIDDEN_DURABLE_VALUE_PATTERNS):
            raise ConstructWorkLoopError(
                "WORK_PRIVATE_REASONING_FORBIDDEN",
                f"{path} contains private credentials, local paths, or prompt material",
            )
        return
    if not isinstance(value, dict):
        return
    for key, nested in value.items():
        compact = re.sub(r"[^a-z]", "", str(key).lower())
        if compact in _FORBIDDEN_REASONING_KEYS:
            raise ConstructWorkLoopError(
                "WORK_PRIVATE_REASONING_FORBIDDEN",
                f"{path}.{key} is not durable work evidence",
            )
        _assert_no_private_reasoning(nested, f"{path}.{key}")


def _payload_evidence_digest(
    payload: dict[str, Any],
    resolved_references: list[dict[str, Any]],
) -> str | None:
    if resolved_references:
        return _sha256(sorted(resolved_references, key=lambda item: item["evidenceId"]))

    def contains_evidence(value: Any) -> bool:
        if isinstance(value, list):
            return any(contains_evidence(item) for item in value)
        if not isinstance(value, dict):
            return False
        return any("evidence" in str(key).lower() or contains_evidence(nested)
                   for key, nested in value.items())

    return _sha256(payload) if contains_evidence(payload) else None


def _bounded_object(value: Any, field: str, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConstructWorkLoopError("WORK_OBJECT_INVALID", f"{field} must be an object")
    _assert_no_private_reasoning(value, field)
    if len(_canonical_bytes(value)) > limit:
        raise ConstructWorkLoopError("WORK_PAYLOAD_OVERSIZED", f"{field} exceeds its byte limit", 413)
    return value


def _validate_program(value: Any, owner_user_id: str) -> dict[str, Any]:
    program = _exact(_bounded_object(value, "program", _MAX_PROGRAM_BYTES), _PROGRAM_FIELDS, "program")
    if program.get("contract") != WORK_PROGRAM_CONTRACT:
        raise ConstructWorkLoopError("WORK_PROGRAM_CONTRACT_INVALID", "program contract is invalid")
    program_id = _safe_id(program.get("programId"), "programId")
    owner = _safe_id(program.get("ownerPrincipalId"), "ownerPrincipalId", principal=True)
    if owner != str(owner_user_id):
        raise ConstructWorkLoopError("WORK_OWNER_SCOPE_MISMATCH", "program owner is not authenticated owner", 403)
    construct_id = _safe_id(program.get("constructId"), "constructId", principal=True)
    thread_id = _safe_id(program.get("threadId"), "threadId")
    session_id = _safe_id(program.get("sessionId"), "sessionId")
    if session_id != thread_id:
        raise ConstructWorkLoopError(
            "WORK_SESSION_SCOPE_MISMATCH",
            "work session must be the canonical durable thread session",
            409,
        )
    goal = program.get("goal")
    if not isinstance(goal, dict) or set(goal) != {"goalId", "revision", "objective", "successCriteria"}:
        raise ConstructWorkLoopError("WORK_PROGRAM_CONTRACT_INVALID", "program goal is invalid")
    _safe_id(goal.get("goalId"), "goal.goalId")
    _safe_id(goal.get("revision"), "goal.revision")
    if not isinstance(goal.get("objective"), str) or not goal["objective"].strip() or len(goal["objective"]) > 4096:
        raise ConstructWorkLoopError("WORK_PROGRAM_CONTRACT_INVALID", "program objective is invalid")
    supplied_hash = _digest(program.get("definitionHash"), "definitionHash")
    body = {key: value for key, value in program.items() if key != "definitionHash"}
    if supplied_hash != _sha256(body):
        raise ConstructWorkLoopError("WORK_PROGRAM_HASH_MISMATCH", "definitionHash does not match canonical program")
    return {**program, "programId": program_id, "ownerPrincipalId": owner,
            "constructId": construct_id, "threadId": thread_id, "definitionHash": supplied_hash}


def _authorization_public_key(
    public_key_pem: str | None = None,
    expected_key_id: str | None = None,
) -> tuple[Ed25519PublicKey, str]:
    pem = str(public_key_pem or os.environ.get("CHATTY_WORK_LOOP_AUTHORIZATION_PUBLIC_KEY_PEM") or "").strip()
    configured_key_id = str(expected_key_id or os.environ.get("CHATTY_WORK_LOOP_AUTHORIZATION_KEY_ID") or "").strip()
    if not pem or not configured_key_id:
        raise ConstructWorkLoopError(
            "WORK_AUTHORIZATION_KEY_UNAVAILABLE",
            "Chatty work-loop authorization verification key is unavailable",
            503,
        )
    try:
        key = serialization.load_pem_public_key(pem.replace("\\n", "\n").encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_INVALID", "authorization key is invalid", 503) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_INVALID", "authorization key must be Ed25519", 503)
    derived = hashlib.sha256(key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )).hexdigest()
    if configured_key_id != derived:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_ID_MISMATCH", "authorization key id is invalid", 503)
    return key, derived


def _validate_authorization(
    value: Any,
    *,
    owner_user_id: str,
    event_payload: dict[str, Any],
    public_key_pem: str | None,
    expected_key_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    authorization = _exact(
        _bounded_object(value, "authorization", 32 * 1024),
        _AUTH_FIELDS,
        "authorization",
    )
    if (
        authorization.get("contract") != WORK_EVENT_AUTHORIZATION_CONTRACT
        or authorization.get("authority") != "chatty-core-host"
        or authorization.get("algorithm") != "ed25519"
    ):
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_INVALID", "authorization contract or authority is invalid", 403)
    _safe_id(authorization.get("authorizationId"), "authorizationId")
    if _safe_id(authorization.get("ownerPrincipalId"), "ownerPrincipalId", principal=True) != str(owner_user_id):
        raise ConstructWorkLoopError("WORK_OWNER_SCOPE_MISMATCH", "authorization owner is not authenticated owner", 403)
    _safe_id(authorization.get("programId"), "authorization.programId")
    _safe_id(authorization.get("constructId"), "authorization.constructId", principal=True)
    _safe_id(authorization.get("threadId"), "authorization.threadId")
    _safe_id(authorization.get("goalRevision"), "authorization.goalRevision")
    _safe_id(authorization.get("resultingGoalRevision"), "authorization.resultingGoalRevision")
    event_type = str(authorization.get("eventType") or "")
    if event_type not in EVENT_TYPES:
        raise ConstructWorkLoopError("WORK_EVENT_TYPE_INVALID", "authorization eventType is invalid")
    sequence = authorization.get("expectedSequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= 1_000_000:
        raise ConstructWorkLoopError("WORK_SEQUENCE_INVALID", "expectedSequence is invalid")
    head_id = authorization.get("expectedHeadEventId")
    head_sha = authorization.get("expectedHeadSha256")
    if sequence == 1:
        if head_id is not None or head_sha is not None:
            raise ConstructWorkLoopError("WORK_HEAD_SCOPE_INVALID", "genesis authorization head must be null")
    else:
        _safe_id(head_id, "expectedHeadEventId")
        _digest(head_sha, "expectedHeadSha256")
    idempotency_key = _safe_id(authorization.get("idempotencyKey"), "idempotencyKey")
    event_payload_sha = _digest(authorization.get("eventPayloadSha256"), "eventPayloadSha256")
    if event_payload_sha != _sha256(event_payload):
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_PAYLOAD_MISMATCH", "authorization does not bind event payload", 403)
    issued_at = _timestamp(authorization.get("issuedAt"), "issuedAt")
    expires_at = _timestamp(authorization.get("expiresAt"), "expiresAt")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=_MAX_AUTHORIZATION_LIFETIME_SECONDS)
        or now < issued_at - timedelta(seconds=30)
        or now >= expires_at
    ):
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_EXPIRED", "authorization is outside its valid lifetime", 403)
    unsigned = {key: authorization[key] for key in authorization if key not in {"payloadSha256", "signature"}}
    payload_sha = _digest(authorization.get("payloadSha256"), "authorization.payloadSha256")
    if payload_sha != _sha256(unsigned):
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_HASH_MISMATCH", "authorization payload hash is invalid", 403)
    key, key_id = _authorization_public_key(public_key_pem, expected_key_id)
    if authorization.get("keyId") != key_id:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_ID_MISMATCH", "authorization key id is not trusted", 403)
    try:
        signature = base64.b64decode(str(authorization.get("signature") or ""), validate=True)
        key.verify(signature, _canonical_bytes(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_SIGNATURE_INVALID", "authorization signature is invalid", 403) from exc
    return {**authorization, "idempotencyKey": idempotency_key,
            "eventPayloadSha256": event_payload_sha, "payloadSha256": payload_sha}


def _validate_batch_authorization(
    value: Any,
    *,
    owner_user_id: str,
    descriptors: list[dict[str, Any]],
    transcript_binding_sha256: str,
    public_key_pem: str | None,
    expected_key_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    authorization = _exact(
        _bounded_object(value, "workEventBatch.authorization", 64 * 1024),
        _WORK_EVENT_BATCH_AUTHORIZATION_FIELDS,
        "workEventBatch.authorization",
    )
    if (
        authorization.get("contract") != WORK_EVENT_BATCH_AUTHORIZATION_CONTRACT
        or authorization.get("authority") != "chatty-core-host"
        or authorization.get("algorithm") != "ed25519"
    ):
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_INVALID",
            "work event batch authorization contract or authority is invalid",
            403,
        )
    _safe_id(authorization.get("authorizationId"), "batchAuthorization.authorizationId")
    if _safe_id(
        authorization.get("ownerPrincipalId"),
        "batchAuthorization.ownerPrincipalId",
        principal=True,
    ) != str(owner_user_id):
        raise ConstructWorkLoopError(
            "WORK_OWNER_SCOPE_MISMATCH",
            "batch authorization owner is not authenticated owner",
            403,
        )
    for field in ("programId", "threadId", "sessionId", "branchId", "goalRevision", "resultingGoalRevision"):
        _safe_id(authorization.get(field), f"batchAuthorization.{field}")
    _safe_id(
        authorization.get("constructId"),
        "batchAuthorization.constructId",
        principal=True,
    )
    _safe_id(
        authorization.get("actorPrincipalId"),
        "batchAuthorization.actorPrincipalId",
        principal=True,
    )
    if (
        authorization.get("actorPrincipalType") != "construct"
        or authorization.get("actorAuthority") != "chatty-core"
    ):
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_ACTOR_INVALID",
            "batch authorization actor authority is invalid",
            403,
        )
    sequence = authorization.get("expectedSequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= 1_000_000:
        raise ConstructWorkLoopError("WORK_SEQUENCE_INVALID", "batch expectedSequence is invalid")
    _safe_id(authorization.get("expectedHeadEventId"), "batchAuthorization.expectedHeadEventId")
    _digest(authorization.get("expectedHeadSha256"), "batchAuthorization.expectedHeadSha256")
    _digest(
        authorization.get("expectedStateReceiptSha256"),
        "batchAuthorization.expectedStateReceiptSha256",
    )
    if _digest(
        authorization.get("transcriptBindingSha256"),
        "batchAuthorization.transcriptBindingSha256",
    ) != transcript_binding_sha256:
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_SCOPE_MISMATCH",
            "batch authorization does not bind transcript identity",
            403,
        )
    expected_event_types = [descriptor["eventType"] for descriptor in descriptors]
    expected_payload_hashes = [descriptor["eventPayloadSha256"] for descriptor in descriptors]
    if (
        authorization.get("eventTypes") != expected_event_types
        or authorization.get("eventPayloadSha256s") != expected_payload_hashes
    ):
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_PAYLOAD_MISMATCH",
            "batch authorization does not bind ordered event payloads",
            403,
        )
    idempotency_key = _safe_id(
        authorization.get("idempotencyKey"),
        "batchAuthorization.idempotencyKey",
    )
    issued_at = _timestamp(authorization.get("issuedAt"), "batchAuthorization.issuedAt")
    expires_at = _timestamp(authorization.get("expiresAt"), "batchAuthorization.expiresAt")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=_MAX_AUTHORIZATION_LIFETIME_SECONDS)
        or now < issued_at - timedelta(seconds=30)
        or now >= expires_at
    ):
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_EXPIRED",
            "batch authorization is outside its valid lifetime",
            403,
        )
    unsigned = {
        key: authorization[key]
        for key in authorization
        if key not in {"payloadSha256", "signature"}
    }
    payload_sha = _digest(
        authorization.get("payloadSha256"),
        "batchAuthorization.payloadSha256",
    )
    if payload_sha != _sha256(unsigned):
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_HASH_MISMATCH",
            "batch authorization payload hash is invalid",
            403,
        )
    key, key_id = _authorization_public_key(public_key_pem, expected_key_id)
    if authorization.get("keyId") != key_id:
        raise ConstructWorkLoopError(
            "WORK_AUTHORIZATION_KEY_ID_MISMATCH",
            "batch authorization key id is not trusted",
            403,
        )
    try:
        signature = base64.b64decode(
            str(authorization.get("signature") or ""), validate=True
        )
        key.verify(signature, _canonical_bytes(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise ConstructWorkLoopError(
            "WORK_EVENT_BATCH_AUTHORIZATION_SIGNATURE_INVALID",
            "batch authorization signature is invalid",
            403,
        ) from exc
    return {
        **authorization,
        "idempotencyKey": idempotency_key,
        "payloadSha256": payload_sha,
    }


def _validate_create_authorization(
    value: Any,
    *,
    owner_user_id: str,
    program: dict[str, Any],
    scope_resolution: dict[str, Any],
    scope_approval_evidence: dict[str, Any],
    public_key_pem: str | None,
    expected_key_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    authorization = _exact(
        _bounded_object(value, "createAuthorization", 32 * 1024),
        _CREATE_AUTH_FIELDS,
        "createAuthorization",
    )
    if (
        authorization.get("contract") != WORK_PROGRAM_CREATE_AUTHORIZATION_CONTRACT
        or authorization.get("authority") != "chatty-core-host"
        or authorization.get("algorithm") != "ed25519"
    ):
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_INVALID", "create authorization is invalid", 403)
    _safe_id(authorization.get("authorizationId"), "authorizationId")
    if _safe_id(authorization.get("ownerPrincipalId"), "ownerPrincipalId", principal=True) != str(owner_user_id):
        raise ConstructWorkLoopError("WORK_OWNER_SCOPE_MISMATCH", "create owner is not authenticated owner", 403)
    for field in ("programId", "threadId", "sessionId", "branchId", "requestedGoalRevision", "idempotencyKey"):
        _safe_id(authorization.get(field), field)
    _safe_id(authorization.get("constructId"), "constructId", principal=True)
    for field in (
        "requestedDefinitionHash", "programPayloadSha256",
        "scopeApprovalEvidenceSha256", "payloadSha256",
    ):
        _digest(authorization.get(field), field)
    if authorization.get("eventTypes") != ["program_requested", "scope_approved"]:
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_INVALID", "create event types are invalid", 403)
    if (
        authorization.get("programId") != program.get("programId")
        or authorization.get("constructId") != program.get("constructId")
        or authorization.get("threadId") != program.get("threadId")
        or authorization.get("sessionId") != program.get("sessionId")
        or authorization.get("branchId") != scope_resolution.get("branchId")
        or authorization.get("requestedDefinitionHash") != program.get("definitionHash")
        or authorization.get("requestedGoalRevision") != (program.get("goal") or {}).get("revision")
        or authorization.get("programPayloadSha256") != _sha256(program)
        or authorization.get("scopeApprovalEvidenceSha256") != _sha256(scope_approval_evidence)
    ):
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_SCOPE_MISMATCH", "create authorization scope is invalid", 403)
    issued_at = _timestamp(authorization.get("issuedAt"), "issuedAt")
    expires_at = _timestamp(authorization.get("expiresAt"), "expiresAt")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=_MAX_AUTHORIZATION_LIFETIME_SECONDS)
        or now < issued_at - timedelta(seconds=30)
        or now >= expires_at
    ):
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_EXPIRED", "create authorization is expired", 403)
    unsigned = {key: authorization[key] for key in authorization if key not in {"payloadSha256", "signature"}}
    if authorization["payloadSha256"] != _sha256(unsigned):
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_HASH_MISMATCH", "create authorization hash is invalid", 403)
    key, key_id = _authorization_public_key(public_key_pem, expected_key_id)
    if authorization.get("keyId") != key_id:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_ID_MISMATCH", "create key id is not trusted", 403)
    try:
        key.verify(
            base64.b64decode(str(authorization.get("signature") or ""), validate=True),
            _canonical_bytes(unsigned),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_CREATE_AUTHORIZATION_SIGNATURE_INVALID", "create signature is invalid", 403) from exc
    return authorization


def _validated_id_list(value: Any, field: str, *, maximum: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ConstructWorkLoopError("WORK_ID_LIST_INVALID", f"{field} is invalid")
    normalized = [_safe_id(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(set(normalized)) != len(normalized):
        raise ConstructWorkLoopError("WORK_ID_LIST_INVALID", f"{field} contains duplicates")
    return normalized


def _validate_handoff_authorization(
    value: Any,
    *,
    owner_user_id: str,
    handoff_payload: dict[str, Any],
    public_key_pem: str | None,
    expected_key_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    authorization = _exact(
        _bounded_object(value, "handoffAuthorization", 64 * 1024),
        _HANDOFF_AUTH_FIELDS,
        "handoffAuthorization",
    )
    if (
        authorization.get("contract") != WORK_HANDOFF_AUTHORIZATION_CONTRACT
        or authorization.get("authority") != "chatty-core-host"
        or authorization.get("algorithm") != "ed25519"
    ):
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_INVALID", "handoff authorization is invalid", 403)
    _safe_id(authorization.get("authorizationId"), "authorizationId")
    if _safe_id(authorization.get("ownerPrincipalId"), "ownerPrincipalId", principal=True) != str(owner_user_id):
        raise ConstructWorkLoopError("WORK_OWNER_SCOPE_MISMATCH", "handoff owner is not authenticated owner", 403)
    for field in ("programId", "threadId", "sessionId", "branchId", "goalRevision", "expectedHeadEventId"):
        _safe_id(authorization.get(field), field)
    for field in ("constructId", "fromPrincipalId", "toPrincipalId"):
        _safe_id(authorization.get(field), field, principal=True)
    _digest(authorization.get("expectedHeadSha256"), "expectedHeadSha256")
    _safe_id(authorization.get("idempotencyKey"), "idempotencyKey")
    delegated_item_ids = _validated_id_list(authorization.get("delegatedItemIds"), "delegatedItemIds")
    if not delegated_item_ids:
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_INVALID", "handoff delegates no work items", 403)
    evidence_ids = _validated_id_list(authorization.get("evidenceReferenceIds"), "evidenceReferenceIds")
    prerequisite_ids = _validated_id_list(
        authorization.get("prerequisiteEvidenceReferenceIds"),
        "prerequisiteEvidenceReferenceIds",
    )
    if not set(prerequisite_ids).issubset(evidence_ids):
        raise ConstructWorkLoopError(
            "WORK_HANDOFF_AUTHORIZATION_INVALID",
            "handoff prerequisites must be within its evidence allowlist",
            403,
        )
    handoff_payload_sha = _digest(authorization.get("handoffPayloadSha256"), "handoffPayloadSha256")
    if handoff_payload_sha != _sha256(handoff_payload):
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_PAYLOAD_MISMATCH", "handoff payload is not authorized", 403)
    issued_at = _timestamp(authorization.get("issuedAt"), "issuedAt")
    expires_at = _timestamp(authorization.get("expiresAt"), "expiresAt")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(hours=24)
        or now < issued_at - timedelta(seconds=30)
        or now >= expires_at
    ):
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_EXPIRED", "handoff authorization is expired", 403)
    unsigned = {key: authorization[key] for key in authorization if key not in {"payloadSha256", "signature"}}
    payload_sha = _digest(authorization.get("payloadSha256"), "authorization.payloadSha256")
    if payload_sha != _sha256(unsigned):
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_HASH_MISMATCH", "handoff authorization hash is invalid", 403)
    key, key_id = _authorization_public_key(public_key_pem, expected_key_id)
    if authorization.get("keyId") != key_id:
        raise ConstructWorkLoopError("WORK_AUTHORIZATION_KEY_ID_MISMATCH", "handoff key id is not trusted", 403)
    try:
        signature = base64.b64decode(str(authorization.get("signature") or ""), validate=True)
        key.verify(signature, _canonical_bytes(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_HANDOFF_AUTHORIZATION_SIGNATURE_INVALID", "handoff signature is invalid", 403) from exc
    return {
        **authorization,
        "delegatedItemIds": delegated_item_ids,
        "evidenceReferenceIds": evidence_ids,
        "prerequisiteEvidenceReferenceIds": prerequisite_ids,
        "handoffPayloadSha256": handoff_payload_sha,
        "payloadSha256": payload_sha,
    }


def _validate_handoff_document(value: Any, *, require_accepted: bool) -> dict[str, Any]:
    handoff = _exact(value, _HANDOFF_FIELDS, "handoff")
    if handoff.get("contract") != WORK_HANDOFF_CONTRACT:
        raise ConstructWorkLoopError("WORK_HANDOFF_INVALID", "handoff contract is invalid")
    delegated = _validated_id_list(handoff.get("delegatedItemIds"), "handoff.delegatedItemIds", maximum=256)
    if not delegated:
        raise ConstructWorkLoopError("WORK_HANDOFF_INVALID", "handoff delegates no items")
    evidence_ids = _validated_id_list(handoff.get("evidenceReferenceIds"), "handoff.evidenceReferenceIds")
    prerequisites = _validated_id_list(
        handoff.get("prerequisiteEvidenceReferenceIds"),
        "handoff.prerequisiteEvidenceReferenceIds",
    )
    if not set(prerequisites).issubset(evidence_ids):
        raise ConstructWorkLoopError("WORK_HANDOFF_INVALID", "handoff prerequisites exceed evidence allowlist")
    capability = _exact(handoff.get("oneUseCapability"), _HANDOFF_CAPABILITY_FIELDS, "handoff.oneUseCapability")
    capability_body = {key: value for key, value in capability.items() if key != "capabilityHash"}
    if (
        capability.get("contract") != "chatty-work-handoff-capability/v1"
        or capability.get("handoffId") != handoff.get("handoffId")
        or capability.get("programId") != handoff.get("programId")
        or capability.get("issuedToPrincipalId") != handoff.get("destinationConstructId")
        or capability.get("allowedEventType") != "handoff_accepted"
        or capability.get("oneUse") is not True
        or _digest(capability.get("capabilityHash"), "handoff.capabilityHash") != _sha256(capability_body)
    ):
        raise ConstructWorkLoopError("WORK_HANDOFF_CAPABILITY_INVALID", "handoff capability is invalid")
    accepted_at = handoff.get("acceptedAt")
    consumed_at = capability.get("consumedAt")
    if require_accepted:
        if accepted_at is None or consumed_at != accepted_at or handoff.get("acceptanceEvidenceReferenceId") is None:
            raise ConstructWorkLoopError("WORK_HANDOFF_ACCEPTANCE_INVALID", "handoff capability was not consumed exactly once")
    elif accepted_at is not None or consumed_at is not None or handoff.get("acceptanceEvidenceReferenceId") is not None:
        raise ConstructWorkLoopError("WORK_HANDOFF_INVALID", "issued handoff must be unconsumed")
    issued_at = _timestamp(handoff.get("issuedAt"), "handoff.issuedAt")
    expires_at = _timestamp(handoff.get("expiresAt"), "handoff.expiresAt")
    if expires_at <= issued_at or (accepted_at is not None and _timestamp(accepted_at, "handoff.acceptedAt") >= expires_at):
        raise ConstructWorkLoopError("WORK_HANDOFF_EXPIRED", "handoff time boundary is invalid", 409)
    handoff_body = {key: value for key, value in handoff.items() if key != "handoffHash"}
    if _digest(handoff.get("handoffHash"), "handoffHash") != _sha256(handoff_body):
        raise ConstructWorkLoopError("WORK_HANDOFF_HASH_MISMATCH", "handoff hash is invalid")
    return handoff


def _signed_event(event: dict[str, Any], *, private_key_pem: str | None) -> dict[str, Any]:
    signature = canonical_projection_signing.sign_canonical_payload(
        event, private_key_pem=private_key_pem
    )
    envelope = {
        "contract": WORK_EVENT_ENVELOPE_CONTRACT,
        **signature,
        "event": event,
        "payloadSha256": _sha256(event),
    }
    if len(_canonical_bytes(envelope)) > _MAX_EVENT_BYTES:
        raise ConstructWorkLoopError(
            "WORK_EVENT_ENVELOPE_OVERSIZED",
            "signed work event envelope exceeds 64 KiB",
            413,
        )
    return envelope


def _signed_document(
    contract: str,
    body: dict[str, Any],
    *,
    private_key_pem: str | None,
) -> dict[str, Any]:
    unsigned = {"contract": contract, **body}
    signature = canonical_projection_signing.sign_canonical_payload(
        unsigned, private_key_pem=private_key_pem
    )
    return {**unsigned, "payloadSha256": _sha256(unsigned), **signature}


def _verified_scope_resolution(
    value: Any,
    *,
    owner_user_id: str,
    private_key_pem: str | None,
    now: datetime,
) -> dict[str, Any]:
    receipt = _exact(
        _bounded_object(value, "scopeResolution", 64 * 1024),
        _SCOPE_RESOLUTION_FIELDS,
        "scopeResolution",
    )
    unsigned = {
        key: value for key, value in receipt.items()
        if key not in {"payloadSha256", "algorithm", "keyId", "signature"}
    }
    if (
        receipt.get("contract") != WORK_SCOPE_RESOLUTION_CONTRACT
        or receipt.get("ownerPrincipalId") != str(owner_user_id)
        or receipt.get("algorithm") != "Ed25519"
        or receipt.get("payloadSha256") != _sha256(unsigned)
        or now >= _timestamp(receipt.get("expiresAt"), "scopeResolution.expiresAt")
    ):
        raise ConstructWorkLoopError("WORK_SCOPE_RESOLUTION_INVALID", "scope resolution is invalid or expired", 403)
    try:
        canonical_projection_signing.verify_canonical_payload(
            unsigned,
            {key: receipt[key] for key in ("algorithm", "keyId", "signature")},
            private_key_pem=private_key_pem,
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        raise ConstructWorkLoopError("WORK_SCOPE_RESOLUTION_INVALID", "scope resolution signature is invalid", 403) from exc
    return receipt


def _stored_event_envelope(row: dict[str, Any]) -> dict[str, Any]:
    envelope = row.get("envelope")
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except json.JSONDecodeError as exc:
            raise ConstructWorkLoopError("WORK_CANONICAL_EVENT_INVALID", "stored event envelope is malformed", 503) from exc
    if not isinstance(envelope, dict):
        raise ConstructWorkLoopError("WORK_CANONICAL_EVENT_INVALID", "stored event envelope is missing", 503)
    if len(_canonical_bytes(envelope)) > _MAX_EVENT_BYTES:
        raise ConstructWorkLoopError(
            "WORK_CANONICAL_EVENT_INVALID",
            "stored work event envelope exceeds 64 KiB",
            503,
        )
    _exact(envelope, frozenset({"contract", "algorithm", "keyId", "event", "payloadSha256", "signature"}), "eventEnvelope")
    event = _exact(envelope.get("event"), _EVENT_FIELDS, "event")
    _exact(event.get("actor"), _ACTOR_FIELDS, "event.actor")
    if (
        envelope.get("contract") != WORK_EVENT_ENVELOPE_CONTRACT
        or envelope.get("algorithm") != "Ed25519"
        or _sha256(event) != envelope.get("payloadSha256")
        or _sha256({key: event[key] for key in event if key != "eventSha256"}) != event.get("eventSha256")
        or _sha256(event.get("payload")) != event.get("payloadSha256")
    ):
        raise ConstructWorkLoopError("WORK_CANONICAL_EVENT_INVALID", "stored event hashes are invalid", 503)
    return envelope


@dataclass
class ConstructWorkLoopService:
    connect: Callable[[], Any] = chatty_body_service._connect
    private_key_pem: str | None = None
    authorization_public_key_pem: str | None = None
    authorization_key_id: str | None = None
    evidence_resolver: Callable[[Any, str, str], dict[str, Any] | None] | None = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def _thread_scope(
        self,
        cur: Any,
        owner_user_id: str,
        thread_id: str,
        construct_id: str,
        *,
        session_id: str | None = None,
        program_id: str | None = None,
    ) -> dict[str, Any]:
        cur.execute(
            """SELECT t.membership_revision,m.principal_type,m.active,
                      i.id::text AS construct_incarnation_id,i.generation
                 FROM ovvaults.conversation_threads t
                 JOIN ovvaults.conversation_thread_memberships m
                   ON m.thread_id=t.thread_id AND m.owner_user_id=t.owner_user_id
                 JOIN ovvaults.construct_incarnations i
                   ON i.owner_user_id=t.owner_user_id
                  AND i.construct_id=m.principal_id AND i.retired_at IS NULL
                WHERE t.owner_user_id=%s AND t.thread_id=%s
                  AND m.principal_id=%s AND m.principal_type='construct'
                  AND m.active=true""",
            (owner_user_id, thread_id, construct_id),
        )
        row = _row(cur.fetchone())
        if not row:
            raise ConstructWorkLoopError("WORK_SCOPE_NOT_FOUND", "owner-qualified active construct thread scope was not found", 404)
        cur.execute(
            """SELECT 1 FROM ovvaults.vault_files
                WHERE user_id::text=%s AND construct_id=%s LIMIT 1""",
            (str(owner_user_id), construct_id),
        )
        if not cur.fetchone():
            raise ConstructWorkLoopError("WORK_CONSTRUCT_NOT_FOUND", "construct is not resolved for authenticated owner", 404)
        branch = _branch_id(
            owner_user_id=str(owner_user_id),
            program_id=str(program_id or session_id or thread_id),
            construct_id=construct_id,
            thread_id=thread_id,
        )
        source_revision = _sha256({
            "ownerPrincipalId": str(owner_user_id),
            "constructId": construct_id,
            "constructIncarnationId": str(row["construct_incarnation_id"]),
            "constructGeneration": int(row["generation"]),
            "threadId": thread_id,
            "sessionId": session_id,
            "branchId": branch,
            "membershipRevision": int(row["membership_revision"]),
        })
        return {
            "membershipRevision": int(row["membership_revision"]),
            "constructIncarnationId": str(row["construct_incarnation_id"]),
            "constructGeneration": int(row["generation"]),
            "branchId": branch,
            "sourceRevision": source_revision,
        }

    def _canonical_singleton_read_scope(
        self,
        cur: Any,
        owner_user_id: str,
        thread_id: str,
        construct_id: str,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Resolve read-only no-active work scope for an existing Chatty singleton.

        This does not authorize work-program creation. It only lets ordinary
        canonical singleton turns prove that no durable work program is active
        without manufacturing a duplicate conversation_threads row.
        """
        expected_thread_id = f"{construct_id}_chat_with_{construct_id}"
        if thread_id != expected_thread_id or session_id != expected_thread_id:
            raise ConstructWorkLoopError(
                "WORK_SCOPE_NOT_FOUND",
                "owner-qualified active construct thread scope was not found",
                404,
            )
        cur.execute(
            """SELECT EXISTS (
                     SELECT 1 FROM ovvaults.vault_files file
                      WHERE file.user_id=%s AND file.construct_id=%s
                   ) AS construct_exists,
                   (
                     SELECT i.id::text FROM ovvaults.construct_incarnations i
                      WHERE i.owner_user_id=%s AND i.construct_id=%s
                        AND i.retired_at IS NULL
                      ORDER BY i.generation DESC LIMIT 1
                   ) AS construct_incarnation_id,
                   (
                     SELECT i.generation FROM ovvaults.construct_incarnations i
                      WHERE i.owner_user_id=%s AND i.construct_id=%s
                        AND i.retired_at IS NULL
                      ORDER BY i.generation DESC LIMIT 1
                   ) AS generation""",
            (
                owner_user_id, construct_id,
                owner_user_id, construct_id,
                owner_user_id, construct_id,
            ),
        )
        incarnation = _row(cur.fetchone())
        if not incarnation or incarnation.get("construct_exists") is False:
            raise ConstructWorkLoopError(
                "WORK_SCOPE_NOT_FOUND",
                "owner-qualified canonical singleton construct scope was not found",
                404,
            )
        canonical_title = (
            f"instances/{construct_id}/chatty/chat_with_{construct_id}.md"
        )
        cur.execute(
            """SELECT source_hash,content
                 FROM ovvaults.transcripts
                WHERE user_id=%s AND lower(title)=lower(%s)
                  AND content IS NOT NULL AND content <> ''
                ORDER BY created_at DESC LIMIT 1""",
            (owner_user_id, canonical_title),
        )
        transcript = _row(cur.fetchone())
        if not transcript:
            raise ConstructWorkLoopError(
                "WORK_SCOPE_NOT_FOUND",
                "owner-qualified canonical singleton transcript scope was not found",
                404,
            )
        transcript_revision = str(transcript.get("source_hash") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", transcript_revision):
            transcript_revision = hashlib.sha256(
                str(transcript.get("content") or "").encode("utf-8")
            ).hexdigest()
        generation = max(1, int(incarnation.get("generation") or 1))
        construct_incarnation_id = str(
            incarnation.get("construct_incarnation_id")
            or (
                "canonical-singleton-"
                + hashlib.sha256(
                    f"{owner_user_id}\n{construct_id}".encode("utf-8")
                ).hexdigest()[:32]
            )
        )
        branch = _branch_id(
            owner_user_id=str(owner_user_id),
            program_id=thread_id,
            construct_id=construct_id,
            thread_id=thread_id,
        )
        source_revision = _sha256({
            "ownerPrincipalId": str(owner_user_id),
            "constructId": construct_id,
            "constructIncarnationId": construct_incarnation_id,
            "constructGeneration": generation,
            "threadId": thread_id,
            "sessionId": session_id,
            "branchId": branch,
            "membershipRevision": generation,
            "canonicalTranscriptRevision": transcript_revision,
            "scopeKind": (
                "canonical_singleton_read_only"
                if incarnation.get("construct_incarnation_id")
                else "legacy_canonical_singleton_read_only"
            ),
        })
        return {
            "membershipRevision": generation,
            "constructIncarnationId": construct_incarnation_id,
            "constructGeneration": generation,
            "branchId": branch,
            "sourceRevision": source_revision,
        }

    def _program(self, cur: Any, owner_user_id: str, program_id: str, *, for_update: bool = False) -> dict[str, Any]:
        cur.execute(
            """SELECT owner_user_id::text AS owner_user_id,program_id,thread_id,
                      session_id,branch_id,construct_id,construct_incarnation_id::text,
                      scope_source_revision,contract_version,initial_program,initial_program_sha256,
                      authorization_sha256,membership_revision,created_by_principal_id,
                      create_idempotency_key,created_at
                 FROM ovvaults.construct_work_programs
                WHERE owner_user_id=%s AND program_id=%s""" + (" FOR UPDATE" if for_update else ""),
            (owner_user_id, program_id),
        )
        result = _row(cur.fetchone())
        if not result:
            raise ConstructWorkLoopError("WORK_PROGRAM_NOT_FOUND", "work program was not found", 404)
        initial = result.get("initial_program")
        if isinstance(initial, str):
            try:
                initial = json.loads(initial)
            except json.JSONDecodeError as exc:
                raise ConstructWorkLoopError("WORK_CANONICAL_PROGRAM_INVALID", "stored program is malformed", 503) from exc
        if not isinstance(initial, dict) or _sha256(initial) != result.get("initial_program_sha256"):
            raise ConstructWorkLoopError("WORK_CANONICAL_PROGRAM_INVALID", "stored program hash is invalid", 503)
        result["initial_program"] = initial
        return result

    def _head(self, cur: Any, owner_user_id: str, program_id: str) -> dict[str, Any] | None:
        cur.execute(
            """SELECT event_id,sequence,event_type,event_sha256,goal_revision,
                      resulting_goal_revision,envelope,created_at
                 FROM ovvaults.construct_work_events
                WHERE owner_user_id=%s AND program_id=%s
                ORDER BY sequence DESC LIMIT 1""",
            (owner_user_id, program_id),
        )
        return _row(cur.fetchone())

    def _lock(self, cur: Any, owner_user_id: str, program_id: str, idempotency_key: str) -> None:
        keys = sorted({
            f"construct-work:{owner_user_id}:{program_id}",
            f"construct-work-idempotency:{owner_user_id}:{program_id}:{idempotency_key}",
        })
        for key in keys:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))

    def _lock_active_scope(
        self,
        cur: Any,
        owner_user_id: str,
        construct_id: str,
        thread_id: str,
    ) -> None:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"construct-work-active:{owner_user_id}:{construct_id}:{thread_id}",),
        )

    def _database_now(self, cur: Any) -> datetime:
        cur.execute("SELECT transaction_timestamp() AS occurred_at")
        row = _row(cur.fetchone())
        if not row or not isinstance(row.get("occurred_at"), datetime):
            raise ConstructWorkLoopError(
                "WORK_DATABASE_TIME_UNAVAILABLE",
                "database-assigned work event time is unavailable",
                503,
            )
        return row["occurred_at"].astimezone(timezone.utc)

    def _assert_authorization_scope(
        self,
        authorization: dict[str, Any],
        program: dict[str, Any],
        *,
        head: dict[str, Any] | None,
    ) -> None:
        expected_sequence = int((head or {}).get("sequence") or 0) + 1
        initial = program.get("initial_program")
        if isinstance(initial, str):
            initial = json.loads(initial)
        current_goal_revision = (
            (head or {}).get("resulting_goal_revision")
            or (initial.get("goal") or {}).get("revision")
        )
        if (
            authorization.get("programId") != program.get("program_id")
            or authorization.get("constructId") != program.get("construct_id")
            or authorization.get("threadId") != program.get("thread_id")
            or authorization.get("ownerPrincipalId") != str(program.get("owner_user_id"))
            or authorization.get("goalRevision") != current_goal_revision
            or authorization.get("expectedSequence") != expected_sequence
            or authorization.get("expectedHeadEventId") != ((head or {}).get("event_id") if head else None)
            or authorization.get("expectedHeadSha256") != ((head or {}).get("event_sha256") if head else None)
        ):
            raise ConstructWorkLoopError("WORK_CAS_SCOPE_MISMATCH", "authorization does not match canonical program head", 409)

    def _actor(
        self,
        *,
        event_type: str,
        owner_user_id: str,
        program: dict[str, Any],
        payload: dict[str, Any],
        cur: Any,
        actor_principal_id: str | None = None,
    ) -> dict[str, str]:
        if event_type in OWNER_EVENT_TYPES:
            return {"principalId": str(owner_user_id), "principalType": "human", "authority": "owner_authenticated"}
        if event_type == "handoff_accepted":
            handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
            principal_id = _safe_id(
                handoff.get("destinationConstructId"),
                "handoff.destinationConstructId",
                principal=True,
            )
        else:
            principal_id = _safe_id(
                actor_principal_id or program["construct_id"],
                "actorPrincipalId",
                principal=True,
            )
        cur.execute(
            """SELECT m.principal_type
                 FROM ovvaults.conversation_thread_memberships m
                 JOIN ovvaults.construct_incarnations i
                   ON i.owner_user_id=m.owner_user_id
                  AND i.construct_id=m.principal_id AND i.retired_at IS NULL
                WHERE m.owner_user_id=%s AND m.thread_id=%s
                  AND m.principal_id=%s AND m.active=true""",
            (owner_user_id, program["thread_id"], principal_id),
        )
        member = _row(cur.fetchone())
        if not member:
            raise ConstructWorkLoopError("WORK_ACTOR_SCOPE_MISMATCH", "event actor is not an active owner-qualified thread member", 403)
        return {"principalId": principal_id, "principalType": str(member["principal_type"]), "authority": "chatty-core"}

    def _verified_work_context(
        self,
        value: Any,
        *,
        owner_user_id: str,
        program: dict[str, Any],
        head: dict[str, Any],
        active_construct_id: str,
    ) -> dict[str, Any]:
        context = _exact(
            _bounded_object(value, "workContextProjection", 512 * 1024),
            _CONTEXT_FIELDS | frozenset({"algorithm", "keyId", "signature"}),
            "workContextProjection",
        )
        unsigned = {
            key: value for key, value in context.items()
            if key not in {"algorithm", "keyId", "signature"}
        }
        projection_body = {
            key: value for key, value in unsigned.items()
            if key != "projectionSha256"
        }
        if (
            context.get("contract") != WORK_CONTEXT_PROJECTION_CONTRACT
            or context.get("derivationAuthority") != "chatty-core"
            or context.get("persistenceAuthority") != AUTHORITY
            or context.get("requiresVvaultSignature") is not True
            or context.get("contextPolicyVersion") != "chatty-context-sea-policy/v1.1"
            or context.get("containsPrivateReasoning") is not False
            or context.get("containsEventPayloads") is not False
            or context.get("algorithm") != "Ed25519"
            or context.get("projectionSha256") != _sha256(projection_body)
        ):
            raise ConstructWorkLoopError(
                "WORK_CONTEXT_INVALID",
                "signed work context authority or hash is invalid",
                409,
            )
        try:
            canonical_projection_signing.verify_canonical_payload(
                unsigned,
                {key: context[key] for key in ("algorithm", "keyId", "signature")},
                private_key_pem=self.private_key_pem,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise ConstructWorkLoopError(
                "WORK_CONTEXT_SIGNATURE_INVALID",
                "signed work context signature is invalid",
                409,
            ) from exc
        if (
            context.get("ownerPrincipalId") != str(owner_user_id)
            or context.get("programId") != program["program_id"]
            or context.get("constructId") != program["construct_id"]
            or context.get("activeConstructId") != active_construct_id
            or context.get("threadId") != program["thread_id"]
            or context.get("sessionId") != program["session_id"]
            or context.get("branchId") != program["branch_id"]
            or context.get("goalRevision") != head.get("resulting_goal_revision")
        ):
            raise ConstructWorkLoopError(
                "WORK_CONTEXT_SCOPE_MISMATCH",
                "signed work context does not match canonical active program scope",
                409,
            )
        receipt = _exact(
            context.get("stateReceipt"),
            _STATE_RECEIPT_FIELDS,
            "workContextProjection.stateReceipt",
        )
        receipt_body = {
            key: value for key, value in receipt.items()
            if key != "receiptSha256"
        }
        if (
            receipt.get("contract") != "chatty-work-state-receipt/v1"
            or receipt.get("receiptSha256") != _sha256(receipt_body)
            or receipt.get("programId") != program["program_id"]
            or receipt.get("headEventId") != head.get("event_id")
            or receipt.get("headEventSha256") != head.get("event_sha256")
            or receipt.get("sequence") != head.get("sequence")
            or receipt.get("eventCount") != head.get("sequence")
        ):
            raise ConstructWorkLoopError(
                "WORK_CONTEXT_STATE_MISMATCH",
                "signed work context state receipt does not match canonical head",
                409,
            )
        return context

    def commit_exchange_work_event_batch(
        self,
        cur: Any,
        *,
        owner_user_id: str,
        target_construct_id: str,
        turn_id: str,
        conversation_session_id: str,
        prompt_content: str,
        response_content: str,
        work_event_batch: dict[str, Any],
        work_context_projection: dict[str, Any],
        transcript_duplicate: bool,
        transcript_row: dict[str, Any],
        duplicate_projection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Commit/read back one Core-authorized delta in the transcript txn."""
        batch = _exact(
            _bounded_object(work_event_batch, "workEventBatch", 384 * 1024),
            _WORK_EVENT_BATCH_FIELDS,
            "workEventBatch",
        )
        batch_body = {key: value for key, value in batch.items() if key != "batchSha256"}
        if (
            batch.get("contract") != WORK_EVENT_BATCH_CONTRACT
            or batch.get("atomicWithTranscriptExchange") is not True
            or batch.get("batchSha256") != _sha256(batch_body)
        ):
            raise ConstructWorkLoopError(
                "WORK_EVENT_BATCH_INVALID",
                "work event batch contract or digest is invalid",
                409,
            )
        binding = _exact(
            batch.get("transcriptBinding"),
            _WORK_TRANSCRIPT_BINDING_FIELDS,
            "workEventBatch.transcriptBinding",
        )
        prompt_event_id = f"{turn_id}:prompt"
        response_event_id = f"{turn_id}:response"
        thread_id = str(binding.get("threadId") or "")
        exchange_core = {
            "contract": WORK_TRANSCRIPT_EXCHANGE_CORE_CONTRACT,
            "turnId": turn_id,
            "threadId": thread_id,
            "promptEventId": prompt_event_id,
            "responseEventId": response_event_id,
            "promptContentSha256": hashlib.sha256(prompt_content.encode("utf-8")).hexdigest(),
            "responseContentSha256": hashlib.sha256(response_content.encode("utf-8")).hexdigest(),
        }
        if (
            binding.get("contract") != WORK_TRANSCRIPT_BINDING_CONTRACT
            or binding.get("turnId") != turn_id
            or binding.get("promptEventId") != prompt_event_id
            or binding.get("responseEventId") != response_event_id
            or binding.get("promptContentSha256") != exchange_core["promptContentSha256"]
            or binding.get("responseContentSha256") != exchange_core["responseContentSha256"]
            or binding.get("exchangePayloadSha256") != _sha256(exchange_core)
        ):
            raise ConstructWorkLoopError(
                "WORK_TRANSCRIPT_BINDING_MISMATCH",
                "work batch does not match exact transcript exchange bytes",
                409,
            )
        target = chatty_body_service._transcript_target(target_construct_id)
        if thread_id != target["thread_id"]:
            raise ConstructWorkLoopError(
                "WORK_TRANSCRIPT_BINDING_MISMATCH",
                "work batch thread does not match canonical singleton",
                409,
            )
        raw_events = batch.get("events")
        if not isinstance(raw_events, list) or len(raw_events) > 3:
            raise ConstructWorkLoopError(
                "WORK_EVENT_BATCH_INVALID",
                "ordinary transcript exchange permits at most three work events",
                409,
            )
        events: list[dict[str, Any]] = []
        for index, raw_descriptor in enumerate(raw_events, start=1):
            descriptor = _exact(
                raw_descriptor,
                _WORK_EVENT_DESCRIPTOR_FIELDS,
                f"workEventBatch.events[{index - 1}]",
            )
            event_type = str(descriptor.get("eventType") or "")
            payload = self._validate_payload_shape(event_type, descriptor.get("payload"))
            if (
                descriptor.get("ordinal") != index
                or descriptor.get("eventPayloadSha256") != _sha256(payload)
            ):
                raise ConstructWorkLoopError(
                    "WORK_EVENT_BATCH_DESCRIPTOR_INVALID",
                    "work event descriptor ordinal or payload digest is invalid",
                    409,
                )
            events.append({
                "ordinal": index,
                "eventType": event_type,
                "payload": payload,
                "eventPayloadSha256": descriptor["eventPayloadSha256"],
            })
        no_delta = batch.get("noDelta") is True
        if (
            batch.get("eventCount") != len(events)
            or no_delta != (len(events) == 0)
            or (no_delta != (batch.get("authorization") is None))
            or (no_delta and (
                batch.get("expectedHeadEventId") is not None
                or batch.get("expectedHeadSha256") is not None
                or batch.get("expectedSequence") != 0
            ))
        ):
            raise ConstructWorkLoopError(
                "WORK_EVENT_BATCH_INVALID",
                "work event count, no-delta, or head contract is invalid",
                409,
            )
        event_types = [event["eventType"] for event in events]
        if events and event_types not in (
            ["evidence_attached"],
            ["reasoning_started", "next_action_proposed"],
            ["reasoning_started", "next_action_proposed", "evidence_attached"],
        ):
            raise ConstructWorkLoopError(
                "WORK_EVENT_BATCH_SEQUENCE_INVALID",
                "ordinary conversational work event ordering is invalid",
                409,
            )
        raw_batch_authorization = batch.get("authorization")
        program_id = (
            raw_batch_authorization.get("programId")
            if isinstance(raw_batch_authorization, dict)
            else work_context_projection.get("programId")
        )
        program_id = _safe_id(program_id, "workEventBatch.programId")
        idempotency_key = (
            raw_batch_authorization.get("idempotencyKey")
            if events and isinstance(raw_batch_authorization, dict)
            else f"work-no-delta-{batch['batchSha256'][:40]}"
        )
        self._lock(cur, str(owner_user_id), program_id, _safe_id(idempotency_key, "idempotencyKey"))
        program = self._program(cur, str(owner_user_id), program_id, for_update=True)
        current_scope = self._thread_scope(
            cur,
            str(owner_user_id),
            program["thread_id"],
            program["construct_id"],
            session_id=program["session_id"],
            program_id=program_id,
        )
        if (
            current_scope["membershipRevision"] != int(program["membership_revision"])
            or current_scope["constructIncarnationId"] != str(program["construct_incarnation_id"])
            or current_scope["sourceRevision"] != program["scope_source_revision"]
            or current_scope["branchId"] != program["branch_id"]
        ):
            raise ConstructWorkLoopError(
                "WORK_MEMBERSHIP_STALE",
                "thread membership changed before atomic transcript persistence",
                409,
            )
        head = self._head(cur, str(owner_user_id), program_id)
        if not head:
            raise ConstructWorkLoopError(
                "WORK_TERMINAL_STATE_IMMUTABLE",
                "active work context has no mutable canonical head",
                409,
            )
        context_head = head
        if transcript_duplicate:
            preview_receipt = (
                work_context_projection.get("stateReceipt")
                if isinstance(work_context_projection, dict) else None
            )
            preview_head_id = (
                preview_receipt.get("headEventId")
                if isinstance(preview_receipt, dict) else None
            )
            cur.execute(
                """SELECT event_id,sequence,event_type,event_sha256,goal_revision,
                          resulting_goal_revision,envelope,created_at
                     FROM ovvaults.construct_work_events
                    WHERE owner_user_id=%s AND program_id=%s AND event_id=%s""",
                (owner_user_id, program_id, preview_head_id),
            )
            context_head = _row(cur.fetchone())
            if not context_head:
                raise ConstructWorkLoopError(
                    "WORK_ATOMIC_READBACK_MISMATCH",
                    "signed pre-turn work head is unavailable for idempotent readback",
                    409,
                )
        elif head.get("event_type") in TERMINAL_EVENT_TYPES:
            raise ConstructWorkLoopError(
                "WORK_TERMINAL_STATE_IMMUTABLE",
                "terminal work program cannot advance with a transcript exchange",
                409,
            )
        context = self._verified_work_context(
            work_context_projection,
            owner_user_id=str(owner_user_id),
            program=program,
            head=context_head,
            active_construct_id=_safe_id(
                target_construct_id, "targetConstructId", principal=True
            ),
        )
        state_receipt_sha = context["stateReceipt"]["receiptSha256"]
        if batch.get("expectedStateReceiptSha256") != state_receipt_sha:
            raise ConstructWorkLoopError(
                "WORK_EVENT_BATCH_STATE_MISMATCH",
                "work batch state receipt does not match signed provider context",
                409,
            )
        self._validate_execution_recovery_artifacts(
            cur, owner_user_id=str(owner_user_id), program=program,
            context_head=context_head, state_receipt_sha256=state_receipt_sha,
            descriptors=events,
        )
        if transcript_duplicate:
            messages = (
                duplicate_projection.get("messages")
                if isinstance(duplicate_projection, dict) else None
            )
            prompt_match = next((item for item in messages or [] if (
                item.get("id") == prompt_event_id
                and item.get("role") == "user"
            )), None)
            response_match = next((item for item in messages or [] if (
                item.get("id") == response_event_id
                and item.get("role") == "assistant"
            )), None)
            if (
                not prompt_match or prompt_match.get("content") != prompt_content
                or not response_match or response_match.get("content") != response_content
            ):
                raise ConstructWorkLoopError(
                    "WORK_ATOMIC_READBACK_MISMATCH",
                    "duplicate transcript pair does not match work batch bytes",
                    409,
                )
        event_envelopes: list[dict[str, Any]] = []
        status = "no_delta"
        if events:
            authorization = _validate_batch_authorization(
                raw_batch_authorization,
                owner_user_id=str(owner_user_id),
                descriptors=events,
                transcript_binding_sha256=_sha256(binding),
                public_key_pem=self.authorization_public_key_pem,
                expected_key_id=self.authorization_key_id,
                now=self.now(),
            )
            if (
                authorization.get("programId") != program_id
                or authorization.get("constructId") != program["construct_id"]
                or authorization.get("actorPrincipalId") != target_construct_id
                or authorization.get("threadId") != program["thread_id"]
                or authorization.get("sessionId") != program["session_id"]
                or authorization.get("branchId") != program["branch_id"]
                or authorization.get("expectedStateReceiptSha256") != state_receipt_sha
                or authorization.get("goalRevision") != context_head.get("resulting_goal_revision")
                or authorization.get("resultingGoalRevision") != authorization.get("goalRevision")
                or batch.get("expectedHeadEventId") != context_head.get("event_id")
                or batch.get("expectedHeadSha256") != context_head.get("event_sha256")
                or batch.get("expectedSequence") != int(context_head.get("sequence") or 0) + 1
                or authorization.get("expectedHeadEventId") != batch.get("expectedHeadEventId")
                or authorization.get("expectedHeadSha256") != batch.get("expectedHeadSha256")
                or authorization.get("expectedSequence") != batch.get("expectedSequence")
            ):
                raise ConstructWorkLoopError(
                    "WORK_EVENT_BATCH_CAS_MISMATCH",
                    "work batch does not match canonical program head",
                    409,
                )
            actor = self._actor(
                event_type=events[0]["eventType"],
                owner_user_id=str(owner_user_id),
                program=program,
                payload=events[0]["payload"],
                cur=cur,
                actor_principal_id=authorization["actorPrincipalId"],
            )
            if (
                actor.get("principalId") != authorization.get("actorPrincipalId")
                or actor.get("principalType") != authorization.get("actorPrincipalType")
                or actor.get("authority") != authorization.get("actorAuthority")
            ):
                raise ConstructWorkLoopError(
                    "WORK_EVENT_BATCH_ACTOR_INVALID",
                    "batch actor does not match active canonical thread membership",
                    403,
                )
            cur.execute(
                """SELECT sequence,event_type,payload_sha256,
                          core_authorization_sha256,envelope
                     FROM ovvaults.construct_work_events
                    WHERE owner_user_id=%s AND program_id=%s
                      AND core_authorization_sha256=%s
                    ORDER BY sequence""",
                (owner_user_id, program_id, authorization["payloadSha256"]),
            )
            existing_rows = [_row(row) for row in cur.fetchall()]
            if existing_rows:
                if len(existing_rows) != len(events):
                    raise ConstructWorkLoopError(
                        "WORK_ATOMIC_PARTIAL_STATE",
                        "atomic work event batch is partial",
                        409,
                    )
                prior_event = {
                    "eventId": context_head["event_id"],
                    "eventSha256": context_head["event_sha256"],
                }
                for descriptor, row in zip(events, existing_rows):
                    envelope = _stored_event_envelope(row)
                    event = envelope["event"]
                    expected_sequence = int(context_head["sequence"]) + descriptor["ordinal"]
                    if (
                        row.get("sequence") != expected_sequence
                        or row.get("event_type") != descriptor["eventType"]
                        or row.get("payload_sha256") != descriptor["eventPayloadSha256"]
                        or row.get("core_authorization_sha256") != authorization["payloadSha256"]
                        or event.get("parentEventId") != prior_event["eventId"]
                        or event.get("parentEventSha256") != prior_event["eventSha256"]
                        or event.get("actor") != actor
                    ):
                        raise ConstructWorkLoopError(
                            "WORK_ATOMIC_READBACK_MISMATCH",
                            "atomic work event batch readback is mismatched",
                            409,
                        )
                    event_envelopes.append(envelope)
                    prior_event = event
            if transcript_duplicate:
                if not existing_rows:
                    raise ConstructWorkLoopError(
                        "WORK_ATOMIC_PARTIAL_STATE",
                        "transcript exists without its atomic work event batch",
                        409,
                    )
                status = "idempotent_readback"
            else:
                if existing_rows:
                    raise ConstructWorkLoopError(
                        "WORK_ATOMIC_PARTIAL_STATE",
                        "work event batch exists without its atomic transcript pair",
                        409,
                    )
                previous = context_head
                for descriptor in events:
                    payload = descriptor["payload"]
                    evidence_references = self._validate_event_evidence(
                        cur,
                        owner_user_id=str(owner_user_id),
                        program=program,
                        event_type=descriptor["eventType"],
                        payload=payload,
                    )
                    control = {
                        "expectedSequence": int(context_head["sequence"]) + descriptor["ordinal"],
                        "expectedHeadEventId": (
                            previous.get("eventId") or previous.get("event_id")
                        ),
                        "expectedHeadSha256": (
                            previous.get("eventSha256") or previous.get("event_sha256")
                        ),
                        "eventType": descriptor["eventType"],
                        "eventPayloadSha256": descriptor["eventPayloadSha256"],
                        "idempotencyKey": (
                            "work-batch-event-"
                            + _sha256({
                                "batchAuthorization": authorization["payloadSha256"],
                                "ordinal": descriptor["ordinal"],
                            })[:40]
                        ),
                        "goalRevision": authorization["goalRevision"],
                        "resultingGoalRevision": authorization["resultingGoalRevision"],
                        "payloadSha256": authorization["payloadSha256"],
                    }
                    envelope = self._insert_event(
                        cur,
                        owner_user_id=str(owner_user_id),
                        program=program,
                        authorization=control,
                        payload=payload,
                        actor=actor,
                        occurred_at=self._database_now(cur),
                        request_digest=_sha256({
                            "workEventBatch": batch,
                            "transcriptBinding": binding,
                            "ordinal": descriptor["ordinal"],
                        }),
                        evidence_digest=_payload_evidence_digest(
                            payload, evidence_references
                        ),
                        stored_authorization=authorization,
                    )
                    event_envelopes.append(envelope)
                    previous = envelope["event"]
                status = "appended"
        elif transcript_duplicate:
            status = "idempotent_readback"
        resulting_event = (
            event_envelopes[-1]["event"] if event_envelopes else context_head
        )
        receipt_body = {
            "contract": WORK_ATOMIC_EXCHANGE_RECEIPT_CONTRACT,
            "status": status,
            "ownerPrincipalId": str(owner_user_id),
            "programId": program_id,
            "turnId": turn_id,
            "conversationSessionId": conversation_session_id,
            "transcriptBindingSha256": _sha256(binding),
            "batchSha256": batch["batchSha256"],
            "stateReceiptSha256": state_receipt_sha,
            "priorHeadEventId": context_head["event_id"],
            "priorHeadSha256": context_head["event_sha256"],
            "priorSequence": context_head["sequence"],
            "eventCount": len(events),
            "workEvents": [{
                "eventId": envelope["event"]["eventId"],
                "eventSha256": envelope["event"]["eventSha256"],
                "sequence": envelope["event"]["sequence"],
                "eventType": envelope["event"]["eventType"],
            } for envelope in event_envelopes],
            "resultingHeadEventId": (
                resulting_event.get("eventId") or resulting_event.get("event_id")
            ),
            "resultingHeadSha256": (
                resulting_event.get("eventSha256") or resulting_event.get("event_sha256")
            ),
            "resultingSequence": resulting_event.get("sequence"),
            "noDelta": no_delta,
            "atomicWithTranscriptExchange": True,
            "transcriptRevision": str(transcript_row.get("source_hash") or ""),
            "transcriptLength": int(
                transcript_row.get("content_full_length") or 0
            ),
            "readbackVerified": True,
        }
        return _signed_document(
            WORK_ATOMIC_EXCHANGE_RECEIPT_CONTRACT,
            {key: value for key, value in receipt_body.items() if key != "contract"},
            private_key_pem=self.private_key_pem,
        )

    def _evidence_reference(
        self,
        value: Any,
        *,
        program: dict[str, Any],
        item_id: str | None,
    ) -> dict[str, Any]:
        reference = _exact(value, _EVIDENCE_FIELDS, "evidenceReference")
        if reference.get("contract") != WORK_EVIDENCE_REFERENCE_CONTRACT:
            raise ConstructWorkLoopError("WORK_EVIDENCE_CONTRACT_INVALID", "evidence reference contract is invalid")
        _safe_id(reference.get("evidenceId"), "evidenceId")
        _digest(reference.get("payloadSha256"), "evidence.payloadSha256")
        _digest(reference.get("receiptSha256"), "evidence.receiptSha256")
        _validated_id_list(reference.get("verifiedFactKinds"), "evidence.verifiedFactKinds", maximum=32)
        scope = _exact(reference.get("scope"), _EVIDENCE_SCOPE_FIELDS, "evidence.scope")
        initial = program["initial_program"]
        if isinstance(initial, str):
            initial = json.loads(initial)
        if (
            scope.get("ownerPrincipalId") != str(program["owner_user_id"])
            or scope.get("programId") != program["program_id"]
            or scope.get("constructId") != program["construct_id"]
            or scope.get("threadId") != program["thread_id"]
            or scope.get("sessionId") != initial.get("sessionId")
            or scope.get("itemId") != item_id
        ):
            raise ConstructWorkLoopError("WORK_EVIDENCE_SCOPE_MISMATCH", "evidence scope does not match canonical work program", 409)
        if reference.get("cryptographicallyVerified") is not True and reference.get("advancementAuthority") is True:
            if reference.get("evidenceType") != "owner_attestation":
                raise ConstructWorkLoopError("WORK_UNVERIFIED_EVIDENCE_CANNOT_ADVANCE", "unverified evidence cannot advance work", 409)
        return reference

    def _default_evidence_resolver(
        self,
        cur: Any,
        owner_user_id: str,
        evidence_id: str,
        *,
        program: dict[str, Any],
    ) -> dict[str, Any] | None:
        cur.execute(
            """SELECT content,content_sha256 AS payload_sha256,participant_frame,
                      participant_frame_signature
                 FROM ovvaults.transcript_events
                WHERE owner_user_id=%s AND thread_id=%s AND event_id=%s""",
            (owner_user_id, program["thread_id"], evidence_id),
        )
        transcript = _row(cur.fetchone())
        if transcript:
            frame = transcript.get("participant_frame")
            if isinstance(frame, str):
                frame = json.loads(frame)
            if (
                hashlib.sha256(str(transcript.get("content") or "").encode("utf-8")).hexdigest()
                != transcript.get("payload_sha256")
                or not isinstance(frame, dict)
                or not conversation_thread_service.verify_payload(
                    frame, str(transcript.get("participant_frame_signature") or "")
                )
            ):
                raise ConstructWorkLoopError("WORK_EVIDENCE_INTEGRITY_FAILED", "transcript evidence is invalid", 409)
            return {
                "payload_sha256": transcript["payload_sha256"],
                "receipt_sha256": transcript["payload_sha256"],
                "authority": "ovvaults.transcript_events",
                "evidence_type": "host_readback",
                "verified_fact_kinds": ["canonical_transcript_event"],
                "advancement_authority": True,
            }
        cur.execute(
            """SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                      evidence_sha256,signature,actor_principal_id,created_at
                 FROM ovvaults.qa_evaluation_events
                WHERE owner_user_id=%s AND thread_id=%s AND qa_event_id::text=%s""",
            (owner_user_id, program["thread_id"], evidence_id),
        )
        qa_row = _row(cur.fetchone())
        if qa_row:
            try:
                envelope = conversation_thread_service.verified_qa_evidence_envelope(
                    qa_row, conversation_thread_service._signing_secret()
                )
            except conversation_thread_service.ConversationContractError as exc:
                raise ConstructWorkLoopError("WORK_EVIDENCE_INTEGRITY_FAILED", "QA evidence is invalid", 409) from exc
            qa_fact_map = {
                "turn_response_received": ["canonical_pair_readback"],
                "tester_turn_persistence_readback_recorded": ["canonical_persistence_readback"],
                "legacy_turn_attachment_verified": ["legacy_canonical_attachment_readback"],
            }
            facts = qa_fact_map.get(envelope.get("eventType"))
            if not facts:
                raise ConstructWorkLoopError("WORK_EVIDENCE_TYPE_UNSUPPORTED", "QA evidence carries no advancement fact", 409)
            return {
                "payload_sha256": envelope["evidenceSha256"],
                "receipt_sha256": envelope["evidenceSha256"],
                "authority": "ovvaults.qa_evaluation_events",
                "evidence_type": "host_readback",
                "verified_fact_kinds": facts,
                "advancement_authority": True,
            }
        projection, status = knowledge_contract.resolve_knowledge_references(
            owner_user_id=str(owner_user_id),
            instance_id=program["construct_id"],
            references=[{"artifact_id": evidence_id, "required": True}],
            private_key_pem=self.private_key_pem,
        )
        artifacts = projection.get("artifacts") if isinstance(projection, dict) else None
        if status != 200 or not isinstance(artifacts, list) or len(artifacts) != 1:
            return None
        artifact = artifacts[0]
        publication = artifact.get("publication_evidence") or {}
        if publication.get("approved") is not True or publication.get("signedPublication") is not True:
            raise ConstructWorkLoopError("WORK_EVIDENCE_NOT_VERIFIED", "artifact publication is not verified", 409)
        facts = ["signed_canonical_publication"]
        for claim in artifact.get("claims") or []:
            claim_id = str(claim.get("claim_id") or "").strip()
            if claim_id:
                facts.append(_safe_id(f"claim:{claim_id}", "claimFactKind"))
        return {
            "payload_sha256": artifact["sha256"],
            "receipt_sha256": artifact["sha256"],
            "authority": "ovvaults.signed-knowledge-publication",
            "evidence_type": "canonical_context",
            "verified_fact_kinds": sorted(set(facts)),
            "advancement_authority": False,
        }

    def _validate_event_evidence(
        self,
        cur: Any,
        *,
        owner_user_id: str,
        program: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[dict[str, Any], str | None]] = []
        try:
            if event_type == "scope_approved":
                candidates.append((payload["approvalEvidence"], None))
            elif event_type == "owner_choice_recorded":
                correction = payload.get("correction") or {}
                candidates.append((payload["evidence"], correction.get("itemId")))
            elif event_type == "handoff_accepted":
                handoff = payload.get("handoff") or {}
                evidence = payload["evidence"]
                evidence_scope = evidence.get("scope") if isinstance(evidence, dict) else {}
                evidence_item_id = evidence_scope.get("itemId") if isinstance(evidence_scope, dict) else None
                if evidence_item_id not in handoff.get("delegatedItemIds", []):
                    raise ConstructWorkLoopError(
                        "WORK_HANDOFF_EVIDENCE_SCOPE_INVALID",
                        "handoff acceptance evidence is outside delegated work",
                        409,
                    )
                candidates.append((evidence, evidence_item_id))
            elif event_type == "evidence_attached":
                candidates.append((payload["evidence"], payload.get("itemId")))
            elif event_type == "resumed":
                candidates.append((payload["evidence"], None))
            elif event_type == "superseded":
                candidates.append((payload["supersessionEvidence"], None))
        except KeyError as exc:
            raise ConstructWorkLoopError(
                "WORK_EVENT_EVIDENCE_REQUIRED",
                f"{event_type} is missing its canonical evidence reference",
                409,
            ) from exc
        for raw, item_id in candidates:
            reference = self._evidence_reference(raw, program=program, item_id=item_id)
            if reference.get("evidenceType") == "owner_attestation":
                if event_type not in {"scope_approved", "owner_choice_recorded", "resumed", "superseded"}:
                    raise ConstructWorkLoopError("WORK_OWNER_EVIDENCE_SCOPE_INVALID", "owner attestation is invalid for this event", 409)
                prefix = "owner-attestation:"
                evidence_id = reference.get("evidenceId", "")
                if not evidence_id.startswith(prefix):
                    raise ConstructWorkLoopError("WORK_OWNER_EVIDENCE_INVALID", "owner attestation identity is invalid", 409)
                locator_id = evidence_id[len(prefix):]
                if not re.match(r"^(correction|waiver):", locator_id):
                    raise ConstructWorkLoopError("WORK_OWNER_EVIDENCE_INVALID", "owner attestation purpose is invalid", 409)
                attestation = {
                    "ownerPrincipalId": str(owner_user_id),
                    "programId": program["program_id"],
                    "locatorId": locator_id,
                    "itemId": item_id,
                }
                expected_fact_kinds = (
                    ["owner_correction"] if locator_id.startswith("correction:")
                    else ["criterion_waived"]
                )
                if (
                    reference.get("authority") != "vvault/authenticated-owner-attestation"
                    or reference.get("payloadSha256") != _sha256(attestation)
                    or reference.get("receiptSha256") != _sha256({"ownerAttestation": attestation})
                    or reference.get("cryptographicallyVerified") is not True
                    or reference.get("advancementAuthority") is not True
                    or reference.get("verifiedFactKinds") != expected_fact_kinds
                ):
                    raise ConstructWorkLoopError("WORK_OWNER_EVIDENCE_INVALID", "owner attestation was not canonically derived", 409)
                continue
            if event_type == "handoff_accepted" and reference.get("evidenceType") == "handoff_acceptance":
                continue
            resolver = self.evidence_resolver
            resolved = (
                resolver(cur, owner_user_id, reference["evidenceId"])
                if resolver is not None
                else self._default_evidence_resolver(
                    cur, owner_user_id, reference["evidenceId"], program=program
                )
            )
            if not resolved or resolved.get("payload_sha256") != reference.get("payloadSha256"):
                raise ConstructWorkLoopError("WORK_EVIDENCE_NOT_FOUND", "evidence reference is not canonically resolved", 409)
            receipt_sha = resolved.get("receipt_sha256") or resolved.get("payload_sha256")
            if receipt_sha != reference.get("receiptSha256"):
                raise ConstructWorkLoopError("WORK_EVIDENCE_HASH_MISMATCH", "evidence receipt hash does not match canonical source", 409)
            expected_advancement = reference.get("evidenceType") in {
                "host_readback", "test_result", "blocker_change", "handoff_acceptance",
            }
            if (
                reference.get("authority") != resolved.get("authority")
                or reference.get("evidenceType") != resolved.get("evidence_type")
                or reference.get("verifiedFactKinds") != resolved.get("verified_fact_kinds")
                or reference.get("cryptographicallyVerified") is not True
                or reference.get("advancementAuthority") is not resolved.get("advancement_authority")
                or reference.get("advancementAuthority") is not expected_advancement
            ):
                raise ConstructWorkLoopError(
                    "WORK_EVIDENCE_AUTHORITY_MISMATCH",
                    "evidence authority or advancement was not server-derived",
                    409,
                )
        return [reference for reference, _item_id in candidates]

    def _canonical_evidence_ids(
        self, cur: Any, owner_user_id: str, program_id: str
    ) -> set[str]:
        cur.execute(
            """SELECT payload FROM ovvaults.construct_work_events
                WHERE owner_user_id=%s AND program_id=%s ORDER BY sequence""",
            (owner_user_id, program_id),
        )
        found: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return
            if value.get("contract") == WORK_EVIDENCE_REFERENCE_CONTRACT:
                found.add(_safe_id(value.get("evidenceId"), "evidenceId"))
            for nested in value.values():
                visit(nested)

        for raw in cur.fetchall():
            row = _row(raw)
            payload = (row or {}).get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ConstructWorkLoopError(
                        "WORK_CANONICAL_EVENT_INVALID",
                        "stored work evidence payload is malformed",
                        503,
                    ) from exc
            visit(payload)
        return found

    def _event(
        self,
        *,
        authorization: dict[str, Any],
        program: dict[str, Any],
        payload: dict[str, Any],
        actor: dict[str, str],
        occurred_at: datetime,
        request_digest: str,
        evidence_digest: str | None,
    ) -> dict[str, Any]:
        sequence = int(authorization["expectedSequence"])
        body = {
            "eventId": _event_id(
                program_id=program["program_id"],
                branch_id=program["branch_id"],
                sequence=sequence,
            ),
            "programId": program["program_id"],
            "ownerPrincipalId": str(program["owner_user_id"]),
            "constructId": program["construct_id"],
            "threadId": program["thread_id"],
            "sessionId": program["session_id"],
            "branchId": program["branch_id"],
            "eventType": authorization["eventType"],
            "sequence": sequence,
            "parentEventId": authorization["expectedHeadEventId"],
            "parentEventSha256": authorization["expectedHeadSha256"],
            "idempotencyKey": authorization["idempotencyKey"],
            "requestDigest": _digest(request_digest, "requestDigest"),
            "coreAuthorizationHash": authorization["payloadSha256"],
            "evidenceDigest": evidence_digest,
            "occurredAt": _iso(occurred_at),
            "actor": actor,
            "goalRevision": authorization["goalRevision"],
            "resultingGoalRevision": authorization["resultingGoalRevision"],
            "payload": payload,
            "payloadSha256": _sha256(payload),
        }
        return {**body, "eventSha256": _sha256(body)}

    def _validate_payload_shape(self, event_type: str, payload: Any) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ConstructWorkLoopError("WORK_EVENT_TYPE_INVALID", "event type is invalid")
        # Chatty Core owns and signs the inner lifecycle semantics. VVAULT only
        # bounds and content-screens the canonical object, then independently
        # resolves the owner/head/evidence surfaces it owns.
        return _bounded_object(payload, "event.payload", _MAX_EVENT_BYTES)

    def _validate_execution_recovery_artifacts(
        self, cur: Any, *, owner_user_id: str, program: dict[str, Any],
        context_head: dict[str, Any], state_receipt_sha256: str,
        descriptors: list[dict[str, Any]],
    ) -> None:
        proposal = next((entry for entry in descriptors if entry.get("eventType") == "next_action_proposed"), None)
        if not proposal:
            return
        payload = proposal.get("payload") or {}
        binding = payload.get("executionProposalBinding")
        if not isinstance(binding, dict):
            return
        expected_fields = {
            "contract", "programId", "itemId", "decisionHash", "nextActionHash",
            "preparedContextReceiptSha256", "capabilityManifestPayloadSha256", "proposalSourceHash",
            "proposalSourceArtifactReference", "capabilityManifestArtifactReference",
            "advancementAuthority", "effectAuthority", "bindingHash",
        }
        if set(binding) != expected_fields or binding.get("contract") != "chatty-work-execution-proposal-binding/v1" \
                or binding.get("advancementAuthority") is not False or binding.get("effectAuthority") is not False \
                or binding.get("bindingHash") != _sha256({key: value for key, value in binding.items() if key != "bindingHash"}):
            raise ConstructWorkLoopError("WORK_EXECUTION_RECOVERY_BINDING_INVALID", "execution recovery binding is invalid", 403)
        references = (
            (binding["proposalSourceArtifactReference"], "proposal_source", "proposalSource"),
            (binding["capabilityManifestArtifactReference"], "capability_manifest", "capabilityManifest"),
        )
        resolved: dict[str, dict[str, Any]] = {}
        for reference, expected_kind, payload_key in references:
            reference_fields = {"contract", "artifactId", "artifactKind", "payloadSha256", "mediaType", "scopeSha256", "expiresAt", "referenceHash"}
            if not isinstance(reference, dict) or set(reference) != reference_fields \
                    or reference.get("contract") != "chatty-work-execution-recovery-artifact-reference/v1" \
                    or reference.get("artifactKind") != expected_kind \
                    or reference.get("referenceHash") != _sha256({key: value for key, value in reference.items() if key != "referenceHash"}):
                raise ConstructWorkLoopError("WORK_EXECUTION_RECOVERY_REFERENCE_INVALID", "execution recovery artifact reference is invalid", 403)
            cur.execute(
                """SELECT artifact,reference,expires_at FROM ovvaults.construct_work_execution_recovery_artifacts
                    WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s FOR SHARE""",
                (owner_user_id, program["program_id"], reference["artifactId"]),
            )
            row = _row(cur.fetchone())
            artifact = row.get("artifact") if row else None
            stored_reference = row.get("reference") if row else None
            if isinstance(artifact, str): artifact = json.loads(artifact)
            if isinstance(stored_reference, str): stored_reference = json.loads(stored_reference)
            scope = artifact.get("scope") if isinstance(artifact, dict) else None
            if not row or stored_reference != reference or not isinstance(scope, dict) \
                    or artifact.get("artifactHash") != reference["payloadSha256"] \
                    or artifact.get("artifactHash") != _sha256({key: value for key, value in artifact.items() if key != "artifactHash"}) \
                    or _sha256(scope) != reference["scopeSha256"] \
                    or self.now() >= _timestamp(reference["expiresAt"], "reference.expiresAt") \
                    or any((scope.get("ownerPrincipalId") != owner_user_id,
                            scope.get("programId") != program["program_id"],
                            scope.get("itemId") != binding["itemId"],
                            scope.get("sourceConstructId") != program["construct_id"],
                            scope.get("threadId") != program["thread_id"],
                            scope.get("sessionId") != program["session_id"],
                            scope.get("branchId") != program["branch_id"],
                            scope.get("goalRevision") != context_head.get("resulting_goal_revision"),
                            scope.get("preCommitHeadEventId") != context_head.get("event_id"),
                            scope.get("preCommitHeadSha256") != context_head.get("event_sha256"),
                            scope.get("preCommitStateReceiptSha256") != state_receipt_sha256,
                            scope.get("preparedContextReceiptSha256") != binding["preparedContextReceiptSha256"])):
                raise ConstructWorkLoopError("WORK_EXECUTION_RECOVERY_ARTIFACT_INVALID", "execution recovery artifact failed scope/readback validation", 409)
            resolved[expected_kind] = artifact[payload_key]
        if resolved["proposal_source"].get("sourceHash") != binding["proposalSourceHash"] \
                or resolved["capability_manifest"].get("payloadSha256") != binding["capabilityManifestPayloadSha256"]:
            raise ConstructWorkLoopError("WORK_EXECUTION_RECOVERY_ARTIFACT_INVALID", "execution recovery artifact payload hashes mismatch", 409)

    def _insert_event(
        self,
        cur: Any,
        *,
        owner_user_id: str,
        program: dict[str, Any],
        authorization: dict[str, Any],
        payload: dict[str, Any],
        actor: dict[str, str],
        occurred_at: datetime,
        request_digest: str,
        evidence_digest: str | None = None,
        consumed_handoff_sha256: str | None = None,
        stored_authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self._event(
            authorization=authorization,
            program=program,
            payload=payload,
            actor=actor,
            occurred_at=occurred_at,
            request_digest=request_digest,
            evidence_digest=evidence_digest,
        )
        envelope = _signed_event(event, private_key_pem=self.private_key_pem)
        canonical_authorization = stored_authorization or authorization
        cur.execute(
            """INSERT INTO ovvaults.construct_work_events
               (owner_user_id,program_id,construct_id,thread_id,session_id,branch_id,
                event_id,sequence,parent_event_id,parent_event_sha256,idempotency_key,
                request_digest,core_authorization_hash,evidence_digest,event_type,
                occurred_at,actor_principal_id,
                actor_principal_type,actor_authority,goal_revision,resulting_goal_revision,payload,
                payload_sha256,event_sha256,core_authorization,
                core_authorization_sha256,consumed_handoff_sha256,
                envelope,signature_algorithm,signature_key_id,signature)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,
                       %s::jsonb,%s,%s,%s)
               RETURNING event_id,sequence,event_sha256,envelope,created_at""",
            (
                owner_user_id, program["program_id"], event["constructId"], event["threadId"],
                event["sessionId"], event["branchId"], event["eventId"], event["sequence"],
                event["parentEventId"], event["parentEventSha256"], event["idempotencyKey"],
                event["requestDigest"], event["coreAuthorizationHash"], event["evidenceDigest"],
                event["eventType"],
                event["occurredAt"], actor["principalId"], actor["principalType"],
                actor["authority"], event["goalRevision"], event["resultingGoalRevision"], _canonical_json(payload),
                event["payloadSha256"], event["eventSha256"], _canonical_json(canonical_authorization),
                canonical_authorization["payloadSha256"], consumed_handoff_sha256,
                _canonical_json(envelope), envelope["algorithm"],
                envelope["keyId"], envelope["signature"],
            ),
        )
        inserted = _row(cur.fetchone())
        if not inserted or inserted.get("event_sha256") != event["eventSha256"]:
            raise ConstructWorkLoopError("WORK_EVENT_READBACK_FAILED", "canonical event readback failed", 503)
        return envelope

    def _assert_parent_program_delegation(
        self,
        cur: Any,
        *,
        owner_user_id: str,
        program: dict[str, Any],
    ) -> None:
        """Require child work to originate from an accepted delegated-item handoff.

        Destination constructs retain their own singleton/thread authorship. A
        handoff therefore authorizes a separately owner-approved child program;
        it never causes the destination to impersonate the parent construct in
        the parent's singleton transcript.
        """
        parent_program_id = program.get("parentProgramId")
        if parent_program_id is None:
            return
        parent_program_id = _safe_id(parent_program_id, "parentProgramId")
        if parent_program_id == program.get("programId"):
            raise ConstructWorkLoopError(
                "WORK_PARENT_PROGRAM_INVALID",
                "work program cannot be its own parent",
                409,
            )
        cur.execute(
            """SELECT program_id
                 FROM ovvaults.construct_work_programs
                WHERE owner_user_id=%s AND program_id=%s
                FOR SHARE""",
            (owner_user_id, parent_program_id),
        )
        if not cur.fetchone():
            raise ConstructWorkLoopError(
                "WORK_PARENT_PROGRAM_NOT_FOUND",
                "parent work program was not found for this owner",
                404,
            )
        cur.execute(
            """SELECT h.handoff_sha256,e.envelope
                 FROM ovvaults.construct_work_handoffs h
                 JOIN ovvaults.construct_work_events e
                   ON e.owner_user_id=h.owner_user_id
                  AND e.program_id=h.program_id
                  AND e.consumed_handoff_sha256=h.handoff_sha256
                WHERE h.owner_user_id=%s AND h.program_id=%s
                  AND h.to_principal_id=%s
                  AND e.event_type='handoff_accepted'
                ORDER BY e.sequence""",
            (owner_user_id, parent_program_id, program.get("constructId")),
        )
        delegated_item_ids: set[str] = set()
        for row_value in cur.fetchall():
            row = _row(row_value)
            envelope = _stored_event_envelope(row)
            event = envelope["event"]
            try:
                canonical_projection_signing.verify_canonical_payload(
                    event,
                    {key: envelope[key] for key in ("algorithm", "keyId", "signature")},
                    private_key_pem=self.private_key_pem,
                )
            except (RuntimeError, ValueError) as exc:
                raise ConstructWorkLoopError(
                    "WORK_PARENT_HANDOFF_INVALID",
                    "parent handoff acceptance signature is invalid",
                    503,
                ) from exc
            accepted = event.get("payload", {}).get("handoff")
            if (
                event.get("programId") != parent_program_id
                or event.get("eventType") != "handoff_accepted"
                or not isinstance(accepted, dict)
                or accepted.get("destinationConstructId") != program.get("constructId")
            ):
                raise ConstructWorkLoopError(
                    "WORK_PARENT_HANDOFF_INVALID",
                    "parent handoff acceptance does not bind the child construct",
                    409,
                )
            delegated_item_ids.update(
                _validated_id_list(
                    accepted.get("delegatedItemIds"),
                    "handoff.delegatedItemIds",
                    maximum=256,
                )
            )
        child_item_ids = {
            _safe_id(item.get("itemId"), "program.items.itemId")
            for item in program.get("items") or []
            if isinstance(item, dict)
        }
        if not child_item_ids or not child_item_ids.issubset(delegated_item_ids):
            raise ConstructWorkLoopError(
                "WORK_PARENT_HANDOFF_SCOPE_MISMATCH",
                "child work items are not covered by an accepted destination handoff",
                403,
            )

    def _idempotent_event(
        self,
        cur: Any,
        *,
        owner_user_id: str,
        program_id: str,
        authorization: dict[str, Any],
    ) -> dict[str, Any] | None:
        cur.execute(
            """SELECT event_id,event_type,payload_sha256,core_authorization_sha256,
                      idempotency_key,envelope
                 FROM ovvaults.construct_work_events
                WHERE owner_user_id=%s AND program_id=%s AND idempotency_key=%s""",
            (owner_user_id, program_id, authorization["idempotencyKey"]),
        )
        existing = _row(cur.fetchone())
        if not existing:
            return None
        if (
            existing.get("event_type") != authorization["eventType"]
            or existing.get("payload_sha256") != authorization["eventPayloadSha256"]
            or existing.get("core_authorization_sha256") != authorization["payloadSha256"]
        ):
            raise ConstructWorkLoopError("WORK_IDEMPOTENCY_CONFLICT", "idempotency key is bound to different canonical bytes", 409)
        envelope = _stored_event_envelope(existing)
        try:
            canonical_projection_signing.verify_canonical_payload(
                envelope["event"],
                {key: envelope[key] for key in ("algorithm", "keyId", "signature")},
                private_key_pem=self.private_key_pem,
            )
        except (RuntimeError, ValueError) as exc:
            raise ConstructWorkLoopError(
                "WORK_CANONICAL_EVENT_INVALID",
                "idempotent canonical event signature is invalid",
                503,
            ) from exc
        return envelope

    def create_program(
        self,
        owner_user_id: str,
        request: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructWorkLoopError("WORK_SERVICE_AUTH_REQUIRED", "program creation requires the trusted Chatty service", 403)
        _exact(request, frozenset({
            "program", "scopeResolution", "scopeApprovalEvidenceReference",
            "createAuthorization",
        }), "createRequest")
        program_value = _validate_program(request.get("program"), str(owner_user_id))
        instant = self.now()
        scope_resolution = _verified_scope_resolution(
            request.get("scopeResolution"),
            owner_user_id=str(owner_user_id),
            private_key_pem=self.private_key_pem,
            now=instant,
        )
        scope_evidence = _exact(
            request.get("scopeApprovalEvidenceReference"),
            _EVIDENCE_FIELDS,
            "scopeApprovalEvidenceReference",
        )
        if (
            scope_resolution.get("scopeApprovalEvidenceReference") != scope_evidence
            or scope_resolution.get("programId") != program_value["programId"]
            or scope_resolution.get("constructId") != program_value["constructId"]
            or scope_resolution.get("threadId") != program_value["threadId"]
            or scope_resolution.get("sessionId") != program_value["sessionId"]
            or scope_resolution.get("requestedDefinitionHash") != program_value["definitionHash"]
            or scope_resolution.get("requestedGoalRevision") != program_value["goal"]["revision"]
            or scope_resolution.get("activeProgramId") is not None
            or scope_evidence.get("evidenceType") != "owner_attestation"
            or scope_evidence.get("advancementAuthority") is not True
            or scope_evidence.get("cryptographicallyVerified") is not True
            or scope_evidence.get("verifiedFactKinds") != ["scope_approved"]
        ):
            raise ConstructWorkLoopError("WORK_CREATE_SCOPE_MISMATCH", "create scope approval does not match program", 403)
        authorization = _validate_create_authorization(
            request.get("createAuthorization"),
            owner_user_id=str(owner_user_id),
            program=program_value,
            scope_resolution=scope_resolution,
            scope_approval_evidence=scope_evidence,
            public_key_pem=self.authorization_public_key_pem,
            expected_key_id=self.authorization_key_id,
            now=instant,
        )
        program_payload = {"program": program_value}
        scope_payload = {
            "definitionHash": program_value["definitionHash"],
            "approvalEvidence": scope_evidence,
        }
        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    self._lock(cur, str(owner_user_id), program_value["programId"], authorization["idempotencyKey"])
                    cur.execute(
                        """SELECT sequence,event_type,payload_sha256,core_authorization_sha256,envelope
                             FROM ovvaults.construct_work_events
                            WHERE owner_user_id=%s AND program_id=%s
                              AND core_authorization_sha256=%s
                            ORDER BY sequence""",
                        (owner_user_id, program_value["programId"], authorization["payloadSha256"]),
                    )
                    existing_rows = [_row(row) for row in cur.fetchall()]
                    if existing_rows:
                        expected = [
                            (1, "program_requested", _sha256(program_payload)),
                            (2, "scope_approved", _sha256(scope_payload)),
                        ]
                        if len(existing_rows) != 2 or any(
                            row.get("sequence") != sequence
                            or row.get("event_type") != event_type
                            or row.get("payload_sha256") != payload_sha
                            or row.get("core_authorization_sha256") != authorization["payloadSha256"]
                            for row, (sequence, event_type, payload_sha) in zip(existing_rows, expected)
                        ):
                            raise ConstructWorkLoopError(
                                "WORK_CREATE_PARTIAL_STATE",
                                "atomic create pair is partial or mismatched",
                                409,
                            )
                        envelopes = [_stored_event_envelope(row) for row in existing_rows]
                        prior_event = None
                        for index, envelope in enumerate(envelopes, start=1):
                            event = envelope["event"]
                            try:
                                canonical_projection_signing.verify_canonical_payload(
                                    event,
                                    {key: envelope[key] for key in ("algorithm", "keyId", "signature")},
                                    private_key_pem=self.private_key_pem,
                                )
                            except (RuntimeError, ValueError) as exc:
                                raise ConstructWorkLoopError(
                                    "WORK_CREATE_CANONICAL_INVALID",
                                    "atomic create event signature is invalid",
                                    503,
                                ) from exc
                            if (
                                event.get("sequence") != index
                                or event.get("programId") != program_value["programId"]
                                or event.get("ownerPrincipalId") != str(owner_user_id)
                                or event.get("constructId") != program_value["constructId"]
                                or event.get("threadId") != program_value["threadId"]
                                or event.get("sessionId") != program_value["sessionId"]
                                or event.get("branchId") != scope_resolution["branchId"]
                                or event.get("eventId") != _event_id(
                                    program_id=program_value["programId"],
                                    branch_id=scope_resolution["branchId"],
                                    sequence=index,
                                )
                                or event.get("parentEventId") != ((prior_event or {}).get("eventId") if prior_event else None)
                                or event.get("parentEventSha256") != ((prior_event or {}).get("eventSha256") if prior_event else None)
                            ):
                                raise ConstructWorkLoopError(
                                    "WORK_CREATE_CANONICAL_INVALID",
                                    "atomic create event chain is invalid",
                                    503,
                                )
                            prior_event = event
                        conn.commit()
                        return {"status": "idempotent_readback", "events": envelopes}
                    self._lock_active_scope(
                        cur,
                        str(owner_user_id),
                        program_value["constructId"],
                        program_value["threadId"],
                    )
                    scope = self._thread_scope(
                        cur, str(owner_user_id), program_value["threadId"], program_value["constructId"],
                        session_id=program_value["sessionId"], program_id=program_value["programId"],
                    )
                    if (
                        scope["branchId"] != scope_resolution["branchId"]
                        or scope["constructIncarnationId"] != scope_resolution["constructIncarnationId"]
                        or scope["membershipRevision"] != scope_resolution["membershipRevision"]
                        or scope["sourceRevision"] != scope_resolution["sourceRevision"]
                    ):
                        raise ConstructWorkLoopError("WORK_SCOPE_RESOLUTION_STALE", "create scope changed after preflight", 409)
                    cur.execute(
                        """SELECT program_id,initial_program_sha256,authorization_sha256
                             FROM ovvaults.construct_work_programs
                            WHERE owner_user_id=%s AND
                              (program_id=%s OR create_idempotency_key=%s)
                            FOR UPDATE""",
                        (owner_user_id, program_value["programId"], authorization["idempotencyKey"]),
                    )
                    collision = _row(cur.fetchone())
                    if collision:
                        raise ConstructWorkLoopError("WORK_PROGRAM_CONFLICT", "program identity is already bound", 409)
                    cur.execute(
                        """SELECT p.program_id
                             FROM ovvaults.construct_work_programs p
                             LEFT JOIN LATERAL (
                               SELECT e.event_type
                                 FROM ovvaults.construct_work_events e
                                WHERE e.owner_user_id=p.owner_user_id
                                  AND e.program_id=p.program_id
                                ORDER BY e.sequence DESC LIMIT 1
                             ) head ON true
                            WHERE p.owner_user_id=%s AND p.construct_id=%s
                              AND p.thread_id=%s
                              AND (head.event_type IS NULL OR head.event_type NOT IN
                                ('completion_verified','cancelled','superseded'))
                            ORDER BY p.created_at DESC,p.program_id LIMIT 1""",
                        (owner_user_id, program_value["constructId"], program_value["threadId"]),
                    )
                    if cur.fetchone():
                        raise ConstructWorkLoopError(
                            "WORK_ACTIVE_PROGRAM_CONFLICT",
                            "an active work program already owns this construct thread",
                            409,
                        )
                    self._assert_parent_program_delegation(
                        cur,
                        owner_user_id=str(owner_user_id),
                        program=program_value,
                    )
                    cur.execute(
                        """INSERT INTO ovvaults.construct_work_programs
                           (owner_user_id,program_id,thread_id,session_id,branch_id,construct_id,
                            construct_incarnation_id,scope_source_revision,contract_version,
                            initial_program,initial_program_sha256,authorization_sha256,
                            membership_revision,created_by_principal_id,create_idempotency_key)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                           RETURNING owner_user_id::text AS owner_user_id,program_id,thread_id,
                                     session_id,branch_id,construct_id,
                                     construct_incarnation_id::text,scope_source_revision,
                                     initial_program,membership_revision""",
                        (
                            owner_user_id, program_value["programId"], program_value["threadId"],
                            program_value["sessionId"], scope["branchId"], program_value["constructId"],
                            scope["constructIncarnationId"], scope["sourceRevision"], WORK_PROGRAM_CONTRACT,
                            _canonical_json(program_value), _sha256(program_value),
                            authorization["payloadSha256"], scope["membershipRevision"],
                            owner_user_id, authorization["idempotencyKey"],
                        ),
                    )
                    program_row = _row(cur.fetchone())
                    occurred_at = self._database_now(cur)
                    genesis_control = {
                        **authorization,
                        "eventType": "program_requested",
                        "expectedSequence": 1,
                        "expectedHeadEventId": None,
                        "expectedHeadSha256": None,
                        "goalRevision": authorization["requestedGoalRevision"],
                        "resultingGoalRevision": authorization["requestedGoalRevision"],
                        "idempotencyKey": f"work-create-event-{_sha256({'authorization': authorization['payloadSha256'], 'ordinal': 1})[:40]}",
                    }
                    genesis = self._insert_event(
                        cur, owner_user_id=str(owner_user_id), program=program_row,
                        authorization=genesis_control, payload=program_payload,
                        actor={"principalId": str(owner_user_id), "principalType": "human", "authority": "owner_authenticated"},
                        occurred_at=occurred_at,
                        request_digest=_sha256({"program": program_value, "createAuthorization": authorization}),
                        stored_authorization=authorization,
                    )
                    approval_control = {
                        **authorization,
                        "eventType": "scope_approved",
                        "expectedSequence": 2,
                        "expectedHeadEventId": genesis["event"]["eventId"],
                        "expectedHeadSha256": genesis["event"]["eventSha256"],
                        "goalRevision": authorization["requestedGoalRevision"],
                        "resultingGoalRevision": authorization["requestedGoalRevision"],
                        "idempotencyKey": f"work-create-event-{_sha256({'authorization': authorization['payloadSha256'], 'ordinal': 2})[:40]}",
                    }
                    approval = self._insert_event(
                        cur, owner_user_id=str(owner_user_id), program=program_row,
                        authorization=approval_control, payload=scope_payload,
                        actor={"principalId": str(owner_user_id), "principalType": "human", "authority": "owner_authenticated"},
                        occurred_at=self._database_now(cur),
                        request_digest=_sha256({
                            "scopeApprovalEvidenceReference": scope_evidence,
                            "createAuthorization": authorization,
                            "genesisEventSha256": genesis["event"]["eventSha256"],
                        }),
                        evidence_digest=_sha256(scope_evidence),
                        stored_authorization=authorization,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"status": "planned", "events": [genesis, approval]}

    def append_event(
        self,
        owner_user_id: str,
        program_id: str,
        request: dict[str, Any],
        *,
        trusted_internal: bool = False,
        consumed_handoff_sha256: str | None = None,
    ) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructWorkLoopError("WORK_SERVICE_AUTH_REQUIRED", "event append requires the trusted Chatty service", 403)
        _safe_id(program_id, "programId")
        _exact(request, frozenset({"payload", "authorization"}), "appendRequest")
        raw_authorization = request.get("authorization")
        event_type = str(raw_authorization.get("eventType") if isinstance(raw_authorization, dict) else "")
        if event_type not in EVENT_TYPES:
            raise ConstructWorkLoopError("WORK_EVENT_TYPE_INVALID", "event type is invalid")
        payload = self._validate_payload_shape(event_type, request.get("payload"))
        instant = self.now()
        authorization = _validate_authorization(
            raw_authorization, owner_user_id=str(owner_user_id), event_payload=payload,
            public_key_pem=self.authorization_public_key_pem,
            expected_key_id=self.authorization_key_id, now=instant,
        )
        if authorization["programId"] != program_id:
            raise ConstructWorkLoopError("WORK_EVENT_SCOPE_MISMATCH", "authorization program does not match route", 403)
        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    self._lock(cur, str(owner_user_id), program_id, authorization["idempotencyKey"])
                    existing = self._idempotent_event(
                        cur, owner_user_id=str(owner_user_id), program_id=program_id,
                        authorization=authorization,
                    )
                    if existing:
                        conn.commit()
                        return {"status": "idempotent_readback", "event": existing}
                    program = self._program(cur, str(owner_user_id), program_id, for_update=True)
                    current_scope = self._thread_scope(
                        cur, str(owner_user_id), program["thread_id"], program["construct_id"],
                        session_id=program["session_id"], program_id=program_id,
                    )
                    if (
                        current_scope["membershipRevision"] != int(program["membership_revision"])
                        or current_scope["constructIncarnationId"] != str(program["construct_incarnation_id"])
                        or current_scope["sourceRevision"] != program["scope_source_revision"]
                        or current_scope["branchId"] != program["branch_id"]
                    ):
                        raise ConstructWorkLoopError("WORK_MEMBERSHIP_STALE", "thread membership changed after work authorization", 409)
                    head = self._head(cur, str(owner_user_id), program_id)
                    if head and head.get("event_type") in TERMINAL_EVENT_TYPES:
                        raise ConstructWorkLoopError("WORK_TERMINAL_STATE_IMMUTABLE", "terminal work program cannot advance", 409)
                    self._assert_authorization_scope(authorization, program, head=head)
                    actor = self._actor(
                        event_type=event_type, owner_user_id=str(owner_user_id),
                        program=program, payload=payload, cur=cur,
                    )
                    evidence_references = self._validate_event_evidence(
                        cur, owner_user_id=str(owner_user_id), program=program,
                        event_type=event_type, payload=payload,
                    )
                    occurred_at = self._database_now(cur)
                    envelope = self._insert_event(
                        cur, owner_user_id=str(owner_user_id), program=program,
                        authorization=authorization, payload=payload, actor=actor,
                        occurred_at=occurred_at,
                        request_digest=_sha256(request),
                        evidence_digest=_payload_evidence_digest(payload, evidence_references),
                        consumed_handoff_sha256=consumed_handoff_sha256,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"status": "appended", "event": envelope}

    def _events(
        self,
        cur: Any,
        owner_user_id: str,
        program_id: str,
        *,
        program: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cur.execute(
            """SELECT sequence,event_id,event_sha256,envelope
                 FROM ovvaults.construct_work_events
                WHERE owner_user_id=%s AND program_id=%s
                ORDER BY sequence""",
            (owner_user_id, program_id),
        )
        rows = [_row(row) for row in cur.fetchall()]
        if len(rows) > _MAX_PROJECTION_EVENTS:
            raise ConstructWorkLoopError("WORK_PROJECTION_OVERSIZED", "work event stream exceeds projection limit", 503)
        envelopes: list[dict[str, Any]] = []
        prior: dict[str, Any] | None = None
        for index, row in enumerate(rows, start=1):
            envelope = _stored_event_envelope(row)
            event = envelope["event"]
            try:
                canonical_projection_signing.verify_canonical_payload(
                    event,
                    {key: envelope[key] for key in ("algorithm", "keyId", "signature")},
                    private_key_pem=self.private_key_pem,
                )
            except (RuntimeError, ValueError) as exc:
                raise ConstructWorkLoopError("WORK_CANONICAL_EVENT_INVALID", "stored event signature is invalid", 503) from exc
            if (
                event["sequence"] != index
                or event["programId"] != program_id
                or event["ownerPrincipalId"] != str(owner_user_id)
                or event["constructId"] != program["construct_id"]
                or event["threadId"] != program["thread_id"]
                or event["sessionId"] != program["session_id"]
                or event["branchId"] != program["branch_id"]
                or event["eventId"] != _event_id(
                    program_id=program_id,
                    branch_id=program["branch_id"],
                    sequence=index,
                )
                or event["eventId"] != row["event_id"]
                or event["eventSha256"] != row["event_sha256"]
                or event["parentEventId"] != ((prior or {}).get("eventId") if prior else None)
                or event["parentEventSha256"] != ((prior or {}).get("eventSha256") if prior else None)
            ):
                raise ConstructWorkLoopError("WORK_EVENT_CHAIN_INVALID", "canonical work event chain is invalid", 503)
            envelopes.append(envelope)
            prior = event
        return envelopes

    def projection(self, owner_user_id: str, program_id: str) -> dict[str, Any]:
        program_id = _safe_id(program_id, "programId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                program = self._program(cur, str(owner_user_id), program_id)
                events = self._events(
                    cur, str(owner_user_id), program_id, program=program
                )
        initial = program["initial_program"]
        if isinstance(initial, str):
            initial = json.loads(initial)
        head = events[-1]["event"] if events else None
        instant = self.now()
        return _signed_document(
            WORK_PROJECTION_CONTRACT,
            {
                "programId": program_id,
                "ownerPrincipalId": str(owner_user_id),
                "constructId": program["construct_id"],
                "threadId": program["thread_id"],
                "sessionId": initial["sessionId"],
                "branchId": program["branch_id"],
                "revision": (head or {}).get("eventSha256"),
                "head": ({
                    "eventId": head["eventId"], "eventSha256": head["eventSha256"],
                    "sequence": head["sequence"],
                } if head else None),
                "events": events,
                "issuedAt": _iso(instant),
                "expiresAt": _iso(instant + timedelta(seconds=_PROJECTION_TTL_SECONDS)),
            },
            private_key_pem=self.private_key_pem,
        )

    def sign_context_projection(
        self,
        owner_user_id: str,
        program_id: str,
        request: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructWorkLoopError("WORK_SERVICE_AUTH_REQUIRED", "context signing requires trusted Chatty service", 403)
        _exact(request, frozenset({"contextProjection"}), "contextRequest")
        context = _exact(
            _bounded_object(request.get("contextProjection"), "contextProjection", 512 * 1024),
            _CONTEXT_FIELDS,
            "contextProjection",
        )
        if (
            context.get("contract") != WORK_CONTEXT_PROJECTION_CONTRACT
            or context.get("derivationAuthority") != "chatty-core"
            or context.get("persistenceAuthority") != AUTHORITY
            or context.get("contextPolicyVersion") != "chatty-context-sea-policy/v1.1"
            or context.get("requiresVvaultSignature") is not True
            or context.get("containsPrivateReasoning") is not False
            or context.get("containsEventPayloads") is not False
        ):
            raise ConstructWorkLoopError("WORK_CONTEXT_INVALID", "context authority or minimization fields are invalid", 409)
        unsigned_body = {key: value for key, value in context.items() if key != "projectionSha256"}
        if _digest(context.get("projectionSha256"), "context.projectionSha256") != _sha256(unsigned_body):
            raise ConstructWorkLoopError("WORK_CONTEXT_HASH_MISMATCH", "context projection hash is invalid", 409)
        _safe_id(
            context.get("activeConstructId"),
            "context.activeConstructId",
            principal=True,
        )
        projection = self.projection(str(owner_user_id), program_id)
        if (
            context.get("programId") != program_id
            or context.get("ownerPrincipalId") != str(owner_user_id)
            or context.get("constructId") != projection.get("constructId")
            or context.get("threadId") != projection.get("threadId")
            or context.get("sessionId") != projection.get("sessionId")
            or context.get("branchId") != projection.get("branchId")
        ):
            raise ConstructWorkLoopError("WORK_CONTEXT_SCOPE_MISMATCH", "context does not match canonical work program", 409)
        receipt = _exact(context.get("stateReceipt"), _STATE_RECEIPT_FIELDS, "stateReceipt")
        receipt_body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
        if (
            receipt.get("contract") != "chatty-work-state-receipt/v1"
            or _digest(receipt.get("receiptSha256"), "receiptSha256") != _sha256(receipt_body)
            or receipt.get("programId") != program_id
            or receipt.get("headEventId") != projection["head"]["eventId"]
            or receipt.get("headEventSha256") != projection["head"]["eventSha256"]
            or receipt.get("sequence") != projection["head"]["sequence"]
            or receipt.get("eventCount") != len(projection["events"])
        ):
            raise ConstructWorkLoopError("WORK_CONTEXT_STATE_MISMATCH", "context state receipt does not bind canonical head", 409)
        signature = canonical_projection_signing.sign_canonical_payload(
            context, private_key_pem=self.private_key_pem
        )
        return {**context, **signature}

    def issue_handoff(
        self,
        owner_user_id: str,
        program_id: str,
        request: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructWorkLoopError("WORK_SERVICE_AUTH_REQUIRED", "handoff issuance requires trusted Chatty service", 403)
        _exact(request, frozenset({"payload", "authorization"}), "handoffIssueRequest")
        payload = _exact(_bounded_object(request.get("payload"), "handoffPayload", 64 * 1024), frozenset({"handoff"}), "handoffPayload")
        handoff = _validate_handoff_document(payload["handoff"], require_accepted=False)
        handoff_hash = handoff["handoffHash"]
        instant = self.now()
        authorization = _validate_handoff_authorization(
            request.get("authorization"), owner_user_id=str(owner_user_id), handoff_payload=payload,
            public_key_pem=self.authorization_public_key_pem,
            expected_key_id=self.authorization_key_id, now=instant,
        )
        if authorization["programId"] != program_id:
            raise ConstructWorkLoopError("WORK_HANDOFF_SCOPE_MISMATCH", "handoff authorization does not match route", 403)
        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    self._lock(cur, str(owner_user_id), program_id, authorization["idempotencyKey"])
                    program = self._program(cur, str(owner_user_id), program_id, for_update=True)
                    head = self._head(cur, str(owner_user_id), program_id)
                    initial = program["initial_program"]
                    item_ids = {
                        item.get("itemId") for item in initial.get("items", [])
                        if isinstance(item, dict)
                    }
                    if (
                        not head
                        or authorization.get("ownerPrincipalId") != str(owner_user_id)
                        or authorization.get("programId") != program_id
                        or authorization.get("constructId") != program["construct_id"]
                        or authorization.get("threadId") != program["thread_id"]
                        or authorization.get("sessionId") != program["session_id"]
                        or authorization.get("branchId") != program["branch_id"]
                        or authorization.get("expectedHeadEventId") != head.get("event_id")
                        or authorization.get("expectedHeadSha256") != head.get("event_sha256")
                        or authorization.get("goalRevision") != head.get("resulting_goal_revision")
                        or authorization.get("fromPrincipalId") != handoff.get("sourceConstructId")
                        or authorization.get("toPrincipalId") != handoff.get("destinationConstructId")
                        or authorization.get("delegatedItemIds") != handoff.get("delegatedItemIds")
                        or authorization.get("evidenceReferenceIds") != handoff.get("evidenceReferenceIds")
                        or authorization.get("prerequisiteEvidenceReferenceIds") != handoff.get("prerequisiteEvidenceReferenceIds")
                        or any(item_id not in item_ids for item_id in authorization.get("delegatedItemIds", []))
                    ):
                        raise ConstructWorkLoopError("WORK_HANDOFF_SCOPE_MISMATCH", "handoff authorization scope is invalid", 403)
                    canonical_evidence_ids = self._canonical_evidence_ids(
                        cur, str(owner_user_id), program_id
                    )
                    if (
                        any(item not in canonical_evidence_ids for item in authorization["evidenceReferenceIds"])
                        or any(item not in canonical_evidence_ids for item in authorization["prerequisiteEvidenceReferenceIds"])
                    ):
                        raise ConstructWorkLoopError(
                            "WORK_HANDOFF_EVIDENCE_UNRESOLVED",
                            "handoff evidence is not present in the canonical program projection",
                            409,
                        )
                    if handoff.get("programId") != program_id:
                        raise ConstructWorkLoopError("WORK_HANDOFF_SCOPE_MISMATCH", "handoff program scope is invalid", 403)
                    for principal in (handoff.get("sourceConstructId"), handoff.get("destinationConstructId")):
                        cur.execute(
                            """SELECT 1
                                 FROM ovvaults.conversation_thread_memberships m
                                 JOIN ovvaults.construct_incarnations i
                                   ON i.owner_user_id=m.owner_user_id
                                  AND i.construct_id=m.principal_id AND i.retired_at IS NULL
                                WHERE m.owner_user_id=%s AND m.thread_id=%s
                                  AND m.principal_id=%s AND m.active=true""",
                            (owner_user_id, program["thread_id"], principal),
                        )
                        if not cur.fetchone():
                            raise ConstructWorkLoopError("WORK_HANDOFF_PRINCIPAL_INVALID", "handoff principals must be active same-owner members", 403)
                    issued_at = _timestamp(handoff.get("issuedAt"), "handoff.issuedAt")
                    expires_at = _timestamp(handoff.get("expiresAt"), "handoff.expiresAt")
                    if not (issued_at <= instant < expires_at) or expires_at - issued_at > timedelta(hours=24):
                        raise ConstructWorkLoopError("WORK_HANDOFF_EXPIRED", "handoff lifetime is invalid", 409)
                    signed = _signed_document(
                        WORK_HANDOFF_ENVELOPE_CONTRACT,
                        {
                            "handoff": handoff,
                            "programRevision": head["event_sha256"],
                            "handoffAuthorizationHash": authorization["payloadSha256"],
                            "authority": AUTHORITY,
                        },
                        private_key_pem=self.private_key_pem,
                    )
                    cur.execute(
                        """INSERT INTO ovvaults.construct_work_handoffs
                           (owner_user_id,program_id,handoff_id,from_principal_id,to_principal_id,
                            issued_head_sequence,issued_head_event_sha256,handoff,handoff_sha256,
                            core_authorization,core_authorization_sha256,idempotency_key,envelope,
                            signature_algorithm,signature_key_id,signature,issued_at,expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,
                                   %s::jsonb,%s,%s,%s,%s,%s)
                           ON CONFLICT (owner_user_id,program_id,idempotency_key) DO NOTHING
                           RETURNING envelope""",
                        (
                            owner_user_id, program_id, handoff["handoffId"], handoff["sourceConstructId"],
                            handoff["destinationConstructId"], head["sequence"], head["event_sha256"],
                            _canonical_json(handoff), handoff_hash, _canonical_json(authorization),
                            authorization["payloadSha256"], authorization["idempotencyKey"],
                            _canonical_json(signed), signed["algorithm"], signed["keyId"],
                            signed["signature"], _iso(issued_at), _iso(expires_at),
                        ),
                    )
                    inserted = cur.fetchone()
                    if not inserted:
                        cur.execute(
                            """SELECT handoff_sha256,core_authorization_sha256,envelope
                                 FROM ovvaults.construct_work_handoffs
                                WHERE owner_user_id=%s AND program_id=%s AND idempotency_key=%s""",
                            (owner_user_id, program_id, authorization["idempotencyKey"]),
                        )
                        existing = _row(cur.fetchone())
                        if not existing or existing["handoff_sha256"] != handoff_hash or existing["core_authorization_sha256"] != authorization["payloadSha256"]:
                            raise ConstructWorkLoopError("WORK_IDEMPOTENCY_CONFLICT", "handoff idempotency key conflicts", 409)
                        signed = existing["envelope"] if isinstance(existing["envelope"], dict) else json.loads(existing["envelope"])
                        unsigned = {
                            key: value for key, value in signed.items()
                            if key not in {"payloadSha256", "algorithm", "keyId", "signature"}
                        }
                        if (
                            signed.get("handoff") != handoff
                            or signed.get("handoffAuthorizationHash") != authorization["payloadSha256"]
                            or signed.get("payloadSha256") != _sha256(unsigned)
                        ):
                            raise ConstructWorkLoopError("WORK_HANDOFF_CANONICAL_INVALID", "stored handoff is invalid", 503)
                        try:
                            canonical_projection_signing.verify_canonical_payload(
                                unsigned,
                                {key: signed[key] for key in ("algorithm", "keyId", "signature")},
                                private_key_pem=self.private_key_pem,
                            )
                        except (KeyError, RuntimeError, ValueError) as exc:
                            raise ConstructWorkLoopError("WORK_HANDOFF_CANONICAL_INVALID", "stored handoff signature is invalid", 503) from exc
                        status = "idempotent_readback"
                    else:
                        status = "issued"
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"status": status, "handoff": signed}

    def accept_handoff(
        self,
        owner_user_id: str,
        program_id: str,
        handoff_id: str,
        request: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        handoff_id = _safe_id(handoff_id, "handoffId")
        if not trusted_internal:
            raise ConstructWorkLoopError("WORK_SERVICE_AUTH_REQUIRED", "handoff acceptance requires trusted Chatty service", 403)
        payload = request.get("payload") if isinstance(request, dict) else None
        if not isinstance(payload, dict) or set(payload) != {"handoff", "evidence"}:
            raise ConstructWorkLoopError("WORK_HANDOFF_ACCEPTANCE_INVALID", "handoff acceptance payload is invalid")
        accepted = _validate_handoff_document(payload["handoff"], require_accepted=True)
        if accepted.get("handoffId") != handoff_id or accepted.get("programId") != program_id:
            raise ConstructWorkLoopError("WORK_HANDOFF_ACCEPTANCE_INVALID", "accepted handoff scope is invalid")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT handoff,handoff_sha256,core_authorization_sha256,
                              envelope,expires_at
                         FROM ovvaults.construct_work_handoffs
                        WHERE owner_user_id=%s AND program_id=%s AND handoff_id=%s""",
                    (owner_user_id, program_id, handoff_id),
                )
                issued_row = _row(cur.fetchone())
        if not issued_row:
            raise ConstructWorkLoopError("WORK_HANDOFF_NOT_FOUND", "handoff was not found", 404)
        issued = issued_row["handoff"] if isinstance(issued_row["handoff"], dict) else json.loads(issued_row["handoff"])
        signed = issued_row["envelope"] if isinstance(issued_row["envelope"], dict) else json.loads(issued_row["envelope"])
        expected_envelope_fields = {
            "contract", "handoff", "programRevision", "handoffAuthorizationHash",
            "authority", "payloadSha256", "algorithm", "keyId", "signature",
        }
        if set(signed) != expected_envelope_fields:
            raise ConstructWorkLoopError("WORK_HANDOFF_CANONICAL_INVALID", "handoff envelope is malformed", 503)
        unsigned = {
            key: value for key, value in signed.items()
            if key not in {"payloadSha256", "algorithm", "keyId", "signature"}
        }
        if (
            signed.get("contract") != WORK_HANDOFF_ENVELOPE_CONTRACT
            or signed.get("authority") != AUTHORITY
            or signed.get("handoff") != issued
            or signed.get("handoffAuthorizationHash") != issued_row["core_authorization_sha256"]
            or signed.get("payloadSha256") != _sha256(unsigned)
        ):
            raise ConstructWorkLoopError("WORK_HANDOFF_CANONICAL_INVALID", "handoff envelope hashes are invalid", 503)
        try:
            canonical_projection_signing.verify_canonical_payload(
                unsigned,
                {key: signed[key] for key in ("algorithm", "keyId", "signature")},
                private_key_pem=self.private_key_pem,
            )
        except (RuntimeError, ValueError) as exc:
            raise ConstructWorkLoopError("WORK_HANDOFF_CANONICAL_INVALID", "handoff signature is invalid", 503) from exc
        for key in _HANDOFF_FIELDS - {"acceptedAt", "acceptanceEvidenceReferenceId", "handoffHash", "oneUseCapability"}:
            if accepted.get(key) != issued.get(key):
                raise ConstructWorkLoopError("WORK_HANDOFF_ACCEPTANCE_INVALID", "accepted handoff changed issued scope", 409)
        issued_capability = issued["oneUseCapability"]
        accepted_capability = accepted["oneUseCapability"]
        for key in _HANDOFF_CAPABILITY_FIELDS - {"consumedAt", "capabilityHash"}:
            if accepted_capability.get(key) != issued_capability.get(key):
                raise ConstructWorkLoopError("WORK_HANDOFF_ACCEPTANCE_INVALID", "accepted handoff changed capability scope", 409)
        if self.now() >= _timestamp(issued.get("expiresAt"), "handoff.expiresAt"):
            raise ConstructWorkLoopError("WORK_HANDOFF_EXPIRED", "handoff is expired", 409)
        result = self.append_event(
            owner_user_id, program_id, request, trusted_internal=True,
            consumed_handoff_sha256=str(issued_row["handoff_sha256"]),
        )
        return {**result, "handoffId": handoff_id, "consumed": True}

    def resolve_scope(self, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        _exact(
            request,
            frozenset({
                "contract", "programId", "constructId", "threadId", "sessionId",
                "requestedDefinitionHash", "requestedGoalRevision",
            }),
            "scopeResolutionRequest",
        )
        if request.get("contract") != WORK_SCOPE_REQUEST_CONTRACT:
            raise ConstructWorkLoopError("WORK_SCOPE_REQUEST_INVALID", "scope request contract is invalid")
        program_id = _safe_id(request.get("programId"), "programId")
        construct_id = _safe_id(request.get("constructId"), "constructId", principal=True)
        thread_id = _safe_id(request.get("threadId"), "threadId")
        requested_session_id = _safe_id(request.get("sessionId"), "sessionId")
        # V1 has one durable work session per canonical conversation thread.
        # The caller may echo the value for digest binding, but cannot allocate
        # an independent work-session identity.
        session_id = thread_id
        if requested_session_id != session_id:
            raise ConstructWorkLoopError(
                "WORK_SESSION_SCOPE_MISMATCH",
                "scope sessionId must equal the canonical threadId",
                409,
            )
        requested_definition_hash = _digest(
            request.get("requestedDefinitionHash"), "requestedDefinitionHash"
        )
        requested_goal_revision = _safe_id(
            request.get("requestedGoalRevision"), "requestedGoalRevision"
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                scope = self._thread_scope(
                    cur,
                    str(owner_user_id),
                    thread_id,
                    construct_id,
                    session_id=session_id,
                    program_id=program_id,
                )
                cur.execute(
                    """SELECT p.program_id
                         FROM ovvaults.construct_work_programs p
                         LEFT JOIN LATERAL (
                           SELECT e.event_type
                             FROM ovvaults.construct_work_events e
                            WHERE e.owner_user_id=p.owner_user_id
                              AND e.program_id=p.program_id
                            ORDER BY e.sequence DESC LIMIT 1
                         ) head ON true
                        WHERE p.owner_user_id=%s AND p.construct_id=%s
                          AND p.thread_id=%s
                          AND (head.event_type IS NULL OR head.event_type NOT IN
                            ('completion_verified','cancelled','superseded'))
                        ORDER BY p.created_at DESC,p.program_id LIMIT 2""",
                    (owner_user_id, construct_id, thread_id),
                )
                active_rows = [_row(row) for row in cur.fetchall()]
                if len(active_rows) > 1:
                    raise ConstructWorkLoopError(
                        "WORK_ACTIVE_PROGRAM_CONFLICT",
                        "multiple active work programs match the owner-qualified construct thread",
                        409,
                    )
                active_program_id = active_rows[0]["program_id"] if active_rows else None
        instant = self.now()
        attestation = {
            "contract": "life-vvault-work-scope-approval-attestation/v1",
            "ownerPrincipalId": str(owner_user_id),
            "programId": program_id,
            "constructId": construct_id,
            "threadId": thread_id,
            "sessionId": session_id,
            "branchId": scope["branchId"],
            "requestedDefinitionHash": requested_definition_hash,
            "requestedGoalRevision": requested_goal_revision,
            "sourceRevision": scope["sourceRevision"],
        }
        attestation_hash = _sha256(attestation)
        scope_approval_evidence = {
            "contract": WORK_EVIDENCE_REFERENCE_CONTRACT,
            "evidenceId": f"owner-attestation:scope-approval:{attestation_hash[:40]}",
            "evidenceType": "owner_attestation",
            "authority": "vvault/authenticated-owner-attestation",
            "scope": {
                "ownerPrincipalId": str(owner_user_id),
                "programId": program_id,
                "constructId": construct_id,
                "itemId": None,
                "threadId": thread_id,
                "sessionId": session_id,
            },
            "payloadSha256": attestation_hash,
            "receiptSha256": _sha256({"scopeApprovalAttestation": attestation}),
            "issuedAt": _iso(instant),
            "cryptographicallyVerified": True,
            "advancementAuthority": True,
            "verifiedFactKinds": ["scope_approved"],
        }
        return _signed_document(
            WORK_SCOPE_RESOLUTION_CONTRACT,
            {
                "ownerPrincipalId": str(owner_user_id),
                "programId": program_id,
                "constructId": construct_id,
                "constructIncarnationId": scope["constructIncarnationId"],
                "threadId": thread_id,
                "sessionId": session_id,
                "branchId": scope["branchId"],
                "membershipRevision": scope["membershipRevision"],
                "activeProgramId": active_program_id,
                "sourceRevision": scope["sourceRevision"],
                "requestedDefinitionHash": requested_definition_hash,
                "requestedGoalRevision": requested_goal_revision,
                "scopeApprovalEvidenceReference": scope_approval_evidence,
                "issuedAt": _iso(instant),
                "expiresAt": _iso(instant + timedelta(seconds=_PROJECTION_TTL_SECONDS)),
            },
            private_key_pem=self.private_key_pem,
        )

    def resolve_active_scope(
        self,
        owner_user_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve the one active program for a canonical owner/thread scope.

        This is deliberately separate from create preflight so an ordinary
        Chatty turn does not mint a scope-approval attestation merely to learn
        whether durable work is already active.
        """
        _exact(
            request,
            frozenset({"contract", "constructId", "threadId", "sessionId"}),
            "activeScopeResolutionRequest",
        )
        if request.get("contract") != WORK_ACTIVE_SCOPE_REQUEST_CONTRACT:
            raise ConstructWorkLoopError(
                "WORK_ACTIVE_SCOPE_REQUEST_INVALID",
                "active scope request contract is invalid",
            )
        construct_id = _safe_id(
            request.get("constructId"), "constructId", principal=True
        )
        thread_id = _safe_id(request.get("threadId"), "threadId")
        requested_session_id = _safe_id(request.get("sessionId"), "sessionId")
        session_id = thread_id
        if requested_session_id != session_id:
            raise ConstructWorkLoopError(
                "WORK_SESSION_SCOPE_MISMATCH",
                "active scope sessionId must equal the canonical threadId",
                409,
            )
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.program_id,p.session_id,p.branch_id,
                              p.construct_incarnation_id::text,
                              p.scope_source_revision,p.membership_revision
                         FROM ovvaults.construct_work_programs p
                         LEFT JOIN LATERAL (
                           SELECT e.event_type
                             FROM ovvaults.construct_work_events e
                            WHERE e.owner_user_id=p.owner_user_id
                              AND e.program_id=p.program_id
                            ORDER BY e.sequence DESC LIMIT 1
                         ) head ON true
                        WHERE p.owner_user_id=%s AND p.construct_id=%s
                          AND p.thread_id=%s
                          AND (head.event_type IS NULL OR head.event_type NOT IN
                            ('completion_verified','cancelled','superseded'))
                        ORDER BY p.created_at DESC,p.program_id LIMIT 2""",
                    (owner_user_id, construct_id, thread_id),
                )
                active_rows = [_row(row) for row in cur.fetchall()]
                if len(active_rows) > 1:
                    raise ConstructWorkLoopError(
                        "WORK_ACTIVE_PROGRAM_CONFLICT",
                        "multiple active work programs match the owner-qualified construct thread",
                        409,
                    )
                active = active_rows[0] if active_rows else None
                active_program_id = active.get("program_id") if active else None
                try:
                    scope = self._thread_scope(
                        cur,
                        str(owner_user_id),
                        thread_id,
                        construct_id,
                        session_id=session_id,
                        program_id=active_program_id or thread_id,
                    )
                except ConstructWorkLoopError as exc:
                    if active is not None or exc.code != "WORK_SCOPE_NOT_FOUND":
                        raise
                    scope = self._canonical_singleton_read_scope(
                        cur,
                        str(owner_user_id),
                        thread_id,
                        construct_id,
                        session_id=session_id,
                    )
                if active and (
                    active.get("session_id") != session_id
                    or active.get("branch_id") != scope["branchId"]
                    or str(active.get("construct_incarnation_id"))
                        != scope["constructIncarnationId"]
                    or active.get("scope_source_revision") != scope["sourceRevision"]
                    or int(active.get("membership_revision") or 0)
                        != scope["membershipRevision"]
                ):
                    raise ConstructWorkLoopError(
                        "WORK_ACTIVE_PROGRAM_SCOPE_STALE",
                        "active work program no longer matches canonical thread scope",
                        409,
                    )
        instant = self.now()
        return _signed_document(
            WORK_ACTIVE_SCOPE_RESOLUTION_CONTRACT,
            {
                "ownerPrincipalId": str(owner_user_id),
                "constructId": construct_id,
                "constructIncarnationId": scope["constructIncarnationId"],
                "threadId": thread_id,
                "sessionId": session_id,
                "branchId": active.get("branch_id") if active else None,
                "membershipRevision": scope["membershipRevision"],
                "activeProgramId": active_program_id,
                "sourceRevision": scope["sourceRevision"],
                "issuedAt": _iso(instant),
                "expiresAt": _iso(
                    instant + timedelta(seconds=_PROJECTION_TTL_SECONDS)
                ),
            },
            private_key_pem=self.private_key_pem,
        )

    def resolve_evidence(
        self,
        owner_user_id: str,
        program_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        locator = _exact(
            request,
            frozenset({"contract", "evidenceType", "authorityClass", "locatorId", "itemId"}),
            "evidenceLocator",
        )
        if locator.get("contract") != WORK_EVIDENCE_LOCATOR_CONTRACT:
            raise ConstructWorkLoopError("WORK_EVIDENCE_LOCATOR_INVALID", "evidence locator contract is invalid")
        evidence_type = str(locator.get("evidenceType") or "")
        if evidence_type not in {
            "canonical_context", "owner_attestation", "host_readback", "test_result",
            "artifact", "procedural_assertion", "blocker_change", "handoff_acceptance",
        }:
            raise ConstructWorkLoopError("WORK_EVIDENCE_LOCATOR_INVALID", "evidence type is invalid")
        authority_class = str(locator.get("authorityClass") or "")
        if authority_class not in {
            "vvault_artifact", "vvault_transcript_event", "chatty_core_receipt",
            "owner_attestation", "plan4_execution_receipt",
        }:
            raise ConstructWorkLoopError("WORK_EVIDENCE_LOCATOR_INVALID", "authority class is invalid")
        allowed_types_by_authority = {
            "vvault_artifact": {"artifact", "canonical_context"},
            "vvault_transcript_event": {"host_readback"},
            "chatty_core_receipt": {
                "host_readback", "test_result", "blocker_change", "handoff_acceptance",
            },
            "owner_attestation": {"owner_attestation"},
            "plan4_execution_receipt": {"host_readback", "test_result", "artifact"},
        }
        if evidence_type not in allowed_types_by_authority.get(authority_class, set()):
            raise ConstructWorkLoopError(
                "WORK_EVIDENCE_TYPE_AUTHORITY_MISMATCH",
                "evidence type is not issued by the requested canonical authority",
                409,
            )
        locator_id = str(locator.get("locatorId") or "").strip()
        if not locator_id or len(locator_id) > 512 or re.search(r"[\x00-\x1f\x7f]", locator_id):
            raise ConstructWorkLoopError("WORK_EVIDENCE_LOCATOR_INVALID", "locator id is invalid")
        item_id = None if locator.get("itemId") is None else _safe_id(locator.get("itemId"), "itemId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                program = self._program(cur, str(owner_user_id), _safe_id(program_id, "programId"))
                head = self._head(cur, str(owner_user_id), program_id)
                if not head:
                    raise ConstructWorkLoopError("WORK_PROGRAM_EMPTY", "work program has no canonical head", 409)
                resolved: dict[str, Any] | None = None
                derived_evidence_type: str | None = None
                verified_fact_kinds: list[str] = []
                if authority_class == "owner_attestation":
                    if evidence_type != "owner_attestation" or not re.match(r"^(correction|waiver):", locator_id):
                        raise ConstructWorkLoopError(
                            "WORK_OWNER_ATTESTATION_PURPOSE_INVALID",
                            "owner attestation must declare a correction or waiver purpose",
                            409,
                        )
                    attestation = {
                        "ownerPrincipalId": str(owner_user_id),
                        "programId": program_id,
                        "locatorId": locator_id,
                        "itemId": item_id,
                    }
                    resolved = {
                        "canonical_id": f"owner-attestation:{locator_id}",
                        "payload_sha256": _sha256(attestation),
                        "receipt_sha256": _sha256({"ownerAttestation": attestation}),
                        "authority": "vvault/authenticated-owner-attestation",
                    }
                    derived_evidence_type = "owner_attestation"
                    verified_fact_kinds = (
                        ["owner_correction"] if locator_id.startswith("correction:")
                        else ["criterion_waived"]
                    )
                elif authority_class == "vvault_transcript_event":
                    cur.execute(
                        """SELECT event_id AS canonical_id,thread_id,content,content_sha256 AS payload_sha256,
                                  participant_frame,participant_frame_signature,
                                  content_sha256 AS receipt_sha256,
                                  'ovvaults.transcript_events'::text AS authority
                             FROM ovvaults.transcript_events
                            WHERE owner_user_id=%s AND thread_id=%s AND event_id=%s""",
                        (owner_user_id, program["thread_id"], locator_id),
                    )
                    resolved = _row(cur.fetchone())
                    if resolved:
                        frame = resolved.get("participant_frame")
                        if isinstance(frame, str):
                            frame = json.loads(frame)
                        content_digest = hashlib.sha256(str(resolved.get("content") or "").encode("utf-8")).hexdigest()
                        if (
                            not isinstance(frame, dict)
                            or content_digest != resolved.get("payload_sha256")
                            or not conversation_thread_service.verify_payload(
                                frame,
                                str(resolved.get("participant_frame_signature") or ""),
                            )
                        ):
                            raise ConstructWorkLoopError(
                                "WORK_EVIDENCE_INTEGRITY_FAILED",
                                "canonical transcript evidence failed frame or content verification",
                                409,
                            )
                        derived_evidence_type = "host_readback"
                        verified_fact_kinds = ["canonical_transcript_event"]
                elif authority_class == "chatty_core_receipt":
                    cur.execute(
                        """SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                                  evidence_sha256,signature,actor_principal_id,created_at
                             FROM ovvaults.qa_evaluation_events
                            WHERE owner_user_id=%s AND thread_id=%s AND qa_event_id::text=%s""",
                        (owner_user_id, program["thread_id"], locator_id),
                    )
                    qa_row = _row(cur.fetchone())
                    if qa_row:
                        try:
                            envelope = conversation_thread_service.verified_qa_evidence_envelope(
                                qa_row,
                                conversation_thread_service._signing_secret(),
                            )
                        except conversation_thread_service.ConversationContractError as exc:
                            raise ConstructWorkLoopError(
                                "WORK_EVIDENCE_INTEGRITY_FAILED",
                                "canonical QA evidence failed signed-envelope verification",
                                409,
                            ) from exc
                        qa_fact_map = {
                            "turn_response_received": ("host_readback", ["canonical_pair_readback"]),
                            "tester_turn_persistence_readback_recorded": ("host_readback", ["canonical_persistence_readback"]),
                            "legacy_turn_attachment_verified": ("host_readback", ["legacy_canonical_attachment_readback"]),
                        }
                        if envelope.get("eventType") not in qa_fact_map:
                            raise ConstructWorkLoopError(
                                "WORK_EVIDENCE_TYPE_UNSUPPORTED",
                                "QA event type does not carry work-advancement facts",
                                409,
                            )
                        derived_evidence_type, verified_fact_kinds = qa_fact_map[envelope["eventType"]]
                        resolved = {
                            "canonical_id": str(envelope["qaEventId"]),
                            "payload_sha256": envelope["evidenceSha256"],
                            "receipt_sha256": envelope["evidenceSha256"],
                            "authority": "ovvaults.qa_evaluation_events",
                        }
                elif authority_class == "plan4_execution_receipt":
                    cur.execute(
                        """SELECT a.artifact_id AS canonical_id,a.artifact_type,a.content_sha256 AS payload_sha256,
                                  a.receipt_sha256,a.receipt,a.signature_algorithm,a.signature_key_id,a.signature,
                                  e.item_id,e.thread_id
                             FROM ovvaults.construct_work_execution_artifacts a
                             JOIN ovvaults.construct_work_executions e
                               ON e.owner_user_id=a.owner_user_id AND e.execution_id=a.execution_id
                            WHERE a.owner_user_id=%s AND a.program_id=%s AND a.artifact_id=%s""",
                        (owner_user_id, program_id, locator_id),
                    )
                    resolved = _row(cur.fetchone())
                    if resolved:
                        receipt = resolved.get("receipt")
                        if isinstance(receipt, str):
                            receipt = json.loads(receipt)
                        body = {
                            key: value for key, value in (receipt or {}).items()
                            if key not in {"payloadSha256", "algorithm", "keyId", "signature"}
                        }
                        try:
                            if (
                                not isinstance(receipt, dict)
                                or receipt.get("payloadSha256") != _sha256(body)
                                or receipt.get("payloadSha256") != resolved.get("receipt_sha256")
                            ):
                                raise ValueError("receipt hash mismatch")
                            canonical_projection_signing.verify_canonical_payload(
                                body,
                                {key: receipt[key] for key in ("algorithm", "keyId", "signature")},
                                private_key_pem=self.private_key_pem,
                            )
                        except (RuntimeError, ValueError, KeyError) as exc:
                            raise ConstructWorkLoopError(
                                "WORK_EVIDENCE_INTEGRITY_FAILED",
                                "execution evidence signature or hash is invalid",
                                409,
                            ) from exc
                        artifact_type = resolved.get("artifact_type")
                        derived_evidence_type = "host_readback" if artifact_type == "effect_readback" else (
                            "test_result" if artifact_type == "result" else "artifact"
                        )
                        verified_fact_kinds = [
                            "execution_readback_verified" if artifact_type == "effect_readback"
                            else "execution_result_immutable"
                        ]
                        resolved["authority"] = "ovvaults.construct_work_execution_artifacts"
                        if item_id is not None and resolved.get("item_id") != item_id:
                            raise ConstructWorkLoopError(
                                "WORK_EVIDENCE_SCOPE_INVALID",
                                "execution evidence item scope mismatch",
                                409,
                            )
                else:
                    resolved = {"artifact_locator": locator_id}
        if resolved and resolved.get("artifact_locator"):
            projection, status = knowledge_contract.resolve_knowledge_references(
                owner_user_id=str(owner_user_id),
                instance_id=program["construct_id"],
                references=[{"artifact_id": resolved["artifact_locator"], "required": True}],
                private_key_pem=self.private_key_pem,
            )
            artifacts = projection.get("artifacts") if isinstance(projection, dict) else None
            if status != 200 or not isinstance(artifacts, list) or len(artifacts) != 1:
                raise ConstructWorkLoopError(
                    "WORK_EVIDENCE_NOT_VERIFIED",
                    "artifact is not an approved signed canonical publication",
                    409,
                )
            artifact = artifacts[0]
            publication = artifact.get("publication_evidence") or {}
            if publication.get("approved") is not True or publication.get("signedPublication") is not True:
                raise ConstructWorkLoopError(
                    "WORK_EVIDENCE_NOT_VERIFIED",
                    "artifact publication signature is not verified",
                    409,
                )
            derived_evidence_type = "canonical_context"
            verified_fact_kinds = ["signed_canonical_publication"]
            for claim in artifact.get("claims") or []:
                claim_id = str(claim.get("claim_id") or "").strip()
                if claim_id:
                    verified_fact_kinds.append(_safe_id(f"claim:{claim_id}", "claimFactKind"))
            resolved = {
                "canonical_id": artifact["artifact_id"],
                "payload_sha256": artifact["sha256"],
                "receipt_sha256": artifact["sha256"],
                "authority": "ovvaults.signed-knowledge-publication",
            }
        if not resolved:
            raise ConstructWorkLoopError("WORK_EVIDENCE_NOT_FOUND", "canonical evidence was not found", 404)
        if derived_evidence_type != evidence_type:
            raise ConstructWorkLoopError(
                "WORK_EVIDENCE_TYPE_AUTHORITY_MISMATCH",
                "requested evidence type does not match canonical source facts",
                409,
            )
        goal_revision = head.get("resulting_goal_revision")
        if not goal_revision:
            raise ConstructWorkLoopError(
                "WORK_CANONICAL_EVENT_INVALID",
                "canonical work head is missing its goal revision",
                503,
            )
        instant = self.now()
        evidence_id = _safe_id(resolved.get("canonical_id"), "resolved.evidenceId")
        advancement = derived_evidence_type in {
            "owner_attestation", "host_readback", "test_result", "blocker_change", "handoff_acceptance",
        }
        reference = {
            "contract": WORK_EVIDENCE_REFERENCE_CONTRACT,
            "evidenceId": evidence_id,
            "evidenceType": derived_evidence_type,
            "authority": resolved["authority"],
            "scope": {
                "ownerPrincipalId": str(owner_user_id),
                "programId": program_id,
                "constructId": program["construct_id"],
                "itemId": item_id,
                "threadId": program["thread_id"],
                "sessionId": program["session_id"],
            },
            "payloadSha256": _digest(resolved["payload_sha256"], "resolved.payloadSha256"),
            "receiptSha256": _digest(
                resolved.get("receipt_sha256") or resolved["payload_sha256"],
                "resolved.receiptSha256",
            ),
            "issuedAt": _iso(instant),
            "cryptographicallyVerified": True,
            "advancementAuthority": advancement,
            "verifiedFactKinds": sorted(set(verified_fact_kinds)),
        }
        return _signed_document(
            WORK_EVIDENCE_RESOLUTION_CONTRACT,
            {
                "ownerPrincipalId": str(owner_user_id),
                "programId": program_id,
                "constructId": program["construct_id"],
                "threadId": program["thread_id"],
                "sessionId": program["session_id"],
                "itemId": item_id,
                "headEventId": head["event_id"],
                "headEventSha256": head["event_sha256"],
                "goalRevision": goal_revision,
                "evidenceReference": reference,
                "issuedAt": _iso(instant),
                "expiresAt": _iso(instant + timedelta(seconds=_PROJECTION_TTL_SECONDS)),
            },
            private_key_pem=self.private_key_pem,
        )

    def preflight_inspect(self, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        _exact(request, frozenset({"programId", "threadId", "constructId"}), "preflightRequest")
        program_id = _safe_id(request.get("programId"), "programId")
        thread_id = _safe_id(request.get("threadId"), "threadId")
        construct_id = _safe_id(request.get("constructId"), "constructId", principal=True)
        with self.connect() as conn:
            with conn.cursor() as cur:
                scope = self._thread_scope(
                    cur,
                    str(owner_user_id),
                    thread_id,
                    construct_id,
                    session_id=thread_id,
                    program_id=program_id,
                )
                cur.execute(
                    """SELECT program_id FROM ovvaults.construct_work_programs
                        WHERE owner_user_id=%s AND program_id=%s""",
                    (owner_user_id, program_id),
                )
                exists = bool(cur.fetchone())
                head = self._head(cur, str(owner_user_id), program_id) if exists else None
        return _signed_document(
            WORK_PREFLIGHT_CONTRACT,
            {
                "programId": program_id,
                "threadId": thread_id,
                "constructId": construct_id,
                "ownerMatched": True,
                "threadMatched": True,
                "constructMatched": True,
                "membershipRevision": scope["membershipRevision"],
                "constructIncarnationId": scope["constructIncarnationId"],
                "branchId": scope["branchId"],
                "sourceRevision": scope["sourceRevision"],
                "programExists": exists,
                "headSequence": int((head or {}).get("sequence") or 0),
                "headEventId": (head or {}).get("event_id"),
                "headEventSha256": (head or {}).get("event_sha256"),
                "contentIncluded": False,
                "noInference": True,
                "noExecution": True,
                "noPersistence": True,
                "authority": AUTHORITY,
            },
            private_key_pem=self.private_key_pem,
        )


construct_work_loop_service = ConstructWorkLoopService()
