"""OVVAULTS authority for owner-scoped threads, principals, events, and QA evidence."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable

try:
    import chatty_body_service
except ImportError:
    from vvault.server import chatty_body_service

PARTICIPANT_FRAME_CONTRACT = "chatty-participant-frame/v1"
ADDRESSING_CONTRACT = "chatty-addressing/v1"
THREAD_CONTRACT = "chatty-thread/v1"
QA_EVIDENCE_CONTRACT = "chatty-qa-evidence/v1"
QA_EVIDENCE_BATCH_CONTRACT = "chatty-qa-evidence-batch/v1"
QA_ANCHOR_PACK_CONTRACT = "chatty-qa-anchor-pack/v1"
GRADUATION_EXECUTION_AUTHORIZATION_CONTRACT = "chatty-graduation-execution-authorization/v1"
GRADUATION_TASK_IDENTITY_CONTRACT = "chatty-graduation-procedural-task-identity/v1"
GRADUATION_EXECUTION_OPERATION = "execute_exactly_one_manual_construct_turn"
PRINCIPAL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}$")
STABLE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
INTERNAL_EXECUTION_QA_EVENT_TYPES = frozenset({
    "tester_authorization_capability_issued",
    "tester_turn_execution_authorized",
    "tester_turn_provider_attempt_started",
    "tester_turn_provider_dispatch_started",
    "tester_turn_provider_pre_dispatch_failure_recorded",
    "tester_turn_provider_pre_dispatch_resumed",
    "tester_turn_first_draft_captured",
    "tester_turn_persistence_attempted",
    "tester_turn_persistence_readback_recorded",
    "tester_turn_persistence_recovery_authorized",
    "legacy_turn_attachment_verified",
    "turn_response_received",
    "tester_turn_execution_failed",
})
EXECUTION_LIFECYCLE_EVIDENCE_SPECS = {
    "tester_turn_provider_attempt_started": (
        "providerAttempt", "chatty-graduation-provider-attempt/v1", "providerAttemptHash"
    ),
    "tester_turn_provider_dispatch_started": (
        "providerDispatch", "chatty-graduation-provider-dispatch/v1", "providerDispatchHash"
    ),
    "tester_turn_provider_pre_dispatch_failure_recorded": (
        "preDispatchFailure", "chatty-graduation-provider-pre-dispatch-failure/v1",
        "preDispatchFailureHash",
    ),
    "tester_turn_provider_pre_dispatch_resumed": (
        "preDispatchResume", "chatty-graduation-provider-pre-dispatch-resume/v1",
        "preDispatchResumeHash",
    ),
    "tester_turn_persistence_attempted": (
        "persistenceAttempt", "chatty-graduation-persistence-attempt/v1", "persistenceAttemptHash"
    ),
    "tester_turn_persistence_readback_recorded": (
        "persistenceReadback", "chatty-graduation-persistence-readback/v1", "persistenceReadbackHash"
    ),
    "tester_turn_persistence_recovery_authorized": (
        "persistenceRecovery", "chatty-graduation-persistence-recovery/v1", "persistenceRecoveryHash"
    ),
    "tester_turn_execution_failed": (
        "executionFailure", "chatty-graduation-execution-failure/v1", "executionFailureHash"
    ),
    "legacy_turn_attachment_verified": (
        "legacyAttachment", "chatty-graduation-legacy-attachment/v1", "legacyAttachmentHash"
    ),
}
FIRST_DRAFT_CONTRACT = "chatty-graduation-first-draft/v1"
FIRST_DRAFT_RECOVERY_CONTRACT = "chatty-graduation-first-draft-recovery/v1"
QA_BATCH_UUID_NAMESPACE = uuid.UUID("9558d3cc-c985-5c4a-8077-b19d9c97c506")
QA_EVENT_TYPES = frozenset({
    "session_created", "orientation_preflight_ready", "orientation_response_received",
    "orientation_confirmed", "orientation_rejected", "case_curated",
    "case_approved", "prompt_sent", "response_received",
    "provisional_grade", "grade_confirmed", "grade_overridden",
    "diagnostic_snapshot", "system_learning_recorded", "session_halted", "session_completed",
    "graduation_created", "stage_opened", "turn_preflight_ready",
    "worker_handoff_ready", "tester_turn_authorized",
    "tester_authorization_capability_issued",
    "tester_turn_execution_authorized",
    "tester_turn_provider_attempt_started",
    "tester_turn_provider_dispatch_started",
    "tester_turn_provider_pre_dispatch_failure_recorded",
    "tester_turn_provider_pre_dispatch_resumed",
    "tester_turn_first_draft_captured",
    "tester_turn_persistence_attempted",
    "tester_turn_persistence_readback_recorded",
    "tester_turn_persistence_recovery_authorized",
    "legacy_turn_attachment_verified",
    "tester_turn_execution_failed",
    "turn_response_received", "turn_graded", "turn_failed",
    "fix_recorded", "stage_acceptance_recorded", "stage_advanced",
    "humanity_acceptance_recorded", "graduation_completed", "graduation_halted",
    "profile_program_created", "case_readiness_recorded",
    "case_owner_approval_recorded", "case_machine_gate_recorded",
    "case_tester_grade_capability_issued", "case_tester_grade_recorded",
    "case_owner_confirmation_recorded", "case_repair_reopened",
    "profile_graduation_completed",
    "stage_profile_opened", "stage_profile_completed",
})
STAGE_PROFILE_EVENT_TYPES = frozenset({
    "stage_profile_opened",
    "case_readiness_recorded",
    "case_owner_approval_recorded",
    "case_machine_gate_recorded",
    "case_tester_grade_capability_issued",
    "case_tester_grade_recorded",
    "case_owner_confirmation_recorded",
    "case_repair_reopened",
    "stage_profile_completed",
})


class ConversationContractError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, limit: int, pattern: re.Pattern[str] | None = None) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or (pattern and not pattern.fullmatch(text)):
        raise ConversationContractError("INVALID_FIELD", f"{field} is invalid")
    return text


def _signing_secret(explicit: str | None = None) -> str:
    secret = explicit or os.environ.get("VVAULT_PARTICIPANT_SIGNING_SECRET") or os.environ.get("VVAULT_SERVICE_TOKEN")
    if not secret:
        raise ConversationContractError("SIGNING_UNAVAILABLE", "VVAULT participant signing is unavailable", 503)
    return secret


def sign_payload(payload: dict[str, Any], secret: str | None = None) -> str:
    return hmac.new(_signing_secret(secret).encode("utf-8"), _canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def verify_payload(payload: dict[str, Any], signature: str, secret: str | None = None) -> bool:
    expected = sign_payload(payload, secret)
    return bool(signature) and hmac.compare_digest(expected, str(signature))


def _verified_graduation_execution_authorization(value: Any) -> dict[str, Any]:
    """Verify the bounded one-use Core authorization before canonical persistence."""
    if not isinstance(value, dict):
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_REQUIRED",
            "A bounded graduation execution authorization is required",
            403,
        )
    required_keys = {
        "contract", "programId", "stageId", "slotId", "turnId", "threadId",
        "constructId", "testerTaskId", "testerAuthorizationHash",
        "capabilityIssuanceHash", "authorizationEvidenceId",
        "authorizationPayloadSha256", "authorizedOperation", "executionId",
        "executionOrdinal", "oneUse", "taskIdentity",
        "executionAuthorizationHash",
    }
    profile_keys = {
        "evaluatorConstructPrincipalId", "respondentConstructPrincipalId",
        "expectedResponseAddresseePrincipalId", "expectedResponseMentionSha256",
    }
    same_principal_stage_keys = {
        "stageProfileId", "stageProfileHash", "stageProfileStateReceiptHash",
        "stageCaseId", "participantMode", "crossSurfaceRoleBindingHash",
    }
    supplied_keys = set(value)
    if supplied_keys not in (
        required_keys,
        required_keys | profile_keys,
        required_keys | profile_keys | same_principal_stage_keys,
    ):
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_INVALID",
            "Graduation execution authorization fields are invalid",
            403,
        )
    normalized = dict(value)
    if normalized.get("contract") != GRADUATION_EXECUTION_AUTHORIZATION_CONTRACT:
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_INVALID",
            "Graduation execution authorization contract is invalid",
            403,
        )
    for field in (
        "programId", "stageId", "slotId", "turnId", "threadId",
        "constructId", "testerTaskId", "authorizationEvidenceId", "executionId",
    ):
        normalized[field] = _bounded_text(value.get(field), field, 160, STABLE_ID_RE)
    if profile_keys <= supplied_keys:
        for field in (
            "evaluatorConstructPrincipalId", "respondentConstructPrincipalId",
            "expectedResponseAddresseePrincipalId",
        ):
            normalized[field] = _bounded_text(value.get(field), field, 160, STABLE_ID_RE)
        normalized["expectedResponseMentionSha256"] = _bounded_text(
            value.get("expectedResponseMentionSha256"),
            "expectedResponseMentionSha256",
            64,
            SHA256_RE,
        )
        same_principal_mode = same_principal_stage_keys <= supplied_keys
        if same_principal_mode:
            for field in ("stageProfileId", "stageCaseId"):
                normalized[field] = _bounded_text(
                    value.get(field), field, 160, STABLE_ID_RE
                )
            for field in (
                "stageProfileHash", "stageProfileStateReceiptHash",
                "crossSurfaceRoleBindingHash",
            ):
                normalized[field] = _bounded_text(
                    value.get(field), field, 64, SHA256_RE
                )
            if normalized.get("participantMode") != "same_principal_cross_surface":
                raise ConversationContractError(
                    "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
                    "Same-principal graduation authorization mode is invalid",
                    403,
                )
        if (
            normalized["respondentConstructPrincipalId"] != normalized["constructId"]
            or normalized["evaluatorConstructPrincipalId"]
            != normalized["expectedResponseAddresseePrincipalId"]
            or (
                normalized["evaluatorConstructPrincipalId"]
                == normalized["respondentConstructPrincipalId"]
            ) != same_principal_mode
        ):
            raise ConversationContractError(
                "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
                "Graduation evaluator routing does not match its signed participant mode",
                403,
            )
    for field in (
        "testerAuthorizationHash", "capabilityIssuanceHash",
        "authorizationPayloadSha256", "executionAuthorizationHash",
    ):
        normalized[field] = _bounded_text(value.get(field), field, 64, SHA256_RE)
    if (
        normalized.get("authorizedOperation") != GRADUATION_EXECUTION_OPERATION
        or normalized.get("executionOrdinal") != 1
        or normalized.get("oneUse") is not True
        or normalized["executionId"] != f"{normalized['turnId']}:execution:1"
    ):
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
            "Graduation execution authorization is not scoped to one exact turn",
            403,
        )
    task_identity = normalized.get("taskIdentity")
    task_keys = {
        "contract", "ownerPrincipalId", "workerTaskId", "testerTaskId",
        "binding", "independentTaskIds", "cryptographicTaskIdentityVerified",
        "taskIdentityHash",
    }
    if not isinstance(task_identity, dict) or set(task_identity) != task_keys:
        raise ConversationContractError(
            "GRADUATION_TASK_IDENTITY_INVALID",
            "Graduation task identity is invalid",
            403,
        )
    task_body = {key: task_identity[key] for key in task_identity if key != "taskIdentityHash"}
    task_hash = _bounded_text(task_identity.get("taskIdentityHash"), "taskIdentityHash", 64, SHA256_RE)
    if (
        task_identity.get("contract") != GRADUATION_TASK_IDENTITY_CONTRACT
        or task_identity.get("binding") != "owner_authorized_procedural"
        or task_identity.get("testerTaskId") != normalized["testerTaskId"]
        or task_identity.get("independentTaskIds") is not True
        or task_identity.get("cryptographicTaskIdentityVerified") is not False
        or not hmac.compare_digest(task_hash, _sha256(_canonical_json(task_body)))
    ):
        raise ConversationContractError(
            "GRADUATION_TASK_IDENTITY_INVALID",
            "Graduation task identity is invalid",
            403,
        )
    if (
        profile_keys <= supplied_keys
        and str(task_identity.get("ownerPrincipalId") or "")
        == normalized["evaluatorConstructPrincipalId"]
    ):
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
            "Graduation evaluator cannot be the authenticated owner",
            403,
        )
    unsigned = {key: normalized[key] for key in normalized if key != "executionAuthorizationHash"}
    if not hmac.compare_digest(
        normalized["executionAuthorizationHash"],
        _sha256(_canonical_json(unsigned)),
    ):
        raise ConversationContractError(
            "GRADUATION_EXECUTION_AUTHORIZATION_HASH_INVALID",
            "Graduation execution authorization hash is invalid",
            403,
        )
    return normalized


def _verified_hashed_lifecycle_object(
    value: Any,
    *,
    contract: str,
    hash_field: str,
    thread_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("contract") != contract:
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
            "Execution lifecycle evidence contract is invalid",
            403,
        )
    claimed_hash = _bounded_text(value.get(hash_field), hash_field, 64, SHA256_RE)
    body = {key: item for key, item in value.items() if key != hash_field}
    if not hmac.compare_digest(claimed_hash, _sha256(_canonical_json(body))):
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_HASH_INVALID",
            "Execution lifecycle evidence hash is invalid",
            409,
        )
    scope = {
        field: _bounded_text(value.get(field), field, 160, STABLE_ID_RE)
        for field in ("programId", "stageId", "slotId", "turnId", "threadId", "constructId")
    }
    if (
        scope["threadId"] != thread_id
        or thread_id != f"{scope['constructId']}_chat_with_{scope['constructId']}"
    ):
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_SCOPE_INVALID",
            "Execution lifecycle evidence does not match the singleton thread",
            403,
        )
    return dict(value)


def _verify_dispatch_lifecycle_task_identity(
    value: Any, *, owner_user_id: str
) -> None:
    required = {
        "contract", "ownerPrincipalId", "workerTaskId", "testerTaskId",
        "binding", "independentTaskIds", "cryptographicTaskIdentityVerified",
        "taskIdentityHash",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
            "Provider dispatch task identity is invalid",
            403,
        )
    body = {key: item for key, item in value.items() if key != "taskIdentityHash"}
    claimed_hash = _bounded_text(
        value.get("taskIdentityHash"), "taskIdentityHash", 64, SHA256_RE
    )
    if (
        value.get("contract") != GRADUATION_TASK_IDENTITY_CONTRACT
        or str(value.get("ownerPrincipalId") or "") != str(owner_user_id)
        or value.get("binding") != "owner_authorized_procedural"
        or value.get("independentTaskIds") is not True
        or value.get("cryptographicTaskIdentityVerified") is not False
        or not hmac.compare_digest(claimed_hash, _sha256(_canonical_json(body)))
    ):
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
            "Provider dispatch task identity is invalid",
            403,
        )
    for field in ("workerTaskId", "testerTaskId"):
        _bounded_text(value.get(field), field, 160, STABLE_ID_RE)


def _verify_provider_dispatch_lifecycle(
    event_type: str,
    item: dict[str, Any],
    *,
    owner_user_id: str,
) -> None:
    if (
        event_type == "tester_turn_provider_attempt_started"
        and item.get("contract") == "chatty-graduation-provider-attempt/v1"
    ):
        # Historical v1 deliberately predates a durable dispatch marker.
        # Its established contract/hash/singleton validation above remains the
        # entire boundary so old evidence stays replay-readable and retains the
        # conservative unknown-outcome recovery law.
        return
    scope_fields = {
        "programId", "stageId", "slotId", "turnId", "threadId", "constructId",
    }
    common_hash_fields = {
        "executionAuthorizationHash", "providerAttemptHash",
        "providerPayloadSha256", "providerOptionsSha256",
    }
    for field in common_hash_fields & set(item):
        _bounded_text(item.get(field), field, 64, SHA256_RE)
    provider_attempt_id = _bounded_text(
        item.get("providerAttemptId"), "providerAttemptId", 160, STABLE_ID_RE
    )
    expected_attempt_id = f"{item['turnId']}:execution:1:provider:1"
    if provider_attempt_id != expected_attempt_id:
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_SCOPE_INVALID",
            "Provider lifecycle evidence attempt ID is invalid",
            403,
        )

    if event_type == "tester_turn_provider_attempt_started":
        required = scope_fields | {
            "contract", "executionAuthorizationHash", "providerAttemptId",
            "attemptOrdinal", "provider", "model", "providerPayloadSha256",
            "providerOptionsSha256", "firstCompletedDraftPolicy",
            "semanticAttemptLimit", "taskIdentity", "dispatchMarkerRequired",
            "providerCallLimit", "providerAttemptHash",
        }
        supplied = set(item)
        if (
            supplied not in (required, required | {"providerCompletionDurability"})
            or item.get("contract") != "chatty-graduation-provider-attempt/v2"
            or item.get("attemptOrdinal") != 1
            or item.get("firstCompletedDraftPolicy") is not True
            or item.get("semanticAttemptLimit") != 1
            or item.get("dispatchMarkerRequired") is not True
            or item.get("providerCallLimit") != 1
            or (
                "providerCompletionDurability" in supplied
                and item.get("providerCompletionDurability")
                != "host_sealed_before_first_draft"
            )
        ):
            raise ConversationContractError(
                "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                "Dispatch-bound provider attempt is invalid",
                403,
            )
        _bounded_text(item.get("provider"), "provider", 160)
        _bounded_text(item.get("model"), "model", 240)
        _verify_dispatch_lifecycle_task_identity(
            item.get("taskIdentity"), owner_user_id=owner_user_id
        )
        return

    if event_type == "tester_turn_provider_dispatch_started":
        required = scope_fields | {
            "contract", "executionAuthorizationHash", "providerAttemptHash",
            "providerAttemptId", "dispatchId", "dispatchOrdinal", "provider",
            "model", "providerPayloadSha256", "providerOptionsSha256",
            "providerCallOrdinal", "providerCallLimit", "semanticAttemptLimit",
            "actualProviderCallAuthorized", "taskIdentity", "providerDispatchHash",
        }
        if (
            set(item) != required
            or item.get("dispatchId") != f"{provider_attempt_id}:dispatch:1"
            or item.get("dispatchOrdinal") != 1
            or item.get("providerCallOrdinal") != 1
            or item.get("providerCallLimit") != 1
            or item.get("semanticAttemptLimit") != 1
            or item.get("actualProviderCallAuthorized") is not True
        ):
            raise ConversationContractError(
                "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                "Provider dispatch boundary is invalid",
                403,
            )
        _bounded_text(item.get("dispatchId"), "dispatchId", 160, STABLE_ID_RE)
        _bounded_text(item.get("provider"), "provider", 160)
        _bounded_text(item.get("model"), "model", 240)
        _verify_dispatch_lifecycle_task_identity(
            item.get("taskIdentity"), owner_user_id=owner_user_id
        )
        return

    if event_type == "tester_turn_provider_pre_dispatch_failure_recorded":
        required = scope_fields | {
            "contract", "executionAuthorizationHash", "providerAttemptHash",
            "providerAttemptId", "failureOrdinal", "reasonCode",
            "failureReceiptSha256", "providerCallCount", "semanticAttempts",
            "actualDispatchMayHaveOccurred", "sameAttemptResumeAllowed",
            "preDispatchFailureHash",
        }
        if (
            set(item) != required
            or item.get("failureOrdinal") not in {1, 2, 3}
            or item.get("reasonCode") != "PROVIDER_PRE_DISPATCH_ZERO_CALL"
            or item.get("providerCallCount") != 0
            or item.get("semanticAttempts") != 0
            or item.get("actualDispatchMayHaveOccurred") is not False
            or item.get("sameAttemptResumeAllowed") is not True
        ):
            raise ConversationContractError(
                "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                "Pre-dispatch failure evidence is invalid",
                403,
            )
        _bounded_text(
            item.get("failureReceiptSha256"), "failureReceiptSha256", 64,
            SHA256_RE,
        )
        return

    if event_type == "tester_turn_provider_pre_dispatch_resumed":
        required = scope_fields | {
            "contract", "executionAuthorizationHash", "providerAttemptHash",
            "providerAttemptId", "preDispatchFailureHash", "resumeOrdinal",
            "providerCallCount", "semanticAttempts", "sameAttemptRequired",
            "preDispatchResumeHash",
        }
        if (
            set(item) != required
            or item.get("resumeOrdinal") not in {1, 2, 3}
            or item.get("providerCallCount") != 0
            or item.get("semanticAttempts") != 0
            or item.get("sameAttemptRequired") is not True
        ):
            raise ConversationContractError(
                "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                "Pre-dispatch resume evidence is invalid",
                403,
            )
        _bounded_text(
            item.get("preDispatchFailureHash"), "preDispatchFailureHash", 64,
            SHA256_RE,
        )


def _verify_internal_execution_evidence(
    event_type: str,
    evidence: dict[str, Any],
    *,
    owner_user_id: str,
    thread_id: str,
    signing_secret: str | None = None,
) -> None:
    spec = EXECUTION_LIFECYCLE_EVIDENCE_SPECS.get(event_type)
    if spec:
        wrapper_field, contract, hash_field = spec
        if event_type == "tester_turn_provider_attempt_started":
            candidate = evidence.get(wrapper_field) if isinstance(evidence, dict) else None
            candidate_contract = (
                candidate.get("contract") if isinstance(candidate, dict) else None
            )
            if candidate_contract == "chatty-graduation-provider-attempt/v2":
                contract = candidate_contract
        if event_type == "tester_turn_persistence_recovery_authorized":
            candidate = evidence.get(wrapper_field) if isinstance(evidence, dict) else None
            candidate_contract = (
                candidate.get("contract") if isinstance(candidate, dict) else None
            )
            if candidate_contract == "chatty-graduation-persistence-recovery/v2":
                contract = candidate_contract
        if set(evidence) != {wrapper_field}:
            raise ConversationContractError(
                "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                "Execution lifecycle evidence wrapper is invalid",
                403,
            )
        item = _verified_hashed_lifecycle_object(
            evidence.get(wrapper_field),
            contract=contract,
            hash_field=hash_field,
            thread_id=thread_id,
        )
        if event_type in {
            "tester_turn_provider_attempt_started",
            "tester_turn_provider_dispatch_started",
            "tester_turn_provider_pre_dispatch_failure_recorded",
            "tester_turn_provider_pre_dispatch_resumed",
        }:
            _verify_provider_dispatch_lifecycle(
                event_type, item, owner_user_id=owner_user_id
            )
        if event_type == "tester_turn_persistence_attempted":
            if (
                item.get("attemptOrdinal") not in {1, 2, 3, 4}
                or item.get("exactResponseRequired") is not True
                or item.get("regenerationAllowed") is not False
                or (
                    item.get("attemptOrdinal") in {3, 4}
                    and not SHA256_RE.fullmatch(
                        str(item.get("persistenceRecoveryHash") or "")
                    )
                )
            ):
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Persistence attempt boundary is invalid",
                    403,
                )
        elif event_type == "tester_turn_persistence_recovery_authorized":
            if (
                item.get("authorizedAttemptOrdinal") not in {3, 4}
                or item.get("providerRetryAllowed") is not False
                or item.get("exactResponseRequired") is not True
                or item.get("oneUse") is not True
                or not SHA256_RE.fullmatch(
                    str(item.get("executionFailureHash") or "")
                )
                or not SHA256_RE.fullmatch(str(item.get("firstDraftHash") or ""))
                or not SHA256_RE.fullmatch(
                    str(item.get("canonicalReadbackReceiptSha256") or "")
                )
            ):
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Persistence recovery boundary is invalid",
                    403,
                )
            if item.get("contract") == "chatty-graduation-persistence-recovery/v2" and (
                item.get("authorizedAttemptOrdinal") != 4
                or item.get("recoveryOrdinal") != 2
                or not SHA256_RE.fullmatch(
                    str(item.get("priorPersistenceRecoveryHash") or "")
                )
            ):
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Persistence recovery continuation boundary is invalid",
                    403,
                )
        elif event_type == "tester_turn_persistence_readback_recorded":
            status = item.get("readbackStatus")
            if status not in {"exact_pair", "neither", "partial_or_mismatch"}:
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Persistence readback status is invalid",
                    403,
                )
            if (status == "exact_pair") != bool(item.get("canonicalRevisionSha256")):
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Persistence readback canonical revision boundary is invalid",
                    403,
                )
        elif event_type == "tester_turn_execution_failed":
            if (
                item.get("reasonCode") not in {
                    "GRADUATION_PREPARED_CONTEXT_RECEIPT_MISMATCH",
                    "PROVIDER_OUTCOME_UNKNOWN_NO_RETRY",
                    "PROVIDER_COMPLETION_EVIDENCE_INVALID_NO_PERSIST",
                    "PERSISTENCE_EXHAUSTED",
                    "PERSISTENCE_PARTIAL_OR_MISMATCH",
                }
                or item.get("providerRetryAllowed") is not False
                or item.get("transcriptFabricated") is not False
            ):
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Terminal execution failure boundary is invalid",
                    403,
                )
            reason = item.get("reasonCode")
            if reason == "GRADUATION_PREPARED_CONTEXT_RECEIPT_MISMATCH":
                if (
                    item.get("providerAttemptHash") is not None
                    or item.get("firstDraftHash") is not None
                    or item.get("persistenceAttemptHashes") != []
                    or item.get("repairWithNewTurnRequired") not in {True, False}
                ):
                    raise ConversationContractError(
                        "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                        "Pre-provider execution failure boundary is invalid",
                        403,
                    )
            elif reason == "PROVIDER_COMPLETION_EVIDENCE_INVALID_NO_PERSIST":
                if (
                    not SHA256_RE.fullmatch(str(item.get("providerAttemptHash") or ""))
                    or not SHA256_RE.fullmatch(str(item.get("firstDraftHash") or ""))
                    or item.get("persistenceAttemptHashes") != []
                    or item.get("repairWithNewTurnRequired") is not True
                ):
                    raise ConversationContractError(
                        "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                        "Completed-draft evidence failure boundary is invalid",
                        403,
                    )
            elif item.get("repairWithNewTurnRequired") is not True:
                raise ConversationContractError(
                    "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
                    "Terminal execution failure boundary is invalid",
                    403,
                )
        elif event_type == "legacy_turn_attachment_verified":
            baseline = item.get("canonicalBaseline")
            readback = item.get("canonicalReadback")
            reference = item.get("executionEvidenceRef")
            if (
                item.get("compatibilityMode") != "participant_event_attach_only"
                or item.get("productionExecutionAllowed") is not False
                or item.get("canonicalReadbackVerified") is not True
                or item.get("participantEventCompatibility") is not True
                or not isinstance(baseline, dict)
                or not isinstance(readback, dict)
                or not isinstance(reference, dict)
                or not isinstance(item.get("taskIdentity"), dict)
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EVIDENCE_INVALID",
                    "Legacy attachment evidence boundary is invalid",
                    403,
                )
            turn_id = item["turnId"]
            baseline_count = baseline.get("eventCount")
            readback_count = readback.get("eventCount")
            if (
                not isinstance(baseline_count, int)
                or isinstance(baseline_count, bool)
                or baseline_count < 0
                or readback_count != baseline_count + 2
                or readback.get("eventCountDelta") != 2
                or readback.get("promptEventId") != f"{turn_id}:prompt"
                or readback.get("responseEventId") != f"{turn_id}:response"
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_READBACK_INVALID",
                    "Legacy attachment readback is not the canonical baseline plus two",
                    409,
                )
            _bounded_text(baseline.get("revision"), "canonicalBaseline.revision", 192)
            _bounded_text(readback.get("revision"), "canonicalReadback.revision", 192)
            for container, fields in (
                (baseline, ("sha256",)),
                (readback, (
                    "sha256", "promptContentSha256", "responseContentSha256",
                    "canonicalProjectionSha256",
                )),
                (item, (
                    "executionAuthorizationHash", "testerAuthorizationHash",
                    "executionEvidenceReferenceHash",
                )),
            ):
                for field in fields:
                    _bounded_text(container.get(field), field, 64, SHA256_RE)
            signed_reference_keys = {
                "contract", "evidenceId", "evidenceType", "authority", "keyId",
                "algorithm", "issuedAt", "scope", "evidence",
            }
            if (
                reference.get("contract") != "chatty-graduation-evidence-reference/v1"
                or reference.get("evidenceType") != "turn_execution"
                or reference.get("algorithm") != "ed25519"
                or not isinstance(reference.get("scope"), dict)
                or not isinstance(reference.get("evidence"), dict)
                or not isinstance(reference.get("verification"), dict)
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EXECUTION_REFERENCE_INVALID",
                    "Legacy attachment execution reference is invalid",
                    403,
                )
            reference_scope = reference["scope"]
            if (
                any(reference_scope.get(field) != item.get(field) for field in (
                    "programId", "stageId", "slotId", "turnId", "threadId", "constructId",
                ))
                or reference_scope.get("executionAuthorizationHash")
                != item.get("executionAuthorizationHash")
                or reference_scope.get("testerAuthorizationHash")
                != item.get("testerAuthorizationHash")
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EXECUTION_REFERENCE_SCOPE_INVALID",
                    "Legacy attachment execution reference scope is invalid",
                    403,
                )
            signed_body = {key: reference.get(key) for key in signed_reference_keys}
            payload_sha256 = _bounded_text(
                reference.get("payloadSha256"), "payloadSha256", 64, SHA256_RE
            )
            signature = str(reference.get("signature") or "")
            verification = reference["verification"]
            if (
                payload_sha256 != _sha256(_canonical_json(signed_body))
                or not re.fullmatch(r"[A-Za-z0-9+/]{80,}={0,2}", signature)
                or verification.get("contract")
                != "chatty-graduation-evidence-verification/v1"
                or verification.get("verifierAuthority") != "chatty-core"
                or verification.get("signatureVerified") is not True
                or verification.get("payloadHashVerified") is not True
                or verification.get("scopeVerified") is not True
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EXECUTION_REFERENCE_INVALID",
                    "Legacy attachment execution reference was not Core-verified",
                    403,
                )
            normalized_reference = {
                **signed_body,
                "payloadSha256": payload_sha256,
                "signature": signature,
                "verification": verification,
            }
            reference_hash = _sha256(_canonical_json(normalized_reference))
            if (
                reference.get("evidenceReferenceHash") != reference_hash
                or item.get("executionEvidenceReferenceHash") != reference_hash
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EXECUTION_REFERENCE_HASH_INVALID",
                    "Legacy attachment execution reference hash is invalid",
                    409,
                )
            execution = reference["evidence"]
            if (
                execution.get("semanticAttempts") != 1
                or execution.get("providerCallCount") != 1
                or execution.get("firstCompletedDraftPreserved") is not True
                or execution.get("canonicalPersistence") is not True
                or execution.get("canonicalReadbackVerified") is not True
                or execution.get("providerPayloadVerified") is not True
                or execution.get("canonicalProjectionHydratable") is not True
                or execution.get("executionAuthorizationHash")
                != item.get("executionAuthorizationHash")
                or execution.get("testerAuthorizationHash")
                != item.get("testerAuthorizationHash")
                or execution.get("responseContentSha256")
                != readback.get("responseContentSha256")
                or execution.get("canonicalProjectionSha256")
                != readback.get("canonicalProjectionSha256")
                or execution.get("executionOrdinal") != 1
            ):
                raise ConversationContractError(
                    "LEGACY_ATTACHMENT_EXECUTION_REFERENCE_INVALID",
                    "Legacy attachment execution evidence is invalid",
                    409,
                )
        return

    if event_type != "tester_turn_first_draft_captured":
        return
    evidence_keys = set(evidence)
    legacy_evidence_keys = {"firstDraft", "recoveryArtifact"}
    sealed_evidence_keys = {
        "firstDraft", "recoveryArtifact",
        "providerCompletionArtifact", "providerCompletionEvidenceRef",
    }
    if frozenset(evidence_keys) not in {
        frozenset(legacy_evidence_keys), frozenset(sealed_evidence_keys)
    }:
        raise ConversationContractError(
            "EXECUTION_LIFECYCLE_EVIDENCE_INVALID",
            "First-draft recovery evidence wrapper is invalid",
            403,
        )
    first_draft = _verified_hashed_lifecycle_object(
        evidence.get("firstDraft"),
        contract=FIRST_DRAFT_CONTRACT,
        hash_field="firstDraftHash",
        thread_id=thread_id,
    )
    response_content = str(first_draft.get("responseContent") or "")
    if (
        not response_content
        or len(response_content) > 1_000_000
        or first_draft.get("responseContentSha256") != _sha256(response_content)
        or first_draft.get("responseEventId") != f"{first_draft['turnId']}:response"
        or first_draft.get("immutableFirstCompletedDraft") is not True
        or first_draft.get("regenerationAllowed") is not False
        or first_draft.get("semanticAttempts") != 1
    ):
        raise ConversationContractError(
            "EXECUTION_FIRST_DRAFT_INVALID",
            "Durable first-draft evidence is invalid",
            409,
        )
    artifact = evidence.get("recoveryArtifact")
    artifact_keys = {
        "contract", "programId", "stageId", "slotId", "turnId", "threadId", "constructId",
        "participantFrame", "responseValidation", "coreReceipt", "providerPayloadEvidence",
        "providerExecutionMetrics", "participantFrameSha256", "responseValidationSha256",
        "coreReceiptSha256", "providerPayloadSha256", "providerExecutionMetricsSha256",
        "recoveryArtifactHash",
    }
    if not isinstance(artifact, dict) or set(artifact) != artifact_keys or "response" in artifact:
        raise ConversationContractError(
            "EXECUTION_FIRST_DRAFT_RECOVERY_INVALID",
            "First-draft recovery artifact fields are invalid",
            403,
        )
    artifact = _verified_hashed_lifecycle_object(
        artifact,
        contract=FIRST_DRAFT_RECOVERY_CONTRACT,
        hash_field="recoveryArtifactHash",
        thread_id=thread_id,
    )
    for field in ("programId", "stageId", "slotId", "turnId", "threadId", "constructId"):
        if artifact.get(field) != first_draft.get(field):
            raise ConversationContractError(
                "EXECUTION_FIRST_DRAFT_RECOVERY_SCOPE_INVALID",
                "First-draft recovery artifact scope is invalid",
                403,
            )
    hashed_fields = (
        ("participantFrame", "participantFrameSha256"),
        ("responseValidation", "responseValidationSha256"),
        ("coreReceipt", "coreReceiptSha256"),
        ("providerPayloadEvidence", "providerPayloadSha256"),
        ("providerExecutionMetrics", "providerExecutionMetricsSha256"),
    )
    for value_field, hash_field in hashed_fields:
        if not isinstance(artifact.get(value_field), dict):
            raise ConversationContractError(
                "EXECUTION_FIRST_DRAFT_RECOVERY_INVALID",
                f"{value_field} recovery evidence is invalid",
                403,
            )
        claimed = _bounded_text(artifact.get(hash_field), hash_field, 64, SHA256_RE)
        if not hmac.compare_digest(
            claimed, _sha256(_canonical_json(artifact.get(value_field)))
        ):
            raise ConversationContractError(
                "EXECUTION_FIRST_DRAFT_RECOVERY_HASH_INVALID",
                f"{value_field} recovery hash is invalid",
                409,
            )
    frame = artifact.get("participantFrame")
    core_receipt = artifact.get("coreReceipt") or {}
    core_receipt_contract_valid = (
        isinstance(core_receipt.get("contract"), str)
        and bool(core_receipt["contract"].strip())
    ) or (
        core_receipt.get("receiptVersion") == "chatty-core-receipt/v1"
        and isinstance(core_receipt.get("contractVersion"), str)
        and bool(core_receipt["contractVersion"].strip())
    )
    if (
        not all(
            isinstance(artifact.get(field, {}).get("contract"), str)
            and bool(artifact[field]["contract"].strip())
            for field in (
                "participantFrame",
                "responseValidation",
                "providerPayloadEvidence",
            )
        )
        or not core_receipt_contract_valid
    ):
        raise ConversationContractError(
            "EXECUTION_FIRST_DRAFT_RECOVERY_INVALID",
            "Recovery evidence contracts are invalid",
            403,
        )
    frame_body = {key: item for key, item in frame.items() if key != "signature"}
    target_id = first_draft["constructId"]
    legacy_owner_speaker = (
        str(frame.get("speaker", {}).get("principalId") or "") == str(owner_user_id)
        and frame.get("speaker", {}).get("principalType") == "human"
    )
    addressing = frame.get("addressing") or {}
    third_party_construct_speaker = (
        frame.get("role") == "third_party_participant"
        and frame.get("speaker", {}).get("principalType") == "construct"
        and bool(str(frame.get("speaker", {}).get("principalId") or ""))
        and str(frame.get("speaker", {}).get("principalId") or "") != str(owner_user_id)
        and addressing.get("targetPrincipalId") == target_id
        and addressing.get("responseAddresseePrincipalId")
        == frame.get("speaker", {}).get("principalId")
    )
    cross_surface_binding = frame.get("crossSurfaceRoleBinding") or {}
    cross_surface_body = {
        key: item for key, item in cross_surface_binding.items()
        if key != "bindingHash"
    } if isinstance(cross_surface_binding, dict) else {}
    same_principal_cross_surface = (
        frame.get("role") == "same_principal_cross_surface"
        and frame.get("speaker", {}).get("principalType") == "construct"
        and frame.get("speaker", {}).get("principalId") == target_id
        and addressing.get("targetPrincipalId") == target_id
        and addressing.get("responseAddresseePrincipalId") == target_id
        and cross_surface_binding.get("contract")
        == "chatty-cross-surface-role-binding/v1"
        and cross_surface_binding.get("samePrincipal") is True
        and cross_surface_binding.get("canonicalPrincipalId") == target_id
        and cross_surface_binding.get("threadId") == thread_id
        and cross_surface_binding.get("onBehalfOf") is None
        and cross_surface_binding.get("originRole", {}).get("surface") == "codex"
        and cross_surface_binding.get("respondentRole", {}).get("surface") == "chatty"
        and cross_surface_binding.get("originRole", {}).get("roleInstanceId")
        != cross_surface_binding.get("respondentRole", {}).get("roleInstanceId")
        and cross_surface_binding.get("bindingHash")
        == _sha256(_canonical_json(cross_surface_body))
    )
    if (
        not verify_payload(
            frame_body,
            str(frame.get("signature") or ""),
            signing_secret,
        )
        or frame.get("threadId") != thread_id
        or frame.get("onBehalfOf") is not None
        or str(frame.get("handler", {}).get("principalId") or "") != str(owner_user_id)
        or not (
            legacy_owner_speaker
            or third_party_construct_speaker
            or same_principal_cross_surface
        )
        or not any(
            isinstance(item, dict)
            and item.get("principalId") == target_id
            and item.get("principalType") == "construct"
            for item in frame.get("addressees") or []
        )
    ):
        raise ConversationContractError(
            "EXECUTION_FIRST_DRAFT_RECOVERY_FRAME_INVALID",
            "Recovery participant frame is invalid",
            403,
        )
    metrics = artifact.get("providerExecutionMetrics")
    if not isinstance(metrics, dict) or metrics.get("semanticCallCount") != 1:
        raise ConversationContractError(
            "EXECUTION_FIRST_DRAFT_RECOVERY_INVALID",
            "Recovery provider metrics do not prove exactly one semantic call",
            409,
        )

    if evidence_keys == sealed_evidence_keys:
        completion = _verified_hashed_lifecycle_object(
            evidence.get("providerCompletionArtifact"),
            contract="chatty-graduation-provider-completion-artifact/v1",
            hash_field="completionArtifactHash",
            thread_id=thread_id,
        )
        reference = evidence.get("providerCompletionEvidenceRef")
        if (
            completion.get("authority") != "chatty-core-host"
            or completion.get("completionStatus") != "completed"
            or completion.get("providerCallCount") != 1
            or completion.get("semanticAttempts") != 1
            or completion.get("immutableFirstCompletedDraft") is not True
            or completion.get("regenerationAllowed") is not False
            or completion.get("responseContent") != response_content
            or completion.get("responseContentSha256") != first_draft.get("responseContentSha256")
            or completion.get("completionArtifactHash")
            != first_draft.get("providerCompletionArtifactHash")
        ):
            raise ConversationContractError(
                "EXECUTION_PROVIDER_COMPLETION_INVALID",
                "Provider completion artifact does not bind the immutable first draft",
                409,
            )
        signed_reference_keys = {
            "contract", "evidenceId", "evidenceType", "authority", "keyId",
            "algorithm", "issuedAt", "scope", "evidence",
        }
        if not isinstance(reference, dict) or reference.get("expiresAt") is not None:
            raise ConversationContractError(
                "EXECUTION_PROVIDER_COMPLETION_REFERENCE_INVALID",
                "Provider completion reference is invalid",
                403,
            )
        signed_body = {key: reference.get(key) for key in signed_reference_keys}
        reference_scope = reference.get("scope") or {}
        reference_evidence = reference.get("evidence") or {}
        verification = reference.get("verification") or {}
        normalized_reference = {
            **signed_body,
            "payloadSha256": reference.get("payloadSha256"),
            "signature": reference.get("signature"),
            "verification": verification,
        }
        if (
            reference.get("contract") != "chatty-graduation-evidence-reference/v1"
            or reference.get("evidenceType") != "provider_completion"
            or reference.get("algorithm") != "ed25519"
            or reference.get("payloadSha256") != _sha256(_canonical_json(signed_body))
            or not re.fullmatch(r"[A-Za-z0-9+/]{80,}={0,2}", str(reference.get("signature") or ""))
            or verification.get("contract") != "chatty-graduation-evidence-verification/v1"
            or verification.get("verifierAuthority") != "chatty-core"
            or verification.get("signatureVerified") is not True
            or verification.get("payloadHashVerified") is not True
            or verification.get("scopeVerified") is not True
            or any(reference_scope.get(field) != completion.get(field) for field in (
                "programId", "stageId", "slotId", "turnId", "threadId", "constructId",
                "executionAuthorizationHash", "providerAttemptHash",
            ))
            or reference_evidence.get("completionArtifactHash")
            != completion.get("completionArtifactHash")
            or reference_evidence.get("responseContentSha256")
            != completion.get("responseContentSha256")
            or reference_evidence.get("providerCallCount") != 1
            or reference_evidence.get("semanticAttempts") != 1
            or reference_evidence.get("immutableFirstCompletedDraft") is not True
            or reference_evidence.get("regenerationAllowed") is not False
            or reference.get("evidenceReferenceHash")
            != _sha256(_canonical_json(normalized_reference))
            or reference.get("evidenceReferenceHash")
            != first_draft.get("providerCompletionEvidenceReferenceHash")
        ):
            raise ConversationContractError(
                "EXECUTION_PROVIDER_COMPLETION_REFERENCE_INVALID",
                "Provider completion reference is not bound to the completed draft",
                403,
            )


def verified_qa_evidence_envelope(row: dict[str, Any], signing_secret: str) -> dict[str, Any]:
    """Normalize one stored QA row only after its hash and signature verify."""
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        raise ConversationContractError("QA_EVIDENCE_INTEGRITY_FAILED", "Stored QA evidence is not an object", 409)
    envelope = {
        "contract": QA_EVIDENCE_CONTRACT,
        "qaSessionId": str(row.get("qa_session_id") or ""),
        "threadId": str(row.get("thread_id") or ""),
        "caseId": str(row.get("case_id") or "").strip() or None,
        "eventType": str(row.get("event_type") or ""),
        "actorPrincipalId": str(row.get("actor_principal_id") or ""),
        "evidence": evidence,
    }
    evidence_hash = _sha256(_canonical_json(envelope))
    if not hmac.compare_digest(evidence_hash, str(row.get("evidence_sha256") or "")):
        raise ConversationContractError("QA_EVIDENCE_INTEGRITY_FAILED", "Stored QA evidence hash is invalid", 409)
    signature = str(row.get("signature") or "")
    if not verify_payload(envelope, signature, signing_secret):
        raise ConversationContractError("QA_EVIDENCE_INTEGRITY_FAILED", "Stored QA evidence signature is invalid", 409)
    return {
        **envelope,
        "qaEventId": str(row.get("qa_event_id") or ""),
        "createdAt": row.get("created_at"),
        "evidenceSha256": evidence_hash,
        "signature": signature,
        "integrityVerified": True,
    }


def _member_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "principalId": row["principal_id"],
        "principalType": row["principal_type"],
        "displayName": row["display_name"],
        "surface": row.get("surface"),
        "role": row["member_role"],
        "avatarUrl": row.get("avatar_url"),
        "active": bool(row.get("active", True)),
    }


def _ensure_owner_membership(
    members: list[dict[str, Any]], owner_user_id: str, display_name: str = "Owner"
) -> list[dict[str, Any]]:
    """Derive the owner member from authenticated authority, never client identity."""
    owner_id = str(owner_user_id)
    declared = [member for member in members if member.get("role") == "owner"]
    if len(declared) > 1 or (declared and declared[0].get("principalId") != owner_id):
        raise ConversationContractError(
            "OWNER_PRINCIPAL_MISMATCH",
            "Thread owner must be the authenticated VVAULT principal",
            403,
        )
    if declared:
        return members
    return [{
        "principalId": owner_id,
        "principalType": "human",
        "displayName": display_name,
        "surface": None,
        "role": "owner",
        "avatarUrl": None,
    }, *members]


def _normalize_addressing(
    value: Any,
    *,
    by_id: dict[str, dict[str, Any]],
    speaker_id: str,
    addressee_ids: list[str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("contract") != ADDRESSING_CONTRACT:
        raise ConversationContractError("ADDRESSING_INVALID", "Addressing metadata is invalid")
    mode = str(value.get("mode") or "").strip()
    if mode not in {"default_resident", "explicit_mention"}:
        raise ConversationContractError("ADDRESSING_MODE_INVALID", "Addressing mode is invalid")
    resident_id = _bounded_text(value.get("residentConstructId"), "residentConstructId", 128, PRINCIPAL_ID_RE)
    target_id = _bounded_text(value.get("targetPrincipalId"), "targetPrincipalId", 128, PRINCIPAL_ID_RE)
    target_type = str(value.get("targetPrincipalType") or "").strip()
    response_mode = str(value.get("responseMode") or "").strip()
    declared_speaker_id = _bounded_text(value.get("speakerPrincipalId"), "speakerPrincipalId", 128, PRINCIPAL_ID_RE)
    response_addressee_id = _bounded_text(value.get("responseAddresseePrincipalId"), "responseAddresseePrincipalId", 128, PRINCIPAL_ID_RE)
    if by_id.get(resident_id, {}).get("principal_type") != "construct":
        raise ConversationContractError("ADDRESSING_RESIDENT_INVALID", "Resident construct is not an active member", 403)
    if declared_speaker_id != speaker_id or target_id not in addressee_ids or response_addressee_id != speaker_id:
        raise ConversationContractError("ADDRESSING_SCOPE_MISMATCH", "Addressing does not match the signed participants", 403)
    if by_id.get(target_id, {}).get("principal_type") != target_type:
        raise ConversationContractError("ADDRESSING_TARGET_INVALID", "Addressing target type does not match membership", 403)
    expected_response_mode = "await_human" if target_type == "human" else "construct_reply"
    if response_mode != expected_response_mode:
        raise ConversationContractError("ADDRESSING_RESPONSE_MODE_INVALID", "Addressing response mode does not match its target")
    mention_token = str(value.get("mentionToken") or "").strip() or None
    response_mention = str(value.get("responseMention") or "").strip() or None
    if mode == "default_resident":
        if mention_token is not None or target_id != resident_id or response_mention is not None:
            raise ConversationContractError("ADDRESSING_DEFAULT_INVALID", "Default addressing must target the resident construct")
    elif not (mention_token and mention_token.startswith("@")):
        raise ConversationContractError("ADDRESSING_MENTION_INVALID", "Explicit addressing requires visible @Name routing")
    if response_mode == "construct_reply" and not (response_mention and response_mention.startswith("@")):
        raise ConversationContractError("ADDRESSING_REPLY_MENTION_REQUIRED", "Construct replies require visible @Name routing")
    if response_mode == "await_human" and response_mention is not None:
        raise ConversationContractError("ADDRESSING_HUMAN_HANDOFF_INVALID", "A human handoff cannot schedule a construct reply")
    return {
        "contract": ADDRESSING_CONTRACT,
        "mode": mode,
        "mentionToken": mention_token,
        "addressedName": _bounded_text(value.get("addressedName"), "addressedName", 120),
        "residentConstructId": resident_id,
        "speakerPrincipalId": speaker_id,
        "targetPrincipalId": target_id,
        "targetPrincipalType": target_type,
        "responseMode": response_mode,
        "responseAddresseePrincipalId": response_addressee_id,
        "responseMention": response_mention,
    }


@dataclass
class ConversationThreadService:
    connect: Callable[[], Any] = chatty_body_service._connect
    signing_secret: str | None = None

    def _assert_resolved_principal(self, cur: Any, owner_user_id: str, member: dict[str, Any]) -> None:
        """Fail closed unless a membership principal belongs to the authenticated owner."""
        if member["principalType"] == "human":
            if member["role"] == "owner" and member["principalId"] != str(owner_user_id):
                raise ConversationContractError("HUMAN_PRINCIPAL_INVALID", "Only the authenticated handler may hold the owner role", 403)
            cur.execute(
                "SELECT id FROM ovvaults.users WHERE id::text=%s LIMIT 1",
                (member["principalId"],),
            )
            if not cur.fetchone():
                raise ConversationContractError("PRINCIPAL_NOT_FOUND", "Human principal is not resolved by VVAULT", 404)
            return
        cur.execute(
            """SELECT construct_id
                 FROM ovvaults.vault_files
                WHERE user_id::text=%s AND construct_id=%s
                LIMIT 1""",
            (str(owner_user_id), member["principalId"]),
        )
        if not cur.fetchone():
            raise ConversationContractError("PRINCIPAL_NOT_FOUND", "Construct principal is not resolved for this owner", 404)

    def _thread(self, cur: Any, owner_user_id: str, thread_id: str) -> dict[str, Any]:
        cur.execute(
            """SELECT thread_id,owner_user_id,title,contract_version,membership_revision,
                      created_at,updated_at,provenance
                 FROM ovvaults.conversation_threads
                WHERE owner_user_id=%s AND thread_id=%s""",
            (owner_user_id, thread_id),
        )
        row = cur.fetchone()
        if not row:
            raise ConversationContractError("THREAD_NOT_FOUND", "Thread not found", 404)
        return dict(row)

    def _qa_thread_scope(self, cur: Any, owner_user_id: str, thread_id: str) -> dict[str, Any]:
        """Resolve an existing participant thread or canonical singleton without creating either."""
        try:
            return {**self._thread(cur, owner_user_id, thread_id), "scope_type": "conversation_thread"}
        except ConversationContractError as exc:
            if exc.code != "THREAD_NOT_FOUND":
                raise
        marker = "_chat_with_"
        construct_id = thread_id.split(marker, 1)[0]
        if (
            not construct_id
            or thread_id != f"{construct_id}{marker}{construct_id}"
            or not PRINCIPAL_ID_RE.fullmatch(construct_id)
        ):
            raise ConversationContractError(
                "QA_THREAD_SCOPE_INVALID",
                "QA evidence requires an existing participant thread or exact canonical singleton",
                404,
            )
        cur.execute(
            """SELECT construct_id
                 FROM ovvaults.vault_files
                WHERE user_id=%s AND construct_id=%s
                LIMIT 1""",
            (owner_user_id, construct_id),
        )
        if not cur.fetchone():
            raise ConversationContractError(
                "QA_SINGLETON_CONSTRUCT_NOT_FOUND",
                "The canonical singleton construct is not owned by the authenticated owner",
                404,
            )
        target = chatty_body_service._transcript_target(construct_id)
        cur.execute(
            """SELECT id,source_hash,created_at
                 FROM ovvaults.transcripts
                WHERE user_id=%s
                  AND lower(title)=lower(%s)
                  AND content IS NOT NULL
                  AND content <> ''
                ORDER BY created_at DESC
                LIMIT 1""",
            (owner_user_id, target["storage_path"]),
        )
        transcript = cur.fetchone()
        if not transcript:
            raise ConversationContractError(
                "QA_SINGLETON_TRANSCRIPT_NOT_FOUND",
                "The canonical singleton transcript does not exist for the authenticated owner",
                404,
            )
        return {
            "thread_id": thread_id,
            "owner_user_id": owner_user_id,
            "construct_id": construct_id,
            "transcript_id": str(transcript.get("id") or ""),
            "transcript_revision": str(transcript.get("source_hash") or ""),
            "scope_type": "canonical_singleton",
        }

    def _members(self, cur: Any, owner_user_id: str, thread_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        cur.execute(
            """SELECT principal_id,principal_type,display_name,surface,member_role,avatar_url,active
                 FROM ovvaults.conversation_thread_memberships
                WHERE owner_user_id=%s AND thread_id=%s AND (%s=false OR active=true)
                ORDER BY created_at,principal_id""",
            (owner_user_id, thread_id, active_only),
        )
        return [dict(row) for row in cur.fetchall()]

    def create_thread(self, owner_user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = _bounded_text(payload.get("threadId") or f"thread-{uuid.uuid4()}", "threadId", 160, STABLE_ID_RE)
        title = _bounded_text(payload.get("title"), "title", 200)
        members = payload.get("members")
        if not isinstance(members, list) or not members:
            raise ConversationContractError("MEMBERS_REQUIRED", "At least one member is required")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for member in members:
            if not isinstance(member, dict):
                raise ConversationContractError("INVALID_MEMBER", "Each member must be an object")
            principal_id = _bounded_text(member.get("principalId"), "principalId", 128, PRINCIPAL_ID_RE)
            if principal_id in seen:
                raise ConversationContractError("DUPLICATE_MEMBER", "Thread members must be unique")
            seen.add(principal_id)
            principal_type = str(member.get("principalType") or "").strip()
            role = str(member.get("role") or "participant").strip()
            if principal_type not in {"human", "construct"} or role not in {"owner", "participant", "evaluator", "respondent", "subject"}:
                raise ConversationContractError("INVALID_MEMBER", "Member type or role is invalid")
            normalized.append({
                "principalId": principal_id,
                "principalType": principal_type,
                "displayName": _bounded_text(member.get("displayName"), "displayName", 120),
                "surface": str(member.get("surface") or "").strip()[:64] or None,
                "role": role,
                "avatarUrl": str(member.get("avatarUrl") or "").strip()[:2048] or None,
            })
        owner_principal = str(owner_user_id)
        requested_owner = str(payload.get("ownerPrincipalId") or owner_principal)
        if requested_owner != owner_principal:
            raise ConversationContractError("OWNER_PRINCIPAL_MISMATCH", "Thread owner must be the authenticated VVAULT principal", 403)
        normalized = _ensure_owner_membership(
            normalized,
            owner_principal,
            _bounded_text(payload.get("ownerDisplayName") or "Owner", "ownerDisplayName", 120),
        )

        with self.connect() as conn:
            with conn.cursor() as cur:
                for member in normalized:
                    self._assert_resolved_principal(cur, owner_user_id, member)
                cur.execute(
                    """INSERT INTO ovvaults.conversation_threads
                       (thread_id,owner_user_id,title,contract_version,provenance)
                       VALUES (%s,%s,%s,%s,%s::jsonb)
                       RETURNING thread_id,owner_user_id,title,contract_version,membership_revision,created_at,updated_at,provenance""",
                    (thread_id, owner_user_id, title, THREAD_CONTRACT, _canonical_json(payload.get("provenance") or {})),
                )
                thread = dict(cur.fetchone())
                for member in normalized:
                    cur.execute(
                        """INSERT INTO ovvaults.conversation_thread_memberships
                           (thread_id,owner_user_id,principal_id,principal_type,display_name,surface,member_role,avatar_url,added_by_user_id,provenance)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (thread_id, owner_user_id, member["principalId"], member["principalType"], member["displayName"], member["surface"], member["role"], member["avatarUrl"], owner_user_id, _canonical_json({"source": "authenticated-owner"})),
                    )
            conn.commit()
        return {"contract": THREAD_CONTRACT, **thread, "members": normalized, "authority": "ovvaults"}

    def get_thread(self, owner_user_id: str, thread_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                thread = self._thread(cur, owner_user_id, thread_id)
                members = self._members(cur, owner_user_id, thread_id, active_only=False)
        return {"contract": THREAD_CONTRACT, **thread, "members": [_member_projection(row) for row in members], "authority": "ovvaults"}

    def set_member(self, owner_user_id: str, thread_id: str, member: dict[str, Any], active: bool = True) -> dict[str, Any]:
        principal_id = _bounded_text(member.get("principalId"), "principalId", 128, PRINCIPAL_ID_RE)
        if not active and principal_id == str(member.get("ownerPrincipalId") or owner_user_id):
            raise ConversationContractError("OWNER_REMOVAL_FORBIDDEN", "The owner membership cannot be removed", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                self._thread(cur, owner_user_id, thread_id)
                if active:
                    principal_type = str(member.get("principalType") or "").strip()
                    role = str(member.get("role") or "participant").strip()
                    if principal_type not in {"human", "construct"} or role not in {"participant", "evaluator", "respondent", "subject"}:
                        raise ConversationContractError("INVALID_MEMBER", "Member type or role is invalid")
                    display_name = _bounded_text(member.get("displayName"), "displayName", 120)
                    self._assert_resolved_principal(cur, owner_user_id, {
                        "principalId": principal_id,
                        "principalType": principal_type,
                        "role": role,
                    })
                    cur.execute(
                        """INSERT INTO ovvaults.conversation_thread_memberships
                           (thread_id,owner_user_id,principal_id,principal_type,display_name,surface,member_role,avatar_url,active,added_by_user_id,removed_at,provenance)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,NULL,%s::jsonb)
                           ON CONFLICT (thread_id,principal_id) DO UPDATE SET
                             principal_type=EXCLUDED.principal_type,display_name=EXCLUDED.display_name,
                             surface=EXCLUDED.surface,member_role=EXCLUDED.member_role,avatar_url=EXCLUDED.avatar_url,
                             active=true,removed_at=NULL
                           RETURNING principal_id,principal_type,display_name,surface,member_role,avatar_url,active""",
                        (thread_id, owner_user_id, principal_id, principal_type, display_name, str(member.get("surface") or "").strip()[:64] or None, role, str(member.get("avatarUrl") or "").strip()[:2048] or None, owner_user_id, _canonical_json({"source": "authenticated-owner"})),
                    )
                else:
                    cur.execute(
                        """UPDATE ovvaults.conversation_thread_memberships
                              SET active=false,removed_at=now()
                            WHERE owner_user_id=%s AND thread_id=%s AND principal_id=%s AND member_role <> 'owner'
                            RETURNING principal_id,principal_type,display_name,surface,member_role,avatar_url,active""",
                        (owner_user_id, thread_id, principal_id),
                    )
                row = cur.fetchone()
                if not row:
                    raise ConversationContractError("MEMBER_NOT_FOUND", "Thread member not found", 404)
                cur.execute(
                    """UPDATE ovvaults.conversation_threads
                          SET membership_revision=membership_revision+1,updated_at=now()
                        WHERE owner_user_id=%s AND thread_id=%s
                        RETURNING membership_revision""",
                    (owner_user_id, thread_id),
                )
                revision = int(cur.fetchone()["membership_revision"])
            conn.commit()
        return {"member": _member_projection(dict(row)), "membershipRevision": revision, "authority": "ovvaults"}

    def participant_frame(self, owner_user_id: str, thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        speaker_id = _bounded_text(payload.get("speakerPrincipalId"), "speakerPrincipalId", 128, PRINCIPAL_ID_RE)
        addressee_ids = [_bounded_text(value, "addresseePrincipalId", 128, PRINCIPAL_ID_RE) for value in payload.get("addresseePrincipalIds") or []]
        subject_ids = [_bounded_text(value, "subjectPrincipalId", 128, PRINCIPAL_ID_RE) for value in payload.get("subjectPrincipalIds") or []]
        with self.connect() as conn:
            with conn.cursor() as cur:
                thread = self._thread(cur, owner_user_id, thread_id)
                members = self._members(cur, owner_user_id, thread_id)
        by_id = {row["principal_id"]: row for row in members}
        required = {speaker_id, *addressee_ids, *subject_ids}
        if not required.issubset(by_id):
            raise ConversationContractError("NONMEMBER_PRINCIPAL", "Every speaker, addressee, and subject must be an active member", 403)
        evaluator = (
            by_id[speaker_id].get("member_role") == "evaluator"
            or str(payload.get("role") or "").strip() == "third_party_evaluator"
        )
        on_behalf_of = payload.get("onBehalfOf")
        if evaluator and (speaker_id in subject_ids or speaker_id == str(payload.get("subjectHumanPrincipalId") or "") or on_behalf_of is not None):
            raise ConversationContractError("EVALUATOR_ATTRIBUTION_INVALID", "Third-party evaluator cannot be the subject or speak on the subject's behalf", 403)
        addressing = _normalize_addressing(
            payload.get("addressing"),
            by_id=by_id,
            speaker_id=speaker_id,
            addressee_ids=addressee_ids,
        )
        frame = {
            "contract": PARTICIPANT_FRAME_CONTRACT,
            "threadId": thread_id,
            "handler": {"principalId": str(owner_user_id), "principalType": "human", "authorized": True},
            "speaker": _member_projection(by_id[speaker_id]),
            "addressees": [_member_projection(by_id[value]) for value in addressee_ids],
            "subjects": [_member_projection(by_id[value]) for value in subject_ids],
            "participants": [_member_projection(row) for row in members],
            "role": str(payload.get("role") or "participant"),
            "onBehalfOf": on_behalf_of,
            "membershipRevision": int(thread["membership_revision"]),
            "authority": "ovvaults",
            **({"addressing": addressing} if addressing is not None else {}),
        }
        return {**frame, "signature": sign_payload(frame, self.signing_secret)}

    def verify_ordinary_singleton_frame(
        self,
        owner_user_id: str,
        target_construct_id: str,
        frame_value: Any,
        execution_authorization_value: Any | None = None,
    ) -> dict[str, Any]:
        """Verify a signed human-to-construct singleton frame.

        Graduation callers may additionally bind the frame to one execution
        authorization. Ordinary trusted transcript/work callers rely on the
        same owner, singleton, membership, and null-delegation verification
        without importing graduation authority into the canonical authorship.
        """
        authorization = (
            _verified_graduation_execution_authorization(
                execution_authorization_value
            )
            if execution_authorization_value is not None
            else None
        )
        if not isinstance(frame_value, dict):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_REQUIRED", "A signed participant frame is required"
            )
        frame = dict(frame_value)
        signature = str(frame.pop("signature", ""))
        if not verify_payload(frame, signature, self.signing_secret):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_SIGNATURE_INVALID",
                "Participant frame signature is invalid",
                403,
            )

        owner_id = str(owner_user_id)
        target_id = _bounded_text(
            target_construct_id, "targetConstructId", 128, PRINCIPAL_ID_RE
        )
        expected_thread_id = f"{target_id}_chat_with_{target_id}"
        speaker = frame.get("speaker") if isinstance(frame.get("speaker"), dict) else {}
        handler = frame.get("handler") if isinstance(frame.get("handler"), dict) else {}
        addressees = frame.get("addressees") if isinstance(frame.get("addressees"), list) else []
        participants = frame.get("participants") if isinstance(frame.get("participants"), list) else []
        target = next((
            item for item in addressees
            if isinstance(item, dict) and item.get("principalId") == target_id
        ), None)
        if (
            frame.get("contract") != PARTICIPANT_FRAME_CONTRACT
            or frame.get("authority") != "ovvaults"
            or frame.get("role") != "participant"
            or frame.get("threadId") != expected_thread_id
            or (
                authorization is not None
                and (
                    authorization["threadId"] != expected_thread_id
                    or authorization["constructId"] != target_id
                )
            )
            or frame.get("onBehalfOf") is not None
            or str(handler.get("principalId") or "") != owner_id
            or handler.get("principalType") != "human"
            or handler.get("authorized") is not True
            or str(speaker.get("principalId") or "") != owner_id
            or speaker.get("principalType") != "human"
            or len(addressees) != 1
            or not isinstance(target, dict)
            or target.get("principalType") != "construct"
        ):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_SCOPE_INVALID",
                "Participant frame is not an owner-authored singleton turn",
                403,
            )
        task_identity = authorization["taskIdentity"] if authorization else None
        if task_identity is not None and str(task_identity.get("ownerPrincipalId") or "") != owner_id:
            raise ConversationContractError(
                "GRADUATION_EXECUTION_AUTHORIZATION_SCOPE_INVALID",
                "Graduation execution authorization belongs to another owner",
                403,
            )

        with self.connect() as conn:
            with conn.cursor() as cur:
                thread = self._thread(cur, owner_user_id, expected_thread_id)
                members = self._members(cur, owner_user_id, expected_thread_id)
                transcript_target = chatty_body_service._transcript_target(target_id)
                cur.execute(
                    """SELECT id
                         FROM ovvaults.transcripts
                        WHERE user_id::text=%s
                          AND lower(title)=lower(%s)
                          AND content IS NOT NULL
                        ORDER BY created_at DESC
                        LIMIT 1""",
                    (owner_id, transcript_target["storage_path"]),
                )
                if not cur.fetchone():
                    raise ConversationContractError(
                        "SINGLETON_TRANSCRIPT_NOT_FOUND",
                        "The existing canonical singleton transcript was not found",
                        404,
                    )
        by_id = {str(row["principal_id"]): row for row in members}
        if (
            int(frame.get("membershipRevision") or 0)
            != int(thread["membership_revision"])
            or by_id.get(owner_id, {}).get("principal_type") != "human"
            or by_id.get(target_id, {}).get("principal_type") != "construct"
            or len(participants) != len(by_id)
            or not all(isinstance(item, dict) for item in participants)
            or {str(item.get("principalId") or "") for item in participants if isinstance(item, dict)}
            != set(by_id)
        ):
            raise ConversationContractError(
                "PARTICIPANT_FRAME_MEMBERSHIP_INVALID",
                "Participant frame does not match current singleton membership",
                409,
            )
        for projection in participants:
            row = by_id.get(str(projection.get("principalId") or ""), {})
            if (
                projection.get("principalType") != row.get("principal_type")
                or projection.get("displayName") != row.get("display_name")
                or projection.get("role") != row.get("member_role")
            ):
                raise ConversationContractError(
                    "PARTICIPANT_FRAME_MEMBERSHIP_INVALID",
                    "Participant frame principal projection is stale",
                    409,
                )
        authorship = {
            "contract": "chatty-canonical-authorship/v1",
            "handler": handler,
            "speaker": speaker,
            "target": target,
            "threadId": expected_thread_id,
            "surface": str(speaker.get("surface") or "chatty")[:64],
            "onBehalfOf": None,
            "participantFrame": {**frame, "signature": signature},
            "participantFrameSignature": signature,
            "authority": "ovvaults",
        }
        if authorization is not None:
            authorship.update({
                "authorizedTurnId": authorization["turnId"],
                "graduationExecutionAuthorizationHash": authorization["executionAuthorizationHash"],
            })
        return authorship

    def verify_qa_anchor_pack(self, owner_user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = _bounded_text(payload.get("qaSessionId"), "qaSessionId", 160, STABLE_ID_RE)
        anchors = payload.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ConversationContractError("QA_ANCHORS_REQUIRED", "QA anchors are required")
        verified: list[dict[str, Any]] = []
        with self.connect() as conn:
            with conn.cursor() as cur:
                for anchor in anchors:
                    if not isinstance(anchor, dict) or anchor.get("authority") != "ovvaults.transcripts" or anchor.get("ownerApproved") is not True:
                        raise ConversationContractError("QA_ANCHOR_INVALID", "Each QA anchor must be owner-approved canonical transcript evidence")
                    case_id = _bounded_text(anchor.get("caseId"), "caseId", 160, STABLE_ID_RE)
                    event_id = _bounded_text(anchor.get("eventId"), "eventId", 160, STABLE_ID_RE)
                    excerpt = _bounded_text(anchor.get("excerpt"), "excerpt", 20_000)
                    excerpt_hash = str(anchor.get("excerptHash") or "").lower()
                    if excerpt_hash != _sha256(excerpt):
                        raise ConversationContractError("QA_ANCHOR_HASH_MISMATCH", "QA anchor excerpt hash does not match its excerpt", 409)
                    cur.execute(
                        """SELECT event_id::text AS source_id,content,content_sha256 AS source_hash,
                                  'ovvaults.transcript_events'::text AS source_table
                             FROM ovvaults.transcript_events
                            WHERE owner_user_id::text=%s AND event_id=%s""",
                        (str(owner_user_id), event_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        cur.execute(
                            """SELECT id::text AS source_id,content,source_hash,
                                      'ovvaults.transcripts'::text AS source_table
                                 FROM ovvaults.transcripts
                                WHERE user_id::text=%s AND id::text=%s""",
                            (str(owner_user_id), event_id),
                        )
                        row = cur.fetchone()
                    if not row or excerpt not in str(row.get("content") or ""):
                        raise ConversationContractError("QA_ANCHOR_NOT_FOUND", "Approved excerpt is not present in the owner-scoped canonical transcript event", 404)
                    verified.append({
                        "caseId": case_id,
                        "eventId": event_id,
                        "excerpt": excerpt,
                        "excerptHash": excerpt_hash,
                        "sourceHash": str(row.get("source_hash") or "") or None,
                        "sourceTable": row["source_table"],
                        "authority": "ovvaults.transcripts",
                        "ownerApproved": True,
                    })
        pack = {
            "contract": QA_ANCHOR_PACK_CONTRACT,
            "qaSessionId": session_id,
            "anchors": verified,
            "authority": "ovvaults",
        }
        return {**pack, "signature": sign_payload(pack, self.signing_secret)}

    def append_event(self, owner_user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _bounded_text(payload.get("eventId"), "eventId", 160, STABLE_ID_RE)
        thread_id = _bounded_text(payload.get("threadId"), "threadId", 160, STABLE_ID_RE)
        content = _bounded_text(payload.get("content"), "content", 100_000)
        frame = payload.get("participantFrame")
        if not isinstance(frame, dict) or frame.get("contract") != PARTICIPANT_FRAME_CONTRACT:
            raise ConversationContractError("PARTICIPANT_FRAME_REQUIRED", "Signed participant frame is required")
        signature = str(frame.get("signature") or "")
        unsigned = {key: value for key, value in frame.items() if key != "signature"}
        if not verify_payload(unsigned, signature, self.signing_secret):
            raise ConversationContractError("PARTICIPANT_FRAME_INVALID", "Participant frame signature is invalid", 403)
        if frame.get("threadId") != thread_id or str(frame.get("handler", {}).get("principalId")) != str(owner_user_id):
            raise ConversationContractError("PARTICIPANT_FRAME_SCOPE_MISMATCH", "Participant frame does not belong to this owner and thread", 403)
        speaker = frame.get("speaker") or {}
        participants = [str(item.get("principalId")) for item in frame.get("participants") or []]
        addressees = [str(item.get("principalId")) for item in frame.get("addressees") or []]
        subjects = [str(item.get("principalId")) for item in frame.get("subjects") or []]
        content_hash = _sha256(content)
        idempotency_key = _bounded_text(payload.get("idempotencyKey") or event_id, "idempotencyKey", 160, STABLE_ID_RE)
        provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
        with self.connect() as conn:
            with conn.cursor() as cur:
                thread = self._thread(cur, owner_user_id, thread_id)
                if int(frame.get("membershipRevision") or 0) != int(thread["membership_revision"]):
                    raise ConversationContractError("STALE_MEMBERSHIP", "Participant frame membership revision is stale", 409)
                cur.execute(
                    """INSERT INTO ovvaults.transcript_events
                       (event_id,owner_user_id,thread_id,author_principal_id,author_principal_type,
                        author_verified,
                        addressee_principal_ids,subject_principal_ids,participant_principal_ids,
                        content,content_sha256,surface,provenance,participant_frame,participant_frame_signature,idempotency_key)
                       VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                       ON CONFLICT (owner_user_id,idempotency_key) DO NOTHING
                       RETURNING event_id,created_at""",
                    (event_id, owner_user_id, thread_id, speaker.get("principalId"), speaker.get("principalType"), addressees, subjects, participants, content, content_hash, str(payload.get("surface") or speaker.get("surface") or "chatty"), _canonical_json(provenance), _canonical_json(unsigned), signature, idempotency_key),
                )
                inserted = cur.fetchone()
                if not inserted:
                    cur.execute(
                        """SELECT event_id,content_sha256,participant_frame_signature,created_at
                             FROM ovvaults.transcript_events
                            WHERE owner_user_id=%s AND idempotency_key=%s""",
                        (owner_user_id, idempotency_key),
                    )
                    existing = dict(cur.fetchone())
                    if existing["event_id"] != event_id or existing["content_sha256"] != content_hash or existing["participant_frame_signature"] != signature:
                        raise ConversationContractError("IDEMPOTENCY_CONFLICT", "Idempotency key was already used for different event content", 409)
                    result = {"eventId": existing["event_id"], "createdAt": existing["created_at"], "duplicateSuppressed": True}
                else:
                    result = {"eventId": inserted["event_id"], "createdAt": inserted["created_at"], "duplicateSuppressed": False}
            conn.commit()
        return {"contract": "chatty-transcript-event/v1", **result, "contentSha256": content_hash, "authority": "ovvaults.transcript_events"}

    def list_events(self, owner_user_id: str, thread_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                self._thread(cur, owner_user_id, thread_id)
                cur.execute(
                    """SELECT event_id,thread_id,author_principal_id,author_principal_type,author_verified,
                              addressee_principal_ids,subject_principal_ids,participant_principal_ids,
                              content,content_sha256,surface,provenance,participant_frame,participant_frame_signature,created_at
                         FROM ovvaults.transcript_events
                        WHERE owner_user_id=%s AND thread_id=%s
                        ORDER BY created_at,event_id""",
                    (owner_user_id, thread_id),
                )
                rows = [dict(row) for row in cur.fetchall()]
        return {"contract": "chatty-transcript-projection/v1", "threadId": thread_id, "events": rows, "authority": "ovvaults.transcript_events"}

    def _prepare_qa_evidence(
        self,
        owner_user_id: str,
        payload: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> tuple[dict[str, Any], str, str]:
        session_id = _bounded_text(payload.get("qaSessionId"), "qaSessionId", 160, STABLE_ID_RE)
        thread_id = _bounded_text(payload.get("threadId"), "threadId", 160, STABLE_ID_RE)
        event_type = str(payload.get("eventType") or "").strip()
        if event_type not in QA_EVENT_TYPES:
            raise ConversationContractError("INVALID_QA_EVENT", "QA event type is invalid")
        if event_type in INTERNAL_EXECUTION_QA_EVENT_TYPES and not trusted_internal:
            raise ConversationContractError(
                "INTERNAL_QA_EVIDENCE_FORBIDDEN",
                "Execution lifecycle evidence requires trusted service authentication",
                403,
            )
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict):
            raise ConversationContractError("INVALID_QA_EVIDENCE", "QA evidence must be an object")
        actor_principal_id = _bounded_text(
            payload.get("actorPrincipalId"),
            "actorPrincipalId",
            128,
            PRINCIPAL_ID_RE,
        )
        if event_type in INTERNAL_EXECUTION_QA_EVENT_TYPES:
            if actor_principal_id != str(owner_user_id):
                raise ConversationContractError(
                    "INTERNAL_QA_ACTOR_INVALID",
                    "Execution lifecycle evidence must be authored by the authenticated owner authority",
                    403,
                )
            _verify_internal_execution_evidence(
                event_type,
                evidence,
                owner_user_id=str(owner_user_id),
                thread_id=thread_id,
                signing_secret=self.signing_secret,
            )
            if event_type == "tester_turn_persistence_attempted":
                attempt = evidence.get("persistenceAttempt")
                attempt = attempt if isinstance(attempt, dict) else {}
                if attempt.get("attemptOrdinal") in {3, 4}:
                    recovery_hash = str(attempt.get("persistenceRecoveryHash") or "")
                    with self.connect() as recovery_conn:
                        with recovery_conn.cursor() as recovery_cur:
                            recovery_cur.execute(
                                """SELECT 1
                                     FROM ovvaults.qa_evaluation_events
                                    WHERE owner_user_id=%s AND qa_session_id=%s
                                      AND thread_id=%s
                                      AND event_type='tester_turn_persistence_recovery_authorized'
                                      AND evidence->'persistenceRecovery'->>'persistenceRecoveryHash'=%s
                                      AND evidence->'persistenceRecovery'->>'turnId'=%s
                                    LIMIT 1""",
                                (
                                    str(owner_user_id), session_id, thread_id,
                                    recovery_hash, str(attempt.get("turnId") or ""),
                                ),
                            )
                            if recovery_cur.fetchone() is None:
                                raise ConversationContractError(
                                    "PERSISTENCE_RECOVERY_AUTHORIZATION_REQUIRED",
                                    "Persistence attempt three requires prior canonical recovery authorization",
                                    403,
                                )
        envelope = {
            "contract": QA_EVIDENCE_CONTRACT,
            "qaSessionId": session_id,
            "threadId": thread_id,
            "caseId": str(payload.get("caseId") or "").strip() or None,
            "eventType": event_type,
            "actorPrincipalId": actor_principal_id,
            "evidence": evidence,
        }
        evidence_json = _canonical_json(envelope)
        evidence_hash = _sha256(evidence_json)
        signature = sign_payload(envelope, self.signing_secret)
        return envelope, evidence_hash, signature

    @staticmethod
    def _profile_event(envelope: dict[str, Any]) -> dict[str, Any] | None:
        evidence = envelope.get("evidence")
        legacy_value = (
            evidence.get("profileEvent") if isinstance(evidence, dict) else None
        )
        stage_value = (
            evidence.get("stageProfileEvent")
            if isinstance(evidence, dict)
            else None
        )
        if legacy_value is not None and stage_value is not None:
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation evidence cannot contain two nested profile events",
            )
        value = stage_value if stage_value is not None else legacy_value
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation profile event evidence must be an object",
            )
        legacy_required = {
            "contract", "eventId", "programId", "sequence", "eventType", "payload",
            "payloadSha256", "previousEventSha256", "occurredAt", "eventSha256",
        }
        stage_required = legacy_required | {"profileStreamId"}
        contract = value.get("contract")
        if (
            (stage_value is not None and contract != "chatty-graduation-stage-profile-event/v1")
            or (legacy_value is not None and contract != "chatty-graduation-profile-event/v2")
            or
            (contract == "chatty-graduation-profile-event/v2" and set(value) != legacy_required)
            or (
                contract == "chatty-graduation-stage-profile-event/v1"
                and set(value) != stage_required
            )
            or contract not in {
                "chatty-graduation-profile-event/v2",
                "chatty-graduation-stage-profile-event/v1",
            }
        ):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation profile event evidence is malformed",
            )
        if contract == "chatty-graduation-stage-profile-event/v1":
            _bounded_text(
                value.get("profileStreamId"), "profileStreamId", 160, STABLE_ID_RE
            )
            if value.get("eventType") not in STAGE_PROFILE_EVENT_TYPES:
                raise ConversationContractError(
                    "QA_PROFILE_EVENT_INVALID",
                    "Cumulative stage-profile event type is invalid",
                )
        if value.get("programId") != envelope.get("qaSessionId"):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_SCOPE_MISMATCH",
                "Graduation profile event program does not match the QA session",
                409,
            )
        if value.get("eventType") != envelope.get("eventType"):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_SCOPE_MISMATCH",
                "Graduation profile event type does not match the QA envelope",
                409,
            )
        sequence = value.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation profile event sequence is invalid",
            )
        if not isinstance(value.get("payload"), dict):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation profile event payload must be an object",
            )
        for field in ("payloadSha256", "eventSha256"):
            if not SHA256_RE.fullmatch(str(value.get(field) or "")):
                raise ConversationContractError(
                    "QA_PROFILE_EVENT_INVALID",
                    f"Graduation profile event {field} is invalid",
                )
        payload_hash = _sha256(_canonical_json(value.get("payload")))
        if value.get("payloadSha256") != payload_hash:
            raise ConversationContractError(
                "QA_PROFILE_EVENT_PAYLOAD_HASH_MISMATCH",
                "Graduation profile event payload hash is invalid",
                409,
            )
        event_keys = (
            "contract", "eventId", "programId", "sequence", "eventType", "payload",
            "payloadSha256", "previousEventSha256", "occurredAt",
        ) + (("profileStreamId",) if contract == "chatty-graduation-stage-profile-event/v1" else ())
        event_body = {key: value[key] for key in event_keys}
        if value.get("eventSha256") != _sha256(_canonical_json(event_body)):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_HASH_MISMATCH",
                "Graduation profile event hash is invalid",
                409,
            )
        previous = value.get("previousEventSha256")
        if sequence == 1:
            if previous is not None:
                raise ConversationContractError(
                    "QA_PROFILE_EVENT_INVALID",
                    "Graduation profile genesis must not claim a previous event",
                )
        elif not SHA256_RE.fullmatch(str(previous or "")):
            raise ConversationContractError(
                "QA_PROFILE_EVENT_INVALID",
                "Graduation profile event previous hash is invalid",
            )
        return value

    @staticmethod
    def _profile_stream_identity(profile_event: dict[str, Any]) -> tuple[str, str]:
        """Return the independently fenced nested stream contract and ID.

        Profile-v2 retains its historical one-stream-per-program identity. New
        cumulative stage profiles use a distinct profileStreamId and therefore
        never extend or mutate Lin's closed profile stream.
        """
        contract = str(profile_event.get("contract") or "")
        stream_id = (
            str(profile_event.get("profileStreamId") or "")
            if contract == "chatty-graduation-stage-profile-event/v1"
            else str(profile_event.get("programId") or "")
        )
        return contract, stream_id

    def _verify_stage_profile_opening_inheritance(
        self,
        cur: Any,
        owner_user_id: str,
        session_id: str,
        envelopes: list[dict[str, Any]],
    ) -> None:
        """Bind successor-profile genesis to canonical predecessor evidence.

        A correctly hashed nested event is not, by itself, evidence that the
        predecessor stage actually graduated.  Resolve the current program
        head and immutable program definition from VVAULT's signed outer QA
        ledger while holding the program advisory lock, then compare the
        opening against those authoritative records.  This keeps caller
        supplied digest-shaped values from becoming inheritance authority.
        """
        openings = []
        for envelope in envelopes:
            profile_event = self._profile_event(envelope)
            if (
                isinstance(profile_event, dict)
                and profile_event.get("contract")
                == "chatty-graduation-stage-profile-event/v1"
                and profile_event.get("eventType") == "stage_profile_opened"
            ):
                openings.append((envelope, profile_event))
        if not openings:
            return
        if len(openings) != 1:
            raise ConversationContractError(
                "QA_STAGE_PROFILE_OPENING_INVALID",
                "One QA append may open exactly one cumulative stage profile",
                409,
            )
        envelope, profile_event = openings[0]
        opening = profile_event.get("payload")
        if not isinstance(opening, dict) or opening.get("contract") != (
            "chatty-graduation-stage-profile-opening/v1"
        ):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_OPENING_INVALID",
                "Cumulative stage-profile genesis requires a typed opening",
                409,
            )
        profile = opening.get("profile")
        predecessor = profile.get("predecessor") if isinstance(profile, dict) else None
        profile_program = opening.get("profileProgram")
        if (
            not isinstance(profile, dict)
            or not isinstance(predecessor, dict)
            or not isinstance(profile_program, dict)
        ):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_OPENING_INVALID",
                "Cumulative stage-profile opening scope is incomplete",
                409,
            )

        # The outer program ledger is VVAULT-signed.  Re-verify every row used
        # as inheritance authority instead of trusting JSON already in storage.
        cur.execute(
            """/* stage-profile-inheritance */
               SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                      evidence_sha256,signature,actor_principal_id,created_at
                 FROM ovvaults.qa_evaluation_events
                WHERE owner_user_id=%s AND qa_session_id=%s
                ORDER BY coalesce(qa_event_sequence, 0),created_at,qa_event_id""",
            (owner_user_id, session_id),
        )
        rows = [dict(row) for row in cur.fetchall()]
        verified = [
            verified_qa_evidence_envelope(row, self.signing_secret)
            for row in rows
        ]
        if not verified:
            raise ConversationContractError(
                "QA_STAGE_PROFILE_PREDECESSOR_REQUIRED",
                "Canonical predecessor program evidence is missing",
                409,
            )
        genesis_events = [
            item for item in verified if item.get("eventType") == "graduation_created"
        ]
        genesis = genesis_events[0] if len(genesis_events) == 1 else None
        head = verified[-1]
        program = (
            genesis.get("evidence", {}).get("program")
            if isinstance(genesis, dict)
            else None
        )
        acceptance = (
            head.get("evidence", {}).get("acceptance")
            if head.get("eventType") == "stage_acceptance_recorded"
            else None
        )
        if not isinstance(program, dict) or not isinstance(acceptance, dict):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_PREDECESSOR_REQUIRED",
                "Current canonical program head is not a stage acceptance",
                409,
            )
        acceptance_hash = str(acceptance.get("acceptanceHash") or "")
        profile_state_hash = str(acceptance.get("profileStateReceiptHash") or "")
        head_hash = str(head.get("evidenceSha256") or "")
        if not all(
            SHA256_RE.fullmatch(value)
            for value in (acceptance_hash, profile_state_hash, head_hash)
        ):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_PREDECESSOR_INVALID",
                "Canonical predecessor acceptance evidence is malformed",
                409,
            )
        owner_id = str(owner_user_id)
        tester_task_id = str(program.get("testerTaskId") or "")
        program_id = str(program.get("programId") or "")
        profile_id = str(profile.get("profileId") or "")
        profile_hash = str(profile.get("profileHash") or "")
        stage_id = str(profile.get("stageId") or "")
        predecessor_stage_id = str(predecessor.get("stageId") or "")
        for value, field in (
            (tester_task_id, "testerTaskId"),
            (program_id, "programId"),
            (profile_id, "profileId"),
            (stage_id, "stageId"),
            (predecessor_stage_id, "predecessorStageId"),
        ):
            _bounded_text(value, field, 160, STABLE_ID_RE)
        if not SHA256_RE.fullmatch(profile_hash):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_OPENING_INVALID",
                "Cumulative stage-profile hash is invalid",
                409,
            )
        if (
            program_id != session_id
            or str(program.get("ownerPrincipalId") or "") != owner_id
            or genesis.get("actorPrincipalId") != owner_id
            or head.get("actorPrincipalId") != owner_id
            or acceptance.get("programId") != program_id
            or opening.get("programId") != program_id
            or opening.get("ownerPrincipalId") != owner_id
            or opening.get("testerTaskId") != tester_task_id
            or profile_event.get("programId") != program_id
            or profile_event.get("profileStreamId") != profile_id
            or opening.get("profileHash") != profile_hash
            or profile_program.get("programId") != program_id
            or profile_program.get("ownerPrincipalId") != owner_id
            or profile_program.get("testerTaskId") != tester_task_id
            or predecessor_stage_id != str(acceptance.get("stageId") or "")
            or predecessor.get("stageAcceptanceHash") != acceptance_hash
            or predecessor.get("profileStateReceiptHash") != profile_state_hash
            or opening.get("inheritedStageAcceptanceHash") != acceptance_hash
            or opening.get("inheritedProfileStateReceiptHash") != profile_state_hash
            or opening.get("openedFromProgramHeadSha256") != head_hash
        ):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_INHERITANCE_MISMATCH",
                "Cumulative stage profile does not inherit the canonical program head",
                409,
            )

        companions = {item.get("eventType"): item for item in envelopes}
        advanced = companions.get("stage_advanced", {}).get("evidence")
        opened = companions.get("stage_opened", {}).get("evidence")
        if (
            not isinstance(advanced, dict)
            or not isinstance(opened, dict)
            or advanced.get("previousStageId") != predecessor_stage_id
            or advanced.get("previousAcceptanceHash") != acceptance_hash
            or advanced.get("nextStageId") != stage_id
            or opened.get("stageId") != stage_id
            or opened.get("inheritedStageAcceptanceHashes") != [acceptance_hash]
            or envelope.get("actorPrincipalId") != owner_id
            or companions["stage_advanced"].get("actorPrincipalId") != owner_id
            or companions["stage_opened"].get("actorPrincipalId") != owner_id
        ):
            raise ConversationContractError(
                "QA_STAGE_PROFILE_TRANSITION_MISMATCH",
                "Stage transition does not match the verified predecessor acceptance",
                409,
            )

    def _lock_and_verify_profile_event_head(
        self,
        cur: Any,
        owner_user_id: str,
        session_id: str,
        envelopes: list[dict[str, Any]],
        *,
        lock: bool = True,
    ) -> None:
        """Fence nested profile streams before canonical QA append.

        Migration 0028 orders the outer QA ledger.  Profile-v2 also carries an
        independently hashed sequence inside its signed evidence, so that head
        must be compared while holding the same owner/session advisory lock.
        """
        profile_events = [
            profile_event
            for envelope in envelopes
            if (profile_event := self._profile_event(envelope)) is not None
        ]
        if not profile_events:
            return
        stream_identities = {
            self._profile_stream_identity(profile_event)
            for profile_event in profile_events
        }
        if len(stream_identities) != 1:
            raise ConversationContractError(
                "QA_PROFILE_EVENT_STREAM_MISMATCH",
                "One QA append batch may extend only one graduation profile stream",
                409,
            )
        profile_contract, profile_stream_id = next(iter(stream_identities))
        if lock:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{owner_user_id}\n{session_id}",),
            )
        self._verify_stage_profile_opening_inheritance(
            cur, owner_user_id, session_id, envelopes
        )
        cur.execute(
            """SELECT COALESCE(
                          evidence->'stageProfileEvent', evidence->'profileEvent'
                       ) AS profile_event,
                       qa_event_sequence
                 FROM ovvaults.qa_evaluation_events
                WHERE owner_user_id=%s AND qa_session_id=%s
                  AND COALESCE(
                        evidence->'stageProfileEvent', evidence->'profileEvent'
                      )->>'contract'=%s
                  AND COALESCE(
                        evidence->'stageProfileEvent'->>'profileStreamId',
                        evidence->'profileEvent'->>'programId'
                      )=%s
                  AND (COALESCE(
                        evidence->'stageProfileEvent', evidence->'profileEvent'
                      )->>'sequence')::bigint = (
                    SELECT max((COALESCE(
                           candidate.evidence->'stageProfileEvent',
                           candidate.evidence->'profileEvent'
                         )->>'sequence')::bigint)
                      FROM ovvaults.qa_evaluation_events candidate
                     WHERE candidate.owner_user_id=%s
                       AND candidate.qa_session_id=%s
                       AND COALESCE(
                             candidate.evidence->'stageProfileEvent',
                             candidate.evidence->'profileEvent'
                           )->>'contract'=%s
                       AND COALESCE(
                             candidate.evidence->'stageProfileEvent'->>'profileStreamId',
                             candidate.evidence->'profileEvent'->>'programId'
                           )=%s
                  )
                ORDER BY qa_event_sequence ASC NULLS LAST, created_at ASC, qa_event_id ASC""",
            (
                owner_user_id, session_id, profile_contract, profile_stream_id,
                owner_user_id, session_id, profile_contract, profile_stream_id,
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]
        head = rows[0].get("profile_event") if rows else None
        if len(rows) > 1:
            def stable_readiness(value: dict[str, Any]) -> dict[str, Any]:
                readiness = value.get("payload", {}).get("readiness", {})
                return {
                    "programId": value.get("programId"),
                    "sequence": value.get("sequence"),
                    "eventType": value.get("eventType"),
                    "previousEventSha256": value.get("previousEventSha256"),
                    "profileHash": readiness.get("profileHash"),
                    "stageId": readiness.get("stageId"),
                    "caseId": readiness.get("caseId"),
                    "attempt": readiness.get("attempt"),
                    "readinessHash": readiness.get("readinessHash"),
                    "canonicalEvidenceHash": readiness.get("canonicalEvidenceHash"),
                    "canonicalStateSha256": readiness.get("canonicalStateSha256"),
                    "runtimeEvidenceSha256": readiness.get("runtimeEvidenceSha256"),
                    "checks": readiness.get("checks"),
                }
            if (
                not isinstance(head, dict)
                or head.get("eventType") != "case_readiness_recorded"
                or any(
                    not isinstance(row.get("profile_event"), dict)
                    or stable_readiness(row["profile_event"]) != stable_readiness(head)
                    for row in rows[1:]
                )
            ):
                raise ConversationContractError(
                    "QA_PROFILE_STREAM_INTEGRITY_FAILED",
                    "Canonical graduation profile stream contains conflicting sibling heads",
                    409,
                )
        expected_sequence = int(head.get("sequence")) + 1 if isinstance(head, dict) else 1
        expected_previous = head.get("eventSha256") if isinstance(head, dict) else None
        for profile_event in profile_events:
            if (
                profile_event["sequence"] != expected_sequence
                or profile_event.get("previousEventSha256") != expected_previous
            ):
                raise ConversationContractError(
                    "QA_PROFILE_EVENT_HEAD_CONFLICT",
                    "Graduation profile event does not extend the canonical profile head",
                    409,
                )
            expected_sequence += 1
            expected_previous = profile_event["eventSha256"]

    def append_qa_evidence(
        self,
        owner_user_id: str,
        payload: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        envelope, evidence_hash, signature = self._prepare_qa_evidence(
            owner_user_id,
            payload,
            trusted_internal=trusted_internal,
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                self._qa_thread_scope(cur, owner_user_id, envelope["threadId"])
                self._lock_and_verify_profile_event_head(
                    cur,
                    owner_user_id,
                    envelope["qaSessionId"],
                    [envelope],
                )
                cur.execute(
                    """INSERT INTO ovvaults.qa_evaluation_events
                       (owner_user_id,qa_session_id,thread_id,case_id,event_type,evidence,evidence_sha256,signature,actor_principal_id)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                       RETURNING qa_event_id,created_at""",
                    (owner_user_id, envelope["qaSessionId"], envelope["threadId"], envelope["caseId"], envelope["eventType"], _canonical_json(envelope["evidence"]), evidence_hash, signature, envelope["actorPrincipalId"]),
                )
                row = dict(cur.fetchone())
            conn.commit()
        return {**envelope, "qaEventId": str(row["qa_event_id"]), "createdAt": row["created_at"], "evidenceSha256": evidence_hash, "signature": signature, "authority": "ovvaults.qa_evaluation_events"}

    def append_qa_evidence_batch(
        self,
        owner_user_id: str,
        payload: dict[str, Any],
        *,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        """Validate then atomically append an ordered, replay-safe QA evidence batch."""
        if not isinstance(payload, dict) or set(payload) != {
            "contract", "qaSessionId", "batchId", "threadId", "events"
        }:
            raise ConversationContractError(
                "INVALID_QA_EVIDENCE_BATCH",
                "QA evidence batch fields are invalid",
            )
        if payload.get("contract") != QA_EVIDENCE_BATCH_CONTRACT:
            raise ConversationContractError(
                "INVALID_QA_EVIDENCE_BATCH_CONTRACT",
                "QA evidence batch contract is invalid",
            )
        session_id = _bounded_text(payload.get("qaSessionId"), "qaSessionId", 160, STABLE_ID_RE)
        batch_id = _bounded_text(payload.get("batchId"), "batchId", 160, STABLE_ID_RE)
        thread_id = _bounded_text(payload.get("threadId"), "threadId", 160, STABLE_ID_RE)
        event_payloads = payload.get("events")
        if not isinstance(event_payloads, list) or not 1 <= len(event_payloads) <= 32:
            raise ConversationContractError(
                "INVALID_QA_EVIDENCE_BATCH",
                "QA evidence batch must contain between one and 32 events",
            )

        prepared: list[tuple[dict[str, Any], str, str, str]] = []
        for index, event in enumerate(event_payloads):
            if not isinstance(event, dict) or set(event) != {
                "caseId", "eventType", "actorPrincipalId", "evidence"
            }:
                raise ConversationContractError(
                    "INVALID_QA_EVIDENCE_BATCH_EVENT",
                    f"QA evidence batch event {index} fields are invalid",
                )
            envelope, evidence_hash, signature = self._prepare_qa_evidence(
                owner_user_id,
                {
                    **event,
                    "qaSessionId": session_id,
                    "threadId": thread_id,
                },
                trusted_internal=trusted_internal,
            )
            event_uuid = str(uuid.uuid5(
                QA_BATCH_UUID_NAMESPACE,
                f"{owner_user_id}\n{session_id}\n{batch_id}\n{index}",
            ))
            prepared.append((envelope, evidence_hash, signature, event_uuid))

        batch_input = {
            "contract": QA_EVIDENCE_BATCH_CONTRACT,
            "qaSessionId": session_id,
            "threadId": thread_id,
            "batchId": batch_id,
            "events": [item[0] for item in prepared],
        }
        batch_hash = _sha256(_canonical_json(batch_input))
        event_ids = [item[3] for item in prepared]
        rows_by_id: dict[str, dict[str, Any]] = {}
        duplicate_suppressed = False

        with self.connect() as conn:
            with conn.cursor() as cur:
                self._qa_thread_scope(cur, owner_user_id, thread_id)
                profile_envelopes = [
                    item[0] for item in prepared
                    if self._profile_event(item[0]) is not None
                ]
                if profile_envelopes:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{owner_user_id}\n{session_id}",),
                    )
                cur.execute(
                    """SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                              evidence_sha256,signature,actor_principal_id,created_at
                         FROM ovvaults.qa_evaluation_events
                        WHERE owner_user_id=%s AND qa_event_id = ANY(%s::uuid[])
                        FOR UPDATE""",
                    (owner_user_id, event_ids),
                )
                existing = [dict(row) for row in cur.fetchall()]
                if existing:
                    if len(existing) != len(prepared):
                        raise ConversationContractError(
                            "QA_EVIDENCE_BATCH_PARTIAL_REPLAY",
                            "QA evidence batch is partially committed",
                            409,
                        )
                    existing_by_id = {str(row["qa_event_id"]): row for row in existing}
                    for envelope, evidence_hash, signature, event_uuid in prepared:
                        row = existing_by_id.get(event_uuid)
                        if (
                            row is None
                            or str(row.get("qa_session_id") or "") != session_id
                            or str(row.get("thread_id") or "") != thread_id
                            or str(row.get("evidence_sha256") or "") != evidence_hash
                            or str(row.get("signature") or "") != signature
                        ):
                            raise ConversationContractError(
                                "QA_EVIDENCE_BATCH_IDEMPOTENCY_CONFLICT",
                                "QA evidence batch ID was already used for different evidence",
                                409,
                            )
                        verified_qa_evidence_envelope(row, self.signing_secret)
                    rows_by_id = existing_by_id
                    duplicate_suppressed = True
                else:
                    self._lock_and_verify_profile_event_head(
                        cur,
                        owner_user_id,
                        session_id,
                        [item[0] for item in prepared],
                        lock=False,
                    )
                    for envelope, evidence_hash, signature, event_uuid in prepared:
                        cur.execute(
                            """INSERT INTO ovvaults.qa_evaluation_events
                               (qa_event_id,owner_user_id,qa_session_id,thread_id,case_id,event_type,
                                evidence,evidence_sha256,signature,actor_principal_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                               RETURNING qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                                         evidence_sha256,signature,actor_principal_id,created_at""",
                            (
                                event_uuid, owner_user_id, session_id, thread_id,
                                envelope["caseId"], envelope["eventType"],
                                _canonical_json(envelope["evidence"]), evidence_hash,
                                signature, envelope["actorPrincipalId"],
                            ),
                        )
                        row = dict(cur.fetchone())
                        rows_by_id[event_uuid] = row
            conn.commit()

        events = []
        for _envelope, _evidence_hash, _signature, event_uuid in prepared:
            verified = verified_qa_evidence_envelope(rows_by_id[event_uuid], self.signing_secret)
            events.append({
                **verified,
                "authority": "ovvaults.qa_evaluation_events",
            })
        batch_receipt = {
            "contract": QA_EVIDENCE_BATCH_CONTRACT,
            "qaSessionId": session_id,
            "threadId": thread_id,
            "batchId": batch_id,
            "batchSha256": batch_hash,
            "eventReceipts": [{
                "qaEventId": event["qaEventId"],
                "evidenceSha256": event["evidenceSha256"],
            } for event in events],
        }
        return {
            **batch_receipt,
            "batchSignature": sign_payload(batch_receipt, self.signing_secret),
            "events": events,
            "eventCount": len(events),
            "duplicateSuppressed": duplicate_suppressed,
            "integrityVerified": True,
            "authority": "ovvaults.qa_evaluation_events",
        }

    def list_qa_evidence(self, owner_user_id: str, qa_session_id: str) -> dict[str, Any]:
        session_id = _bounded_text(
            qa_session_id, "qaSessionId", 160, STABLE_ID_RE
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT qa_event_id,qa_session_id,thread_id,case_id,event_type,evidence,
                              evidence_sha256,signature,actor_principal_id,created_at
                        FROM ovvaults.qa_evaluation_events
                        WHERE owner_user_id=%s AND qa_session_id=%s
                        ORDER BY coalesce(qa_event_sequence, 0),created_at,qa_event_id""",
                    (owner_user_id, session_id),
                )
                rows = [
                    verified_qa_evidence_envelope(dict(row), self.signing_secret)
                    for row in cur.fetchall()
                ]
        return {
            "contract": QA_EVIDENCE_CONTRACT,
            "qaSessionId": session_id,
            "events": rows,
            "authority": "ovvaults.qa_evaluation_events",
        }


conversation_thread_service = ConversationThreadService()
