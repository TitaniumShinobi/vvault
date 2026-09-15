"""Durable, owner-qualified execution evidence for Chatty Core.

This service never executes a tool.  It verifies signed Core/owner/host
evidence, assigns database time, signs an append-only execution chain, and
issues fenced leases/start permits.  Unknown outcomes remain unknown until a
signed readback and Core-authorized recovery selection resolve them.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vvault.server import canonical_projection_signing, chatty_body_service, conversation_thread_service
from vvault.server.construct_work_loop_service import (
    ConstructWorkLoopError,
    _CONTEXT_FIELDS as _WORK_CONTEXT_FIELDS,
    _STATE_RECEIPT_FIELDS as _WORK_STATE_RECEIPT_FIELDS,
    _assert_no_private_reasoning,
    construct_work_loop_service as canonical_work_loop_service,
)


EXECUTION_PROGRAM = "chatty-execution-program/v1"
WORK_EXECUTION_INTENT = "chatty-work-execution-intent/v1"
EXECUTION_CAPABILITY_MANIFEST = "chatty-execution-capability-manifest/v1"
WORK_EXECUTION_PROPOSAL_BINDING = "chatty-work-execution-proposal-binding/v1"
EVENT_AUTHORIZATION = "chatty-execution-event-authorization/v1"
APPROVAL_CAPABILITY = "chatty-execution-approval-capability/v1"
APPROVAL_DISCLOSURE = "chatty-execution-approval-disclosure/v1"
APPROVAL_DISCLOSURE_ENVELOPE = "life-vvault-execution-approval-disclosure-envelope/v1"
EXECUTION_LEASE = "chatty-execution-lease/v1"
START_PERMIT = "chatty-execution-start-permit/v1"
HOST_RECEIPT = "chatty-execution-host-receipt/v1"
EFFECT_DISPATCH_MARKER = "chatty-execution-effect-dispatch-marker/v1"
EXECUTION_READBACK = "chatty-execution-readback/v1"
EXECUTION_RECOVERY = "chatty-execution-recovery/v1"
RECOVERY_CAPABILITY = "chatty-execution-recovery-capability/v1"
PROVIDER_FALLBACK_CAPABILITY = "chatty-execution-provider-fallback-capability/v1"
EXECUTION_EVIDENCE = "chatty-execution-evidence-reference/v1"
EVENT_ENVELOPE = "life-vvault-execution-event-envelope/v1"
EXECUTION_PROJECTION = "life-vvault-execution-projection/v1"
EXECUTION_CONTEXT_PROJECTION = "life-vvault-execution-context-projection/v1"
ARGUMENT_RESOLUTION = "life-vvault-execution-argument-resolution/v1"
RESULT_ARTIFACT = "chatty-execution-result-artifact/v1"
RESULT_RESOLUTION = "life-vvault-execution-result-resolution/v1"
EXECUTION_RECOVERY_QUEUE = "life-vvault-execution-recovery-queue/v1"
EXECUTION_PREFLIGHT = "life-vvault-execution-preflight-inspection/v1"
OWNER_CONTROL_ATTESTATION = "life-vvault-execution-owner-control-attestation/v1"
FINALIZATION_AUTHORIZATION = "chatty-execution-work-finalization-authorization/v1"
FINALIZATION_BATCH = "chatty-execution-work-finalization-batch/v1"
FINALIZATION_RECEIPT = "life-vvault-execution-work-finalization-receipt/v1"
WORK_EXECUTION_RECOVERY_ARTIFACT = "chatty-work-execution-recovery-artifact/v1"
WORK_EXECUTION_RECOVERY_ARTIFACT_REFERENCE = "chatty-work-execution-recovery-artifact-reference/v1"
WORK_EXECUTION_RECOVERY_ARTIFACT_ENVELOPE = "life-vvault-work-execution-recovery-artifact-envelope/v1"
WORK_EXECUTION_RECOVERY_ARTIFACTS_ENVELOPE = "life-vvault-work-execution-recovery-artifacts-envelope/v1"
HYDRO_EXECUTION_ASSIGNMENT = "chatty-hydro-execution-assignment/v1"
HYDRO_EXECUTION_GRAPH_SOURCE = "chatty-hydro-execution-graph-source/v1"
HYDRO_EXECUTION_GRAPH = "life-vvault-hydro-execution-graph/v1"
HYDRO_EXECUTION_SCOPE = "chatty-hydro-execution-scope/v1"
HYDRO_EXECUTION_GRAPH_BINDING = "chatty-hydro-execution-graph-binding/v1"
HYDRO_RECOVERY_PROJECTION = "life-vvault-hydro-recovery-projection/v1"
HYDRO_SYNTHESIS_INPUT_RESOLUTION = "life-vvault-hydro-synthesis-input-resolution/v1"
HYDRO_SYNTHESIS_RESULT_CONTENT_RESOLUTION = "life-vvault-hydro-synthesis-result-content-resolution/v1"
HYDRO_WORKER_REQUEST = "chatty-hydro-worker-request/v1"
HYDRO_WORKER_REQUEST_AUTHORIZATION = "chatty-hydro-worker-request-authorization/v1"
HYDRO_WORKER_REQUEST_REFERENCE = "life-vvault-hydro-worker-request-reference/v1"
HYDRO_WORKER_INFERENCE_ARGUMENTS = "chatty-hydro-worker-inference-arguments/v1"
AUTHORITY = "vvault/ovvaults"

EVENT_TYPES = frozenset({
    "execution_requested", "approval_capability_issued", "execution_authorized",
    "execution_hydro_synthesis_inputs_resolved",
    "execution_lease_acquired", "execution_lease_renewed", "execution_attempt_started", "execution_effect_dispatched",
    "execution_attempt_outcome_recorded", "execution_readback_recorded",
    "execution_recovery_selected", "execution_step_verified", "execution_step_completed",
    "execution_step_failed", "execution_outcome_unknown", "execution_cancel_requested",
    "execution_cancel_acknowledged", "execution_completed", "execution_failed", "execution_rejected",
})
OPERATIONS = frozenset({
    "workspace.file.read", "workspace.patch.apply", "workspace.command.execute",
    "network.https.fetch", "provider.generate", "hydro.graph.dispatch",
    "artifact.readback.verify",
})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_MAX_EVENT_BYTES = 64 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_AUTH_SECONDS = 300
_MAX_KEY_FILE_BYTES = 256 * 1024


def _read_trusted_config_file(path_value: str, *, error_prefix: str) -> tuple[str | None, str | None]:
    path = Path(str(path_value or ""))
    try:
        if not path.is_absolute():
            raise ValueError("path is not absolute")
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise ValueError("path is not a regular file")
        if metadata.st_uid not in {0, os.getuid()} or metadata.st_mode & 0o022 \
                or not metadata.st_mode & stat.S_IRUSR:
            raise ValueError("file ownership or mode is unsafe")
        if metadata.st_size < 1 or metadata.st_size > _MAX_KEY_FILE_BYTES:
            raise ValueError("file size is invalid")
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError, ValueError):
        return None, f"{error_prefix}_FILE_INVALID"


def _execution_authorization_public_key_config() -> tuple[str | None, str | None, str]:
    inline = os.getenv("CHATTY_EXECUTION_AUTHORIZATION_PUBLIC_KEY_PEM", "").strip()
    file_path = os.getenv("CHATTY_EXECUTION_AUTHORIZATION_PUBLIC_KEY_FILE", "").strip()
    if inline and file_path:
        return None, "EXECUTION_AUTHORIZATION_KEY_SOURCE_CONFLICT", "conflict"
    if file_path:
        content, error = _read_trusted_config_file(file_path, error_prefix="EXECUTION_AUTHORIZATION_KEY")
        return content, error, "file"
    if inline:
        return inline, None, "inline"
    return None, "EXECUTION_AUTHORIZATION_KEY_UNCONFIGURED", "none"


def _host_key_registry_config(raw: str | None) -> tuple[str, str | None, str]:
    if raw is not None:
        return raw, None, "argument"
    inline = os.getenv("CHATTY_WORK_EXECUTION_HOST_KEYS_JSON", "").strip()
    file_path = os.getenv("CHATTY_WORK_EXECUTION_HOST_KEYS_FILE", "").strip()
    if inline and file_path:
        return "", "EXECUTION_HOST_KEY_REGISTRY_SOURCE_CONFLICT", "conflict"
    if file_path:
        content, error = _read_trusted_config_file(file_path, error_prefix="EXECUTION_HOST_KEY_REGISTRY")
        return content or "", error, "file"
    if inline:
        return inline, None, "inline"
    return "", "EXECUTION_HOST_KEY_REGISTRY_UNCONFIGURED", "none"


def _load_host_key_registry(
    raw: str | None = None, *, reserved_core_key_id: str | None = None,
) -> tuple[dict[str, tuple[str, str]], str | None]:
    encoded, source_error, _source = _host_key_registry_config(raw)
    if source_error:
        return {}, source_error
    core_key_id = (reserved_core_key_id if reserved_core_key_id is not None
                   else (os.getenv("CHATTY_EXECUTION_AUTHORIZATION_KEY_ID") if raw is None else None))
    if not encoded.strip():
        return {}, "EXECUTION_HOST_KEY_REGISTRY_UNCONFIGURED"
    try:
        document = json.loads(encoded)
        if not isinstance(document, dict) or len(document) > 64:
            raise ValueError("registry shape")
        result: dict[str, tuple[str, str]] = {}
        for host_id, entry in document.items():
            _id(host_id, "hostId")
            if not isinstance(entry, dict) or set(entry) != {"keyId", "publicKeyPem"}:
                raise ValueError("registry entry")
            _key, derived = _public_key(entry["publicKeyPem"], entry["keyId"])
            if core_key_id and derived == core_key_id:
                raise ConstructExecutionError(
                    "EXECUTION_KEY_DOMAIN_COLLISION",
                    "execution host key must be distinct from the Core authorization key",
                    503,
                )
            result[host_id] = (entry["publicKeyPem"], derived)
        return result, None
    except ConstructExecutionError as exc:
        return {}, exc.code if exc.code == "EXECUTION_KEY_DOMAIN_COLLISION" else "EXECUTION_HOST_KEY_REGISTRY_INVALID"
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}, "EXECUTION_HOST_KEY_REGISTRY_INVALID"


def host_key_registry_readiness() -> dict[str, Any]:
    _encoded, _source_error, source = _host_key_registry_config(None)
    return {"configured": bool(_HOST_KEY_REGISTRY), "valid": HOST_KEY_REGISTRY_ERROR is None,
            "domainSeparated": HOST_KEY_REGISTRY_ERROR != "EXECUTION_KEY_DOMAIN_COLLISION",
            "source": source,
            "hostCount": len(_HOST_KEY_REGISTRY), "hostKeyIds": sorted(key_id for _pem, key_id in _HOST_KEY_REGISTRY.values()),
            "errorCode": HOST_KEY_REGISTRY_ERROR}


def execution_authorization_key_readiness() -> dict[str, Any]:
    """Expose key identity only; never expose the trusted Core public key bytes."""
    public_key_pem, source_error, source = _execution_authorization_public_key_config()
    configured_key_id = os.getenv("CHATTY_EXECUTION_AUTHORIZATION_KEY_ID")
    configured = bool(public_key_pem and configured_key_id)
    derived_key_id = None
    error_code = None
    if source_error and source_error != "EXECUTION_AUTHORIZATION_KEY_UNCONFIGURED":
        error_code = source_error
    elif configured:
        try:
            _key, derived_key_id = _public_key(public_key_pem, configured_key_id)
            if derived_key_id in {key_id for _pem, key_id in _HOST_KEY_REGISTRY.values()}:
                error_code = "EXECUTION_KEY_DOMAIN_COLLISION"
        except ConstructExecutionError as exc:
            error_code = exc.code
    else:
        error_code = "EXECUTION_AUTHORIZATION_KEY_UNCONFIGURED"
    return {
        "configured": configured,
        "valid": configured and error_code is None,
        "domainSeparated": error_code != "EXECUTION_KEY_DOMAIN_COLLISION",
        "source": source,
        "keyId": derived_key_id,
        "errorCode": error_code,
    }


def _configured_host_key(host_id: str) -> tuple[str, str] | None:
    return _HOST_KEY_REGISTRY.get(str(host_id or ""))


class ConstructExecutionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _bytes(value: Any) -> bytes:
    return canonical_projection_signing.canonical_json_bytes(value)


def _sha(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _exact(value: Any, fields: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ConstructExecutionError("EXECUTION_CONTRACT_INVALID", f"{name} fields are invalid")
    _assert_no_private_reasoning(value)
    return value


def _id(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _ID.fullmatch(result):
        raise ConstructExecutionError("EXECUTION_ID_INVALID", f"{field} is invalid")
    return result


def _digest(value: Any, field: str) -> str:
    result = str(value or "")
    if not _SHA.fullmatch(result):
        raise ConstructExecutionError("EXECUTION_DIGEST_INVALID", f"{field} is invalid")
    return result


def _provider_result_matches_draft(content: Any, receipt: dict[str, Any]) -> bool:
    if not isinstance(content, dict) or content.get("contract") != "chatty-provider-generation-host-result/v1":
        return False
    output = content.get("output")
    declared = content.get("outputSha256")
    if not isinstance(output, str) or not _SHA.fullmatch(str(declared or "")):
        return False
    raw_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
    return raw_sha256 == declared == receipt.get("providerDraftSha256")


def _hydro_worker_result_matches_receipt(content: Any, receipt: dict[str, Any]) -> bool:
    fields = {
        "contract", "resultId", "requestId", "requestHash", "graphId", "assignmentId",
        "assignmentHash", "assignmentKind", "workerPrincipalId", "executionId", "stepId",
        "attemptOrdinal", "providerRouteId", "provider", "model", "providerCallCount", "output",
        "outputSha256", "outputBytes", "providerDraftSha256", "transcriptPersisted",
        "semanticRetryCount", "completedAt", "resultHash",
    }
    if not isinstance(content, dict) or set(content) != fields \
            or content.get("contract") != "chatty-hydro-worker-result/v1":
        return False
    output = content.get("output")
    if not isinstance(output, str) or not output or "\x00" in output:
        return False
    raw_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
    body = {key: value for key, value in content.items() if key != "resultHash"}
    assignment_kind = (receipt.get("hydroScope") or {}).get("assignmentKind")
    receipt_draft_hash = raw_hash if assignment_kind == "synthesis" else None
    return all((
        content.get("resultHash") == _sha(body),
        content.get("outputSha256") == raw_hash,
        content.get("providerDraftSha256") == raw_hash,
        content.get("assignmentKind") == assignment_kind,
        receipt.get("providerDraftSha256") == receipt_draft_hash,
        content.get("outputBytes") == len(output.encode("utf-8")),
        content.get("providerCallCount") == 1,
        content.get("transcriptPersisted") is False,
        content.get("semanticRetryCount") == 0,
        content.get("requestHash") == receipt.get("hydroWorkerRequestSha256"),
        content.get("graphId") == (receipt.get("hydroScope") or {}).get("graphId"),
        content.get("assignmentHash") == (receipt.get("hydroScope") or {}).get("assignmentHash"),
        content.get("assignmentId") == receipt.get("stepId"),
        content.get("stepId") == receipt.get("stepId"),
        content.get("executionId") == receipt.get("executionId"),
        content.get("attemptOrdinal") == receipt.get("attemptOrdinal"),
        content.get("provider") == receipt.get("providerId"),
        content.get("workerPrincipalId") == receipt.get("responsibleConstructId"),
    ))


def _time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConstructExecutionError("EXECUTION_TIMESTAMP_INVALID", f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ConstructExecutionError("EXECUTION_TIMESTAMP_INVALID", f"{field} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_key(pem: str | None, expected_key_id: str | None) -> tuple[Ed25519PublicKey, str]:
    raw = (pem or "").strip()
    if not raw:
        raise ConstructExecutionError("EXECUTION_VERIFICATION_KEY_UNAVAILABLE", "verification key unavailable", 503)
    try:
        key = serialization.load_pem_public_key(raw.encode())
    except (TypeError, ValueError) as exc:
        raise ConstructExecutionError("EXECUTION_VERIFICATION_KEY_INVALID", "verification key invalid", 503) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ConstructExecutionError("EXECUTION_VERIFICATION_KEY_INVALID", "verification key is not Ed25519", 503)
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(der).hexdigest()
    if expected_key_id and key_id != expected_key_id:
        raise ConstructExecutionError("EXECUTION_VERIFICATION_KEY_MISMATCH", "verification key identity mismatch", 503)
    return key, key_id


_HOST_KEY_REGISTRY, HOST_KEY_REGISTRY_ERROR = _load_host_key_registry()


def _verify_signed(
    value: Any, *, fields: frozenset[str], contract: str, public_key_pem: str | None,
    expected_key_id: str | None, now: datetime | None = None, algorithm: str = "Ed25519",
) -> dict[str, Any]:
    document = _exact(value, fields, contract)
    if document.get("contract") != contract or document.get("algorithm") != algorithm:
        raise ConstructExecutionError("EXECUTION_SIGNED_CONTRACT_INVALID", f"{contract} is invalid", 403)
    body = {k: v for k, v in document.items() if k not in {"payloadSha256", "algorithm", "keyId", "signature"}}
    if _digest(document.get("payloadSha256"), "payloadSha256") != _sha(body):
        raise ConstructExecutionError("EXECUTION_SIGNED_HASH_INVALID", f"{contract} hash is invalid", 403)
    if now is not None and "expiresAt" in body:
        issued, expires = _time(body.get("issuedAt"), "issuedAt"), _time(body.get("expiresAt"), "expiresAt")
        if expires <= issued or now < issued - timedelta(seconds=30) or now >= expires:
            raise ConstructExecutionError("EXECUTION_SIGNED_EVIDENCE_EXPIRED", f"{contract} expired", 403)
    key, key_id = _public_key(public_key_pem, expected_key_id)
    if document.get("keyId") != key_id:
        raise ConstructExecutionError("EXECUTION_SIGNED_KEY_INVALID", f"{contract} key is not trusted", 403)
    try:
        key.verify(base64.b64decode(str(document.get("signature") or ""), validate=True), _bytes(body))
    except (InvalidSignature, ValueError) as exc:
        raise ConstructExecutionError("EXECUTION_SIGNED_SIGNATURE_INVALID", f"{contract} signature is invalid", 403) from exc
    return document


def _hydro_signed_fields(value: Any, fields: frozenset[str]) -> frozenset[str]:
    result = fields
    if isinstance(value, dict) and "hydroScope" in value:
        _validate_hydro_scope(value.get("hydroScope"))
        result = result | {"hydroScope"}
    if isinstance(value, dict) and "hydroSynthesisInputResolutionSha256" in value:
        _digest(value.get("hydroSynthesisInputResolutionSha256"), "hydroSynthesisInputResolutionSha256")
        result = result | {"hydroSynthesisInputResolutionSha256"}
    if isinstance(value, dict) and "hydroWorkerRequestSha256" in value:
        _digest(value.get("hydroWorkerRequestSha256"), "hydroWorkerRequestSha256")
        result = result | {"hydroWorkerRequestSha256"}
    return result


def _host_signed_fields(value: Any) -> frozenset[str]:
    result = _hydro_signed_fields(value, _HOST_FIELDS)
    if isinstance(value, dict) and "failureCode" in value:
        if value.get("failureCode") not in {
            "WORK_EXECUTION_CANCELLED",
            "WORK_EXECUTION_START_PERMIT_EXPIRED",
            "WORK_EXECUTION_ARGUMENTS_UNAVAILABLE",
            "WORK_EXECUTION_RESOURCE_SCOPE_REJECTED",
            "WORK_EXECUTION_PREIMAGE_MISMATCH",
            "WORK_EXECUTION_POSTIMAGE_MISMATCH",
            "WORK_EXECUTION_EFFECT_FAILED",
            "WORK_EXECUTION_OUTCOME_UNKNOWN",
        }:
            raise ConstructExecutionError(
                "EXECUTION_HOST_FAILURE_CODE_INVALID", "host failure code invalid", 409
            )
        result = result | {"failureCode"}
    return result


_AUTH_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "ownerPrincipalId", "executionId",
    "programId", "itemId", "sourceConstructId", "responsibleConstructId", "threadId", "sessionId", "branchId",
    "expectedSequence", "expectedHeadEventId", "expectedHeadSha256", "eventType",
    "eventPayloadSha256", "idempotencyKey", "issuedAt", "expiresAt", "payloadSha256",
    "algorithm", "keyId", "signature",
})
_APPROVAL_FIELDS = frozenset({
    "contract", "approvalId", "authority", "ownerPrincipalId", "executionId", "programId",
    "itemId", "definitionHash", "workExecutionIntentHash", "approvalDisclosureSha256",
    "approvedStepIds", "riskCeiling", "maxAttemptsPerStep",
    "oneUse", "issuedAt", "expiresAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_APPROVAL_DISCLOSURE_FIELDS = frozenset({
    "contract", "executionId", "programId", "itemId", "definitionHash",
    "workExecutionIntentHash", "steps", "approvalDisclosureSha256",
})
_APPROVAL_DISCLOSURE_STEP_FIELDS = frozenset({
    "stepId", "ordinal", "operation", "required", "argumentsArtifactId",
    "argumentsSha256", "resourceKeys", "risk", "providerCandidates",
    "idempotencyMode", "readbackMode", "timeoutMs", "maxOutputBytes", "actionDisclosure",
})
_APPROVAL_DISCLOSURE_ENVELOPE_FIELDS = frozenset({
    "contract", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "expectedHeadEventId", "expectedHeadSha256", "approvalDisclosure",
    "approvalDisclosureSha256", "canonicalArgumentsVerified", "containsRawArguments",
    "containsCredentials", "containsPrivateReasoning", "issuedAt", "expiresAt",
    "payloadSha256", "algorithm", "keyId", "signature",
})
_LEASE_FIELDS = frozenset({
    "contract", "leaseId", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "stepId", "attemptOrdinal", "providerId", "hostId", "responsibleConstructId", "resourceKeys",
    "expectedHeadEventId", "expectedHeadSha256", "idempotencyKey", "renewalOrdinal", "issuedAt",
    "expiresAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_PERMIT_FIELDS = frozenset({
    "contract", "permitId", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "stepId", "attemptOrdinal", "providerId", "hostId", "responsibleConstructId", "leasePayloadSha256",
    "approvalPayloadSha256", "stepHash", "argumentsSha256", "idempotencyKey",
    "preStartHeadEventId", "preStartHeadSha256", "issuedAt", "expiresAt", "payloadSha256",
    "algorithm", "keyId", "signature",
})
_HOST_FIELDS = frozenset({
    "contract", "receiptId", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "stepId", "attemptOrdinal", "providerId", "hostId", "responsibleConstructId", "permitPayloadSha256",
    "operation", "stepHash", "argumentsSha256", "idempotencyKey", "dispatchMarkerPayloadSha256",
    "outcome", "effectCommitted", "outputArtifacts",
    "outputSha256", "providerDraftSha256", "invocationCount", "startedAt", "completedAt",
    "payloadSha256", "algorithm", "keyId", "signature",
})
_DISPATCH_MARKER_FIELDS = frozenset({
    "contract", "dispatchId", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "stepId", "attemptOrdinal", "operation", "providerId", "hostId", "responsibleConstructId",
    "permitPayloadSha256", "stepHash", "argumentsSha256", "idempotencyKey",
    "preDispatchHeadEventId", "preDispatchHeadSha256", "oneUse", "dispatchedAt",
    "payloadSha256", "algorithm", "keyId", "signature",
})
_HYDRO_GRAPH_SIGNED_FIELDS = frozenset({
    "contract", "authority", "graphId", "executionId", "ownerPrincipalId", "programId",
    "itemId", "sourceConstructId", "parentResponsibleConstructId", "threadId", "sessionId",
    "branchId", "goalRevision", "workHeadEventId", "workHeadSha256",
    "workStateReceiptSha256", "decisionHash", "nextActionHash",
    "preparedContextReceiptSha256", "sourceArgumentsSha256", "assignments", "maxParallel",
    "maxDepth", "createdAt", "expiresAt", "graphHash", "payloadSha256", "algorithm",
    "keyId", "signature",
})
_HYDRO_SYNTHESIS_RESOLUTION_FIELDS = frozenset({
    "contract", "authority", "ownerPrincipalId", "programId", "executionId", "graphId",
    "synthesisAssignmentId", "synthesisStepId", "expectedHeadEventId", "expectedHeadSha256",
    "dependencyResults", "failedOptionalAssignmentIds", "issuedAt", "payloadSha256", "algorithm",
    "keyId", "signature",
})
_HYDRO_WORKER_REQUEST_AUTHORIZATION_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "ownerPrincipalId", "programId", "itemId",
    "executionId", "graphId", "assignmentId", "workerPrincipalId", "attemptOrdinal",
    "startPermitPayloadSha256", "parentExecutionHeadEventId", "parentExecutionHeadSha256",
    "preparedContextReceiptSha256", "contextRevisionVectorSha256",
    "providerPayloadSha256", "requestHash", "oneUse",
    "issuedAt", "expiresAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_HYDRO_WORKER_REQUEST_REFERENCE_FIELDS = frozenset({
    "contract", "authority", "referenceId", "ownerPrincipalId", "programId", "itemId",
    "executionId", "graphId", "assignmentId", "workerPrincipalId", "attemptOrdinal",
    "startPermitPayloadSha256", "parentExecutionHeadEventId", "parentExecutionHeadSha256",
    "preparedContextReceiptSha256", "contextRevisionVectorSha256",
    "providerMessagePayloadSha256", "providerPayloadSha256", "requestId", "requestHash",
    "authorizationPayloadSha256", "containsProviderMessages", "issuedAt", "expiresAt",
    "payloadSha256", "algorithm", "keyId", "signature",
})
_CAPABILITY_MANIFEST_FIELDS = frozenset({
    "contract", "authority", "hostId", "ownerPrincipalId", "capabilities",
    "issuedAt", "expiresAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_CAPABILITY_FIELDS = frozenset({
    "contract", "capabilityId", "hostId", "operation", "actionClass", "riskCeiling",
    "resourceScopes", "credentialReferenceKinds", "idempotencyMode", "readbackMode",
    "compensationMode", "maxDurationMs", "maxInputBytes", "maxOutputBytes",
    "concurrencyClass", "networkPolicy", "expiresAt", "capabilityHash",
})
_PROPOSAL_BINDING_FIELDS = frozenset({
    "contract", "programId", "itemId", "decisionHash", "nextActionHash",
    "preparedContextReceiptSha256", "capabilityManifestPayloadSha256",
    "proposalSourceHash", "proposalSourceArtifactReference", "capabilityManifestArtifactReference",
    "advancementAuthority", "effectAuthority", "bindingHash",
})
_RECOVERY_ARTIFACT_SCOPE_FIELDS = frozenset({
    "ownerPrincipalId", "programId", "itemId", "sourceConstructId", "responsibleConstructId",
    "threadId", "sessionId", "branchId", "goalRevision", "preCommitHeadEventId",
    "preCommitHeadSha256", "preCommitStateReceiptSha256", "preparedContextReceiptSha256",
})
_RECOVERY_ARTIFACT_REFERENCE_FIELDS = frozenset({
    "contract", "artifactId", "artifactKind", "payloadSha256", "mediaType",
    "scopeSha256", "expiresAt", "referenceHash",
})
_PROPOSAL_SOURCE_FIELDS = frozenset({
    "contract", "sourceKind", "sourceEvidenceSha256", "operation", "hostId", "inputArtifacts",
    "argumentsArtifact", "argumentsSha256", "resourceKeys", "risk", "providerCandidates",
    "timeoutMs", "maxOutputBytes", "completionFactKinds", "createdAt", "advancementAuthority",
    "effectAuthority", "sourceHash",
})
_PROPOSAL_ENVELOPE_FIELDS = frozenset({
    "contract", "proposalArtifactId", "ownerPrincipalId", "mediaType", "artifactSha256",
    "candidate", "capabilityManifest", "executionProposalBinding", "advancementAuthority",
    "effectAuthority", "issuedAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_READBACK_FIELDS = frozenset({
    "contract", "readbackId", "authority", "ownerPrincipalId", "executionId", "programId", "itemId",
    "stepId", "attemptOrdinal", "hostId", "idempotencyKey", "outcome", "expectedResultSha256",
    "observedResultSha256", "evidenceArtifactIds", "observedAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_RECOVERY_CAPABILITY_FIELDS = frozenset({
    "contract", "capabilityId", "authority", "ownerPrincipalId", "executionId",
    "programId", "itemId", "stepId", "attemptOrdinal", "selection", "definitionHash",
    "workExecutionIntentHash", "approvalDisclosureSha256", "readbackPayloadSha256", "expectedHeadEventId",
    "expectedHeadSha256", "oneUse", "issuedAt", "expiresAt", "payloadSha256",
    "algorithm", "keyId", "signature",
})
_PROVIDER_FALLBACK_CAPABILITY_FIELDS = frozenset({
    "contract", "capabilityId", "authority", "ownerPrincipalId", "executionId",
    "programId", "itemId", "stepId", "attemptOrdinal", "selection", "definitionHash",
    "workExecutionIntentHash", "approvalDisclosureSha256", "hostReceiptPayloadSha256", "nextProviderId",
    "expectedHeadEventId", "expectedHeadSha256", "oneUse", "issuedAt", "expiresAt",
    "payloadSha256", "algorithm", "keyId", "signature",
})
_ARTIFACT_RECEIPT_FIELDS = frozenset({
    "contract", "artifactId", "ownerPrincipalId", "programId", "executionId",
    "artifactType", "contentSha256", "canonicalLocator", "sourceReceiptPayloadSha256",
    "issuedAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_RESULT_ARTIFACT_FIELDS = frozenset({
    "contract", "artifactId", "mediaType", "content", "contentSha256",
})
_EXECUTION_EVIDENCE_FIELDS = frozenset({
    "contract", "evidenceId", "authority", "ownerPrincipalId", "executionId", "programId",
    "itemId", "stepId", "hostReceiptPayloadSha256", "verifiedFactKinds",
    "workEvidenceReference", "issuedAt", "payloadSha256", "algorithm", "keyId", "signature",
})
_EXECUTION_CONTEXT_FIELDS = frozenset({
    "contract", "derivationAuthority", "persistenceAuthority", "contextPolicyVersion",
    "ownerPrincipalId", "executionId", "programId", "itemId", "sourceConstructId",
    "responsibleConstructId", "threadId", "sessionId", "branchId", "goalRevision",
    "definitionHash", "workBindingHash", "workExecutionIntentHash",
    "preparedContextReceiptSha256", "status", "executionStateReceipt", "steps",
    "nextCommands", "containsArguments", "containsCredentials", "containsPrivateReasoning",
    "projectionSha256",
})
_EXECUTION_STATE_RECEIPT_FIELDS = frozenset({
    "contract", "executionId", "definitionHash", "status", "sequence", "headEventId",
    "headEventSha256", "stateSha256", "receiptSha256",
})
_FINALIZATION_AUTHORIZATION_FIELDS = frozenset({
    "contract", "authorizationId", "authority", "keyId", "algorithm", "ownerPrincipalId",
    "executionId", "programId", "itemId", "sourceConstructId", "responsibleConstructId",
    "threadId", "sessionId", "executionBranchId", "workBranchId", "goalRevision",
    "executionExpectedSequence", "executionExpectedHeadEventId", "executionExpectedHeadSha256",
    "executionExpectedStateReceiptSha256", "workExpectedSequence", "workExpectedHeadEventId",
    "workExpectedHeadSha256", "workExpectedStateReceiptSha256", "transcriptBindingSha256",
    "executionEvents", "workEvents", "executionExpectedResultingStatus", "workExpectedResultingStatus",
    "idempotencyKey", "issuedAt", "expiresAt", "payloadSha256", "signature",
})
_FINALIZATION_BATCH_FIELDS = frozenset({
    "contract", "authorization", "executionEvents", "workEvents", "transcriptBindingSha256",
    "executionExpectedStateReceiptSha256", "workExpectedStateReceiptSha256", "atomic", "batchSha256",
})
_WORK_EVIDENCE_REFERENCE_FIELDS = frozenset({
    "contract", "evidenceId", "evidenceType", "authority", "scope", "payloadSha256",
    "receiptSha256", "verifiedFactKinds", "issuedAt", "cryptographicallyVerified",
    "advancementAuthority",
})


def _validate_authorization(value: Any, *, owner: str, payload: dict[str, Any], public_key_pem: str | None,
                            key_id: str | None, now: datetime) -> dict[str, Any]:
    auth = _verify_signed(value, fields=_AUTH_FIELDS, contract=EVENT_AUTHORIZATION,
                          public_key_pem=public_key_pem, expected_key_id=key_id, now=now, algorithm="ed25519")
    if auth.get("authority") != "chatty-core-host" or auth.get("ownerPrincipalId") != owner:
        raise ConstructExecutionError("EXECUTION_AUTHORIZATION_SCOPE_INVALID", "authorization owner/authority mismatch", 403)
    if auth.get("eventType") not in EVENT_TYPES or auth.get("eventPayloadSha256") != _sha(payload):
        raise ConstructExecutionError("EXECUTION_AUTHORIZATION_PAYLOAD_INVALID", "authorization payload mismatch", 403)
    if _time(auth["expiresAt"], "expiresAt") - _time(auth["issuedAt"], "issuedAt") > timedelta(seconds=_MAX_AUTH_SECONDS):
        raise ConstructExecutionError("EXECUTION_AUTHORIZATION_LIFETIME_INVALID", "authorization lifetime too long", 403)
    return auth


def _validate_proposal_binding(value: Any) -> dict[str, Any]:
    binding = _exact(value, _PROPOSAL_BINDING_FIELDS, "workExecutionProposalBinding")
    if binding.get("contract") != WORK_EXECUTION_PROPOSAL_BINDING \
            or binding.get("advancementAuthority") is not False \
            or binding.get("effectAuthority") is not False \
            or binding.get("bindingHash") != _sha({key: entry for key, entry in binding.items() if key != "bindingHash"}):
        raise ConstructExecutionError(
            "EXECUTION_PROPOSAL_BINDING_INVALID", "canonical work execution proposal binding is invalid", 403,
        )
    for field in ("decisionHash", "nextActionHash", "preparedContextReceiptSha256",
                  "capabilityManifestPayloadSha256", "proposalSourceHash"):
        _digest(binding.get(field), field)
    for field, kind in (("proposalSourceArtifactReference", "proposal_source"),
                        ("capabilityManifestArtifactReference", "capability_manifest")):
        reference = _exact(binding.get(field), _RECOVERY_ARTIFACT_REFERENCE_FIELDS, field)
        expected_media = f"application/vnd.chatty.work-execution-{kind.replace('_', '-')}+json"
        if reference.get("contract") != WORK_EXECUTION_RECOVERY_ARTIFACT_REFERENCE \
                or reference.get("artifactKind") != kind or reference.get("mediaType") != expected_media \
                or reference.get("referenceHash") != _sha({key: entry for key, entry in reference.items() if key != "referenceHash"}):
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_REFERENCE_INVALID", f"{field} is invalid", 403)
    return binding


def _validate_capability_manifest(
    value: Any, *, owner: str, candidate: dict[str, Any], public_key_pem: str | None,
    expected_key_id: str | None, host_key_resolver: Callable[[str], tuple[str, str] | None] | None,
    now: datetime | None,
) -> dict[str, Any]:
    manifest = _verify_signed(
        value, fields=_CAPABILITY_MANIFEST_FIELDS, contract=EXECUTION_CAPABILITY_MANIFEST,
        public_key_pem=public_key_pem, expected_key_id=expected_key_id, now=now,
        algorithm="ed25519",
    )
    host_id = _id(candidate.get("hostId"), "hostId")
    host_key = host_key_resolver(host_id) if host_key_resolver else None
    if not host_key:
        raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
    _host_public, host_key_id = _public_key(host_key[0], host_key[1])
    if expected_key_id and host_key_id == expected_key_id:
        raise ConstructExecutionError(
            "EXECUTION_KEY_DOMAIN_COLLISION",
            "capability-manifest authority and execution host keys must be distinct",
            503,
        )
    if manifest.get("authority") != "chatty-core-host-registry" \
            or manifest.get("hostId") != host_id \
            or manifest.get("ownerPrincipalId") not in {None, owner}:
        raise ConstructExecutionError(
            "EXECUTION_CAPABILITY_MANIFEST_SCOPE_INVALID", "capability manifest authority/scope is invalid", 403,
        )
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
        raise ConstructExecutionError("EXECUTION_CAPABILITY_MANIFEST_INVALID", "capability manifest is invalid", 403)
    risk_order = ["low", "moderate", "high", "critical"]
    eligible = False
    for index, raw in enumerate(capabilities):
        capability = _exact(raw, _CAPABILITY_FIELDS, f"capabilities[{index}]")
        if capability.get("contract") != "chatty-execution-capability/v1" \
                or capability.get("hostId") != host_id \
                or capability.get("capabilityHash") != _sha({key: entry for key, entry in capability.items() if key != "capabilityHash"}):
            raise ConstructExecutionError("EXECUTION_CAPABILITY_INVALID", "capability manifest entry is invalid", 403)
        if capability.get("operation") not in OPERATIONS \
                or capability.get("actionClass") not in {"read_only", "tool_effect", "persistence", "inference", "verification"} \
                or capability.get("riskCeiling") not in risk_order:
            raise ConstructExecutionError("EXECUTION_CAPABILITY_INVALID", "capability manifest entry is invalid", 403)
        resources = capability.get("resourceScopes")
        if not isinstance(resources, list) or len(resources) > 64 or len(set(resources)) != len(resources):
            raise ConstructExecutionError("EXECUTION_CAPABILITY_INVALID", "capability resource scopes are invalid", 403)
        candidate_risk = candidate.get("risk")
        eligible = eligible or all((
            capability.get("operation") == candidate.get("operation"),
            capability.get("actionClass") == candidate.get("actionClass"),
            candidate_risk in risk_order and risk_order.index(candidate_risk) <= risk_order.index(capability["riskCeiling"]),
            int(candidate.get("timeoutMs") or 0) <= int(capability.get("maxDurationMs") or 0),
            int(candidate.get("maxOutputBytes") or 0) <= int(capability.get("maxOutputBytes") or 0),
            all(any(resource == scope or resource.startswith(f"{scope}:") for scope in resources)
                for resource in candidate.get("resourceKeys", [])),
        ))
    if not eligible:
        raise ConstructExecutionError(
            "EXECUTION_CAPABILITY_SCOPE_INVALID", "proposal is outside the signed capability manifest", 403,
        )
    if candidate.get("capabilityManifestPayloadSha256") != manifest.get("payloadSha256"):
        raise ConstructExecutionError(
            "EXECUTION_CAPABILITY_MANIFEST_HASH_MISMATCH", "proposal capability manifest hash mismatch", 403,
        )
    return manifest


def _validate_recovery_artifact(value: Any, *, expected_kind: str) -> dict[str, Any]:
    payload_key = "proposalSource" if expected_kind == "proposal_source" else "capabilityManifest"
    artifact = _exact(
        value,
        {"contract", "artifactKind", "scope", payload_key, "retentionPolicy", "createdAt", "expiresAt",
         "advancementAuthority", "effectAuthority", "artifactHash"},
        f"workExecutionRecoveryArtifact.{expected_kind}",
    )
    scope = _exact(artifact.get("scope"), _RECOVERY_ARTIFACT_SCOPE_FIELDS, "workExecutionRecoveryArtifact.scope")
    created, expires = _time(artifact.get("createdAt"), "createdAt"), _time(artifact.get("expiresAt"), "expiresAt")
    if artifact.get("contract") != WORK_EXECUTION_RECOVERY_ARTIFACT \
            or artifact.get("artifactKind") != expected_kind \
            or artifact.get("retentionPolicy") != "bounded_recovery_7d" \
            or artifact.get("advancementAuthority") is not False or artifact.get("effectAuthority") is not False \
            or expires - created < timedelta(minutes=5) or expires - created > timedelta(days=7) \
            or artifact.get("artifactHash") != _sha({key: entry for key, entry in artifact.items() if key != "artifactHash"}) \
            or len(_bytes(artifact)) > _MAX_EVENT_BYTES:
        raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_INVALID", "recovery artifact is invalid", 403)
    for field in ("preCommitHeadSha256", "preCommitStateReceiptSha256", "preparedContextReceiptSha256"):
        _digest(scope.get(field), field)
    for field in ("ownerPrincipalId", "programId", "itemId", "sourceConstructId", "responsibleConstructId",
                  "threadId", "sessionId", "branchId", "goalRevision", "preCommitHeadEventId"):
        _id(scope.get(field), field)
    return artifact


def _recovery_artifact_reference(artifact: dict[str, Any]) -> dict[str, Any]:
    kind = artifact["artifactKind"]
    body = {
        "contract": WORK_EXECUTION_RECOVERY_ARTIFACT_REFERENCE,
        "artifactId": f"work-execution-recovery-{kind.replace('_', '-')}-{artifact['artifactHash'][:40]}",
        "artifactKind": kind, "payloadSha256": artifact["artifactHash"],
        "mediaType": f"application/vnd.chatty.work-execution-{kind.replace('_', '-')}+json",
        "scopeSha256": _sha(artifact["scope"]), "expiresAt": artifact["expiresAt"],
    }
    return {**body, "referenceHash": _sha(body)}


def _finalization_descriptor(value: Any, *, include_payload: bool, allowed: set[str], name: str) -> dict[str, Any]:
    fields = {"ordinal", "eventType", "eventPayloadSha256", "actor"}
    if include_payload:
        fields.add("eventPayload")
    descriptor = _exact(value, fields, name)
    actor = _exact(descriptor.get("actor"), {"principalId", "principalType", "authority"}, f"{name}.actor")
    if descriptor.get("eventType") not in allowed or actor.get("principalType") not in {"construct", "system"} \
            or actor.get("authority") not in {"chatty-core", "vvault"}:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_DESCRIPTOR_INVALID", f"{name} is invalid", 403)
    if include_payload and descriptor.get("eventPayloadSha256") != _sha(descriptor.get("eventPayload")):
        raise ConstructExecutionError("EXECUTION_FINALIZATION_PAYLOAD_INVALID", f"{name} payload hash mismatch", 403)
    return descriptor


def _validate_finalization_batch(value: Any, *, owner: str, public_key_pem: str | None,
                                 expected_key_id: str | None, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = _exact(value, _FINALIZATION_BATCH_FIELDS, "executionWorkFinalizationBatch")
    if batch.get("contract") != FINALIZATION_BATCH or batch.get("atomic") is not True \
            or batch.get("batchSha256") != _sha({key: entry for key, entry in batch.items() if key != "batchSha256"}):
        raise ConstructExecutionError("EXECUTION_FINALIZATION_BATCH_INVALID", "finalization batch is invalid", 403)
    auth = _exact(batch.get("authorization"), _FINALIZATION_AUTHORIZATION_FIELDS, "executionWorkFinalizationAuthorization")
    body = {key: entry for key, entry in auth.items() if key not in {"payloadSha256", "signature"}}
    if auth.get("contract") != FINALIZATION_AUTHORIZATION or auth.get("authority") != "chatty-core-host" \
            or auth.get("algorithm") != "ed25519" or auth.get("ownerPrincipalId") != owner \
            or auth.get("payloadSha256") != _sha(body):
        raise ConstructExecutionError("EXECUTION_FINALIZATION_AUTHORIZATION_INVALID", "finalization authorization is invalid", 403)
    key, key_id = _public_key(public_key_pem, expected_key_id)
    if auth.get("keyId") != key_id:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_AUTHORIZATION_KEY_INVALID", "finalization key is not trusted", 403)
    try:
        key.verify(base64.b64decode(str(auth.get("signature") or ""), validate=True), _bytes(body))
    except (InvalidSignature, ValueError) as exc:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_AUTHORIZATION_SIGNATURE_INVALID", "finalization signature is invalid", 403) from exc
    issued, expires = _time(auth.get("issuedAt"), "issuedAt"), _time(auth.get("expiresAt"), "expiresAt")
    if expires <= issued or expires - issued > timedelta(seconds=_MAX_AUTH_SECONDS) or now < issued - timedelta(seconds=30) or now >= expires:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_AUTHORIZATION_EXPIRED", "finalization authorization expired", 403)
    execution_events = batch.get("executionEvents")
    work_events = batch.get("workEvents")
    if not isinstance(execution_events, list) or not 2 <= len(execution_events) <= 3 \
            or not isinstance(work_events, list) or not 1 <= len(work_events) <= 2:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_EVENTS_INVALID", "finalization event counts are invalid", 403)
    execution_allowed = {"execution_step_verified", "execution_step_completed", "execution_completed"}
    work_allowed = {"evidence_attached", "work_item_completed"}
    normalized_exec = [_finalization_descriptor(entry, include_payload=True, allowed=execution_allowed,
                                                 name=f"executionEvents[{index}]")
                       for index, entry in enumerate(execution_events)]
    normalized_work = [_finalization_descriptor(entry, include_payload=True, allowed=work_allowed,
                                                 name=f"workEvents[{index}]")
                       for index, entry in enumerate(work_events)]
    if [entry["ordinal"] for entry in normalized_exec] != list(range(1, len(normalized_exec) + 1)) \
            or [entry["eventType"] for entry in normalized_exec] not in [
                ["execution_step_verified", "execution_step_completed"],
                ["execution_step_verified", "execution_step_completed", "execution_completed"],
            ] or [entry["ordinal"] for entry in normalized_work] != list(range(1, len(normalized_work) + 1)) \
            or [entry["eventType"] for entry in normalized_work] not in [
                ["evidence_attached"], ["evidence_attached", "work_item_completed"],
            ]:
        raise ConstructExecutionError("EXECUTION_FINALIZATION_EVENT_ORDER_INVALID", "finalization event order is invalid", 403)
    def auth_descriptor(entry: dict[str, Any]) -> dict[str, Any]:
        return {key: entry[key] for key in ("ordinal", "eventType", "eventPayloadSha256", "actor")}
    if auth.get("executionEvents") != [auth_descriptor(entry) for entry in normalized_exec] \
            or auth.get("workEvents") != [auth_descriptor(entry) for entry in normalized_work] \
            or batch.get("transcriptBindingSha256") != auth.get("transcriptBindingSha256") \
            or batch.get("executionExpectedStateReceiptSha256") != auth.get("executionExpectedStateReceiptSha256") \
            or batch.get("workExpectedStateReceiptSha256") != auth.get("workExpectedStateReceiptSha256"):
        raise ConstructExecutionError("EXECUTION_FINALIZATION_AUTHORIZATION_SCOPE_INVALID", "batch differs from signed authorization", 403)
    if auth.get("executionExpectedResultingStatus") not in {
        "approval_pending", "authorized", "lease_pending", "ready", "attempt_in_flight",
        "verification_pending", "cancel_pending", "outcome_unknown", "completion_pending",
        "complete", "failed",
    } or auth.get("workExpectedResultingStatus") not in {
        "waiting_execution_authority", "planned", "completion_pending",
    }:
        raise ConstructExecutionError(
            "EXECUTION_FINALIZATION_RESULT_STATUS_INVALID", "finalization resulting status is invalid", 403,
        )
    return batch, auth


def _validate_hydro_scope(value: Any) -> dict[str, Any]:
    fields = {"contract", "graphId", "graphPayloadSha256", "assignmentId", "assignmentHash",
              "parentExecutionId", "parentResponsibleConstructId", "workerPrincipalId", "assignmentKind"}
    scope = _exact(value, fields, "hydroExecutionScope")
    if scope.get("contract") != HYDRO_EXECUTION_SCOPE or scope.get("assignmentKind") not in {"worker", "synthesis"}:
        raise ConstructExecutionError("HYDRO_EXECUTION_SCOPE_INVALID", "Hydro execution scope is invalid", 403)
    for field in ("graphId", "assignmentId", "parentExecutionId", "parentResponsibleConstructId", "workerPrincipalId"):
        _id(scope.get(field), field)
    for field in ("graphPayloadSha256", "assignmentHash"):
        _digest(scope.get(field), field)
    return scope


def _validate_hydro_binding(value: Any) -> dict[str, Any]:
    fields = {"contract", "graphId", "graphPayloadSha256", "graphHash", "sourceArgumentsSha256",
              "assignmentIds", "assignmentHashes", "synthesisAssignmentId", "maxParallel", "bindingHash"}
    binding = _exact(value, fields, "hydroExecutionGraphBinding")
    body = {key: entry for key, entry in binding.items() if key != "bindingHash"}
    if binding.get("contract") != HYDRO_EXECUTION_GRAPH_BINDING \
            or binding.get("bindingHash") != _sha(body) \
            or not isinstance(binding.get("assignmentIds"), list) \
            or not 2 <= len(binding["assignmentIds"]) <= 32 \
            or len(set(binding["assignmentIds"])) != len(binding["assignmentIds"]) \
            or not isinstance(binding.get("assignmentHashes"), list) \
            or len(binding["assignmentHashes"]) != len(binding["assignmentIds"]) \
            or binding.get("synthesisAssignmentId") not in binding["assignmentIds"] \
            or not isinstance(binding.get("maxParallel"), int) or isinstance(binding.get("maxParallel"), bool) \
            or not 1 <= binding["maxParallel"] <= 4:
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_BINDING_INVALID", "Hydro graph binding is invalid", 403)
    for field in ("graphPayloadSha256", "graphHash", "sourceArgumentsSha256", "bindingHash"):
        _digest(binding.get(field), field)
    for value in binding["assignmentHashes"]:
        _digest(value, "assignmentHash")
    return binding


def _validate_hydro_assignment(value: Any, index: int) -> dict[str, Any]:
    fields = {"contract", "assignmentId", "ordinal", "kind", "workerPrincipalId",
              "dependencyAssignmentIds", "hostId", "capabilityId", "inputArtifacts",
              "argumentsArtifact", "argumentsSha256", "resourceKeys", "risk", "required",
              "providerCandidates", "idempotencyMode", "readbackMode", "timeoutMs",
              "maxOutputBytes", "completionFactKinds", "assignmentHash"}
    assignment = _exact(value, fields, f"hydroExecutionAssignment[{index}]")
    body = {key: entry for key, entry in assignment.items() if key != "assignmentHash"}
    if any((
        assignment.get("contract") != HYDRO_EXECUTION_ASSIGNMENT,
        assignment.get("ordinal") != index + 1,
        assignment.get("kind") not in {"worker", "synthesis"},
        not isinstance(assignment.get("required"), bool),
        not isinstance(assignment.get("dependencyAssignmentIds"), list)
        or len(assignment["dependencyAssignmentIds"]) > 32
        or len(set(assignment["dependencyAssignmentIds"])) != len(assignment["dependencyAssignmentIds"]),
        not isinstance(assignment.get("resourceKeys"), list) or len(assignment["resourceKeys"]) > 32
        or len(set(assignment["resourceKeys"])) != len(assignment["resourceKeys"]),
        not isinstance(assignment.get("providerCandidates"), list)
        or len(assignment["providerCandidates"]) > 3
        or len(set(assignment["providerCandidates"])) != len(assignment["providerCandidates"]),
        not isinstance(assignment.get("inputArtifacts"), list) or len(assignment["inputArtifacts"]) > 16,
        not isinstance(assignment.get("completionFactKinds"), list)
        or len(assignment["completionFactKinds"]) > 32
        or len(set(assignment["completionFactKinds"])) != len(assignment["completionFactKinds"]),
        assignment.get("idempotencyMode") not in {"none", "native_exact", "readback_proven"},
        assignment.get("readbackMode") not in {"none", "signed"},
        assignment.get("idempotencyMode") == "readback_proven" and assignment.get("readbackMode") != "signed",
        assignment.get("risk") not in {"low", "moderate", "high", "critical"},
        not isinstance(assignment.get("timeoutMs"), int) or isinstance(assignment.get("timeoutMs"), bool)
        or not 1000 <= assignment["timeoutMs"] <= 3_600_000,
        not isinstance(assignment.get("maxOutputBytes"), int) or isinstance(assignment.get("maxOutputBytes"), bool)
        or not 1 <= assignment["maxOutputBytes"] <= _MAX_RESULT_BYTES,
        not isinstance(assignment.get("argumentsArtifact"), dict)
        or assignment["argumentsArtifact"].get("sha256") != assignment.get("argumentsSha256"),
        assignment.get("assignmentHash") != _sha(body),
    )):
        raise ConstructExecutionError("HYDRO_EXECUTION_ASSIGNMENT_INVALID", "Hydro assignment is invalid", 403)
    for field in ("assignmentId", "workerPrincipalId", "hostId", "capabilityId"):
        _id(assignment.get(field), field)
    for value in assignment["dependencyAssignmentIds"] + assignment["resourceKeys"] \
            + assignment["providerCandidates"] + assignment["completionFactKinds"]:
        _id(value, "hydroAssignmentMember")
    for artifact in assignment["inputArtifacts"] + [assignment["argumentsArtifact"]]:
        reference = _exact(artifact, {"artifactId", "sha256", "mediaType"}, "hydroExecutionArtifact")
        _id(reference.get("artifactId"), "artifactId")
        _digest(reference.get("sha256"), "artifact.sha256")
    return assignment


def _validate_hydro_graph_source(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _exact(value, {"contract", "graphId", "nodes", "maxParallel", "maxDepth"},
                    "hydroExecutionGraphSource")
    if source.get("contract") != HYDRO_EXECUTION_GRAPH_SOURCE \
            or not isinstance(source.get("nodes"), list) or not 2 <= len(source["nodes"]) <= 32 \
            or not isinstance(source.get("maxParallel"), int) or isinstance(source.get("maxParallel"), bool) \
            or not 1 <= source["maxParallel"] <= 4 \
            or not isinstance(source.get("maxDepth"), int) or isinstance(source.get("maxDepth"), bool) \
            or not 1 <= source["maxDepth"] <= 8:
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_INVALID", "canonical Hydro graph source is invalid", 403)
    _id(source.get("graphId"), "graphId")
    node_fields = {"nodeId", "ordinal", "kind", "workerPrincipalId", "dependencyNodeIds", "hostId",
                   "capabilityId", "inputArtifacts", "argumentsArtifact", "argumentsSha256", "resourceKeys",
                   "risk", "required", "idempotencyMode", "readbackMode", "timeoutMs", "maxOutputBytes",
                   "completionFactKinds"}
    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(source["nodes"]):
        node = _exact(raw, node_fields, f"hydroExecutionGraphSource.nodes[{index}]")
        if any((
            node.get("ordinal") != index + 1, node.get("kind") not in {"worker", "synthesis"},
            not isinstance(node.get("required"), bool),
            not isinstance(node.get("dependencyNodeIds"), list) or len(node["dependencyNodeIds"]) > 32
            or len(set(node["dependencyNodeIds"])) != len(node["dependencyNodeIds"]),
            not isinstance(node.get("resourceKeys"), list) or len(node["resourceKeys"]) > 32
            or len(set(node["resourceKeys"])) != len(node["resourceKeys"]),
            not isinstance(node.get("inputArtifacts"), list) or len(node["inputArtifacts"]) > 16,
            not isinstance(node.get("completionFactKinds"), list) or len(node["completionFactKinds"]) > 32
            or len(set(node["completionFactKinds"])) != len(node["completionFactKinds"]),
            node.get("risk") not in {"low", "moderate", "high", "critical"},
            node.get("idempotencyMode") not in {"none", "native_exact", "readback_proven"},
            node.get("readbackMode") not in {"none", "signed"},
            node.get("idempotencyMode") == "readback_proven" and node.get("readbackMode") != "signed",
            not isinstance(node.get("timeoutMs"), int) or isinstance(node.get("timeoutMs"), bool)
            or not 1_000 <= node["timeoutMs"] <= 3_600_000,
            not isinstance(node.get("maxOutputBytes"), int) or isinstance(node.get("maxOutputBytes"), bool)
            or not 1 <= node["maxOutputBytes"] <= _MAX_RESULT_BYTES,
            not isinstance(node.get("argumentsArtifact"), dict)
            or node["argumentsArtifact"].get("sha256") != node.get("argumentsSha256"),
        )):
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_NODE_INVALID", "canonical Hydro node is invalid", 403)
        for field in ("nodeId", "workerPrincipalId", "hostId", "capabilityId"):
            _id(node.get(field), field)
        for member_value in node["dependencyNodeIds"] + node["resourceKeys"] + node["completionFactKinds"]:
            _id(member_value, "hydroNodeMember")
        for artifact_value in node["inputArtifacts"] + [node["argumentsArtifact"]]:
            artifact = _exact(artifact_value, {"artifactId", "sha256", "mediaType"}, "hydroNodeArtifact")
            _id(artifact.get("artifactId"), "artifactId")
            _digest(artifact.get("sha256"), "artifact.sha256")
        nodes.append(node)
    by_id = {node["nodeId"]: node for node in nodes}
    if len(by_id) != len(nodes) or any(
        dependency not in by_id or dependency == node["nodeId"]
        for node in nodes for dependency in node["dependencyNodeIds"]
    ):
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_TOPOLOGY_INVALID", "canonical Hydro topology is invalid", 403)
    depths: dict[str, int] = {}
    visiting: set[str] = set()
    def depth_of(node_id: str) -> int:
        if node_id in visiting:
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_CYCLE_INVALID", "canonical Hydro graph contains a cycle", 403)
        if node_id in depths:
            return depths[node_id]
        visiting.add(node_id)
        result = 1 + max([depth_of(item) for item in by_id[node_id]["dependencyNodeIds"]] or [0])
        visiting.remove(node_id)
        if result > source["maxDepth"]:
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_DEPTH_EXCEEDED", "canonical Hydro graph exceeds maxDepth", 403)
        depths[node_id] = result
        return result
    for node_id in by_id:
        depth_of(node_id)
    workers = [node for node in nodes if node["kind"] == "worker"]
    synthesis = [node for node in nodes if node["kind"] == "synthesis"]
    if len(synthesis) != 1 or not workers or not any(node["required"] for node in workers) \
            or synthesis[0]["required"] is not True or synthesis[0]["ordinal"] != len(nodes) \
            or synthesis[0]["dependencyNodeIds"] != [node["nodeId"] for node in workers]:
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_SOURCE_SYNTHESIS_INVALID", "canonical Hydro synthesis boundary is invalid", 403)
    return {**source, "nodes": nodes}, synthesis[0]


def _hydro_safe_options(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider options exceed depth", 403)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > 2_048 or "\x00" in value:
            raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider option string is invalid", 403)
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider option number is invalid", 403)
        return value
    if isinstance(value, list):
        if len(value) > 32:
            raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider options exceed capacity", 403)
        return [_hydro_safe_options(entry, depth=depth + 1) for entry in value]
    if not isinstance(value, dict) or len(value) > 32:
        raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider options are invalid", 403)
    result: dict[str, Any] = {}
    for key in sorted(value):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key) \
                or re.search(r"(?:secret|password|credential|api[_-]?key|private[_-]?key|access[_-]?token|bearer[_-]?token)", key, re.I):
            raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "provider option key is invalid", 403)
        result[key] = _hydro_safe_options(value[key], depth=depth + 1)
    return result


def _validate_hydro_worker_request(value: Any) -> dict[str, Any]:
    fields = {"contract", "requestId", "scope", "providerRoute", "messages",
              "providerMessagePayloadSha256", "providerPayloadSha256", "synthesisInputResolutionSha256",
              "budgets", "transcriptPersistence", "providerCallLimit", "semanticRetryAllowed", "requestHash"}
    request = _exact(value, fields, "hydroWorkerRequest")
    if request.get("contract") != HYDRO_WORKER_REQUEST:
        raise ConstructExecutionError("HYDRO_WORKER_REQUEST_INVALID", "Hydro worker request contract is invalid", 403)
    scope_fields = {"ownerPrincipalId", "programId", "itemId", "executionId", "graphId", "graphPayloadSha256",
                    "assignmentId", "assignmentHash", "assignmentKind", "workerPrincipalId",
                    "parentResponsibleConstructId", "parentWorkHeadEventId", "parentWorkHeadSha256",
                    "parentExecutionHeadEventId", "parentExecutionHeadSha256", "executionStateReceiptSha256",
                    "startPermitPayloadSha256", "attemptOrdinal", "preparedContextReceiptSha256",
                    "contextRevisionVectorSha256"}
    scope = _exact(request.get("scope"), scope_fields, "hydroWorkerRequest.scope")
    for field in ("ownerPrincipalId", "programId", "itemId", "executionId", "graphId", "assignmentId",
                  "workerPrincipalId", "parentResponsibleConstructId", "parentWorkHeadEventId",
                  "parentExecutionHeadEventId"):
        _id(scope.get(field), field)
    for field in ("graphPayloadSha256", "assignmentHash", "parentWorkHeadSha256",
                  "parentExecutionHeadSha256", "executionStateReceiptSha256", "startPermitPayloadSha256",
                  "preparedContextReceiptSha256", "contextRevisionVectorSha256"):
        _digest(scope.get(field), field)
    if scope.get("assignmentKind") not in {"worker", "synthesis"} \
            or not isinstance(scope.get("attemptOrdinal"), int) or isinstance(scope.get("attemptOrdinal"), bool) \
            or not 1 <= scope["attemptOrdinal"] <= 2:
        raise ConstructExecutionError("HYDRO_WORKER_REQUEST_SCOPE_INVALID", "Hydro worker request scope is invalid", 403)
    route_fields = {"contract", "hostId", "routeId", "provider", "model", "routeRevision",
                    "registryEvidenceSha256", "options", "optionsSha256"}
    route = _exact(request.get("providerRoute"), route_fields, "hydroWorkerRequest.providerRoute")
    if route.get("contract") != "chatty-hydro-provider-route/v1":
        raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_ROUTE_INVALID", "Hydro provider route is invalid", 403)
    for field in ("hostId", "routeId", "provider", "model", "routeRevision"):
        _id(route.get(field), field)
    _digest(route.get("registryEvidenceSha256"), "registryEvidenceSha256")
    options = _hydro_safe_options(route.get("options"))
    if route.get("optionsSha256") != _sha(options):
        raise ConstructExecutionError("HYDRO_WORKER_PROVIDER_OPTIONS_INVALID", "Hydro provider options hash mismatch", 403)
    route = {**route, "options": options}
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ConstructExecutionError("HYDRO_WORKER_MESSAGES_INVALID", "Hydro worker request requires two messages", 403)
    normalized_messages = []
    total_bytes = 0
    for index, raw in enumerate(messages):
        message = _exact(raw, {"ordinal", "role", "content", "contentSha256", "contentBytes"},
                         f"hydroWorkerRequest.messages[{index}]")
        content = message.get("content")
        encoded = content.encode("utf-8") if isinstance(content, str) else b""
        if message.get("ordinal") != index + 1 or message.get("role") != ("system" if index == 0 else "user") \
                or not content or "\x00" in content or len(encoded) > 512 * 1024 \
                or message.get("contentBytes") != len(encoded) \
                or message.get("contentSha256") != hashlib.sha256(encoded).hexdigest():
            raise ConstructExecutionError("HYDRO_WORKER_MESSAGES_INVALID", "Hydro worker message evidence is invalid", 403)
        total_bytes += len(encoded)
        normalized_messages.append(message)
    if total_bytes > 512 * 1024:
        raise ConstructExecutionError("HYDRO_WORKER_INPUT_CAPACITY_EXCEEDED", "Hydro worker input exceeds capacity", 413)
    provider_messages = [{"role": entry["role"], "content": entry["content"]} for entry in normalized_messages]
    raw_provider_sha = hashlib.sha256(json.dumps(provider_messages, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    budgets = _exact(request.get("budgets"), {"maxInputBytes", "maxOutputBytes", "timeoutMs"},
                     "hydroWorkerRequest.budgets")
    if any((
        request.get("providerMessagePayloadSha256") != raw_provider_sha,
        request.get("providerPayloadSha256") != _sha({"route": route, "messages": provider_messages}),
        budgets.get("maxInputBytes") != total_bytes,
        not isinstance(budgets.get("maxOutputBytes"), int) or isinstance(budgets.get("maxOutputBytes"), bool)
        or not 1 <= budgets["maxOutputBytes"] <= _MAX_RESULT_BYTES,
        not isinstance(budgets.get("timeoutMs"), int) or isinstance(budgets.get("timeoutMs"), bool)
        or not 1_000 <= budgets["timeoutMs"] <= 3_600_000,
        request.get("transcriptPersistence") != "forbidden", request.get("providerCallLimit") != 1,
        request.get("semanticRetryAllowed") is not False,
        (scope["assignmentKind"] == "synthesis") != (request.get("synthesisInputResolutionSha256") is not None),
    )):
        raise ConstructExecutionError("HYDRO_WORKER_REQUEST_INVALID", "Hydro worker request is invalid", 403)
    if request.get("synthesisInputResolutionSha256") is not None:
        _digest(request["synthesisInputResolutionSha256"], "synthesisInputResolutionSha256")
    normalized = {**request, "scope": scope, "providerRoute": route, "messages": normalized_messages, "budgets": budgets}
    if request.get("requestHash") != _sha({key: entry for key, entry in normalized.items() if key != "requestHash"}):
        raise ConstructExecutionError("HYDRO_WORKER_REQUEST_HASH_INVALID", "Hydro worker request hash mismatch", 403)
    return normalized


def _validate_hydro_worker_inference_arguments(
    value: Any, *, assignment: dict[str, Any], graph: dict[str, Any], signing_secret: str | None = None,
) -> dict[str, Any]:
    fields = {"contract", "assignmentId", "assignmentKind", "workerPrincipalId", "objective",
              "expectedOutput", "requiredEvidenceIds", "participantFrame",
              "participantFrameSha256", "modelPreference", "argumentsHash"}
    arguments = _exact(value, fields, "hydroWorkerInferenceArguments")
    if arguments.get("contract") != HYDRO_WORKER_INFERENCE_ARGUMENTS:
        raise ConstructExecutionError("HYDRO_WORKER_ARGUMENTS_INVALID", "worker inference arguments contract is invalid", 403)
    frame = arguments.get("participantFrame")
    if not isinstance(frame, dict) or frame.get("contract") != "chatty-participant-frame/v1" \
            or len(_bytes(frame)) > 32 * 1024:
        raise ConstructExecutionError("HYDRO_WORKER_PARTICIPANT_FRAME_INVALID", "signed participant frame is invalid", 403)
    unsigned_frame = {key: entry for key, entry in frame.items() if key != "signature"}
    try:
        signature_valid = conversation_thread_service.verify_payload(
            unsigned_frame, str(frame.get("signature") or ""), signing_secret,
        )
    except Exception as exc:
        raise ConstructExecutionError(
            "HYDRO_WORKER_PARTICIPANT_FRAME_UNAVAILABLE", "participant-frame verification is unavailable", 503,
        ) from exc
    speaker = frame.get("speaker") if isinstance(frame.get("speaker"), dict) else {}
    addressing = frame.get("addressing") if isinstance(frame.get("addressing"), dict) else {}
    handler = frame.get("handler") if isinstance(frame.get("handler"), dict) else {}
    addressees = frame.get("addressees") if isinstance(frame.get("addressees"), list) else []
    target_in_addressees = any(
        isinstance(entry, dict) and entry.get("principalId") == assignment.get("workerPrincipalId")
        and entry.get("principalType") == "construct" for entry in addressees
    )
    required_evidence_ids = arguments.get("requiredEvidenceIds")
    if any((
        not signature_valid,
        arguments.get("assignmentId") != assignment.get("assignmentId"),
        arguments.get("assignmentKind") != assignment.get("kind"),
        arguments.get("workerPrincipalId") != assignment.get("workerPrincipalId"),
        not isinstance(arguments.get("objective"), str) or not arguments["objective"]
        or len(arguments["objective"]) > 8_192 or "\x00" in arguments["objective"],
        not isinstance(arguments.get("expectedOutput"), str) or not arguments["expectedOutput"]
        or len(arguments["expectedOutput"]) > 2_048 or "\x00" in arguments["expectedOutput"],
        not isinstance(required_evidence_ids, list) or len(required_evidence_ids) > 50
        or len(set(required_evidence_ids or [])) != len(required_evidence_ids or []),
        any(not isinstance(entry, str) or not _ID.fullmatch(entry)
            for entry in (required_evidence_ids or [])),
        arguments.get("modelPreference") is not None
        and (not isinstance(arguments.get("modelPreference"), str)
             or not _ID.fullmatch(arguments["modelPreference"])),
        arguments.get("participantFrameSha256") != _sha(frame),
        frame.get("authority") != "ovvaults",
        frame.get("threadId") != graph.get("threadId"),
        frame.get("onBehalfOf") is not None,
        handler.get("principalId") != graph.get("ownerPrincipalId"),
        handler.get("principalType") != "human", handler.get("authorized") is not True,
        speaker.get("principalId") != graph.get("parentResponsibleConstructId"),
        speaker.get("principalType") != "construct",
        addressing.get("targetPrincipalId") != assignment.get("workerPrincipalId"),
        not target_in_addressees,
    )):
        raise ConstructExecutionError(
            "HYDRO_WORKER_ARGUMENTS_SCOPE_INVALID",
            "worker inference arguments do not bind the signed graph principals", 409,
        )
    body = {key: entry for key, entry in arguments.items() if key != "argumentsHash"}
    if arguments.get("argumentsHash") != _sha(body) or _sha(arguments) != assignment.get("argumentsSha256"):
        raise ConstructExecutionError("HYDRO_WORKER_ARGUMENTS_HASH_INVALID", "worker inference argument hash is invalid", 409)
    return arguments


def _validate_hydro_graph_body(value: Any, *, owner: str, now: datetime) -> tuple[dict[str, Any], str]:
    fields = {"contract", "authority", "graphId", "executionId", "ownerPrincipalId", "programId",
              "itemId", "sourceConstructId", "parentResponsibleConstructId", "threadId", "sessionId",
              "branchId", "goalRevision", "workHeadEventId", "workHeadSha256", "workStateReceiptSha256",
              "decisionHash", "nextActionHash", "preparedContextReceiptSha256", "sourceArgumentsSha256",
              "assignments", "maxParallel", "maxDepth", "createdAt", "expiresAt", "graphHash"}
    graph = _exact(value, fields, "hydroExecutionGraph")
    if graph.get("contract") != HYDRO_EXECUTION_GRAPH or graph.get("authority") != AUTHORITY \
            or graph.get("ownerPrincipalId") != owner or graph.get("sessionId") != graph.get("threadId") \
            or not isinstance(graph.get("assignments"), list) or not 2 <= len(graph["assignments"]) <= 32 \
            or not isinstance(graph.get("maxParallel"), int) or isinstance(graph.get("maxParallel"), bool) \
            or not 1 <= graph["maxParallel"] <= 4 \
            or not isinstance(graph.get("maxDepth"), int) or isinstance(graph.get("maxDepth"), bool) \
            or not 1 <= graph["maxDepth"] <= 8:
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_INVALID", "Hydro execution graph is invalid", 403)
    for field in ("graphId", "executionId", "ownerPrincipalId", "programId", "itemId",
                  "sourceConstructId", "parentResponsibleConstructId", "threadId", "sessionId",
                  "branchId", "goalRevision", "workHeadEventId"):
        _id(graph.get(field), field)
    for field in ("workHeadSha256", "workStateReceiptSha256", "decisionHash", "nextActionHash",
                  "preparedContextReceiptSha256", "sourceArgumentsSha256", "graphHash"):
        _digest(graph.get(field), field)
    assignments = [_validate_hydro_assignment(entry, index) for index, entry in enumerate(graph["assignments"])]
    by_id = {entry["assignmentId"]: entry for entry in assignments}
    if len(by_id) != len(assignments) or any(
        dependency not in by_id or dependency == entry["assignmentId"]
        for entry in assignments for dependency in entry["dependencyAssignmentIds"]
    ):
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_TOPOLOGY_INVALID", "Hydro graph topology is invalid", 403)
    depths: dict[str, int] = {}
    visiting: set[str] = set()
    def assignment_depth(assignment_id: str) -> int:
        if assignment_id in visiting:
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_CYCLE_INVALID", "Hydro graph contains a cycle", 403)
        if assignment_id in depths:
            return depths[assignment_id]
        visiting.add(assignment_id)
        result = 1 + max([assignment_depth(item) for item in by_id[assignment_id]["dependencyAssignmentIds"]] or [0])
        visiting.remove(assignment_id)
        if result > graph["maxDepth"]:
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_DEPTH_EXCEEDED", "Hydro graph depth exceeds bound", 403)
        depths[assignment_id] = result
        return result
    for assignment_id in by_id:
        assignment_depth(assignment_id)
    workers = [entry for entry in assignments if entry["kind"] == "worker"]
    synthesis = [entry for entry in assignments if entry["kind"] == "synthesis"]
    if len(synthesis) != 1 or not workers or not any(entry["required"] for entry in workers) \
            or synthesis[0]["required"] is not True \
            or synthesis[0]["ordinal"] != len(assignments) \
            or synthesis[0]["dependencyAssignmentIds"] != [entry["assignmentId"] for entry in workers]:
        raise ConstructExecutionError("HYDRO_EXECUTION_SYNTHESIS_BOUNDARY_INVALID", "Hydro synthesis boundary is invalid", 403)
    created, expires = _time(graph.get("createdAt"), "createdAt"), _time(graph.get("expiresAt"), "expiresAt")
    body = {key: entry for key, entry in graph.items() if key != "graphHash"}
    if graph.get("graphHash") != _sha(body) or expires <= created or expires - created > timedelta(minutes=15) \
            or now < created - timedelta(seconds=30) or now >= expires or len(_bytes(graph)) > _MAX_EVENT_BYTES - 1024:
        raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_HASH_INVALID", "Hydro graph hash, time, or capacity is invalid", 403)
    return graph, synthesis[0]["assignmentId"]


def _validate_hydro_capability_manifest(
    value: Any, *, owner: str, graph: dict[str, Any], public_key_pem: str | None,
    expected_key_id: str | None, host_key_resolver: Callable[[str], tuple[str, str] | None] | None,
    now: datetime,
) -> dict[str, Any]:
    manifest = _verify_signed(
        value, fields=_CAPABILITY_MANIFEST_FIELDS, contract=EXECUTION_CAPABILITY_MANIFEST,
        public_key_pem=public_key_pem, expected_key_id=expected_key_id, now=now,
        algorithm="ed25519",
    )
    assignments = graph["assignments"]
    host_ids = {entry["hostId"] for entry in assignments}
    if manifest.get("authority") != "chatty-core-host-registry" \
            or manifest.get("ownerPrincipalId") not in {None, owner} \
            or host_ids != {manifest.get("hostId")}:
        raise ConstructExecutionError(
            "HYDRO_EXECUTION_CAPABILITY_MANIFEST_SCOPE_INVALID",
            "Hydro assignments must share the signed host-registry scope", 403,
        )
    host_key = host_key_resolver(manifest["hostId"]) if host_key_resolver else None
    if not host_key:
        raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
    _host_public, host_key_id = _public_key(host_key[0], host_key[1])
    if expected_key_id and host_key_id == expected_key_id:
        raise ConstructExecutionError(
            "EXECUTION_KEY_DOMAIN_COLLISION",
            "capability-manifest authority and execution host keys must be distinct", 503,
        )
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
        raise ConstructExecutionError("EXECUTION_CAPABILITY_MANIFEST_INVALID", "capability manifest is invalid", 403)
    normalized: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(capabilities):
        capability = _exact(raw, _CAPABILITY_FIELDS, f"capabilities[{index}]")
        if capability.get("contract") != "chatty-execution-capability/v1" \
                or capability.get("hostId") != manifest.get("hostId") \
                or capability.get("capabilityHash") != _sha({key: entry for key, entry in capability.items()
                                                              if key != "capabilityHash"}):
            raise ConstructExecutionError("EXECUTION_CAPABILITY_INVALID", "capability manifest entry is invalid", 403)
        normalized[capability.get("capabilityId")] = capability
    risk_order = ["low", "moderate", "high", "critical"]
    for assignment in assignments:
        capability = normalized.get(assignment["capabilityId"])
        scopes = (capability or {}).get("resourceScopes") or []
        if not capability or any((
            capability.get("hostId") != assignment["hostId"],
            capability.get("operation") != "hydro.graph.dispatch",
            assignment.get("risk") not in risk_order,
            capability.get("riskCeiling") not in risk_order,
            assignment.get("risk") in risk_order and capability.get("riskCeiling") in risk_order
            and risk_order.index(assignment["risk"]) > risk_order.index(capability["riskCeiling"]),
            assignment.get("idempotencyMode") != capability.get("idempotencyMode"),
            assignment.get("readbackMode") != capability.get("readbackMode"),
            int(assignment.get("timeoutMs") or 0) > int(capability.get("maxDurationMs") or 0),
            int(assignment.get("maxOutputBytes") or 0) > int(capability.get("maxOutputBytes") or 0),
            any(not any(resource == scope or resource.startswith(f"{scope}:") for scope in scopes)
                for resource in assignment.get("resourceKeys", [])),
        )):
            raise ConstructExecutionError(
                "HYDRO_EXECUTION_CAPABILITY_INVALID",
                "Hydro assignment is outside the signed capability manifest", 403,
            )
    return manifest


def _validate_program(program: Any, intent: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {
        "contract", "executionId", "ownerPrincipalId", "programId", "itemId", "sourceConstructId",
        "responsibleConstructId", "threadId", "sessionId", "branchId", "goalRevision", "workHeadEventId",
        "workHeadSha256", "workStateReceiptSha256", "decisionHash", "nextActionHash",
        "preparedContextReceiptSha256", "contextPolicyVersion", "steps", "policy", "budgets", "createdAt", "definitionHash",
    }
    if not isinstance(program, dict) or set(program) not in {frozenset(required), frozenset(required | {"hydroGraphBinding"})}:
        raise ConstructExecutionError("EXECUTION_PROGRAM_INVALID", "execution program fields are invalid")
    program = dict(program)
    if program.get("contract") != EXECUTION_PROGRAM or program.get("contextPolicyVersion") != "chatty-context-sea-policy/v1.2":
        raise ConstructExecutionError("EXECUTION_PROGRAM_INVALID", "program contract is invalid")
    body = {k: v for k, v in program.items() if k != "definitionHash"}
    if program.get("definitionHash") != _sha(body) or len(_bytes({"program": program})) > _MAX_EVENT_BYTES - 2048:
        raise ConstructExecutionError("EXECUTION_PROGRAM_HASH_INVALID", "program hash/capacity is invalid")
    steps = program.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ConstructExecutionError("EXECUTION_PROGRAM_STEPS_INVALID", "execution requires one to 32 canonical steps")
    step_ids: set[str] = set()
    action_ids: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ConstructExecutionError("EXECUTION_STEP_INVALID", "execution step is invalid")
        step_fields = {"contract", "stepId", "actionId", "ordinal", "kind", "operation", "required",
                       "dependencyStepIds", "responsibleConstructId", "capabilityId", "hostId",
                       "inputArtifacts", "argumentsArtifact", "argumentsSha256", "resourceKeys", "risk",
                       "providerCandidates", "idempotencyMode", "readbackMode", "timeoutMs",
                       "maxOutputBytes", "completionFactKinds", "stepHash"}
        if frozenset(step) not in {frozenset(step_fields), frozenset(step_fields | {"hydroScope"})}:
            raise ConstructExecutionError("EXECUTION_STEP_INVALID", "execution step fields are invalid")
        step_id, action_id = _id(step.get("stepId"), "stepId"), _id(step.get("actionId"), "actionId")
        dependencies = step.get("dependencyStepIds")
        if any((step_id in step_ids, action_id in action_ids, step.get("ordinal") != index + 1,
                step.get("operation") not in OPERATIONS, not isinstance(step.get("required"), bool),
                not isinstance(dependencies, list) or len(dependencies) > 32 or len(set(dependencies or [])) != len(dependencies or []),
                not isinstance(step.get("resourceKeys"), list) or len(step.get("resourceKeys")) > 32
                or len(set(step.get("resourceKeys") or [])) != len(step.get("resourceKeys") or []))):
            raise ConstructExecutionError("EXECUTION_STEP_SCOPE_INVALID", "execution step scope is invalid")
        if step.get("stepHash") != _sha({k: v for k, v in step.items() if k != "stepHash"}):
            raise ConstructExecutionError("EXECUTION_STEP_HASH_INVALID", "execution step hash is invalid")
        if not isinstance(step.get("argumentsArtifact"), dict) \
                or step["argumentsArtifact"].get("sha256") != step.get("argumentsSha256"):
            raise ConstructExecutionError("EXECUTION_ARGUMENT_ARTIFACT_MISMATCH", "execution step argument artifact mismatch")
        step_ids.add(step_id)
        action_ids.add(action_id)
    if any(dependency not in step_ids or dependency == step["stepId"]
           for step in steps for dependency in step["dependencyStepIds"]):
        raise ConstructExecutionError("EXECUTION_PROGRAM_STEPS_INVALID", "execution dependencies are invalid")
    by_id = {step["stepId"]: step for step in steps}
    depths: dict[str, int] = {}
    visiting: set[str] = set()
    def depth(step_id: str) -> int:
        if step_id in visiting:
            raise ConstructExecutionError("EXECUTION_PROGRAM_CYCLE_INVALID", "execution graph contains a cycle")
        if step_id in depths:
            return depths[step_id]
        visiting.add(step_id)
        result = 1 + max([depth(value) for value in by_id[step_id]["dependencyStepIds"]] or [0])
        visiting.remove(step_id)
        if result > 8:
            raise ConstructExecutionError("EXECUTION_PROGRAM_DEPTH_EXCEEDED", "execution graph depth exceeds eight")
        depths[step_id] = result
        return result
    for step_id in step_ids:
        depth(step_id)
    policy = program.get("policy") or {}
    budgets = program.get("budgets") or {}
    if set(policy) != {"completionMode", "optionalFailureMode", "providerFallbackMode"} \
            or set(budgets) != {"maxParallel", "maxAttemptsPerStep", "maxDurationMs"} \
            or policy.get("completionMode") not in {"all_required", "quorum"} \
            or policy.get("optionalFailureMode") not in {"fail", "degraded"} \
            or policy.get("providerFallbackMode") != "before_first_output_only" \
            or not isinstance(budgets.get("maxParallel"), int) or isinstance(budgets.get("maxParallel"), bool) \
            or not 1 <= budgets["maxParallel"] <= 4 \
            or not isinstance(budgets.get("maxAttemptsPerStep"), int) or isinstance(budgets.get("maxAttemptsPerStep"), bool) \
            or not 1 <= budgets["maxAttemptsPerStep"] <= 2 \
            or not isinstance(budgets.get("maxDurationMs"), int) or isinstance(budgets.get("maxDurationMs"), bool) \
            or not 1_000 <= budgets["maxDurationMs"] <= 86_400_000:
        raise ConstructExecutionError("EXECUTION_PROGRAM_POLICY_INVALID", "execution policy is invalid")
    intent_fields = {
        "contract", "intentId", "executionId", "programId", "itemId", "ownerPrincipalId", "sourceConstructId",
        "responsibleConstructId", "threadId", "sessionId", "branchId", "goalRevision", "workHeadEventId",
        "workHeadSha256", "workStateReceiptSha256", "decisionHash", "nextActionHash", "preparedContextReceiptSha256",
        "capabilityManifestPayloadSha256", "proposalArtifactId", "proposalPayloadSha256", "actionClass", "operation", "hostId", "capabilityId", "inputArtifacts",
        "argumentsArtifact", "argumentsSha256", "resourceKeys", "risk", "providerCandidates", "idempotencyMode", "readbackMode",
        "timeoutMs", "maxOutputBytes", "completionFactKinds", "createdAt", "intentHash",
    }
    intent = _exact(intent, intent_fields, "workExecutionIntent")
    if intent.get("contract") != WORK_EXECUTION_INTENT or intent.get("intentHash") != _sha({k: v for k, v in intent.items() if k != "intentHash"}):
        raise ConstructExecutionError("EXECUTION_INTENT_HASH_INVALID", "work execution intent hash is invalid")
    for field in ("executionId", "programId", "itemId", "ownerPrincipalId", "sourceConstructId", "responsibleConstructId",
                  "threadId", "sessionId", "branchId", "goalRevision", "workHeadEventId", "workHeadSha256",
                  "workStateReceiptSha256", "decisionHash", "nextActionHash", "preparedContextReceiptSha256"):
        if intent.get(field) != program.get(field):
            raise ConstructExecutionError("EXECUTION_INTENT_SCOPE_INVALID", f"intent {field} mismatch")
    single_step = len(steps) == 1
    hydro_fanout = len(steps) > 1 and intent.get("operation") == "hydro.graph.dispatch" \
        and all(step.get("kind") in {"hydro_worker", "hydro_synthesis"} and step.get("operation") == "hydro.graph.dispatch"
                for step in steps)
    if not single_step and not hydro_fanout:
        raise ConstructExecutionError("EXECUTION_INTENT_STEP_MISMATCH", "multi-step execution must be a bounded Hydro fan-out")
    if single_step:
        step = steps[0]
        if step.get("responsibleConstructId") != program.get("responsibleConstructId"):
            raise ConstructExecutionError("EXECUTION_STEP_SCOPE_INVALID", "single execution step principal mismatch")
        for intent_field, step_field in (("operation", "operation"), ("hostId", "hostId"), ("capabilityId", "capabilityId"),
                                         ("argumentsArtifact", "argumentsArtifact"), ("argumentsSha256", "argumentsSha256"), ("inputArtifacts", "inputArtifacts"),
                                         ("resourceKeys", "resourceKeys"), ("completionFactKinds", "completionFactKinds")):
            if intent.get(intent_field) != step.get(step_field):
                raise ConstructExecutionError("EXECUTION_INTENT_STEP_MISMATCH", f"intent {intent_field} mismatch")
    else:
        binding = _validate_hydro_binding(program.get("hydroGraphBinding"))
        scopes = [_validate_hydro_scope(step.get("hydroScope")) for step in steps]
        workers = [step for step in steps if step.get("kind") == "hydro_worker"]
        synthesis = [step for step in steps if step.get("kind") == "hydro_synthesis"]
        if any((
            len(synthesis) != 1, not workers, synthesis and synthesis[0].get("required") is not True,
            synthesis and synthesis[0].get("ordinal") != len(steps),
            synthesis and synthesis[0].get("stepId") != binding.get("synthesisAssignmentId"),
            synthesis and synthesis[0].get("dependencyStepIds") != [step["stepId"] for step in workers],
            binding.get("sourceArgumentsSha256") != intent.get("argumentsSha256"),
            binding.get("maxParallel") != (program.get("budgets") or {}).get("maxParallel"),
            binding.get("assignmentIds") != [step["stepId"] for step in steps],
            binding.get("assignmentHashes") != [scope["assignmentHash"] for scope in scopes],
            any(scope.get("graphId") != binding.get("graphId")
                or scope.get("graphPayloadSha256") != binding.get("graphPayloadSha256")
                or scope.get("parentExecutionId") != program.get("executionId")
                or scope.get("parentResponsibleConstructId") != program.get("responsibleConstructId")
                or scope.get("workerPrincipalId") != step.get("responsibleConstructId")
                or scope.get("assignmentId") != step.get("stepId")
                or scope.get("assignmentKind") != ("synthesis" if step.get("kind") == "hydro_synthesis" else "worker")
                for scope, step in zip(scopes, steps)),
        )):
            raise ConstructExecutionError("HYDRO_EXECUTION_PROGRAM_BINDING_INVALID", "Hydro program differs from signed graph", 403)
    return program, intent


def _approval_disclosure(
    program: dict[str, Any], intent: dict[str, Any], action_disclosures: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project only typed effect facts; argument values never cross this boundary."""
    program, intent = _validate_program(program, intent)
    steps = []
    for raw in sorted(program["steps"], key=lambda entry: entry.get("ordinal", 0)):
        reference = _exact(raw.get("argumentsArtifact"), {"artifactId", "sha256", "mediaType"},
                           "executionApprovalDisclosure.argumentsArtifact")
        step = {
            "stepId": _id(raw.get("stepId"), "stepId"),
            "ordinal": raw.get("ordinal"),
            "operation": raw.get("operation"),
            "required": raw.get("required"),
            "argumentsArtifactId": _id(reference.get("artifactId"), "argumentsArtifactId"),
            "argumentsSha256": _digest(raw.get("argumentsSha256"), "argumentsSha256"),
            "resourceKeys": raw.get("resourceKeys"),
            "risk": raw.get("risk"),
            "providerCandidates": raw.get("providerCandidates"),
            "idempotencyMode": raw.get("idempotencyMode"),
            "readbackMode": raw.get("readbackMode"),
            "timeoutMs": raw.get("timeoutMs"),
            "maxOutputBytes": raw.get("maxOutputBytes"),
            "actionDisclosure": action_disclosures.get(raw.get("stepId")),
        }
        _exact(step, _APPROVAL_DISCLOSURE_STEP_FIELDS, "executionApprovalDisclosure.step")
        if any((
            not isinstance(step["ordinal"], int) or isinstance(step["ordinal"], bool),
            step["operation"] not in OPERATIONS,
            not isinstance(step["required"], bool),
            not isinstance(step["resourceKeys"], list) or len(step["resourceKeys"]) > 32,
            not isinstance(step["providerCandidates"], list) or len(step["providerCandidates"]) > 3,
            step["risk"] not in {"low", "moderate", "high", "critical"},
            step["idempotencyMode"] not in {"none", "native_exact", "readback_proven"},
            step["readbackMode"] not in {"none", "signed"},
            not isinstance(step["timeoutMs"], int) or isinstance(step["timeoutMs"], bool),
            not isinstance(step["maxOutputBytes"], int) or isinstance(step["maxOutputBytes"], bool),
            reference.get("sha256") != step["argumentsSha256"],
            not isinstance(step["actionDisclosure"], dict),
        )):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_INVALID", "approval disclosure step is invalid", 409,
            )
        steps.append(step)
    if [entry["ordinal"] for entry in steps] != list(range(1, len(steps) + 1)):
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_INVALID", "approval disclosure step order is invalid", 409,
        )
    body = {
        "contract": APPROVAL_DISCLOSURE,
        "executionId": program["executionId"],
        "programId": program["programId"],
        "itemId": program["itemId"],
        "definitionHash": program["definitionHash"],
        "workExecutionIntentHash": intent["intentHash"],
        "steps": steps,
    }
    if len(_bytes(body)) > 32 * 1024:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_CAPACITY_EXCEEDED", "approval disclosure exceeds 32 KiB", 413,
        )
    return {**body, "approvalDisclosureSha256": _sha(body)}


def _validate_approval_disclosure(value: Any) -> dict[str, Any]:
    disclosure = _exact(value, _APPROVAL_DISCLOSURE_FIELDS, "executionApprovalDisclosure")
    if disclosure.get("contract") != APPROVAL_DISCLOSURE:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_INVALID", "approval disclosure contract is invalid", 409,
        )
    steps = disclosure.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_INVALID", "approval disclosure steps are invalid", 409,
        )
    for index, step in enumerate(steps):
        _exact(step, _APPROVAL_DISCLOSURE_STEP_FIELDS, f"executionApprovalDisclosure.steps[{index}]")
    body = {key: entry for key, entry in disclosure.items() if key != "approvalDisclosureSha256"}
    if disclosure.get("approvalDisclosureSha256") != _sha(body) or len(_bytes(body)) > 32 * 1024:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_HASH_INVALID", "approval disclosure hash is invalid", 409,
        )
    return disclosure


def _raw_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _approval_root(workspace_root: Any) -> tuple[str, str]:
    root = str(workspace_root or "")
    if not root.startswith("/") or "\x00" in root:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "workspace root is invalid", 409,
        )
    root_sha = _raw_sha(root)
    return f"workspace-{root_sha[:12]}", root_sha


def _approval_relative_target(value: Any) -> str:
    target = str(value or "")
    if not target or target.startswith("/") or "\x00" in target \
            or any(part in {"", ".", ".."} for part in target.split("/")):
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "relative target is invalid", 409,
        )
    return target


def _bounded_exact_patch_diff(before: str, after: str, target: str, create: bool) -> dict[str, Any]:
    text = "".join(difflib.unified_diff(
        [] if create else before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="/dev/null" if create else f"a/{target}",
        tofile=f"b/{target}",
        n=3,
        lineterm="\n",
    ))
    encoded = text.encode("utf-8")
    if len(encoded) > 16 * 1024:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DIFF_CAPACITY_EXCEEDED",
            "exact patch diff exceeds the bounded approval surface", 413,
        )
    return {
        "contract": "chatty-workspace-patch-diff-preview/v1",
        "format": "unified",
        "complete": True,
        "text": text,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "byteLength": len(encoded),
    }


def _assert_disclosure_safe_text(value: str) -> str:
    try:
        _assert_no_private_reasoning(value)
    except ConstructWorkLoopError as exc:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_SENSITIVE_VALUE",
            "approval disclosure value contains credentials, private keys, local paths, or prompt material", 409,
        ) from exc
    return value


def _safe_approval_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "approval option nesting is invalid", 409,
        )
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "approval option number is invalid", 409,
            )
        return value
    if isinstance(value, str):
        if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "approval option string is invalid", 409,
            )
        return _assert_disclosure_safe_text(value)
    if isinstance(value, list) and len(value) <= 32:
        return [_safe_approval_value(entry, depth=depth + 1) for entry in value]
    if isinstance(value, dict) and len(value) <= 32:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", str(key))
               or re.search(r"secret|token|password|credential|api[_-]?key|private[_-]?key", str(key), re.I)
               for key in value):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "approval option key is invalid", 409,
            )
        return {key: _safe_approval_value(value[key], depth=depth + 1) for key in sorted(value)}
    raise ConstructExecutionError(
        "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "approval option value is invalid", 409,
    )


def _action_approval_disclosure(
    operation: str, arguments: dict[str, Any], step: dict[str, Any], owner: str, *,
    artifact_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(arguments, dict) or _sha(arguments) != step.get("argumentsSha256"):
        raise ConstructExecutionError(
            "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_MISMATCH",
            "canonical argument bytes do not match the signed execution step", 409,
        )
    if operation == "workspace.file.read":
        required, allowed = {"contract", "workspaceRoot", "targetPath", "expectedSha256", "encoding"}, \
            {"contract", "workspaceRoot", "targetPath", "expectedSha256", "encoding"}
        if set(arguments) != required or arguments["contract"] != "chatty-workspace-file-read-operation/v1" \
                or arguments["encoding"] not in {"utf8", "base64"}:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "file-read arguments are invalid", 409)
        alias, root_sha = _approval_root(arguments["workspaceRoot"])
        expected = arguments["expectedSha256"]
        if expected is not None: _digest(expected, "expectedSha256")
        return {"contract": "chatty-workspace-file-read-approval/v1", "workspaceRootAlias": alias,
                "workspaceRootSha256": root_sha, "relativeTarget": _approval_relative_target(arguments["targetPath"]),
                "expectedSha256": expected, "expectedBytes": None, "encoding": arguments["encoding"]}
    if operation == "workspace.patch.apply":
        fields = {"contract", "workspaceRoot", "targetPath", "create", "beforeSha256", "afterContent", "afterSha256"}
        if set(arguments) != fields or arguments["contract"] != "chatty-workspace-patch-operation/v1" \
                or not isinstance(arguments["create"], bool) or not isinstance(arguments["afterContent"], str) \
                or _raw_sha(arguments["afterContent"]) != arguments["afterSha256"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "patch arguments are invalid", 409)
        _digest(arguments["beforeSha256"], "beforeSha256"); _digest(arguments["afterSha256"], "afterSha256")
        alias, root_sha = _approval_root(arguments["workspaceRoot"])
        target = _approval_relative_target(arguments["targetPath"])
        metadata = artifact_metadata or {}
        preimage = metadata.get("content")
        reference = metadata.get("reference")
        receipt = metadata.get("receipt")
        expected_preimage_fields = {
            "contract", "workspaceRootSha256", "targetPath", "targetPathSha256",
            "state", "content", "contentSha256", "contentBytes",
        }
        if not isinstance(preimage, dict) or set(preimage) != expected_preimage_fields \
                or preimage.get("contract") != "chatty-workspace-patch-preimage-evidence/v1" \
                or preimage.get("workspaceRootSha256") != root_sha \
                or preimage.get("targetPath") != target \
                or preimage.get("targetPathSha256") != _raw_sha(target) \
                or not isinstance(reference, dict) or not isinstance(receipt, dict) \
                or receipt.get("contract") != "life-vvault-execution-input-artifact/v1" \
                or receipt.get("artifactId") != reference.get("artifactId") \
                or receipt.get("sha256") != reference.get("sha256") \
                or _sha(preimage) != reference.get("sha256"):
            raise ConstructExecutionError(
                "EXECUTION_PATCH_PREIMAGE_EVIDENCE_INVALID",
                "canonical patch preimage evidence is invalid", 409,
            )
        state, content = preimage.get("state"), preimage.get("content")
        before = "" if state == "target_absent" else content
        if any((
            state not in {"present", "target_absent"},
            arguments["create"] != (state == "target_absent"),
            state == "target_absent" and content is not None,
            state == "present" and not isinstance(content, str),
            preimage.get("contentSha256") != _raw_sha(before),
            preimage.get("contentSha256") != arguments["beforeSha256"],
            preimage.get("contentBytes") != len(before.encode("utf-8")),
        )):
            raise ConstructExecutionError(
                "EXECUTION_PATCH_PREIMAGE_EVIDENCE_INVALID",
                "canonical patch preimage differs from approved arguments", 409,
            )
        diff = _bounded_exact_patch_diff(before, arguments["afterContent"], target, arguments["create"])
        diff["argumentsSha256"] = step["argumentsSha256"]
        diff["preimageArtifactSha256"] = reference["sha256"]
        return {"contract": "chatty-workspace-patch-approval/v1", "workspaceRootAlias": alias,
                "workspaceRootSha256": root_sha, "relativeTarget": target,
                "create": arguments["create"], "beforeSha256": arguments["beforeSha256"],
                "afterSha256": arguments["afterSha256"], "beforeBytes": preimage["contentBytes"],
                "afterBytes": len(arguments["afterContent"].encode("utf-8")),
                "preimageEvidence": {
                    "contract": "life-vvault-workspace-patch-preimage-reference/v1",
                    "artifactId": reference["artifactId"], "artifactSha256": reference["sha256"],
                    "receiptPayloadSha256": receipt.get("payloadSha256"), "state": state,
                    "contentSha256": preimage["contentSha256"], "contentBytes": preimage["contentBytes"],
                },
                "diffPreview": diff}
    if operation == "network.https.fetch":
        required = {"contract", "url", "allowedHosts", "allowedContentTypes"}
        if not required.issubset(arguments) or set(arguments) - (required | {"method", "accept"}) \
                or arguments["contract"] != "chatty-https-fetch-operation/v1":
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "HTTPS arguments are invalid", 409)
        parsed = urlsplit(str(arguments["url"] or ""))
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment \
                or host not in arguments["allowedHosts"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "HTTPS URL is not allowed", 409)
        search = parsed.query or ""
        return {"contract": "chatty-https-fetch-approval/v1", "scheme": "https", "host": host,
                "port": parsed.port or 443, "path": parsed.path or "/", "queryPresent": bool(search),
                "querySha256": _raw_sha(search) if search else None,
                "method": "HEAD" if arguments.get("method") == "HEAD" else "GET"}
    if operation == "provider.generate":
        fields = {"contract", "routeId", "provider", "model", "routeRevision", "messages", "options"}
        if set(arguments) != fields or arguments["contract"] != "chatty-provider-generation-operation/v1" \
                or not isinstance(arguments["messages"], list) or len(arguments["messages"]) > 256:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "provider arguments are invalid", 409)
        options = _safe_approval_value(arguments["options"])
        if not isinstance(options, dict) or len(_bytes(options)) > 8 * 1024:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "provider options are invalid", 409)
        messages = []
        for ordinal, message in enumerate(arguments["messages"], 1):
            if not isinstance(message, dict) or set(message) != {"role", "content"} \
                    or message["role"] not in {"system", "developer", "user", "assistant", "tool"} \
                    or not isinstance(message["content"], str):
                raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "provider message is invalid", 409)
            messages.append({"ordinal": ordinal, "role": message["role"],
                             "contentSha256": _raw_sha(message["content"]),
                             "contentBytes": len(message["content"].encode("utf-8"))})
        return {"contract": "chatty-provider-generation-approval/v1", "routeId": arguments["routeId"],
                "provider": arguments["provider"], "model": arguments["model"],
                "routeRevision": arguments["routeRevision"], "options": options,
                "optionsSha256": _sha(options), "messageCount": len(messages), "messages": messages}
    if operation == "workspace.command.execute":
        fields = {"contract", "commandId", "executable", "argv", "workspaceRoot", "timeoutMs"}
        if set(arguments) != fields or arguments["contract"] != "chatty-workspace-command-operation/v1" \
                or not isinstance(arguments["argv"], list) or len(arguments["argv"]) > 64 \
                or any(not isinstance(value, str) or len(value) > 512 or "\x00" in value for value in arguments["argv"]):
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "command arguments are invalid", 409)
        alias, root_sha = _approval_root(arguments["workspaceRoot"])
        executable = _assert_disclosure_safe_text(str(arguments["executable"] or ""))
        argv = [_assert_disclosure_safe_text(value) for value in arguments["argv"]]
        return {"contract": "chatty-workspace-command-approval/v1", "workspaceRootAlias": alias,
                "workspaceRootSha256": root_sha, "commandId": arguments["commandId"],
                "executable": executable, "argv": argv,
                "timeoutMs": arguments["timeoutMs"], "credentialReferenceIds": []}
    if operation == "hydro.graph.dispatch":
        allowed = {"contract", "graph", "graphSha256", "limits", "maxOutputBytes"}
        if not {"contract", "graph", "graphSha256", "limits"}.issubset(arguments) or set(arguments) - allowed \
                or arguments["contract"] != "chatty-hydro-graph-dispatch-operation/v1" \
                or _sha(arguments["graph"]) != arguments["graphSha256"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "Hydro arguments are invalid", 409)
        graph, limits = arguments["graph"], arguments["limits"]
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or len(graph["nodes"]) > 32 \
                or not isinstance(graph.get("workers"), list) or len(graph["workers"]) > 4 \
                or not isinstance(limits, dict) or set(limits) != {"maxOutputBytes", "maxDurationMs"}:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "Hydro graph is invalid", 409)
        return {"contract": "chatty-hydro-dispatch-approval/v1", "graphSha256": arguments["graphSha256"],
                "nodeCount": len(graph["nodes"]), "workerCount": len(graph["workers"]),
                "resourceKeys": step["resourceKeys"], "limits": {
                    "maxOutputBytes": arguments.get("maxOutputBytes", limits["maxOutputBytes"]),
                    "maxDurationMs": limits["maxDurationMs"]}}
    if operation == "artifact.readback.verify":
        fields = {"contract", "artifactId", "ownerPrincipalId", "expectedSha256"}
        if set(arguments) != fields or arguments["contract"] != "chatty-artifact-readback-verify-operation/v1" \
                or arguments["ownerPrincipalId"] != owner or not artifact_metadata \
                or artifact_metadata.get("content_sha256") != arguments["expectedSha256"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "artifact readback arguments are invalid", 409)
        return {"contract": "chatty-artifact-readback-approval/v1", "artifactId": arguments["artifactId"],
                "artifactSha256": arguments["expectedSha256"], "artifactBytes": arguments.get("artifactBytes"),
                "mediaType": arguments.get("mediaType")}
    raise ConstructExecutionError(
        "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID", "operation has no informed-approval disclosure", 409,
    )


def _event_id(execution_id: str, sequence: int) -> str:
    return f"execution-event-{_sha({'executionId': execution_id, 'sequence': sequence})[:40]}"


def _signed(body: dict[str, Any], private_key_pem: str | None) -> dict[str, Any]:
    signature = canonical_projection_signing.sign_canonical_payload(body, private_key_pem=private_key_pem)
    return {**body, "payloadSha256": _sha(body), **signature}


def _event(program: dict[str, Any], auth: dict[str, Any], payload: dict[str, Any], actor: dict[str, str],
           occurred_at: datetime, evidence_digest: str | None = None) -> dict[str, Any]:
    body = {
        "eventId": _event_id(program["executionId"], auth["expectedSequence"]),
        "executionId": program["executionId"], "ownerPrincipalId": program["ownerPrincipalId"],
        "programId": program["programId"], "itemId": program["itemId"],
        "sourceConstructId": program["sourceConstructId"], "responsibleConstructId": program["responsibleConstructId"],
        "threadId": program["threadId"], "sessionId": program["sessionId"], "branchId": program["branchId"],
        "eventType": auth["eventType"], "sequence": auth["expectedSequence"],
        "parentEventId": auth["expectedHeadEventId"], "parentEventSha256": auth["expectedHeadSha256"],
        "idempotencyKey": auth["idempotencyKey"],
        "requestDigest": _sha({"payload": payload, "authorization": auth}),
        "coreAuthorizationHash": auth["payloadSha256"], "evidenceDigest": evidence_digest,
        "occurredAt": _iso(occurred_at), "actor": actor, "payload": payload, "payloadSha256": _sha(payload),
    }
    return {**body, "eventSha256": _sha(body)}


def _envelope(event: dict[str, Any], private_key_pem: str | None) -> dict[str, Any]:
    signature = canonical_projection_signing.sign_canonical_payload(event, private_key_pem=private_key_pem)
    result = {"contract": EVENT_ENVELOPE, **signature, "event": event, "payloadSha256": _sha(event)}
    if len(_bytes(result)) > _MAX_EVENT_BYTES:
        raise ConstructExecutionError("EXECUTION_EVENT_CAPACITY_EXCEEDED", "execution event exceeds 64 KiB")
    return result


def _row(value: Any) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


def _execution_step_id(event_type: str, payload: dict[str, Any]) -> str | None:
    key = {
        "execution_lease_acquired": "lease", "execution_lease_renewed": "lease",
        "execution_attempt_started": "startPermit", "execution_effect_dispatched": "dispatchMarker",
        "execution_attempt_outcome_recorded": "hostReceipt", "execution_readback_recorded": "readback",
        "execution_recovery_selected": "recovery",
        "execution_hydro_synthesis_inputs_resolved": "resolution",
    }.get(event_type)
    if key:
        value = payload.get(key)
        if not isinstance(value, dict):
            return None
        return value.get("synthesisStepId") if event_type == "execution_hydro_synthesis_inputs_resolved" \
            else value.get("stepId")
    if event_type in {
        "execution_step_verified", "execution_step_completed", "execution_step_failed",
        "execution_outcome_unknown", "execution_cancel_requested", "execution_cancel_acknowledged",
    }:
        return payload.get("stepId")
    return None


def _step_event_documents(events: list[dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    result = []
    step_scoped = {
        "execution_lease_acquired", "execution_lease_renewed", "execution_attempt_started",
        "execution_hydro_synthesis_inputs_resolved",
        "execution_effect_dispatched", "execution_attempt_outcome_recorded", "execution_readback_recorded",
        "execution_recovery_selected", "execution_step_verified", "execution_step_completed",
        "execution_step_failed", "execution_outcome_unknown", "execution_cancel_requested",
        "execution_cancel_acknowledged",
    }
    for entry in events:
        event = entry["event"]
        candidate = _execution_step_id(event["eventType"], event.get("payload") or {})
        # Canonical v1 contracts always carry stepId.  The null fallback keeps
        # previously stored, schema-validated single-step evidence readable.
        if candidate == step_id or (candidate is None and event["eventType"] in step_scoped):
            result.append(event)
    return result


def _assert_hydro_document_scope(program: dict[str, Any], document: dict[str, Any]) -> dict[str, Any] | None:
    step = next((entry for entry in program.get("steps", [])
                 if entry.get("stepId") == document.get("stepId")), None)
    if not step:
        raise ConstructExecutionError("EXECUTION_STEP_NOT_FOUND", "signed evidence step is not canonical", 409)
    expected = step.get("hydroScope")
    if document.get("hydroScope") != expected:
        raise ConstructExecutionError(
            "HYDRO_EXECUTION_SIGNED_SCOPE_INVALID",
            "signed execution evidence differs from the canonical Hydro assignment", 409,
        )
    return step


def _assert_transition(events: list[dict[str, Any]], event_type: str, payload: dict[str, Any]) -> None:
    """Mirror the security-critical lifecycle edges without becoming Core's fold authority."""
    types = [entry["event"]["eventType"] for entry in events]
    last = types[-1] if types else None
    if not types:
        if event_type != "execution_requested":
            raise ConstructExecutionError("EXECUTION_TRANSITION_INVALID", "execution must begin with execution_requested", 409)
        return
    terminal = {"execution_completed", "execution_failed", "execution_rejected"}
    if last in terminal:
        raise ConstructExecutionError("EXECUTION_TERMINAL", "terminal execution cannot advance", 409)
    step_id = _execution_step_id(event_type, payload)
    step_events = _step_event_documents(events, step_id) if step_id else [entry["event"] for entry in events]
    step_types = [entry["eventType"] for entry in step_events]
    step_last = step_types[-1] if step_types else None
    required_previous = {
        "approval_capability_issued": {"execution_requested"},
        "execution_authorized": {"approval_capability_issued"},
        "execution_hydro_synthesis_inputs_resolved": {"execution_authorized"},
        "execution_lease_acquired": {"execution_authorized", "execution_recovery_selected",
                                      "execution_hydro_synthesis_inputs_resolved"},
        "execution_attempt_started": {"execution_lease_acquired", "execution_lease_renewed"},
        "execution_effect_dispatched": {"execution_attempt_started"},
        "execution_attempt_outcome_recorded": {"execution_attempt_started", "execution_effect_dispatched", "execution_cancel_requested"},
        "execution_readback_recorded": {"execution_attempt_outcome_recorded", "execution_cancel_acknowledged", "execution_outcome_unknown", "execution_step_failed"},
        "execution_recovery_selected": {"execution_readback_recorded", "execution_attempt_outcome_recorded", "execution_outcome_unknown", "execution_step_failed"},
        "execution_step_verified": {"execution_attempt_outcome_recorded", "execution_cancel_acknowledged", "execution_readback_recorded", "execution_recovery_selected"},
        "execution_step_completed": {"execution_step_verified"},
        "execution_cancel_acknowledged": {"execution_attempt_outcome_recorded"},
    }
    if event_type in required_previous:
        predecessor = step_last if step_id else last
        if event_type == "execution_lease_acquired" and predecessor is None \
                and "execution_authorized" in types:
            predecessor = "execution_authorized"
        if event_type == "execution_hydro_synthesis_inputs_resolved" and predecessor is None \
                and "execution_authorized" in types:
            predecessor = "execution_authorized"
        if predecessor not in required_previous[event_type]:
            raise ConstructExecutionError(
                "EXECUTION_TRANSITION_INVALID", f"{event_type} cannot follow {predecessor}", 409,
            )
    if event_type == "approval_capability_issued" and event_type in types:
        raise ConstructExecutionError("EXECUTION_APPROVAL_ALREADY_ISSUED", "approval is immutable", 409)
    if event_type == "execution_authorized" and event_type in types:
        raise ConstructExecutionError("EXECUTION_APPROVAL_ALREADY_CONSUMED", "approval is one-use", 409)
    if event_type == "execution_rejected" and any(value in types for value in {"approval_capability_issued", "execution_authorized"}):
        raise ConstructExecutionError("EXECUTION_REJECTION_TOO_LATE", "execution rejection is only valid before approval", 409)
    if event_type == "execution_cancel_acknowledged" and "execution_cancel_requested" not in step_types:
        raise ConstructExecutionError("EXECUTION_CANCEL_NOT_REQUESTED", "cancellation acknowledgement requires an owner cancellation request", 409)
    if event_type == "execution_outcome_unknown" and step_last == "execution_cancel_requested" \
            and (len(step_types) < 2 or step_types[-2] != "execution_effect_dispatched"):
        raise ConstructExecutionError("EXECUTION_CANCEL_RECONCILIATION_INVALID", "only a dispatched cancellation may become outcome unknown", 409)
    if event_type in {"execution_lease_acquired", "execution_lease_renewed"} and "execution_authorized" not in types:
        # A retry is legal only after a signed not-committed readback and a
        # Core recovery selection; both remain visible in the append-only chain.
        raise ConstructExecutionError("EXECUTION_NOT_AUTHORIZED", "lease requires consumed approval", 409)
    if event_type == "execution_lease_renewed" and step_last not in {"execution_lease_acquired", "execution_lease_renewed"}:
        raise ConstructExecutionError("EXECUTION_LEASE_RENEWAL_INVALID", "lease renewal requires current lease", 409)
    if event_type == "execution_recovery_selected":
        recovery = payload.get("recovery") or {}
        if recovery.get("selection") == "retry_not_committed":
            readbacks = [e["payload"].get("readback") for e in step_events if e["eventType"] == "execution_readback_recorded"]
            if step_last != "execution_readback_recorded" or not readbacks or readbacks[-1].get("outcome") != "not_committed":
                raise ConstructExecutionError("EXECUTION_RETRY_UNPROVEN", "retry requires signed not_committed readback", 409)
        if recovery.get("selection") == "provider_fallback":
            receipt = (step_events[-1].get("payload") or {}).get("hostReceipt") if step_events else None
            if step_last != "execution_attempt_outcome_recorded" or not isinstance(receipt, dict) or any((
                receipt.get("outcome") not in {"failed", "not_started"},
                receipt.get("effectCommitted") != "false",
                receipt.get("outputArtifacts") != [],
                receipt.get("outputSha256") is not None,
                receipt.get("providerDraftSha256") is not None,
            )):
                raise ConstructExecutionError(
                    "EXECUTION_PROVIDER_FALLBACK_UNPROVEN",
                    "provider fallback requires a current no-output failed provider attempt", 409,
                )
        if recovery.get("selection") in {"abandon_unknown", "compensate", "cancel"} and not recovery.get("ownerEvidenceReference"):
            raise ConstructExecutionError("EXECUTION_RECOVERY_OWNER_EVIDENCE_REQUIRED", "owner evidence is required", 409)
    if event_type == "execution_completed":
        completed_steps = {e["event"]["payload"].get("stepId") for e in events if e["event"]["eventType"] == "execution_step_completed"}
        program = events[0]["event"]["payload"].get("program") or {}
        required_steps = {step.get("stepId") for step in program.get("steps", []) if step.get("required")}
        if not required_steps.issubset(completed_steps):
            raise ConstructExecutionError("EXECUTION_COMPLETION_UNVERIFIED", "required execution steps are not verified", 409)
        optional_steps = {step.get("stepId") for step in program.get("steps", []) if not step.get("required")}
        failed_steps = {
            _execution_step_id(e["event"]["eventType"], e["event"].get("payload") or {})
            for e in events if e["event"]["eventType"] in {
                "execution_step_failed", "execution_outcome_unknown", "execution_cancel_acknowledged",
            }
        }
        optional_mode = (program.get("policy") or {}).get("optionalFailureMode")
        if optional_mode == "fail" and optional_steps.intersection(failed_steps):
            raise ConstructExecutionError(
                "EXECUTION_COMPLETION_OPTIONAL_FAILURE", "optional step failure policy requires execution failure", 409,
            )
        unresolved_optional = optional_steps - completed_steps - failed_steps
        if unresolved_optional:
            raise ConstructExecutionError(
                "EXECUTION_COMPLETION_PENDING", "optional execution steps are not terminal", 409,
            )


def _owner_evidence_from_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if event_type in {"execution_rejected", "execution_cancel_requested"}:
        return payload.get("ownerEvidence")
    if event_type == "execution_recovery_selected":
        return (payload.get("recovery") or {}).get("ownerEvidenceReference")
    return None


def _validate_recovery(value: Any) -> dict[str, Any]:
    fields = {
        "contract", "recoveryId", "executionId", "stepId", "attemptOrdinal", "selection",
        "readbackPayloadSha256", "ownerEvidenceReference", "recoveryCapability", "selectedAt", "recoveryHash",
    }
    if isinstance(value, dict) and "hydroScope" in value:
        fields.add("hydroScope")
    recovery = _exact(value, fields, "executionRecovery")
    if "hydroScope" in recovery:
        _validate_hydro_scope(recovery["hydroScope"])
    if recovery.get("contract") != EXECUTION_RECOVERY \
            or recovery.get("recoveryHash") != _sha({key: entry for key, entry in recovery.items() if key != "recoveryHash"}):
        raise ConstructExecutionError("EXECUTION_RECOVERY_HASH_INVALID", "execution recovery hash is invalid", 403)
    if recovery.get("selection") not in {
        "retry_not_committed", "provider_fallback", "finalize_committed", "abandon_unknown", "compensate", "cancel",
    }:
        raise ConstructExecutionError("EXECUTION_RECOVERY_SELECTION_INVALID", "execution recovery selection is invalid", 403)
    if recovery.get("selection") in {"retry_not_committed", "provider_fallback"} \
            and not isinstance(recovery.get("recoveryCapability"), dict):
        raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_REQUIRED", "retry requires one-use recovery capability", 403)
    if recovery.get("selection") not in {"retry_not_committed", "provider_fallback"} \
            and recovery.get("recoveryCapability") is not None:
        raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_INVALID", "recovery capability is invalid for selection", 403)
    if recovery.get("selection") == "provider_fallback" and any((
        recovery.get("readbackPayloadSha256") is not None,
        recovery.get("ownerEvidenceReference") is not None,
    )):
        raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_INVALID", "provider fallback cannot assert readback or owner evidence", 403)
    return recovery


@dataclass
class ConstructExecutionService:
    connect: Callable[[], Any] = chatty_body_service._connect
    private_key_pem: str | None = None
    core_public_key_pem: str | None = None
    core_key_id: str | None = None
    owner_public_key_pem: str | None = None
    owner_key_id: str | None = None
    host_key_resolver: Callable[[str], tuple[str, str] | None] | None = None

    def __post_init__(self) -> None:
        self.private_key_pem = self.private_key_pem or os.getenv("VVAULT_OFFLINE_SNAPSHOT_PRIVATE_KEY_PEM")
        if not self.core_public_key_pem:
            configured_pem, config_error, _source = _execution_authorization_public_key_config()
            self.core_public_key_pem = configured_pem
            self.core_key_config_error = config_error
        else:
            self.core_key_config_error = None
        self.core_key_id = self.core_key_id or os.getenv("CHATTY_EXECUTION_AUTHORIZATION_KEY_ID")
        self.owner_public_key_pem = self.owner_public_key_pem or os.getenv("CHATTY_OWNER_AUTHORIZATION_PUBLIC_KEY_PEM")
        self.owner_key_id = self.owner_key_id or os.getenv("CHATTY_OWNER_AUTHORIZATION_KEY_ID")

    def _host_key(self, host_id: str) -> tuple[str, str] | None:
        """Resolve only a host-domain key and reject Core-key reuse fail closed."""
        key_info = self.host_key_resolver(str(host_id or "")) if self.host_key_resolver else None
        if not key_info:
            return None
        _host_public, host_key_id = _public_key(key_info[0], key_info[1])
        if self.core_public_key_pem:
            _core_public, core_key_id = _public_key(self.core_public_key_pem, self.core_key_id)
            if host_key_id == core_key_id:
                raise ConstructExecutionError(
                    "EXECUTION_KEY_DOMAIN_COLLISION",
                    "execution host key must be distinct from the Core authorization key",
                    503,
                )
        return key_info[0], host_key_id

    @staticmethod
    def _database_now(cur: Any) -> datetime:
        cur.execute("SELECT transaction_timestamp() AS transaction_now")
        value = _row(cur.fetchone())
        if not value or value.get("transaction_now") is None:
            raise ConstructExecutionError(
                "EXECUTION_DATABASE_TIME_UNAVAILABLE", "database transaction time unavailable", 503,
            )
        instant = value["transaction_now"]
        if isinstance(instant, datetime):
            if instant.tzinfo is None:
                raise ConstructExecutionError(
                    "EXECUTION_DATABASE_TIME_INVALID", "database transaction time is not timezone-aware", 503,
                )
            return instant.astimezone(timezone.utc)
        return _time(instant, "transactionNow")

    def _program_row(self, cur: Any, owner: str, execution_id: str, lock: bool = False) -> dict[str, Any]:
        cur.execute(
            """SELECT * FROM ovvaults.construct_work_executions
               WHERE owner_user_id=%s AND execution_id=%s""" + (" FOR UPDATE" if lock else ""),
            (owner, execution_id),
        )
        row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("EXECUTION_NOT_FOUND", "execution not found", 404)
        return row

    def _approval_disclosure_row(
        self, cur: Any, *, owner: str, execution_id: str, disclosure_sha256: str,
        expected_head_event_id: str, expected_head_sha256: str, current: datetime,
    ) -> dict[str, Any]:
        cur.execute(
            """SELECT disclosure,envelope,expected_head_event_id,expected_head_sha256,expires_at
                 FROM ovvaults.construct_work_execution_approval_disclosures
                WHERE owner_user_id=%s AND execution_id=%s AND approval_disclosure_sha256=%s
                  AND expected_head_event_id=%s AND expected_head_sha256=%s
                ORDER BY created_at DESC LIMIT 1 FOR SHARE""",
            (owner, execution_id, disclosure_sha256, expected_head_event_id, expected_head_sha256),
        )
        row = _row(cur.fetchone())
        disclosure = (row or {}).get("disclosure")
        envelope = (row or {}).get("envelope")
        if isinstance(disclosure, str):
            disclosure = json.loads(disclosure)
        if isinstance(envelope, str):
            envelope = json.loads(envelope)
        if not row or _time(row.get("expires_at"), "approvalDisclosure.expiresAt") <= current:
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_REQUIRED",
                "a live canonical approval disclosure is required", 409,
            )
        disclosure = _validate_approval_disclosure(disclosure)
        vvault_public = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        verified = _verify_signed(
            envelope, fields=_APPROVAL_DISCLOSURE_ENVELOPE_FIELDS,
            contract=APPROVAL_DISCLOSURE_ENVELOPE,
            public_key_pem=vvault_public, expected_key_id=None, now=current,
        )
        if any((
            verified.get("authority") != AUTHORITY,
            verified.get("ownerPrincipalId") != owner,
            verified.get("executionId") != execution_id,
            verified.get("expectedHeadEventId") != expected_head_event_id,
            verified.get("expectedHeadSha256") != expected_head_sha256,
            verified.get("approvalDisclosure") != disclosure,
            verified.get("approvalDisclosureSha256") != disclosure_sha256,
            disclosure.get("approvalDisclosureSha256") != disclosure_sha256,
            verified.get("canonicalArgumentsVerified") is not True,
            verified.get("containsRawArguments") is not False,
            verified.get("containsCredentials") is not False,
            verified.get("containsPrivateReasoning") is not False,
        )):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_INVALID",
                "canonical approval disclosure evidence is invalid", 409,
            )
        return {**row, "disclosure": disclosure, "envelope": verified}

    def _canonical_approval_disclosure(
        self, cur: Any, *, owner: str, row: dict[str, Any],
    ) -> dict[str, Any]:
        program = row.get("execution_program")
        intent = row.get("execution_intent")
        if isinstance(program, str): program = json.loads(program)
        if isinstance(intent, str): intent = json.loads(intent)
        program, intent = _validate_program(program, intent)
        self._assert_input_artifacts(cur, owner=owner, program=program, intent=intent)
        action_disclosures: dict[str, dict[str, Any]] = {}
        for step in program["steps"]:
            reference = step["argumentsArtifact"]
            cur.execute(
                """SELECT program_id,media_type,content,content_sha256
                     FROM ovvaults.construct_work_execution_inputs
                    WHERE owner_user_id=%s AND artifact_id=%s FOR SHARE""",
                (owner, reference["artifactId"]),
            )
            artifact = _row(cur.fetchone())
            arguments = (artifact or {}).get("content")
            if isinstance(arguments, str): arguments = json.loads(arguments)
            if not artifact or artifact.get("program_id") != program["programId"] \
                    or artifact.get("media_type") != "application/json" \
                    or artifact.get("content_sha256") != reference["sha256"] \
                    or _sha(arguments) != reference["sha256"]:
                raise ConstructExecutionError(
                    "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_MISMATCH",
                    "canonical arguments differ from the signed execution step", 409,
                )
            artifact_metadata = None
            if step["operation"] == "workspace.patch.apply":
                patch_references = []
                for input_reference in step.get("inputArtifacts", []):
                    cur.execute(
                        """SELECT program_id,media_type,content,content_sha256,receipt
                             FROM ovvaults.construct_work_execution_inputs
                            WHERE owner_user_id=%s AND artifact_id=%s FOR SHARE""",
                        (owner, input_reference.get("artifactId")),
                    )
                    input_artifact = _row(cur.fetchone())
                    input_content = (input_artifact or {}).get("content")
                    if isinstance(input_content, str): input_content = json.loads(input_content)
                    if isinstance(input_content, dict) \
                            and input_content.get("contract") == "chatty-workspace-patch-preimage-evidence/v1":
                        patch_references.append((input_reference, input_artifact, input_content))
                if len(patch_references) != 1:
                    raise ConstructExecutionError(
                        "EXECUTION_PATCH_PREIMAGE_EVIDENCE_REQUIRED",
                        "one canonical patch preimage artifact is required", 409,
                    )
                input_reference, input_artifact, input_content = patch_references[0]
                input_receipt = input_artifact.get("receipt") if input_artifact else None
                if isinstance(input_receipt, str): input_receipt = json.loads(input_receipt)
                if not input_artifact or input_artifact.get("program_id") != program["programId"] \
                        or input_artifact.get("media_type") != "application/json" \
                        or input_artifact.get("content_sha256") != input_reference.get("sha256") \
                        or _sha(input_content) != input_reference.get("sha256"):
                    raise ConstructExecutionError(
                        "EXECUTION_PATCH_PREIMAGE_EVIDENCE_INVALID",
                        "canonical patch preimage artifact differs from the signed execution step", 409,
                    )
                artifact_metadata = {
                    "reference": input_reference, "content": input_content, "receipt": input_receipt,
                }
            if step["operation"] == "artifact.readback.verify":
                target_id = arguments.get("artifactId") if isinstance(arguments, dict) else None
                cur.execute(
                    """SELECT artifact_id,content_sha256,media_type,byte_length
                         FROM ovvaults.construct_work_execution_artifacts
                        WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s
                       UNION ALL
                       SELECT artifact_id,content_sha256,media_type,
                              octet_length(convert_to(content::text,'UTF8')) AS byte_length
                         FROM ovvaults.construct_work_execution_inputs
                        WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s""",
                    (owner, program["programId"], target_id, owner, program["programId"], target_id),
                )
                matches = [_row(value) for value in cur.fetchall()]
                if len(matches) != 1:
                    raise ConstructExecutionError(
                        "EXECUTION_APPROVAL_DISCLOSURE_ARGUMENT_INVALID",
                        "artifact readback target is not a unique canonical artifact", 409,
                    )
                artifact_metadata = matches[0]
            action_disclosures[step["stepId"]] = _action_approval_disclosure(
                step["operation"], arguments, step, owner, artifact_metadata=artifact_metadata,
            )
        return _approval_disclosure(program, intent, action_disclosures)

    def stage_input_artifact(self, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        request = _exact(request, {"programId", "artifactId", "mediaType", "content", "contentSha256", "idempotencyKey"}, "executionInputArtifact")
        program_id, artifact_id = _id(request["programId"], "programId"), _id(request["artifactId"], "artifactId")
        media_type = str(request["mediaType"] or "")
        if media_type != "application/json" or request["contentSha256"] != _sha(request["content"]):
            raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_INVALID", "input artifact media/hash is invalid", 403)
        if len(_bytes(request["content"])) > 16 * 1024 * 1024:
            raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_TOO_LARGE", "input artifact exceeds 16 MiB", 413)
        _assert_no_private_reasoning(request["content"])
        body = {"contract": "life-vvault-execution-input-artifact/v1", "ownerPrincipalId": str(owner_user_id),
                "programId": program_id, "artifactId": artifact_id, "sha256": request["contentSha256"],
                "mediaType": media_type, "issuedAt": _iso(datetime.now(timezone.utc))}
        receipt = _signed(body, self.private_key_pem)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM ovvaults.construct_work_programs WHERE owner_user_id=%s AND program_id=%s FOR SHARE", (owner_user_id, program_id))
                if not cur.fetchone():
                    raise ConstructExecutionError("EXECUTION_WORK_PROGRAM_NOT_FOUND", "work program not found", 404)
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_inputs
                      (owner_user_id,program_id,artifact_id,media_type,content,content_sha256,idempotency_key,
                       receipt,receipt_sha256,signature_algorithm,signature_key_id,signature)
                      VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s)
                      ON CONFLICT (owner_user_id,artifact_id) DO NOTHING""",
                    (owner_user_id, program_id, artifact_id, media_type, json.dumps(request["content"]), request["contentSha256"],
                     _id(request["idempotencyKey"], "idempotencyKey"), json.dumps(receipt), receipt["payloadSha256"],
                     receipt["algorithm"], receipt["keyId"], receipt["signature"]),
                )
                cur.execute("SELECT program_id,media_type,content,content_sha256,receipt FROM ovvaults.construct_work_execution_inputs WHERE owner_user_id=%s AND artifact_id=%s", (owner_user_id, artifact_id))
                stored = _row(cur.fetchone())
                stored_content = stored.get("content") if stored else None
                if isinstance(stored_content, str):
                    stored_content = json.loads(stored_content)
                if not stored or stored.get("program_id") != program_id or stored.get("media_type") != media_type \
                        or stored.get("content_sha256") != request["contentSha256"] or _sha(stored_content) != request["contentSha256"]:
                    raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_CONFLICT", "staged artifact canonical bytes differ", 409)
        return receipt

    def stage_recovery_artifacts(self, owner_user_id: str, request: dict[str, Any], *,
                                 trusted_internal: bool = False, now: datetime | None = None) -> dict[str, Any]:
        """Persist inert pre-commit proposal source/manifest bytes for crash recovery."""
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "recovery artifact staging requires trusted Chatty service", 403)
        request = _exact(
            request, {"contract", "proposalSourceArtifact", "capabilityManifestArtifact", "scopeSha256"},
            "workExecutionRecoveryArtifacts",
        )
        if request.get("contract") != "chatty-work-execution-recovery-artifacts/v1":
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_CONTRACT_INVALID", "recovery artifacts contract is invalid", 403)
        source_artifact = _validate_recovery_artifact(request["proposalSourceArtifact"], expected_kind="proposal_source")
        manifest_artifact = _validate_recovery_artifact(request["capabilityManifestArtifact"], expected_kind="capability_manifest")
        if source_artifact["scope"] != manifest_artifact["scope"] \
                or request.get("scopeSha256") != _sha(source_artifact["scope"]):
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_SCOPE_INVALID", "recovery artifact scopes differ", 403)
        current = now or datetime.now(timezone.utc)
        if current >= _time(source_artifact["expiresAt"], "expiresAt") \
                or current >= _time(manifest_artifact["expiresAt"], "expiresAt"):
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_EXPIRED", "recovery artifact expired", 403)
        source = _exact(source_artifact["proposalSource"], _PROPOSAL_SOURCE_FIELDS, "workExecutionProposalSource")
        if source.get("contract") != "chatty-work-execution-proposal-source/v1" \
                or source.get("advancementAuthority") is not False or source.get("effectAuthority") is not False \
                or source.get("sourceHash") != _sha({key: entry for key, entry in source.items() if key != "sourceHash"}) \
                or source.get("operation") not in OPERATIONS:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_SOURCE_INVALID", "proposal source is invalid", 403)
        manifest = manifest_artifact["capabilityManifest"]
        capabilities = manifest.get("capabilities") if isinstance(manifest, dict) else None
        matching = next((entry for entry in capabilities or [] if isinstance(entry, dict)
                         and entry.get("operation") == source.get("operation")
                         and entry.get("hostId") == source.get("hostId")), None)
        if not matching:
            raise ConstructExecutionError("EXECUTION_CAPABILITY_SCOPE_INVALID", "proposal source lacks a signed host capability", 403)
        manifest = _validate_capability_manifest(
            manifest, owner=str(owner_user_id), candidate={
                **source, "ownerPrincipalId": source_artifact["scope"].get("ownerPrincipalId"),
                "actionClass": matching.get("actionClass"),
                "capabilityManifestPayloadSha256": manifest.get("payloadSha256"),
            }, public_key_pem=self.core_public_key_pem, expected_key_id=self.core_key_id,
            host_key_resolver=self._host_key, now=current,
        )
        scope = source_artifact["scope"]
        if scope.get("ownerPrincipalId") != str(owner_user_id) or scope.get("sessionId") != scope.get("threadId"):
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_SCOPE_INVALID", "recovery artifact owner/session scope is invalid", 403)
        references = [_recovery_artifact_reference(source_artifact), _recovery_artifact_reference(manifest_artifact)]
        artifacts = [source_artifact, manifest_artifact]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.construct_id,p.thread_id,p.session_id,p.branch_id,e.resulting_goal_revision,
                              e.event_id,e.event_sha256
                         FROM ovvaults.construct_work_programs p
                         JOIN LATERAL (SELECT resulting_goal_revision,event_id,event_sha256
                                        FROM ovvaults.construct_work_events
                                       WHERE owner_user_id=p.owner_user_id AND program_id=p.program_id
                                       ORDER BY sequence DESC LIMIT 1) e ON TRUE
                        WHERE p.owner_user_id=%s AND p.program_id=%s FOR SHARE OF p""",
                    (str(owner_user_id), scope["programId"]),
                )
                work = _row(cur.fetchone())
                if not work or any((scope["sourceConstructId"] != work["construct_id"],
                                    scope["threadId"] != work["thread_id"], scope["sessionId"] != work["session_id"],
                                    scope["branchId"] != work["branch_id"], scope["goalRevision"] != work["resulting_goal_revision"],
                                    scope["preCommitHeadEventId"] != work["event_id"],
                                    scope["preCommitHeadSha256"] != work["event_sha256"])):
                    raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_STALE_SCOPE", "recovery artifact is not bound to the current work head", 409)
                self._assert_input_artifacts(
                    cur, owner=str(owner_user_id), program={"programId": scope["programId"]},
                    intent={"argumentsArtifact": source["argumentsArtifact"], "argumentsSha256": source["argumentsSha256"],
                            "inputArtifacts": source["inputArtifacts"]},
                )
                receipts = []
                for artifact, reference in zip(artifacts, references):
                    body = {
                        "contract": WORK_EXECUTION_RECOVERY_ARTIFACT_ENVELOPE,
                        "ownerPrincipalId": str(owner_user_id), "programId": scope["programId"],
                        "artifact": artifact, "reference": reference,
                        "advancementAuthority": False, "effectAuthority": False, "issuedAt": _iso(current),
                    }
                    receipt = _signed(body, self.private_key_pem)
                    cur.execute(
                        """INSERT INTO ovvaults.construct_work_execution_recovery_artifacts
                          (owner_user_id,program_id,artifact_id,artifact_kind,scope,scope_sha256,artifact,
                           artifact_sha256,reference,reference_sha256,receipt,receipt_sha256,expires_at)
                          VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb,%s,%s)
                          ON CONFLICT (owner_user_id,artifact_id) DO NOTHING""",
                        (str(owner_user_id), scope["programId"], reference["artifactId"], artifact["artifactKind"],
                         json.dumps(scope), reference["scopeSha256"], json.dumps(artifact), artifact["artifactHash"],
                         json.dumps(reference), reference["referenceHash"], json.dumps(receipt), receipt["payloadSha256"],
                         artifact["expiresAt"]),
                    )
                    cur.execute(
                        """SELECT artifact,reference,receipt FROM ovvaults.construct_work_execution_recovery_artifacts
                            WHERE owner_user_id=%s AND artifact_id=%s""",
                        (str(owner_user_id), reference["artifactId"]),
                    )
                    stored = _row(cur.fetchone())
                    stored_artifact = stored.get("artifact") if stored else None
                    stored_reference = stored.get("reference") if stored else None
                    if isinstance(stored_artifact, str): stored_artifact = json.loads(stored_artifact)
                    if isinstance(stored_reference, str): stored_reference = json.loads(stored_reference)
                    if not stored or stored_artifact != artifact or stored_reference != reference:
                        raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_CONFLICT", "recovery artifact ID is bound to different bytes", 409)
                    receipts.append(receipt)
        response_body = {
            "contract": WORK_EXECUTION_RECOVERY_ARTIFACTS_ENVELOPE,
            "ownerPrincipalId": str(owner_user_id), "programId": scope["programId"],
            "scopeSha256": request["scopeSha256"],
            "proposalSourceArtifact": receipts[0], "proposalSourceArtifactReference": references[0],
            "capabilityManifestArtifact": receipts[1], "capabilityManifestArtifactReference": references[1],
            "advancementAuthority": False, "effectAuthority": False, "issuedAt": _iso(current),
        }
        return _signed(response_body, self.private_key_pem)

    def get_recovery_artifact(self, owner_user_id: str, artifact_id: str, *, trusted_internal: bool = False,
                              now: datetime | None = None) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "recovery artifact read requires trusted Chatty service", 403)
        artifact_id = _id(artifact_id, "artifactId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT receipt FROM ovvaults.construct_work_execution_recovery_artifacts WHERE owner_user_id=%s AND artifact_id=%s", (str(owner_user_id), artifact_id))
                row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_NOT_FOUND", "recovery artifact not found", 404)
        receipt = row["receipt"] if isinstance(row["receipt"], dict) else json.loads(row["receipt"])
        verified = _verify_signed(
            receipt,
            fields=frozenset({"contract", "ownerPrincipalId", "programId", "artifact", "reference",
                              "advancementAuthority", "effectAuthority", "issuedAt", "payloadSha256",
                              "algorithm", "keyId", "signature"}),
            contract=WORK_EXECUTION_RECOVERY_ARTIFACT_ENVELOPE,
            public_key_pem=canonical_projection_signing.public_key_document(private_key_pem=self.private_key_pem)["publicKeyPem"],
            expected_key_id=None,
        )
        artifact = verified.get("artifact")
        reference = verified.get("reference")
        if verified.get("ownerPrincipalId") != str(owner_user_id) \
                or not isinstance(artifact, dict) or not isinstance(reference, dict) \
                or reference.get("artifactId") != artifact_id \
                or reference.get("payloadSha256") != artifact.get("artifactHash") \
                or (now or datetime.now(timezone.utc)) >= _time(reference.get("expiresAt"), "reference.expiresAt") \
                or artifact.get("artifactHash") != _sha({key: value for key, value in artifact.items() if key != "artifactHash"}):
            raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_INVALID", "stored recovery artifact failed readback", 409)
        return verified

    def stage_proposal(self, owner_user_id: str, request: dict[str, Any], *, trusted_internal: bool = False,
                       now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"candidate", "capabilityManifest"}, "executionProposalStage")
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "proposal staging requires trusted Chatty service", 403)
        fields = {"contract", "candidateId", "ownerPrincipalId", "programId", "itemId", "sourceConstructId",
                  "responsibleConstructId", "threadId", "sessionId", "branchId", "goalRevision", "workHeadEventId",
                  "workHeadSha256", "workStateReceiptSha256", "decisionHash", "nextActionHash",
                  "preparedContextReceiptSha256", "capabilityManifestPayloadSha256", "actionClass", "operation",
                  "hostId", "inputArtifacts", "argumentsArtifact", "argumentsSha256", "resourceKeys", "risk",
                  "providerCandidates", "timeoutMs", "maxOutputBytes", "completionFactKinds", "sourceKind",
                  "sourceEvidenceSha256", "createdAt", "advancementAuthority", "effectAuthority", "candidateHash"}
        candidate = _exact(request["candidate"], fields, "executionProposalCandidate")
        if candidate.get("contract") != "chatty-work-execution-proposal-candidate/v1" \
                or candidate.get("candidateHash") != _sha({k: v for k, v in candidate.items() if k != "candidateHash"}):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_HASH_INVALID", "proposal candidate hash is invalid", 403)
        if candidate.get("ownerPrincipalId") != str(owner_user_id) \
                or candidate.get("advancementAuthority") is not False or candidate.get("effectAuthority") is not False:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_AUTHORITY_INVALID", "proposal owner/authority is invalid", 403)
        if candidate.get("sourceKind") not in {"core_structured_inference", "authenticated_owner_typed"}:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_SOURCE_INVALID", "proposal source is invalid", 403)
        if candidate.get("operation") not in OPERATIONS or not isinstance(candidate.get("resourceKeys"), list) \
                or len(candidate["resourceKeys"]) > 32 or len(set(candidate["resourceKeys"])) != len(candidate["resourceKeys"]):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_OPERATION_INVALID", "proposal operation/resources invalid")
        manifest = _validate_capability_manifest(
            request["capabilityManifest"], owner=str(owner_user_id), candidate=candidate,
            public_key_pem=self.core_public_key_pem, expected_key_id=self.core_key_id,
            host_key_resolver=self._host_key, now=now or datetime.now(timezone.utc),
        )
        current = now or datetime.now(timezone.utc)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.construct_id,p.thread_id,p.session_id,p.branch_id,
                              e.resulting_goal_revision,e.event_id,e.event_sha256,e.event_type,e.payload
                         FROM ovvaults.construct_work_programs p
                         JOIN LATERAL (SELECT resulting_goal_revision,event_id,event_sha256,event_type,payload
                                        FROM ovvaults.construct_work_events
                                       WHERE owner_user_id=p.owner_user_id AND program_id=p.program_id
                                       ORDER BY sequence DESC LIMIT 1) e ON TRUE
                        WHERE p.owner_user_id=%s AND p.program_id=%s FOR SHARE OF p""",
                    (owner_user_id, candidate["programId"]),
                )
                work = _row(cur.fetchone())
                if not work or any((candidate["sourceConstructId"] != work["construct_id"],
                                    candidate["threadId"] != work["thread_id"], candidate["sessionId"] != work["session_id"],
                                    candidate["branchId"] != work["branch_id"], candidate["goalRevision"] != work["resulting_goal_revision"],
                                    candidate["workHeadEventId"] != work["event_id"], candidate["workHeadSha256"] != work["event_sha256"])):
                    raise ConstructExecutionError("EXECUTION_PROPOSAL_STALE_SCOPE", "proposal is not bound to current canonical work head", 409)
                work_payload = work.get("payload")
                if isinstance(work_payload, str):
                    work_payload = json.loads(work_payload)
                binding = _validate_proposal_binding(
                    (work_payload or {}).get("executionProposalBinding")
                    if work.get("event_type") == "next_action_proposed" else None
                )
                if any((binding.get("programId") != candidate["programId"],
                        binding.get("itemId") != candidate["itemId"],
                        binding.get("decisionHash") != candidate["decisionHash"],
                        binding.get("nextActionHash") != candidate["nextActionHash"],
                        binding.get("preparedContextReceiptSha256") != candidate["preparedContextReceiptSha256"],
                        binding.get("capabilityManifestPayloadSha256") != candidate["capabilityManifestPayloadSha256"])):
                    raise ConstructExecutionError("EXECUTION_PROPOSAL_BINDING_MISMATCH", "proposal differs from the canonical work execution binding", 409)
                recovered: dict[str, dict[str, Any]] = {}
                for field in ("proposalSourceArtifactReference", "capabilityManifestArtifactReference"):
                    reference = binding[field]
                    cur.execute(
                        """SELECT artifact,reference FROM ovvaults.construct_work_execution_recovery_artifacts
                            WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s FOR SHARE""",
                        (str(owner_user_id), candidate["programId"], reference["artifactId"]),
                    )
                    stored_recovery = _row(cur.fetchone())
                    stored_artifact = stored_recovery.get("artifact") if stored_recovery else None
                    stored_reference = stored_recovery.get("reference") if stored_recovery else None
                    if isinstance(stored_artifact, str): stored_artifact = json.loads(stored_artifact)
                    if isinstance(stored_reference, str): stored_reference = json.loads(stored_reference)
                    if not stored_recovery or stored_reference != reference \
                            or stored_artifact.get("artifactHash") != reference["payloadSha256"] \
                            or current >= _time(reference["expiresAt"], "reference.expiresAt"):
                        raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_NOT_FOUND", "bound recovery artifact is unavailable", 409)
                    recovered[reference["artifactKind"]] = stored_artifact
                source = recovered["proposal_source"]["proposalSource"]
                recovered_manifest = recovered["capability_manifest"]["capabilityManifest"]
                if recovered_manifest != manifest \
                        or binding["proposalSourceHash"] != source.get("sourceHash") \
                        or any(candidate.get(field) != source.get(field) for field in (
                            "operation", "hostId", "inputArtifacts", "argumentsArtifact", "argumentsSha256",
                            "resourceKeys", "risk", "providerCandidates", "timeoutMs", "maxOutputBytes",
                            "completionFactKinds", "sourceKind", "sourceEvidenceSha256",
                        )):
                    raise ConstructExecutionError("EXECUTION_RECOVERY_ARTIFACT_MISMATCH", "candidate differs from bound recovery artifacts", 409)
                self._assert_input_artifacts(
                    cur, owner=str(owner_user_id), program={"programId": candidate["programId"]},
                    intent={"argumentsArtifact": candidate["argumentsArtifact"], "argumentsSha256": candidate["argumentsSha256"],
                            "inputArtifacts": candidate["inputArtifacts"]},
                )
                body = {"contract": "life-vvault-work-execution-proposal-envelope/v1",
                        "proposalArtifactId": candidate["candidateId"], "ownerPrincipalId": str(owner_user_id),
                        "mediaType": "application/vnd.chatty.work-execution-proposal+json",
                        "artifactSha256": _sha(candidate), "candidate": candidate,
                        "capabilityManifest": manifest, "executionProposalBinding": binding,
                        "advancementAuthority": False, "effectAuthority": False,
                        "issuedAt": _iso(now or datetime.now(timezone.utc))}
                receipt = _signed(body, self.private_key_pem)
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_proposals
                      (owner_user_id,program_id,proposal_id,item_id,source_construct_id,responsible_construct_id,
                       thread_id,session_id,branch_id,goal_revision,work_head_event_id,work_head_sha256,
                       work_state_receipt_sha256,proposal_source,candidate,proposal_hash,capability_manifest,
                       capability_manifest_sha256,execution_proposal_binding,execution_proposal_binding_sha256,
                       receipt,receipt_sha256)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb,%s)
                      ON CONFLICT (owner_user_id,proposal_id) DO NOTHING""",
                    (owner_user_id, candidate["programId"], candidate["candidateId"], candidate["itemId"],
                     candidate["sourceConstructId"], candidate["responsibleConstructId"], candidate["threadId"],
                     candidate["sessionId"], candidate["branchId"], candidate["goalRevision"], candidate["workHeadEventId"],
                     candidate["workHeadSha256"], candidate["workStateReceiptSha256"], candidate["sourceKind"],
                     json.dumps(candidate), candidate["candidateHash"], json.dumps(manifest), manifest["payloadSha256"],
                     json.dumps(binding), binding["bindingHash"], json.dumps(receipt), receipt["payloadSha256"]),
                )
                cur.execute("SELECT candidate,capability_manifest,execution_proposal_binding,receipt FROM ovvaults.construct_work_execution_proposals WHERE owner_user_id=%s AND proposal_id=%s", (owner_user_id, candidate["candidateId"]))
                stored = _row(cur.fetchone())
                stored_candidate = stored.get("candidate") if stored else None
                stored_manifest = stored.get("capability_manifest") if stored else None
                stored_binding = stored.get("execution_proposal_binding") if stored else None
                if isinstance(stored_candidate, str):
                    stored_candidate = json.loads(stored_candidate)
                if isinstance(stored_manifest, str):
                    stored_manifest = json.loads(stored_manifest)
                if isinstance(stored_binding, str):
                    stored_binding = json.loads(stored_binding)
                if not stored or _sha(stored_candidate) != _sha(candidate) \
                        or _sha(stored_manifest) != _sha(manifest) or _sha(stored_binding) != _sha(binding):
                    raise ConstructExecutionError("EXECUTION_PROPOSAL_IDEMPOTENCY_CONFLICT", "proposal ID is bound to different bytes", 409)
        return receipt

    def get_proposal(self, owner_user_id: str, proposal_id: str) -> dict[str, Any]:
        proposal_id = _id(proposal_id, "proposalId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT receipt FROM ovvaults.construct_work_execution_proposals WHERE owner_user_id=%s AND proposal_id=%s", (owner_user_id, proposal_id))
                row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_NOT_FOUND", "proposal not found", 404)
        receipt = row["receipt"]
        receipt = receipt if isinstance(receipt, dict) else json.loads(receipt)
        verified = _verify_signed(
            receipt, fields=_PROPOSAL_ENVELOPE_FIELDS,
            contract="life-vvault-work-execution-proposal-envelope/v1",
            public_key_pem=canonical_projection_signing.public_key_document(
                private_key_pem=self.private_key_pem
            )["publicKeyPem"], expected_key_id=None,
        )
        if verified.get("ownerPrincipalId") != str(owner_user_id) \
                or verified.get("proposalArtifactId") != proposal_id \
                or verified.get("artifactSha256") != _sha(verified.get("candidate")):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_ENVELOPE_INVALID", "canonical proposal envelope is invalid", 409)
        _validate_proposal_binding(verified.get("executionProposalBinding"))
        _validate_capability_manifest(
            verified.get("capabilityManifest"), owner=str(owner_user_id), candidate=verified.get("candidate"),
            public_key_pem=self.core_public_key_pem, expected_key_id=self.core_key_id,
            host_key_resolver=self._host_key, now=None,
        )
        return verified

    def _assert_input_artifacts(self, cur: Any, *, owner: str, program: dict[str, Any], intent: dict[str, Any]) -> None:
        references = [intent["argumentsArtifact"], *intent.get("inputArtifacts", [])]
        for step in program.get("steps", []):
            references.extend([step["argumentsArtifact"], *step.get("inputArtifacts", [])])
        unique_references: dict[str, dict[str, Any]] = {}
        for reference in references:
            if not isinstance(reference, dict):
                raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_NOT_VERIFIED", "execution artifact reference is invalid", 409)
            prior = unique_references.get(str(reference.get("artifactId")))
            if prior is not None and prior != reference:
                raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_CONFLICT", "artifact ID is bound to different bytes", 409)
            unique_references[str(reference.get("artifactId"))] = reference
        for reference in unique_references.values():
            _exact(reference, {"artifactId", "sha256", "mediaType"}, "executionArtifactReference")
            cur.execute("""SELECT program_id,media_type,content,content_sha256 FROM ovvaults.construct_work_execution_inputs
                           WHERE owner_user_id=%s AND artifact_id=%s FOR SHARE""", (owner, reference["artifactId"]))
            stored = _row(cur.fetchone())
            content = stored.get("content") if stored else None
            if isinstance(content, str):
                content = json.loads(content)
            if not stored or stored.get("program_id") != program["programId"] or stored.get("media_type") != reference["mediaType"] \
                    or stored.get("content_sha256") != reference["sha256"] or _sha(content) != reference["sha256"]:
                raise ConstructExecutionError("EXECUTION_INPUT_ARTIFACT_NOT_VERIFIED", "execution input artifact is not canonical for this owner/program", 409)
        if intent["argumentsArtifact"]["sha256"] != intent["argumentsSha256"]:
            raise ConstructExecutionError("EXECUTION_ARGUMENT_ARTIFACT_MISMATCH", "argument artifact hash mismatch", 409)

    def _assert_proposal(self, cur: Any, *, owner: str, program: dict[str, Any], intent: dict[str, Any]) -> None:
        proposal_id = _id(intent.get("proposalArtifactId"), "proposalArtifactId")
        cur.execute("SELECT candidate,capability_manifest,execution_proposal_binding,receipt FROM ovvaults.construct_work_execution_proposals WHERE owner_user_id=%s AND proposal_id=%s FOR SHARE", (owner, proposal_id))
        row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_NOT_FOUND", "canonical proposal artifact not found", 409)
        candidate = row["candidate"] if isinstance(row["candidate"], dict) else json.loads(row["candidate"])
        manifest = row["capability_manifest"] if isinstance(row["capability_manifest"], dict) else json.loads(row["capability_manifest"])
        binding = row["execution_proposal_binding"] if isinstance(row["execution_proposal_binding"], dict) else json.loads(row["execution_proposal_binding"])
        receipt = row["receipt"] if isinstance(row["receipt"], dict) else json.loads(row["receipt"])
        vvault_public = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        verified_receipt = _verify_signed(
            receipt, fields=_PROPOSAL_ENVELOPE_FIELDS,
            contract="life-vvault-work-execution-proposal-envelope/v1",
            public_key_pem=vvault_public, expected_key_id=None,
        )
        if verified_receipt.get("candidate") != candidate \
                or verified_receipt.get("capabilityManifest") != manifest \
                or verified_receipt.get("executionProposalBinding") != binding:
            raise ConstructExecutionError("EXECUTION_PROPOSAL_ENVELOPE_INVALID", "canonical proposal envelope mismatch", 409)
        _validate_proposal_binding(binding)
        _validate_capability_manifest(
            manifest, owner=owner, candidate=candidate, public_key_pem=self.core_public_key_pem,
            expected_key_id=self.core_key_id, host_key_resolver=self._host_key, now=None,
        )
        if len(program.get("steps", [])) > 1:
            capabilities = manifest.get("capabilities") or []
            risk_order = ["low", "moderate", "high", "critical"]
            for step in program["steps"]:
                capability = next((entry for entry in capabilities
                                   if entry.get("capabilityId") == step.get("capabilityId")), None)
                resources = (capability or {}).get("resourceScopes") or []
                if not capability or any((
                    capability.get("hostId") != step.get("hostId"),
                    capability.get("operation") != step.get("operation"),
                    step.get("risk") not in risk_order,
                    capability.get("riskCeiling") not in risk_order,
                    risk_order.index(step["risk"]) > risk_order.index(capability["riskCeiling"]),
                    int(step.get("timeoutMs") or 0) > int(capability.get("maxDurationMs") or 0),
                    int(step.get("maxOutputBytes") or 0) > int(capability.get("maxOutputBytes") or 0),
                    any(not any(resource == scope or resource.startswith(f"{scope}:") for scope in resources)
                        for resource in step.get("resourceKeys", [])),
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_CAPABILITY_SCOPE_INVALID",
                        "Hydro assignment is outside the signed capability manifest", 403,
                    )
        if intent.get("proposalPayloadSha256") != _sha(candidate):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_HASH_MISMATCH", "intent proposal hash mismatch", 409)
        field_pairs = (
            ("programId", "programId"), ("itemId", "itemId"), ("ownerPrincipalId", "ownerPrincipalId"),
            ("sourceConstructId", "sourceConstructId"), ("responsibleConstructId", "responsibleConstructId"),
            ("threadId", "threadId"), ("sessionId", "sessionId"), ("branchId", "branchId"),
            ("goalRevision", "goalRevision"), ("workHeadEventId", "workHeadEventId"),
            ("workHeadSha256", "workHeadSha256"), ("workStateReceiptSha256", "workStateReceiptSha256"),
            ("decisionHash", "decisionHash"), ("nextActionHash", "nextActionHash"),
            ("preparedContextReceiptSha256", "preparedContextReceiptSha256"),
            ("capabilityManifestPayloadSha256", "capabilityManifestPayloadSha256"),
            ("actionClass", "actionClass"), ("operation", "operation"), ("hostId", "hostId"),
            ("inputArtifacts", "inputArtifacts"), ("argumentsArtifact", "argumentsArtifact"),
            ("argumentsSha256", "argumentsSha256"), ("resourceKeys", "resourceKeys"), ("risk", "risk"),
            ("providerCandidates", "providerCandidates"), ("timeoutMs", "timeoutMs"),
            ("maxOutputBytes", "maxOutputBytes"), ("completionFactKinds", "completionFactKinds"),
        )
        if any(intent.get(intent_key) != candidate.get(candidate_key) for intent_key, candidate_key in field_pairs):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_INTENT_MISMATCH", "intent is not derived from canonical proposal", 409)
        if any(program.get(field) != candidate.get(field) for field in ("programId", "itemId", "ownerPrincipalId", "sourceConstructId",
                                                                         "responsibleConstructId", "threadId", "sessionId", "branchId",
                                                                         "goalRevision", "workHeadEventId", "workHeadSha256", "workStateReceiptSha256",
                                                                         "decisionHash", "nextActionHash")):
            raise ConstructExecutionError("EXECUTION_PROPOSAL_PROGRAM_MISMATCH", "proposal does not bind execution program", 409)
        if intent.get("capabilityManifestPayloadSha256") != manifest.get("payloadSha256") \
                or binding.get("capabilityManifestPayloadSha256") != manifest.get("payloadSha256"):
            raise ConstructExecutionError("EXECUTION_CAPABILITY_MANIFEST_HASH_MISMATCH", "execution capability manifest hash mismatch", 409)

    def _events(self, cur: Any, owner: str, execution_id: str) -> list[dict[str, Any]]:
        cur.execute(
            """SELECT envelope FROM ovvaults.construct_work_execution_events
               WHERE owner_user_id=%s AND execution_id=%s ORDER BY sequence""",
            (owner, execution_id),
        )
        result = []
        for raw in cur.fetchall():
            envelope = (_row(raw) or {}).get("envelope")
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            result.append(envelope)
        return result

    def _persist_hydro_event(
        self, cur: Any, *, owner: str, program: dict[str, Any], event: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        binding = program.get("hydroGraphBinding")
        if not binding:
            return
        event_type = event["eventType"]
        step_id = _execution_step_id(event_type, payload)
        step = next((entry for entry in program["steps"] if entry.get("stepId") == step_id), None)
        scope = (step or {}).get("hydroScope") or {}
        common = (owner, program["programId"], program["executionId"], binding["graphId"],
                  scope.get("assignmentId"), step_id, (step or {}).get("responsibleConstructId"))
        if event_type == "execution_authorized":
            cur.execute(
                """SELECT payload->'approval' AS approval
                     FROM ovvaults.construct_work_execution_events
                    WHERE owner_user_id=%s AND execution_id=%s AND event_type='approval_capability_issued'
                      AND payload->'approval'->>'payloadSha256'=%s
                    ORDER BY sequence DESC LIMIT 1""",
                (owner, program["executionId"], payload["approvalPayloadSha256"]),
            )
            issued = _row(cur.fetchone())
            approval = (issued or {}).get("approval")
            if isinstance(approval, str): approval = json.loads(approval)
            if not isinstance(approval, dict):
                raise ConstructExecutionError("EXECUTION_APPROVAL_CONSUMPTION_INVALID", "issued approval is unavailable", 409)
            approval_common = (owner, program["programId"], program["executionId"], binding["graphId"], None, None, None)
            self._persist_hydro_capability_consumption(
                cur, common=approval_common, kind="owner_approval", capability_id=approval["approvalId"],
                payload_sha=approval["payloadSha256"], event=event,
            )
        elif event_type in {"execution_lease_acquired", "execution_lease_renewed"}:
            lease = payload["lease"]
            cur.execute(
                """SELECT count(DISTINCT step_id) AS active_count
                     FROM ovvaults.construct_work_execution_leases
                    WHERE owner_user_id=%s AND execution_id=%s AND expires_at>%s AND step_id<>%s""",
                (owner, program["executionId"], event["occurredAt"], step_id),
            )
            active = int((_row(cur.fetchone()) or {}).get("active_count") or 0)
            if active >= int((program.get("budgets") or {}).get("maxParallel") or 1):
                raise ConstructExecutionError(
                    "HYDRO_EXECUTION_MAX_PARALLEL_EXCEEDED",
                    "Hydro lease would exceed the signed maxParallel bound", 409,
                )
            cur.execute(
                """INSERT INTO ovvaults.construct_work_execution_leases
                  (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,worker_principal_id,
                   attempt_ordinal,lease_id,renewal_ordinal,resource_keys,lease,lease_payload_sha256,
                   event_id,event_sha256,issued_at,expires_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)""",
                (*common, lease["attemptOrdinal"], lease["leaseId"], lease["renewalOrdinal"],
                 json.dumps(lease["resourceKeys"]), json.dumps(lease), lease["payloadSha256"],
                 event["eventId"], event["eventSha256"], lease["issuedAt"], lease["expiresAt"]),
            )
            for resource_key in lease["resourceKeys"]:
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_resource_fences
                      (owner_user_id,resource_key,execution_id,graph_id,assignment_id,step_id,
                       attempt_ordinal,lease_id,renewal_ordinal,lease_payload_sha256,fenced_at,expires_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (owner, resource_key, program["executionId"], binding["graphId"], scope["assignmentId"],
                     step_id, lease["attemptOrdinal"], lease["leaseId"], lease["renewalOrdinal"],
                     lease["payloadSha256"], lease["issuedAt"], lease["expiresAt"]),
                )
        elif event_type == "execution_attempt_started":
            permit = payload["startPermit"]
            cur.execute(
                """INSERT INTO ovvaults.construct_work_hydro_worker_attempts
                  (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,worker_principal_id,
                   attempt_ordinal,permit_id,permit_payload_sha256,lease_payload_sha256,
                   parent_execution_event_id,parent_execution_head_sha256,event_id,event_sha256,start_permit,started_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (*common, permit["attemptOrdinal"], permit["permitId"], permit["payloadSha256"],
                 permit["leasePayloadSha256"], permit["preStartHeadEventId"], permit["preStartHeadSha256"],
                 event["eventId"], event["eventSha256"], json.dumps(permit), event["occurredAt"]),
            )
            self._persist_hydro_capability_consumption(
                cur, common=common, kind="start_permit", capability_id=permit["permitId"],
                payload_sha=permit["payloadSha256"], event=event,
            )
        elif event_type == "execution_effect_dispatched":
            marker = payload["dispatchMarker"]
            self._persist_hydro_capability_consumption(
                cur, common=common, kind="dispatch_marker", capability_id=marker["dispatchId"],
                payload_sha=marker["payloadSha256"], event=event,
            )
        elif event_type == "execution_attempt_outcome_recorded":
            receipt = payload["hostReceipt"]
            artifacts = receipt.get("outputArtifacts") or []
            result_artifact = artifacts[0] if receipt.get("outcome") == "completed" and artifacts else None
            cur.execute(
                """INSERT INTO ovvaults.construct_work_hydro_worker_results
                  (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,worker_principal_id,
                   attempt_ordinal,host_receipt_payload_sha256,outcome,effect_committed,result_artifact_id,
                   result_content_sha256,result_output_sha256,worker_request_sha256,
                   event_id,event_sha256,host_receipt,recorded_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (*common, receipt["attemptOrdinal"], receipt["payloadSha256"], receipt["outcome"],
                 receipt["effectCommitted"], (result_artifact or {}).get("artifactId"),
                 receipt.get("outputSha256") if result_artifact else None,
                 receipt.get("providerDraftSha256") if result_artifact else None,
                 receipt.get("hydroWorkerRequestSha256"), event["eventId"],
                 event["eventSha256"], json.dumps(receipt), event["occurredAt"]),
            )
        elif event_type == "execution_readback_recorded":
            readback = payload["readback"]
            cur.execute(
                """INSERT INTO ovvaults.construct_work_hydro_worker_readbacks
                  (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,worker_principal_id,
                   attempt_ordinal,readback_id,readback_payload_sha256,outcome,observed_result_sha256,
                   event_id,event_sha256,readback,observed_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (*common, readback["attemptOrdinal"], readback["readbackId"], readback["payloadSha256"],
                 readback["outcome"], readback.get("observedResultSha256"), event["eventId"],
                 event["eventSha256"], json.dumps(readback), readback["observedAt"]),
            )
        elif event_type == "execution_recovery_selected":
            recovery = payload["recovery"]
            capability = recovery.get("recoveryCapability") or {}
            if capability:
                kind = "provider_fallback" if recovery.get("selection") == "provider_fallback" else "recovery"
                self._persist_hydro_capability_consumption(
                    cur, common=common, kind=kind, capability_id=capability["capabilityId"],
                    payload_sha=capability["payloadSha256"], event=event,
                )
        elif event_type == "execution_hydro_synthesis_inputs_resolved":
            resolution = payload["resolution"]
            cur.execute(
                """INSERT INTO ovvaults.construct_work_hydro_synthesis_resolutions
                  (owner_user_id,program_id,execution_id,graph_id,synthesis_assignment_id,synthesis_step_id,
                   expected_head_event_id,expected_head_sha256,resolution,resolution_payload_sha256,
                   event_id,event_sha256,issued_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)""",
                (owner, program["programId"], program["executionId"], binding["graphId"],
                 resolution["synthesisAssignmentId"], resolution["synthesisStepId"],
                 resolution["expectedHeadEventId"], resolution["expectedHeadSha256"], json.dumps(resolution),
                 resolution["payloadSha256"], event["eventId"], event["eventSha256"], resolution["issuedAt"]),
            )

    @staticmethod
    def _persist_hydro_capability_consumption(
        cur: Any, *, common: tuple[Any, ...], kind: str, capability_id: str,
        payload_sha: str, event: dict[str, Any],
    ) -> None:
        owner, program_id, execution_id, graph_id, assignment_id, step_id, _worker = common
        cur.execute(
            """INSERT INTO ovvaults.construct_work_execution_capability_consumptions
              (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,capability_kind,
               capability_id,capability_payload_sha256,event_id,event_sha256,consumed_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (owner, program_id, execution_id, graph_id, assignment_id, step_id, kind, capability_id,
             payload_sha, event["eventId"], event["eventSha256"], event["occurredAt"]),
        )

    def _append(self, cur: Any, *, owner: str, row: dict[str, Any], payload: dict[str, Any],
                authorization: dict[str, Any], actor: dict[str, str], now: datetime,
                evidence_digest: str | None = None) -> dict[str, Any]:
        program = row.get("execution_program")
        if isinstance(program, str):
            program = json.loads(program)
        auth = _validate_authorization(authorization, owner=owner, payload=payload,
                                       public_key_pem=self.core_public_key_pem, key_id=self.core_key_id, now=now)
        for field in ("executionId", "programId", "itemId", "sourceConstructId", "responsibleConstructId", "threadId", "sessionId", "branchId"):
            if auth.get(field) != program.get(field):
                raise ConstructExecutionError("EXECUTION_AUTHORIZATION_SCOPE_INVALID", f"authorization {field} mismatch", 403)
        cur.execute(
            """SELECT envelope,payload_sha256,core_authorization_hash FROM ovvaults.construct_work_execution_events
               WHERE owner_user_id=%s AND execution_id=%s AND idempotency_key=%s""",
            (owner, program["executionId"], auth["idempotencyKey"]),
        )
        duplicate = _row(cur.fetchone())
        if duplicate:
            if duplicate.get("payload_sha256") != _sha(payload) or duplicate.get("core_authorization_hash") != auth["payloadSha256"]:
                raise ConstructExecutionError("EXECUTION_IDEMPOTENCY_CONFLICT", "idempotency bytes differ", 409)
            return duplicate["envelope"] if isinstance(duplicate["envelope"], dict) else json.loads(duplicate["envelope"])
        existing_events = self._events(cur, owner, program["executionId"])
        head = existing_events[-1]["event"] if existing_events else None
        if auth["expectedSequence"] != len(existing_events) + 1 or auth["expectedHeadEventId"] != (head or {}).get("eventId") \
                or auth["expectedHeadSha256"] != (head or {}).get("eventSha256"):
            raise ConstructExecutionError("EXECUTION_HEAD_CONFLICT", "execution head compare-and-swap failed", 409)
        _assert_transition(existing_events, auth["eventType"], payload)
        vvault_public = canonical_projection_signing.public_key_document(private_key_pem=self.private_key_pem)["publicKeyPem"]
        if auth["eventType"] in {"execution_lease_acquired", "execution_lease_renewed"}:
            lease = _verify_signed(payload.get("lease"), fields=_hydro_signed_fields(payload.get("lease"), _LEASE_FIELDS), contract=EXECUTION_LEASE,
                                   public_key_pem=vvault_public, expected_key_id=None, now=now)
            _assert_hydro_document_scope(program, lease)
            if lease.get("ownerPrincipalId") != owner or lease.get("executionId") != program["executionId"] \
                    or lease.get("expectedHeadEventId") != auth["expectedHeadEventId"] \
                    or lease.get("expectedHeadSha256") != auth["expectedHeadSha256"]:
                raise ConstructExecutionError("EXECUTION_LEASE_SCOPE_INVALID", "lease does not bind current execution head", 409)
        if auth["eventType"] == "execution_attempt_started":
            permit_document = _verify_signed(payload.get("startPermit"), fields=_hydro_signed_fields(payload.get("startPermit"), _PERMIT_FIELDS), contract=START_PERMIT,
                                             public_key_pem=vvault_public, expected_key_id=None, now=now)
            _assert_hydro_document_scope(program, permit_document)
            if permit_document.get("ownerPrincipalId") != owner or permit_document.get("executionId") != program["executionId"]:
                raise ConstructExecutionError("EXECUTION_START_SCOPE_INVALID", "start permit execution scope mismatch", 409)
        if auth["eventType"] == "execution_hydro_synthesis_inputs_resolved":
            _exact(payload, {"resolution"}, "hydroSynthesisInputsResolved")
            resolution = _verify_signed(
                payload.get("resolution"), fields=_HYDRO_SYNTHESIS_RESOLUTION_FIELDS,
                contract=HYDRO_SYNTHESIS_INPUT_RESOLUTION, public_key_pem=vvault_public,
                expected_key_id=None,
            )
            step = next((entry for entry in program.get("steps", [])
                         if entry.get("stepId") == resolution.get("synthesisStepId")), None)
            if not step or step.get("kind") != "hydro_synthesis":
                raise ConstructExecutionError("HYDRO_SYNTHESIS_STEP_INVALID", "synthesis step is not canonical", 409)
            expected_results, expected_failed = self._hydro_dependency_snapshot(
                cur, owner=owner, program=program, events=existing_events, synthesis_step=step,
            )
            if any((
                resolution.get("authority") != AUTHORITY,
                resolution.get("ownerPrincipalId") != owner,
                resolution.get("programId") != program.get("programId"),
                resolution.get("executionId") != program.get("executionId"),
                resolution.get("graphId") != (program.get("hydroGraphBinding") or {}).get("graphId"),
                resolution.get("synthesisAssignmentId") != (step.get("hydroScope") or {}).get("assignmentId"),
                resolution.get("expectedHeadEventId") != auth.get("expectedHeadEventId"),
                resolution.get("expectedHeadSha256") != auth.get("expectedHeadSha256"),
                resolution.get("dependencyResults") != expected_results,
                resolution.get("failedOptionalAssignmentIds") != expected_failed,
                _time(resolution.get("issuedAt"), "hydroResolution.issuedAt") > now + timedelta(seconds=30),
                actor != {"principalId": "vvault", "principalType": "system", "authority": "vvault"},
            )):
                raise ConstructExecutionError(
                    "HYDRO_SYNTHESIS_INPUT_RESOLUTION_SCOPE_INVALID",
                    "synthesis resolution differs from immutable dependency evidence", 409,
                )
        if auth["eventType"] == "execution_effect_dispatched":
            marker = payload.get("dispatchMarker")
            host_id = marker.get("hostId") if isinstance(marker, dict) else None
            host_key = self._host_key(host_id)
            if not host_key:
                raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
            marker = _verify_signed(
                marker, fields=_hydro_signed_fields(marker, _DISPATCH_MARKER_FIELDS), contract=EFFECT_DISPATCH_MARKER,
                public_key_pem=host_key[0], expected_key_id=host_key[1], now=None,
            )
            latest_permit = next((
                (entry["event"].get("payload") or {}).get("startPermit")
                for entry in reversed(existing_events)
                if entry["event"].get("eventType") == "execution_attempt_started"
                and ((entry["event"].get("payload") or {}).get("startPermit") or {}).get("stepId") == marker.get("stepId")
            ), None)
            operation = next((step.get("operation") for step in program.get("steps", [])
                              if step.get("stepId") == marker.get("stepId")), None)
            worker_principal = next((step.get("responsibleConstructId") for step in program.get("steps", [])
                                     if step.get("stepId") == marker.get("stepId")), None)
            marker_step = next((step for step in program.get("steps", [])
                                if step.get("stepId") == marker.get("stepId")), None)
            latest_resolution = next((
                (entry["event"].get("payload") or {}).get("resolution")
                for entry in reversed(existing_events)
                if entry["event"].get("eventType") == "execution_hydro_synthesis_inputs_resolved"
            ), None)
            staged_worker_request = None
            if (marker_step or {}).get("hydroScope"):
                cur.execute(
                    """SELECT request_hash,start_permit_payload_sha256,worker_principal_id,assignment_id,
                              attempt_ordinal,synthesis_input_resolution_sha256
                         FROM ovvaults.construct_work_hydro_worker_requests
                        WHERE owner_user_id=%s AND execution_id=%s AND step_id=%s AND attempt_ordinal=%s
                        FOR SHARE""",
                    (owner, program["executionId"], marker.get("stepId"), marker.get("attemptOrdinal")),
                )
                staged_worker_request = _row(cur.fetchone())
            if any((
                marker.get("authority") != host_id,
                marker.get("ownerPrincipalId") != owner,
                marker.get("executionId") != program["executionId"],
                marker.get("programId") != program["programId"],
                marker.get("itemId") != program["itemId"],
                marker.get("responsibleConstructId") != worker_principal,
                marker.get("preDispatchHeadEventId") != auth.get("expectedHeadEventId"),
                marker.get("preDispatchHeadSha256") != auth.get("expectedHeadSha256"),
                marker.get("oneUse") is not True,
                not isinstance(latest_permit, dict),
                marker.get("permitPayloadSha256") != (latest_permit or {}).get("payloadSha256"),
                marker.get("stepId") != (latest_permit or {}).get("stepId"),
                marker.get("attemptOrdinal") != (latest_permit or {}).get("attemptOrdinal"),
                marker.get("providerId") != (latest_permit or {}).get("providerId"),
                marker.get("hostId") != (latest_permit or {}).get("hostId"),
                marker.get("stepHash") != (latest_permit or {}).get("stepHash"),
                marker.get("argumentsSha256") != (latest_permit or {}).get("argumentsSha256"),
                marker.get("idempotencyKey") != (latest_permit or {}).get("idempotencyKey"),
                not isinstance(latest_permit, dict) or now >= _time((latest_permit or {}).get("expiresAt"), "startPermit.expiresAt"),
                _time(marker.get("dispatchedAt"), "dispatchMarker.dispatchedAt") < _time((latest_permit or {}).get("issuedAt"), "startPermit.issuedAt"),
                _time(marker.get("dispatchedAt"), "dispatchMarker.dispatchedAt") > now + timedelta(seconds=30),
                marker.get("operation") != operation,
                marker.get("hydroScope") != (marker_step or {}).get("hydroScope"),
                bool((marker_step or {}).get("hydroScope")) and not staged_worker_request,
                bool((marker_step or {}).get("hydroScope"))
                and marker.get("hydroWorkerRequestSha256") != (staged_worker_request or {}).get("request_hash"),
                bool((marker_step or {}).get("hydroScope"))
                and (staged_worker_request or {}).get("start_permit_payload_sha256") != marker.get("permitPayloadSha256"),
                bool((marker_step or {}).get("hydroScope"))
                and (staged_worker_request or {}).get("worker_principal_id") != worker_principal,
                bool((marker_step or {}).get("hydroScope"))
                and (staged_worker_request or {}).get("assignment_id") != marker.get("stepId"),
                ((marker_step or {}).get("kind") == "hydro_synthesis")
                != (marker.get("hydroSynthesisInputResolutionSha256") is not None),
                (marker_step or {}).get("kind") == "hydro_synthesis"
                and marker.get("hydroSynthesisInputResolutionSha256") != (latest_resolution or {}).get("payloadSha256"),
                (marker_step or {}).get("kind") == "hydro_synthesis"
                and (staged_worker_request or {}).get("synthesis_input_resolution_sha256")
                != (latest_resolution or {}).get("payloadSha256"),
                actor != {"principalId": host_id, "principalType": "execution_host", "authority": "execution_host"},
            )):
                raise ConstructExecutionError("EXECUTION_DISPATCH_MARKER_SCOPE_INVALID", "dispatch marker does not bind the current start permit", 409)
        if auth["eventType"] == "approval_capability_issued":
            _exact(payload, {"approval", "approvalDisclosure"}, "executionApprovalIssued")
            owner_public = self.owner_public_key_pem or vvault_public
            approval = _verify_signed(
                payload.get("approval"), fields=_APPROVAL_FIELDS, contract=APPROVAL_CAPABILITY,
                public_key_pem=owner_public, expected_key_id=self.owner_key_id, now=now,
            )
            canonical_disclosure = self._canonical_approval_disclosure(cur, owner=owner, row=row)
            if any((
                payload.get("approvalDisclosure") != canonical_disclosure,
                approval.get("approvalDisclosureSha256") != canonical_disclosure["approvalDisclosureSha256"],
                approval.get("definitionHash") != program["definitionHash"],
                approval.get("workExecutionIntentHash") != row.get("execution_intent_hash"),
                approval.get("approvedStepIds") != [step["stepId"] for step in program["steps"]],
            )):
                raise ConstructExecutionError(
                    "EXECUTION_APPROVAL_DISCLOSURE_MISMATCH",
                    "issued approval does not bind the canonical informed disclosure", 409,
                )
            self._approval_disclosure_row(
                cur, owner=owner, execution_id=program["executionId"],
                disclosure_sha256=canonical_disclosure["approvalDisclosureSha256"],
                expected_head_event_id=auth.get("expectedHeadEventId"),
                expected_head_sha256=auth.get("expectedHeadSha256"), current=now,
            )
        if auth["eventType"] == "execution_authorized":
            _exact(payload, {"approvalPayloadSha256", "approvalDisclosureSha256"}, "executionAuthorized")
            issued_events = [entry["event"] for entry in existing_events
                             if entry["event"]["eventType"] == "approval_capability_issued"]
            issued = [entry["payload"].get("approval") for entry in issued_events]
            if not issued or payload.get("approvalPayloadSha256") != issued[-1].get("payloadSha256") \
                    or payload.get("approvalDisclosureSha256") != issued[-1].get("approvalDisclosureSha256") \
                    or payload.get("approvalDisclosureSha256") != (issued_events[-1]["payload"].get("approvalDisclosure") or {}).get("approvalDisclosureSha256"):
                raise ConstructExecutionError("EXECUTION_APPROVAL_CONSUMPTION_INVALID", "authorization does not consume issued approval", 409)
            owner_public = self.owner_public_key_pem or vvault_public
            _verify_signed(
                issued[-1], fields=_APPROVAL_FIELDS, contract=APPROVAL_CAPABILITY,
                public_key_pem=owner_public, expected_key_id=self.owner_key_id, now=now,
            )
        if auth["eventType"] == "execution_recovery_selected":
            recovery = _validate_recovery(payload.get("recovery"))
            _assert_hydro_document_scope(program, recovery)
            if recovery.get("executionId") != program["executionId"]:
                raise ConstructExecutionError("EXECUTION_RECOVERY_SCOPE_INVALID", "recovery execution scope mismatch", 403)
            if recovery.get("selection") == "retry_not_committed":
                owner_public = self.owner_public_key_pem or vvault_public
                capability = _verify_signed(
                    recovery.get("recoveryCapability"), fields=_hydro_signed_fields(recovery.get("recoveryCapability"), _RECOVERY_CAPABILITY_FIELDS),
                    contract=RECOVERY_CAPABILITY, public_key_pem=owner_public,
                    expected_key_id=self.owner_key_id, now=now,
                )
                latest_readback = next((
                    entry["event"]["payload"].get("readback")
                    for entry in reversed(existing_events)
                    if entry["event"].get("eventType") == "execution_readback_recorded"
                ), None)
                if any((
                    capability.get("hydroScope") != recovery.get("hydroScope"),
                    capability.get("authority") != "chatty-owner-authorization",
                    capability.get("ownerPrincipalId") != owner,
                    capability.get("executionId") != program["executionId"],
                    capability.get("programId") != program["programId"],
                    capability.get("itemId") != program["itemId"],
                    capability.get("stepId") != recovery.get("stepId"),
                    capability.get("attemptOrdinal") != recovery.get("attemptOrdinal"),
                    capability.get("selection") != "retry_not_committed",
                    capability.get("definitionHash") != program["definitionHash"],
                    capability.get("workExecutionIntentHash") != row.get("execution_intent_hash"),
                    capability.get("approvalDisclosureSha256") != next((
                        ((entry["event"].get("payload") or {}).get("approval") or {}).get("approvalDisclosureSha256")
                        for entry in reversed(existing_events)
                        if entry["event"].get("eventType") == "approval_capability_issued"
                    ), None),
                    capability.get("readbackPayloadSha256") != recovery.get("readbackPayloadSha256"),
                    capability.get("expectedHeadEventId") != auth.get("expectedHeadEventId"),
                    capability.get("expectedHeadSha256") != auth.get("expectedHeadSha256"),
                    capability.get("oneUse") is not True,
                    not isinstance(latest_readback, dict),
                    (latest_readback or {}).get("outcome") != "not_committed",
                    (latest_readback or {}).get("payloadSha256") != capability.get("readbackPayloadSha256"),
                )):
                    raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_SCOPE_INVALID", "recovery capability scope is invalid", 403)
                consumed_ids = {
                    (((entry["event"].get("payload") or {}).get("recovery") or {}).get("recoveryCapability") or {}).get("capabilityId")
                    for entry in existing_events if entry["event"].get("eventType") == "execution_recovery_selected"
                }
                if capability.get("capabilityId") in consumed_ids:
                    raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_CONSUMED", "recovery capability is one-use", 409)
            elif recovery.get("selection") == "provider_fallback":
                capability = _verify_signed(
                    recovery.get("recoveryCapability"), fields=_hydro_signed_fields(recovery.get("recoveryCapability"), _PROVIDER_FALLBACK_CAPABILITY_FIELDS),
                    contract=PROVIDER_FALLBACK_CAPABILITY, public_key_pem=vvault_public,
                    expected_key_id=None, now=now,
                )
                latest_receipt = next((
                    entry["event"]["payload"].get("hostReceipt")
                    for entry in reversed(existing_events)
                    if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                ), None)
                if any((
                    capability.get("hydroScope") != recovery.get("hydroScope"),
                    capability.get("authority") != AUTHORITY,
                    capability.get("ownerPrincipalId") != owner,
                    capability.get("executionId") != program["executionId"],
                    capability.get("programId") != program["programId"],
                    capability.get("itemId") != program["itemId"],
                    capability.get("stepId") != recovery.get("stepId"),
                    capability.get("attemptOrdinal") != recovery.get("attemptOrdinal"),
                    capability.get("selection") != "provider_fallback",
                    capability.get("definitionHash") != program["definitionHash"],
                    capability.get("workExecutionIntentHash") != row.get("execution_intent_hash"),
                    capability.get("approvalDisclosureSha256") != next((
                        ((entry["event"].get("payload") or {}).get("approval") or {}).get("approvalDisclosureSha256")
                        for entry in reversed(existing_events)
                        if entry["event"].get("eventType") == "approval_capability_issued"
                    ), None),
                    capability.get("hostReceiptPayloadSha256") != (latest_receipt or {}).get("payloadSha256"),
                    capability.get("expectedHeadEventId") != auth.get("expectedHeadEventId"),
                    capability.get("expectedHeadSha256") != auth.get("expectedHeadSha256"),
                    capability.get("oneUse") is not True,
                    not isinstance(latest_receipt, dict),
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_PROVIDER_FALLBACK_CAPABILITY_SCOPE_INVALID",
                        "provider fallback capability scope is invalid", 403,
                    )
                step = next((entry for entry in program.get("steps", [])
                             if entry.get("stepId") == recovery.get("stepId")), None)
                providers = (step or {}).get("providerCandidates") or []
                current_provider = (latest_receipt or {}).get("providerId")
                if current_provider not in providers \
                        or providers.index(current_provider) + 1 >= len(providers) \
                        or capability.get("nextProviderId") != providers[providers.index(current_provider) + 1]:
                    raise ConstructExecutionError(
                        "EXECUTION_PROVIDER_FALLBACK_CAPABILITY_SCOPE_INVALID",
                        "provider fallback target is not the next canonical provider", 403,
                    )
                consumed_ids = {
                    (((entry["event"].get("payload") or {}).get("recovery") or {}).get("recoveryCapability") or {}).get("capabilityId")
                    for entry in existing_events if entry["event"].get("eventType") == "execution_recovery_selected"
                }
                if capability.get("capabilityId") in consumed_ids:
                    raise ConstructExecutionError(
                        "EXECUTION_RECOVERY_CAPABILITY_CONSUMED", "provider fallback capability is one-use", 409,
                    )
        if auth["eventType"] == "execution_cancel_acknowledged":
            acknowledgement = _exact(payload, {"stepId", "hostReceiptPayloadSha256"}, "executionCancelAcknowledgement")
            prior_receipt = ((existing_events[-1]["event"].get("payload") or {}).get("hostReceipt")
                             if existing_events else None)
            if not isinstance(prior_receipt, dict) or any((
                acknowledgement.get("stepId") != prior_receipt.get("stepId"),
                acknowledgement.get("hostReceiptPayloadSha256") != prior_receipt.get("payloadSha256"),
            )):
                raise ConstructExecutionError("EXECUTION_CANCEL_ACKNOWLEDGEMENT_INVALID", "cancellation acknowledgement does not bind the stored host receipt", 409)
        owner_evidence = _owner_evidence_from_payload(auth["eventType"], payload)
        if owner_evidence is not None:
            owner_evidence = _exact(owner_evidence, _WORK_EVIDENCE_REFERENCE_FIELDS, "workEvidenceReference")
            if owner_evidence.get("contract") != "chatty-work-evidence-reference/v1" \
                    or owner_evidence.get("evidenceType") != "owner_attestation" \
                    or owner_evidence.get("cryptographicallyVerified") is not True \
                    or owner_evidence.get("advancementAuthority") is not True:
                raise ConstructExecutionError("EXECUTION_OWNER_EVIDENCE_INVALID", "owner evidence is not canonical", 403)
            scope = owner_evidence.get("scope") or {}
            if scope.get("ownerPrincipalId") != owner or scope.get("programId") != program["programId"] \
                    or scope.get("constructId") != program["sourceConstructId"] \
                    or scope.get("itemId") != program["itemId"] or scope.get("threadId") != program["threadId"] \
                    or scope.get("sessionId") != program["sessionId"]:
                raise ConstructExecutionError("EXECUTION_OWNER_EVIDENCE_SCOPE_INVALID", "owner evidence scope mismatch", 403)
            if auth["eventType"] in {"execution_cancel_requested", "execution_rejected"}:
                expected_action = "cancel" if auth["eventType"] == "execution_cancel_requested" else "reject"
                expected_fact = "execution_cancelled" if expected_action == "cancel" else "execution_rejected"
                cur.execute(
                    """SELECT action,expected_head_event_id,expected_head_sha256,evidence_reference,expires_at
                         FROM ovvaults.construct_work_execution_owner_controls
                        WHERE owner_user_id=%s AND execution_id=%s AND evidence_id=%s FOR SHARE""",
                    (owner, program["executionId"], owner_evidence["evidenceId"]),
                )
                attestation = _row(cur.fetchone())
                stored_reference = (attestation or {}).get("evidence_reference")
                if isinstance(stored_reference, str):
                    stored_reference = json.loads(stored_reference)
                if not attestation or any((
                    attestation.get("action") != expected_action,
                    attestation.get("expected_head_event_id") != auth.get("expectedHeadEventId"),
                    attestation.get("expected_head_sha256") != auth.get("expectedHeadSha256"),
                    stored_reference != owner_evidence,
                    _time(attestation.get("expires_at"), "ownerControl.expiresAt") <= now,
                    owner_evidence.get("verifiedFactKinds") != [expected_fact],
                    any(
                        ((entry["event"].get("payload") or {}).get("ownerEvidence") or {}).get("evidenceId")
                        == owner_evidence["evidenceId"] for entry in existing_events
                    ),
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_OWNER_CONTROL_ATTESTATION_INVALID",
                        "owner control attestation is stale, mismatched, or already consumed", 409,
                    )
            else:
                cur.execute("SELECT payload FROM ovvaults.construct_work_events WHERE owner_user_id=%s AND program_id=%s", (owner, program["programId"]))
                target = _sha(owner_evidence)
                found = False
                def visit(value: Any) -> None:
                    nonlocal found
                    if found:
                        return
                    if isinstance(value, dict):
                        if value.get("contract") == "chatty-work-evidence-reference/v1" and _sha(value) == target:
                            found = True
                        for nested in value.values():
                            visit(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            visit(nested)
                for candidate in cur.fetchall():
                    value = (_row(candidate) or {}).get("payload")
                    if isinstance(value, str):
                        value = json.loads(value)
                    visit(value)
                if not found:
                    raise ConstructExecutionError("EXECUTION_OWNER_EVIDENCE_NOT_FOUND", "owner evidence is not in canonical work history", 409)
        event = _event(program, auth, payload, actor, now, evidence_digest)
        envelope = _envelope(event, self.private_key_pem)
        permit = payload.get("startPermit") if auth["eventType"] == "execution_attempt_started" else None
        if permit and (permit.get("preStartHeadEventId") != event["parentEventId"] or permit.get("preStartHeadSha256") != event["parentEventSha256"]):
            raise ConstructExecutionError("EXECUTION_START_FENCE_INVALID", "start permit does not bind the current lease head", 409)
        attempt_ordinal = None
        attempt_id = None
        consumed_approval = None
        consumed_approval_disclosure = None
        lease_id = None
        if isinstance(payload.get("lease"), dict):
            attempt_ordinal, lease_id = payload["lease"].get("attemptOrdinal"), payload["lease"].get("leaseId")
        if permit:
            attempt_ordinal = permit.get("attemptOrdinal")
        scoped_document = next((value for key in ("lease", "startPermit", "dispatchMarker", "hostReceipt", "readback", "recovery")
                                if isinstance((value := payload.get(key)), dict)), None)
        scoped_step_id = (scoped_document or {}).get("stepId")
        attempt_ordinal = attempt_ordinal or (scoped_document or {}).get("attemptOrdinal")
        if scoped_step_id and attempt_ordinal:
            attempt_id = f"execution-attempt-{_sha({'executionId': program['executionId'], 'stepId': scoped_step_id, 'attemptOrdinal': attempt_ordinal})[:40]}"
        if auth["eventType"] == "execution_authorized":
            consumed_approval = payload.get("approvalPayloadSha256")
            consumed_approval_disclosure = payload.get("approvalDisclosureSha256")
        cur.execute(
            """INSERT INTO ovvaults.construct_work_execution_events
              (owner_user_id,program_id,execution_id,item_id,source_construct_id,responsible_construct_id,
               thread_id,session_id,branch_id,event_id,sequence,parent_event_id,parent_event_sha256,event_type,
               attempt_id,attempt_ordinal,consumed_approval_sha256,consumed_approval_disclosure_sha256,lease_id,idempotency_key,request_digest,
               core_authorization_hash,evidence_digest,occurred_at,actor_principal_id,actor_principal_type,
               actor_authority,payload,payload_sha256,event_sha256,envelope,signature_algorithm,
               signature_key_id,signature)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s) RETURNING envelope""",
            (owner, event["programId"], event["executionId"], event["itemId"], event["sourceConstructId"],
             event["responsibleConstructId"], event["threadId"], event["sessionId"], event["branchId"],
             event["eventId"], event["sequence"], event["parentEventId"], event["parentEventSha256"],
             event["eventType"], attempt_id, attempt_ordinal, consumed_approval, consumed_approval_disclosure,
             lease_id, event["idempotencyKey"],
             event["requestDigest"], event["coreAuthorizationHash"], event["evidenceDigest"], event["occurredAt"],
             actor["principalId"], actor["principalType"], actor["authority"], json.dumps(payload),
             event["payloadSha256"], event["eventSha256"], json.dumps(envelope), envelope["algorithm"],
             envelope["keyId"], envelope["signature"]),
        )
        self._persist_hydro_event(cur, owner=owner, program=program, event=event, payload=payload)
        return envelope

    def prepare_hydro_graph(self, owner_user_id: str, request: dict[str, Any], *,
                            trusted_internal: bool = False, now: datetime | None = None) -> dict[str, Any]:
        """Resolve a proposal locator and stage a VVAULT-derived bounded Hydro graph.

        Raw graphs and capability manifests are deliberately not accepted on this
        boundary.  Both are resolved from immutable, owner-qualified VVAULT rows.
        """
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro graph staging requires trusted Chatty service", 403)
        request = _exact(request, {"programId", "proposalArtifactId"}, "hydroExecutionGraphPrepare")
        owner = str(owner_user_id)
        program_id = _id(request["programId"], "programId")
        proposal_id = _id(request["proposalArtifactId"], "proposalArtifactId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                current = now if now is not None else self._database_now(cur)
                cur.execute(
                    """SELECT candidate,capability_manifest,execution_proposal_binding,receipt
                         FROM ovvaults.construct_work_execution_proposals
                        WHERE owner_user_id=%s AND program_id=%s AND proposal_id=%s FOR SHARE""",
                    (owner, program_id, proposal_id),
                )
                proposal_row = _row(cur.fetchone())
                if not proposal_row:
                    raise ConstructExecutionError("EXECUTION_PROPOSAL_NOT_FOUND", "canonical proposal artifact not found", 404)
                candidate = proposal_row.get("candidate")
                manifest = proposal_row.get("capability_manifest")
                binding = proposal_row.get("execution_proposal_binding")
                receipt = proposal_row.get("receipt")
                if isinstance(candidate, str): candidate = json.loads(candidate)
                if isinstance(manifest, str): manifest = json.loads(manifest)
                if isinstance(binding, str): binding = json.loads(binding)
                if isinstance(receipt, str): receipt = json.loads(receipt)
                verified_proposal = _verify_signed(
                    receipt, fields=_PROPOSAL_ENVELOPE_FIELDS,
                    contract="life-vvault-work-execution-proposal-envelope/v1",
                    public_key_pem=canonical_projection_signing.public_key_document(
                        private_key_pem=self.private_key_pem
                    )["publicKeyPem"], expected_key_id=None,
                )
                if any((
                    verified_proposal.get("ownerPrincipalId") != owner,
                    verified_proposal.get("proposalArtifactId") != proposal_id,
                    verified_proposal.get("candidate") != candidate,
                    verified_proposal.get("capabilityManifest") != manifest,
                    verified_proposal.get("executionProposalBinding") != binding,
                    verified_proposal.get("artifactSha256") != _sha(candidate),
                    candidate.get("programId") != program_id,
                    candidate.get("operation") != "hydro.graph.dispatch",
                    candidate.get("advancementAuthority") is not False,
                    candidate.get("effectAuthority") is not False,
                    candidate.get("candidateHash") != _sha({key: value for key, value in candidate.items()
                                                             if key != "candidateHash"}),
                )):
                    raise ConstructExecutionError(
                        "HYDRO_EXECUTION_PROPOSAL_INVALID",
                        "Hydro graph source proposal failed immutable signed readback", 409,
                    )
                _validate_proposal_binding(binding)
                argument_reference = _exact(candidate.get("argumentsArtifact"),
                                            {"artifactId", "sha256", "mediaType"}, "argumentsArtifact")
                cur.execute(
                    """SELECT program_id,media_type,content,content_sha256
                         FROM ovvaults.construct_work_execution_inputs
                        WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s FOR SHARE""",
                    (owner, program_id, argument_reference["artifactId"]),
                )
                argument_row = _row(cur.fetchone())
                arguments = (argument_row or {}).get("content")
                if isinstance(arguments, str): arguments = json.loads(arguments)
                expected_argument_keys = {"contract", "graph", "graphSha256", "limits"}
                if not isinstance(arguments, dict) or set(arguments) not in {
                    frozenset(expected_argument_keys), frozenset(expected_argument_keys | {"maxOutputBytes"})
                } or any((
                    argument_row.get("media_type") != "application/json" if argument_row else True,
                    argument_row.get("content_sha256") != candidate.get("argumentsSha256") if argument_row else True,
                    argument_reference.get("sha256") != candidate.get("argumentsSha256"),
                    _sha(arguments) != candidate.get("argumentsSha256"),
                    arguments.get("contract") != "chatty-hydro-graph-dispatch-operation/v1",
                )):
                    raise ConstructExecutionError(
                        "HYDRO_EXECUTION_ARGUMENT_ARTIFACT_INVALID",
                        "canonical Hydro argument artifact failed owner-scoped readback", 409,
                    )
                source, source_synthesis = _validate_hydro_graph_source(arguments.get("graph"))
                limits = _exact(arguments.get("limits"),
                                {"maxParallel", "maxDepth", "maxDurationMs", "maxOutputBytes"},
                                "hydroExecutionLimits")
                if any((
                    arguments.get("graphSha256") != _sha(source),
                    limits.get("maxParallel") != source["maxParallel"],
                    limits.get("maxDepth") != source["maxDepth"],
                    not isinstance(limits.get("maxDurationMs"), int)
                    or isinstance(limits.get("maxDurationMs"), bool)
                    or not 1_000 <= limits["maxDurationMs"] <= 86_400_000,
                    not isinstance(limits.get("maxOutputBytes"), int)
                    or isinstance(limits.get("maxOutputBytes"), bool)
                    or not 1 <= limits["maxOutputBytes"] <= _MAX_RESULT_BYTES,
                    arguments.get("maxOutputBytes", limits.get("maxOutputBytes")) != limits.get("maxOutputBytes"),
                    any(node["timeoutMs"] > limits["maxDurationMs"]
                        or node["maxOutputBytes"] > limits["maxOutputBytes"] for node in source["nodes"]),
                )):
                    raise ConstructExecutionError(
                        "HYDRO_EXECUTION_ARGUMENT_LIMIT_INVALID",
                        "canonical Hydro graph or limit digest is invalid", 409,
                    )
                execution_id = f"execution-{_sha({'candidateHash': candidate['candidateHash'], 'proposalArtifactId': proposal_id})[:40]}"
                assignments = []
                for node in source["nodes"]:
                    assignment_body = {
                        "contract": HYDRO_EXECUTION_ASSIGNMENT, "assignmentId": node["nodeId"],
                        "ordinal": node["ordinal"], "kind": node["kind"],
                        "workerPrincipalId": node["workerPrincipalId"],
                        "dependencyAssignmentIds": node["dependencyNodeIds"], "hostId": node["hostId"],
                        "capabilityId": node["capabilityId"], "inputArtifacts": node["inputArtifacts"],
                        "argumentsArtifact": node["argumentsArtifact"], "argumentsSha256": node["argumentsSha256"],
                        "resourceKeys": node["resourceKeys"], "risk": node["risk"], "required": node["required"],
                        "providerCandidates": [], "idempotencyMode": node["idempotencyMode"],
                        "readbackMode": node["readbackMode"], "timeoutMs": node["timeoutMs"],
                        "maxOutputBytes": node["maxOutputBytes"],
                        "completionFactKinds": node["completionFactKinds"],
                    }
                    assignments.append({**assignment_body, "assignmentHash": _sha(assignment_body)})
                graph_fixed = {
                    "contract": HYDRO_EXECUTION_GRAPH, "authority": AUTHORITY, "graphId": source["graphId"],
                    "executionId": execution_id, "ownerPrincipalId": owner, "programId": program_id,
                    "itemId": candidate["itemId"], "sourceConstructId": candidate["sourceConstructId"],
                    "parentResponsibleConstructId": candidate["responsibleConstructId"],
                    "threadId": candidate["threadId"], "sessionId": candidate["sessionId"],
                    "branchId": candidate["branchId"], "goalRevision": candidate["goalRevision"],
                    "workHeadEventId": candidate["workHeadEventId"], "workHeadSha256": candidate["workHeadSha256"],
                    "workStateReceiptSha256": candidate["workStateReceiptSha256"],
                    "decisionHash": candidate["decisionHash"], "nextActionHash": candidate["nextActionHash"],
                    "preparedContextReceiptSha256": candidate["preparedContextReceiptSha256"],
                    "sourceArgumentsSha256": candidate["argumentsSha256"], "assignments": assignments,
                    "maxParallel": source["maxParallel"], "maxDepth": source["maxDepth"],
                }
                cur.execute(
                    """SELECT projection FROM ovvaults.construct_work_hydro_graphs
                        WHERE owner_user_id=%s AND graph_id=%s""", (owner, source["graphId"]),
                )
                existing_graph_row = _row(cur.fetchone())
                existing_projection = (existing_graph_row or {}).get("projection")
                if isinstance(existing_projection, str): existing_projection = json.loads(existing_projection)
                if existing_projection:
                    verified_graph = _verify_signed(
                        existing_projection, fields=_HYDRO_GRAPH_SIGNED_FIELDS, contract=HYDRO_EXECUTION_GRAPH,
                        public_key_pem=canonical_projection_signing.public_key_document(
                            private_key_pem=self.private_key_pem
                        )["publicKeyPem"], expected_key_id=None, now=current,
                    )
                    graph = {key: value for key, value in verified_graph.items()
                             if key not in {"payloadSha256", "algorithm", "keyId", "signature"}}
                    if any(graph.get(key) != value for key, value in graph_fixed.items()):
                        raise ConstructExecutionError(
                            "HYDRO_EXECUTION_GRAPH_CONFLICT", "Hydro graph ID is bound to a different proposal", 409,
                        )
                else:
                    graph_core = {**graph_fixed, "createdAt": _iso(current),
                                  "expiresAt": _iso(current + timedelta(minutes=5))}
                    graph = {**graph_core, "graphHash": _sha(graph_core)}
                graph, synthesis_assignment_id = _validate_hydro_graph_body(graph, owner=owner, now=current)
                if synthesis_assignment_id != source_synthesis["nodeId"]:
                    raise ConstructExecutionError("HYDRO_EXECUTION_SYNTHESIS_BOUNDARY_INVALID", "Hydro synthesis mapping is invalid", 409)
                _validate_hydro_capability_manifest(
                    manifest, owner=owner, graph=graph, public_key_pem=self.core_public_key_pem,
                    expected_key_id=self.core_key_id, host_key_resolver=self._host_key, now=current,
                )
                cur.execute(
                    """SELECT p.construct_id,p.thread_id,p.session_id,p.branch_id,
                              e.resulting_goal_revision,e.event_id,e.event_sha256
                         FROM ovvaults.construct_work_programs p
                         JOIN LATERAL (
                           SELECT resulting_goal_revision,event_id,event_sha256
                             FROM ovvaults.construct_work_events
                            WHERE owner_user_id=p.owner_user_id AND program_id=p.program_id
                            ORDER BY sequence DESC LIMIT 1
                         ) e ON TRUE
                        WHERE p.owner_user_id=%s AND p.program_id=%s FOR SHARE OF p""",
                    (owner, graph["programId"]),
                )
                work = _row(cur.fetchone())
                if not work or any((
                    work.get("construct_id") != graph["sourceConstructId"],
                    work.get("thread_id") != graph["threadId"],
                    work.get("session_id") != graph["sessionId"],
                    work.get("branch_id") != graph["branchId"],
                    work.get("resulting_goal_revision") != graph["goalRevision"],
                    work.get("event_id") != graph["workHeadEventId"],
                    work.get("event_sha256") != graph["workHeadSha256"],
                )):
                    raise ConstructExecutionError("HYDRO_EXECUTION_WORK_SCOPE_INVALID", "Hydro graph is not bound to the canonical work head", 409)
                workers = sorted({assignment["workerPrincipalId"] for assignment in graph["assignments"]})
                cur.execute(
                    """SELECT construct_id FROM ovvaults.construct_incarnations
                        WHERE owner_user_id=%s AND construct_id=ANY(%s) AND lifecycle_state='active'""",
                    (owner, workers),
                )
                active_workers = {str((_row(value) or {}).get("construct_id")) for value in cur.fetchall()}
                if active_workers != set(workers):
                    raise ConstructExecutionError(
                        "HYDRO_EXECUTION_WORKER_PRINCIPAL_INVALID",
                        "every Hydro assignment must name an active construct owned by the authenticated owner", 403,
                    )
                cur.execute(
                    """SELECT membership_revision FROM ovvaults.conversation_threads
                        WHERE owner_user_id=%s AND thread_id=%s FOR SHARE""",
                    (owner, graph["threadId"]),
                )
                thread_row = _row(cur.fetchone())
                cur.execute(
                    """SELECT principal_id,principal_type FROM ovvaults.conversation_thread_memberships
                        WHERE owner_user_id=%s AND thread_id=%s AND active=true""",
                    (owner, graph["threadId"]),
                )
                member_types = {
                    str((_row(entry) or {}).get("principal_id")): (_row(entry) or {}).get("principal_type")
                    for entry in cur.fetchall()
                }
                if not thread_row:
                    raise ConstructExecutionError(
                        "HYDRO_WORKER_PARTICIPANT_THREAD_INVALID",
                        "Hydro worker participant thread is not canonical for the owner", 409,
                    )
                for assignment in graph["assignments"]:
                    self._assert_input_artifacts(
                        cur, owner=owner, program={"programId": graph["programId"]},
                        intent={"argumentsArtifact": assignment["argumentsArtifact"],
                                "argumentsSha256": assignment["argumentsSha256"],
                                "inputArtifacts": assignment["inputArtifacts"]},
                    )
                    cur.execute(
                        """SELECT media_type,content,content_sha256
                             FROM ovvaults.construct_work_execution_inputs
                            WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s FOR SHARE""",
                        (owner, graph["programId"], assignment["argumentsArtifact"]["artifactId"]),
                    )
                    argument_artifact = _row(cur.fetchone())
                    worker_arguments = (argument_artifact or {}).get("content")
                    if isinstance(worker_arguments, str):
                        worker_arguments = json.loads(worker_arguments)
                    if not isinstance(worker_arguments, dict) or any((
                        (argument_artifact or {}).get("media_type") != "application/json",
                        (argument_artifact or {}).get("content_sha256") != assignment["argumentsSha256"],
                        _sha(worker_arguments) != assignment["argumentsSha256"],
                    )):
                        raise ConstructExecutionError(
                            "HYDRO_WORKER_ARGUMENT_ARTIFACT_INVALID",
                            "Hydro worker argument artifact failed canonical readback", 409,
                        )
                    _validate_hydro_worker_inference_arguments(
                        worker_arguments, assignment=assignment, graph=graph,
                    )
                    frame = worker_arguments["participantFrame"]
                    participants = frame.get("participants") if isinstance(frame.get("participants"), list) else []
                    participant_ids = {
                        str(entry.get("principalId")) for entry in participants if isinstance(entry, dict)
                    }
                    if any((
                        int(frame.get("membershipRevision") or 0)
                        != int(thread_row.get("membership_revision") or 0),
                        participant_ids != set(member_types),
                        member_types.get(graph["parentResponsibleConstructId"]) != "construct",
                        member_types.get(assignment["workerPrincipalId"]) != "construct",
                    )):
                        raise ConstructExecutionError(
                            "HYDRO_WORKER_PARTICIPANT_MEMBERSHIP_INVALID",
                            "Hydro worker participant frame does not match current owner-qualified membership", 409,
                        )
                projection = existing_projection or _signed(graph, self.private_key_pem)
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_hydro_graphs
                      (owner_user_id,program_id,execution_id,graph_id,source_arguments_sha256,
                       graph_payload_sha256,graph_hash,max_parallel,max_depth,synthesis_assignment_id,
                       projection,projection_sha256,signature_algorithm,signature_key_id,signature)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                      ON CONFLICT (owner_user_id,graph_id) DO NOTHING""",
                    (owner, graph["programId"], graph["executionId"], graph["graphId"],
                     graph["sourceArgumentsSha256"], projection["payloadSha256"], graph["graphHash"],
                     graph["maxParallel"], graph["maxDepth"], synthesis_assignment_id,
                     json.dumps(projection), projection["payloadSha256"], projection["algorithm"],
                     projection["keyId"], projection["signature"]),
                )
                for assignment in graph["assignments"]:
                    cur.execute(
                        """INSERT INTO ovvaults.construct_work_hydro_assignments
                          (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,
                           worker_principal_id,assignment_kind,ordinal,required,dependency_assignment_ids,
                           capability_id,host_id,arguments_artifact_id,arguments_media_type,arguments_sha256,
                           prepared_context_receipt_sha256,prepared_request_sha256,resource_keys,assignment_hash,assignment)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)
                          ON CONFLICT (owner_user_id,graph_id,assignment_id) DO NOTHING""",
                        (owner, graph["programId"], graph["executionId"], graph["graphId"],
                         assignment["assignmentId"], assignment["assignmentId"], assignment["workerPrincipalId"],
                         assignment["kind"], assignment["ordinal"], assignment["required"],
                         json.dumps(assignment["dependencyAssignmentIds"]), assignment["capabilityId"],
                         assignment["hostId"], assignment["argumentsArtifact"]["artifactId"],
                         assignment["argumentsArtifact"]["mediaType"], assignment["argumentsSha256"],
                         graph["preparedContextReceiptSha256"], _sha({
                             "preparedContextReceiptSha256": graph["preparedContextReceiptSha256"],
                             "workerPrincipalId": assignment["workerPrincipalId"],
                             "argumentsArtifact": assignment["argumentsArtifact"],
                             "argumentsSha256": assignment["argumentsSha256"],
                             "providerCandidates": assignment["providerCandidates"],
                         }),
                         json.dumps(assignment["resourceKeys"]), assignment["assignmentHash"], json.dumps(assignment)),
                    )
                cur.execute(
                    """SELECT projection FROM ovvaults.construct_work_hydro_graphs
                        WHERE owner_user_id=%s AND graph_id=%s""", (owner, graph["graphId"]),
                )
                stored = _row(cur.fetchone())
                stored_projection = (stored or {}).get("projection")
                if isinstance(stored_projection, str):
                    stored_projection = json.loads(stored_projection)
                if stored_projection != projection:
                    raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_CONFLICT", "Hydro graph ID is bound to different bytes", 409)
        return projection

    def get_hydro_graph(self, owner_user_id: str, graph_id: str, *,
                        trusted_internal: bool = False, now: datetime | None = None) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro graph read requires trusted Chatty service", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT projection FROM ovvaults.construct_work_hydro_graphs WHERE owner_user_id=%s AND graph_id=%s",
                            (str(owner_user_id), _id(graph_id, "graphId")))
                row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("HYDRO_EXECUTION_GRAPH_NOT_FOUND", "Hydro graph not found", 404)
        projection = row["projection"] if isinstance(row["projection"], dict) else json.loads(row["projection"])
        verified = _verify_signed(
            projection, fields=_HYDRO_GRAPH_SIGNED_FIELDS, contract=HYDRO_EXECUTION_GRAPH,
            public_key_pem=canonical_projection_signing.public_key_document(
                private_key_pem=self.private_key_pem
            )["publicKeyPem"], expected_key_id=None, now=now,
        )
        _validate_hydro_graph_body(
            {key: value for key, value in verified.items()
             if key not in {"payloadSha256", "algorithm", "keyId", "signature"}},
            owner=str(owner_user_id), now=now or datetime.now(timezone.utc),
        )
        return verified

    def _hydro_dependency_snapshot(
        self, cur: Any, *, owner: str, program: dict[str, Any], events: list[dict[str, Any]],
        synthesis_step: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        results: list[dict[str, Any]] = []
        failed_optional: list[str] = []
        for dependency_step_id in synthesis_step["dependencyStepIds"]:
            dependency = next(step for step in program["steps"] if step["stepId"] == dependency_step_id)
            completed = any(
                entry["event"].get("eventType") == "execution_step_completed"
                and (entry["event"].get("payload") or {}).get("stepId") == dependency_step_id
                for entry in events
            )
            if not completed:
                terminal = any(
                    _execution_step_id(entry["event"].get("eventType"), entry["event"].get("payload") or {})
                    == dependency_step_id and entry["event"].get("eventType") in {
                        "execution_step_failed", "execution_outcome_unknown", "execution_cancel_acknowledged"
                    } for entry in events
                )
                if not dependency["required"] and terminal \
                        and program["policy"]["optionalFailureMode"] == "degraded":
                    failed_optional.append(dependency["hydroScope"]["assignmentId"])
                    continue
                raise ConstructExecutionError(
                    "HYDRO_SYNTHESIS_DEPENDENCY_UNVERIFIED",
                    f"Hydro dependency {dependency_step_id} is not canonically complete", 409,
                )
            receipt = next((
                (entry["event"].get("payload") or {}).get("hostReceipt")
                for entry in reversed(events)
                if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                and ((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("stepId")
                == dependency_step_id
            ), None)
            readback = next((
                (entry["event"].get("payload") or {}).get("readback")
                for entry in reversed(events)
                if entry["event"].get("eventType") == "execution_readback_recorded"
                and ((entry["event"].get("payload") or {}).get("readback") or {}).get("stepId")
                == dependency_step_id
            ), None)
            artifacts = (receipt or {}).get("outputArtifacts") or []
            artifact = artifacts[0] if artifacts else None
            if not isinstance(receipt, dict) or receipt.get("outcome") != "completed" \
                    or not isinstance(artifact, dict) or receipt.get("outputSha256") is None \
                    or (dependency["readbackMode"] == "signed"
                        and (not isinstance(readback, dict) or readback.get("outcome") != "committed")):
                raise ConstructExecutionError(
                    "HYDRO_SYNTHESIS_DEPENDENCY_EVIDENCE_INVALID",
                    f"Hydro dependency {dependency_step_id} lacks immutable result/readback evidence", 409,
                )
            cur.execute(
                """SELECT content_sha256,content_bytes,media_type FROM ovvaults.construct_work_execution_artifacts
                    WHERE owner_user_id=%s AND execution_id=%s AND artifact_id=%s
                      AND artifact_type='result' FOR SHARE""",
                (owner, program["executionId"], artifact.get("artifactId")),
            )
            stored_artifact = _row(cur.fetchone())
            content_bytes = (stored_artifact or {}).get("content_bytes")
            if isinstance(content_bytes, memoryview):
                content_bytes = content_bytes.tobytes()
            try:
                result_document = json.loads(content_bytes.decode("utf-8")) if isinstance(content_bytes, bytes) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                result_document = None
            if not stored_artifact or any((
                stored_artifact.get("content_sha256") != receipt.get("outputSha256"),
                stored_artifact.get("media_type") != "application/json",
                not isinstance(result_document, dict),
                _sha(result_document) != receipt.get("outputSha256"),
                not _hydro_worker_result_matches_receipt(result_document, receipt),
            )):
                raise ConstructExecutionError(
                    "HYDRO_SYNTHESIS_RESULT_ARTIFACT_INVALID",
                    f"Hydro dependency {dependency_step_id} result artifact failed canonical readback", 409,
                )
            results.append({
                "assignmentId": dependency["hydroScope"]["assignmentId"],
                "stepId": dependency_step_id,
                "workerPrincipalId": dependency["responsibleConstructId"],
                "attemptOrdinal": receipt["attemptOrdinal"],
                "resultArtifactId": artifact["artifactId"],
                "contentSha256": result_document["outputSha256"],
                "hostReceiptPayloadSha256": receipt["payloadSha256"],
                "readbackPayloadSha256": readback["payloadSha256"]
                if dependency["readbackMode"] == "signed" else None,
            })
        return results, failed_optional

    def prepare_hydro_synthesis_inputs(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        trusted_internal: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro synthesis resolution requires trusted Chatty service", 403)
        request = _exact(request, {"synthesisStepId"}, "hydroSynthesisInputRequest")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), True)
                current = now if now is not None else self._database_now(cur)
                program = row["execution_program"] if isinstance(row["execution_program"], dict) \
                    else json.loads(row["execution_program"])
                events = self._events(cur, str(owner_user_id), execution_id)
                step = next((entry for entry in program["steps"]
                             if entry["stepId"] == request["synthesisStepId"]), None)
                if not step or step.get("kind") != "hydro_synthesis" or not program.get("hydroGraphBinding"):
                    raise ConstructExecutionError("HYDRO_SYNTHESIS_STEP_INVALID", "synthesis step is not canonical", 409)
                if any(entry["event"].get("eventType") == "execution_hydro_synthesis_inputs_resolved"
                       for entry in events):
                    raise ConstructExecutionError("HYDRO_SYNTHESIS_ALREADY_RESOLVED", "synthesis inputs are immutable", 409)
                dependency_results, failed_optional = self._hydro_dependency_snapshot(
                    cur, owner=str(owner_user_id), program=program, events=events, synthesis_step=step,
                )
                head = events[-1]["event"]
        body = {
            "contract": HYDRO_SYNTHESIS_INPUT_RESOLUTION, "authority": AUTHORITY,
            "ownerPrincipalId": str(owner_user_id), "programId": program["programId"],
            "executionId": execution_id, "graphId": program["hydroGraphBinding"]["graphId"],
            "synthesisAssignmentId": step["hydroScope"]["assignmentId"],
            "synthesisStepId": step["stepId"], "expectedHeadEventId": head["eventId"],
            "expectedHeadSha256": head["eventSha256"], "dependencyResults": dependency_results,
            "failedOptionalAssignmentIds": failed_optional, "issuedAt": _iso(current),
        }
        return _signed(body, self.private_key_pem)

    def resolve_hydro_synthesis_result_contents(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        trusted_internal: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve ordered owner-private child outputs for one fenced synthesis attempt."""
        if not trusted_internal:
            raise ConstructExecutionError(
                "EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro synthesis result content requires trusted Chatty service", 403,
            )
        request = _exact(
            request, {"synthesisStepId", "synthesisInputResolutionPayloadSha256"},
            "hydroSynthesisResultContentRequest",
        )
        synthesis_step_id = _id(request["synthesisStepId"], "synthesisStepId")
        resolution_sha = _digest(
            request["synthesisInputResolutionPayloadSha256"],
            "synthesisInputResolutionPayloadSha256",
        )
        owner, execution_id = str(owner_user_id), _id(execution_id, "executionId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                current = now if now is not None else self._database_now(cur)
                row = self._program_row(cur, owner, execution_id, True)
                program = row["execution_program"] if isinstance(row["execution_program"], dict) \
                    else json.loads(row["execution_program"])
                events = self._events(cur, owner, execution_id)
                step = next((entry for entry in program.get("steps", [])
                             if entry.get("stepId") == synthesis_step_id), None)
                resolution = next((
                    (entry["event"].get("payload") or {}).get("resolution")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_hydro_synthesis_inputs_resolved"
                    and (((entry["event"].get("payload") or {}).get("resolution") or {}).get("payloadSha256")
                         == resolution_sha)
                ), None)
                head = events[-1]["event"] if events else None
                head_permit = ((head or {}).get("payload") or {}).get("startPermit")
                if not step or step.get("kind") != "hydro_synthesis" or not isinstance(resolution, dict) \
                        or (head or {}).get("eventType") != "execution_attempt_started" \
                        or not isinstance(head_permit, dict) or head_permit.get("stepId") != synthesis_step_id:
                    raise ConstructExecutionError(
                        "HYDRO_SYNTHESIS_RESULT_CONTENT_STATE_INVALID",
                        "synthesis contents require the current fenced pre-dispatch synthesis attempt", 409,
                    )
                vvault_public = canonical_projection_signing.public_key_document(
                    private_key_pem=self.private_key_pem
                )["publicKeyPem"]
                resolution = _verify_signed(
                    resolution, fields=_HYDRO_SYNTHESIS_RESOLUTION_FIELDS,
                    contract=HYDRO_SYNTHESIS_INPUT_RESOLUTION, public_key_pem=vvault_public,
                    expected_key_id=None,
                )
                results: list[dict[str, Any]] = []
                total_bytes = 0
                for source in resolution["dependencyResults"]:
                    receipt = next((
                        (entry["event"].get("payload") or {}).get("hostReceipt")
                        for entry in reversed(events)
                        if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                        and (((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("payloadSha256")
                             == source["hostReceiptPayloadSha256"])
                    ), None)
                    if not isinstance(receipt, dict):
                        raise ConstructExecutionError(
                            "HYDRO_SYNTHESIS_RESULT_CONTENT_EVIDENCE_INVALID",
                            "synthesis child host receipt is unavailable", 409,
                        )
                    host_key = self._host_key(receipt.get("hostId"))
                    if not host_key:
                        raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
                    _verify_signed(
                        receipt, fields=_host_signed_fields(receipt), contract=HOST_RECEIPT,
                        public_key_pem=host_key[0], expected_key_id=host_key[1],
                    )
                    cur.execute(
                        """SELECT content_sha256,media_type,content_bytes
                             FROM ovvaults.construct_work_execution_artifacts
                            WHERE owner_user_id=%s AND execution_id=%s AND artifact_id=%s
                              AND artifact_type='result' FOR SHARE""",
                        (owner, execution_id, source["resultArtifactId"]),
                    )
                    artifact = _row(cur.fetchone())
                    content_bytes = (artifact or {}).get("content_bytes")
                    if isinstance(content_bytes, memoryview):
                        content_bytes = content_bytes.tobytes()
                    try:
                        result_document = json.loads(content_bytes.decode("utf-8")) \
                            if isinstance(content_bytes, bytes) else None
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        result_document = None
                    output = (result_document or {}).get("output")
                    output_bytes = output.encode("utf-8") if isinstance(output, str) else b""
                    if not artifact or any((
                        artifact.get("media_type") != "application/json",
                        artifact.get("content_sha256") != receipt.get("outputSha256"),
                        not isinstance(result_document, dict),
                        _sha(result_document) != receipt.get("outputSha256"),
                        not _hydro_worker_result_matches_receipt(result_document, receipt),
                        not output_bytes or len(output_bytes) > _MAX_RESULT_BYTES,
                        hashlib.sha256(output_bytes).hexdigest() != source.get("contentSha256"),
                        result_document.get("assignmentId") != source.get("assignmentId"),
                        result_document.get("workerPrincipalId") != source.get("workerPrincipalId"),
                    )):
                        raise ConstructExecutionError(
                            "HYDRO_SYNTHESIS_RESULT_CONTENT_EVIDENCE_INVALID",
                            "synthesis child result bytes failed immutable canonical readback", 409,
                        )
                    total_bytes += len(output_bytes)
                    results.append({
                        "assignmentId": source["assignmentId"], "stepId": source["stepId"],
                        "workerPrincipalId": source["workerPrincipalId"],
                        "resultArtifactId": source["resultArtifactId"], "content": output,
                        "contentSha256": source["contentSha256"], "contentBytes": len(output_bytes),
                        "hostReceiptPayloadSha256": source["hostReceiptPayloadSha256"],
                        "readbackPayloadSha256": source["readbackPayloadSha256"],
                    })
                if total_bytes > 512 * 1024:
                    raise ConstructExecutionError(
                        "HYDRO_SYNTHESIS_RESULT_CONTENT_CAPACITY_EXCEEDED",
                        "ordered synthesis result content exceeds 512 KiB", 413,
                    )
        body = {
            "contract": HYDRO_SYNTHESIS_RESULT_CONTENT_RESOLUTION, "authority": AUTHORITY,
            "ownerPrincipalId": owner, "programId": program["programId"],
            "executionId": execution_id, "graphId": program["hydroGraphBinding"]["graphId"],
            "synthesisAssignmentId": resolution["synthesisAssignmentId"],
            "synthesisStepId": synthesis_step_id,
            "synthesisInputResolutionPayloadSha256": resolution["payloadSha256"],
            "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
            "results": results, "failedOptionalAssignmentIds": resolution["failedOptionalAssignmentIds"],
            "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=60)),
        }
        return _signed(body, self.private_key_pem)

    def hydro_recovery_projection(
        self, owner_user_id: str, execution_id: str, *, trusted_internal: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a signed, content-free per-assignment crash recovery view.

        The projection is derived exclusively from the immutable execution program and
        its VVAULT-signed event chain.  It carries no advancement or effect authority.
        """
        if not trusted_internal:
            raise ConstructExecutionError(
                "EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro recovery projection requires trusted Chatty service", 403,
            )
        execution_id = _id(execution_id, "executionId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, False)
                current = now if now is not None else self._database_now(cur)
                events = self._events(cur, str(owner_user_id), execution_id)
        program = row["execution_program"] if isinstance(row["execution_program"], dict) \
            else json.loads(row["execution_program"])
        binding = program.get("hydroGraphBinding")
        if not binding or len(program.get("steps", [])) < 2 or not events:
            raise ConstructExecutionError(
                "HYDRO_EXECUTION_PROGRAM_REQUIRED", "execution is not a canonical Hydro fan-out", 409,
            )
        completed = {
            (entry["event"].get("payload") or {}).get("stepId")
            for entry in events if entry["event"].get("eventType") == "execution_step_completed"
        }
        assignments: list[dict[str, Any]] = []
        for step in sorted(program["steps"], key=lambda candidate: candidate["ordinal"]):
            scope = _validate_hydro_scope(step.get("hydroScope"))
            step_events = _step_event_documents(events, step["stepId"])
            latest_type = step_events[-1]["eventType"] if step_events else None
            documents: dict[str, dict[str, Any]] = {}
            for entry in step_events:
                payload = entry.get("payload") or {}
                for key in ("lease", "startPermit", "dispatchMarker", "hostReceipt", "readback", "recovery"):
                    if isinstance(payload.get(key), dict):
                        documents[key] = payload[key]
            lease, permit = documents.get("lease"), documents.get("startPermit")
            marker, receipt = documents.get("dispatchMarker"), documents.get("hostReceipt")
            readback, recovery = documents.get("readback"), documents.get("recovery")
            attempt = next((candidate.get("attemptOrdinal") for candidate in
                            (readback, receipt, marker, permit, lease, recovery)
                            if isinstance(candidate, dict) and candidate.get("attemptOrdinal") is not None), None)
            result_artifacts = (receipt or {}).get("outputArtifacts") or []
            result_artifact = result_artifacts[0] if result_artifacts else None
            result_id = result_artifact.get("artifactId") if isinstance(result_artifact, dict) else None
            result_sha = (receipt or {}).get("outputSha256") if result_id else None
            recovery_capability = (recovery or {}).get("recoveryCapability") or {}
            status, safe_action = "authorized", "none"
            dependencies_complete = all(item in completed for item in step.get("dependencyStepIds", []))
            if latest_type is None and dependencies_complete:
                status, safe_action = "ready", "acquire_lease"
            elif latest_type in {"execution_lease_acquired", "execution_lease_renewed"}:
                status, safe_action = "authorized", "none"
            elif latest_type == "execution_attempt_started":
                status, safe_action = "dispatch_pending", "resume_pre_dispatch"
            elif latest_type == "execution_effect_dispatched":
                status, safe_action = "attempt_in_flight", "reconcile_dispatched_effect"
            elif latest_type == "execution_attempt_outcome_recorded":
                outcome = (receipt or {}).get("outcome")
                if outcome == "completed":
                    status, safe_action = ("result_captured", "record_readback") \
                        if step.get("readbackMode") == "signed" else ("verification_pending", "verify_result")
                elif outcome in {"failed", "not_started"}:
                    providers = step.get("providerCandidates") or []
                    provider = (receipt or {}).get("providerId")
                    provider_index = providers.index(provider) if provider in providers else -1
                    fallback = all(((receipt or {}).get("effectCommitted") == "false",
                                    (receipt or {}).get("outputArtifacts") == [],
                                    (receipt or {}).get("outputSha256") is None,
                                    provider_index >= 0 and provider_index + 1 < len(providers)))
                    status, safe_action = ("provider_fallback_pending", "await_recovery") \
                        if fallback else ("failed", "none")
                elif outcome == "cancelled":
                    status, safe_action = "cancelled", "none"
                else:
                    status, safe_action = "outcome_unknown", "await_recovery"
            elif latest_type == "execution_readback_recorded":
                outcome = (readback or {}).get("outcome")
                status, safe_action = {
                    "committed": ("verification_pending", "verify_result"),
                    "not_committed": ("recovery_pending", "await_recovery"),
                    "unknown": ("outcome_unknown", "await_recovery"),
                }.get(outcome, ("outcome_unknown", "await_recovery"))
            elif latest_type == "execution_recovery_selected":
                status, safe_action = "authorized", "acquire_lease"
            elif latest_type == "execution_step_verified":
                status, safe_action = "verification_pending", "complete_step"
            elif latest_type == "execution_step_completed":
                status, safe_action = "complete", "none"
            elif latest_type == "execution_step_failed":
                status, safe_action = "failed", "none"
            elif latest_type == "execution_outcome_unknown":
                status, safe_action = "outcome_unknown", "await_recovery"
            elif latest_type == "execution_cancel_requested":
                status, safe_action = "cancel_pending", "reconcile_dispatched_effect" if marker else "none"
            elif latest_type == "execution_cancel_acknowledged":
                outcome = (receipt or {}).get("outcome")
                status, safe_action = {
                    "completed": ("result_captured", "record_readback")
                    if step.get("readbackMode") == "signed" else ("verification_pending", "verify_result"),
                    "unknown": ("outcome_unknown", "await_recovery"),
                    "cancelled": ("cancelled", "none"),
                }.get(outcome, ("failed", "none"))
            assignments.append({
                "assignmentId": scope["assignmentId"], "stepId": step["stepId"],
                "workerPrincipalId": scope["workerPrincipalId"],
                "assignmentKind": scope["assignmentKind"], "status": status,
                "attemptOrdinal": attempt,
                "leasePayloadSha256": (lease or {}).get("payloadSha256"),
                "startPermitPayloadSha256": (permit or {}).get("payloadSha256"),
                "dispatchMarkerPayloadSha256": (marker or {}).get("payloadSha256"),
                "hostReceiptPayloadSha256": (receipt or {}).get("payloadSha256"),
                "resultArtifactId": result_id, "resultContentSha256": result_sha,
                "readbackPayloadSha256": (readback or {}).get("payloadSha256"),
                "recoveryCapabilityId": recovery_capability.get("capabilityId"),
                "resourceKeys": step.get("resourceKeys", []), "safeRecoveryAction": safe_action,
            })
        head = events[-1]["event"]
        body = {
            "contract": HYDRO_RECOVERY_PROJECTION, "authority": AUTHORITY,
            "ownerPrincipalId": str(owner_user_id), "programId": program["programId"],
            "executionId": execution_id, "graphId": binding["graphId"],
            "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
            "assignments": assignments, "issuedAt": _iso(current),
            "advancementAuthority": False, "effectAuthority": False,
        }
        return _signed(body, self.private_key_pem)

    def stage_hydro_worker_request(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        trusted_internal: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist only a Core-authorized content-free worker-request reference."""
        if not trusted_internal:
            raise ConstructExecutionError(
                "EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro worker request staging requires trusted Chatty service", 403,
            )
        request = _exact(request, {"request", "authorization"}, "hydroWorkerRequestStage")
        worker_request = _validate_hydro_worker_request(request["request"])
        scope = worker_request["scope"]
        owner, execution_id = str(owner_user_id), _id(execution_id, "executionId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                current = now if now is not None else self._database_now(cur)
                authorization = _verify_signed(
                    request["authorization"], fields=_HYDRO_WORKER_REQUEST_AUTHORIZATION_FIELDS,
                    contract=HYDRO_WORKER_REQUEST_AUTHORIZATION,
                    public_key_pem=self.core_public_key_pem, expected_key_id=self.core_key_id,
                    now=current, algorithm="ed25519",
                )
                row = self._program_row(cur, owner, execution_id, True)
                events = self._events(cur, owner, execution_id)
                program = row["execution_program"] if isinstance(row["execution_program"], dict) \
                    else json.loads(row["execution_program"])
                head = events[-1]["event"] if events else None
                step = next((candidate for candidate in program.get("steps", [])
                             if candidate.get("stepId") == scope.get("assignmentId")), None)
                start_permit = next((
                    (entry["event"].get("payload") or {}).get("startPermit")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_attempt_started"
                    and (((entry["event"].get("payload") or {}).get("startPermit") or {}).get("stepId")
                         == scope.get("assignmentId"))
                ), None)
                graph_binding = program.get("hydroGraphBinding") or {}
                step_scope = (step or {}).get("hydroScope") or {}
                auth_scope = {
                    "ownerPrincipalId": owner, "programId": program.get("programId"), "itemId": program.get("itemId"),
                    "executionId": execution_id, "graphId": graph_binding.get("graphId"),
                    "assignmentId": (step or {}).get("stepId"),
                    "workerPrincipalId": (step or {}).get("responsibleConstructId"),
                    "attemptOrdinal": (start_permit or {}).get("attemptOrdinal"),
                    "startPermitPayloadSha256": (start_permit or {}).get("payloadSha256"),
                    "parentExecutionHeadEventId": (head or {}).get("eventId"),
                    "parentExecutionHeadSha256": (head or {}).get("eventSha256"),
                    "preparedContextReceiptSha256": program.get("preparedContextReceiptSha256"),
                    "contextRevisionVectorSha256": scope.get("contextRevisionVectorSha256"),
                    "providerPayloadSha256": worker_request.get("providerPayloadSha256"),
                    "requestHash": worker_request.get("requestHash"),
                }
                if not step or not head or not isinstance(start_permit, dict) or any((
                    (head or {}).get("eventType") != "execution_attempt_started",
                    scope.get("ownerPrincipalId") != owner, scope.get("executionId") != execution_id,
                    scope.get("programId") != program.get("programId"), scope.get("itemId") != program.get("itemId"),
                    scope.get("graphId") != graph_binding.get("graphId"),
                    scope.get("graphPayloadSha256") != graph_binding.get("graphPayloadSha256"),
                    scope.get("assignmentId") != step.get("stepId"),
                    scope.get("assignmentHash") != step_scope.get("assignmentHash"),
                    scope.get("assignmentKind") != step_scope.get("assignmentKind"),
                    scope.get("workerPrincipalId") != step.get("responsibleConstructId"),
                    scope.get("parentResponsibleConstructId") != program.get("responsibleConstructId"),
                    scope.get("parentWorkHeadEventId") != program.get("workHeadEventId"),
                    scope.get("parentWorkHeadSha256") != program.get("workHeadSha256"),
                    scope.get("parentExecutionHeadEventId") != head.get("eventId"),
                    scope.get("parentExecutionHeadSha256") != head.get("eventSha256"),
                    scope.get("startPermitPayloadSha256") != start_permit.get("payloadSha256"),
                    scope.get("attemptOrdinal") != start_permit.get("attemptOrdinal"),
                    scope.get("preparedContextReceiptSha256") != program.get("preparedContextReceiptSha256"),
                    worker_request["providerRoute"].get("hostId") != step.get("hostId"),
                    worker_request["budgets"].get("maxOutputBytes") != step.get("maxOutputBytes"),
                    worker_request["budgets"].get("timeoutMs") != step.get("timeoutMs"),
                    any(authorization.get(key) != value for key, value in auth_scope.items()),
                    authorization.get("authority") != "chatty-core-host", authorization.get("oneUse") is not True,
                )):
                    raise ConstructExecutionError(
                        "HYDRO_WORKER_REQUEST_SCOPE_INVALID",
                        "Hydro worker request does not bind the current signed attempt", 409,
                    )
                resolution_sha = worker_request.get("synthesisInputResolutionSha256")
                if step_scope.get("assignmentKind") == "synthesis":
                    canonical_resolution = next((
                        ((entry["event"].get("payload") or {}).get("resolution") or {}).get("payloadSha256")
                        for entry in reversed(events)
                        if entry["event"].get("eventType") == "execution_hydro_synthesis_inputs_resolved"
                    ), None)
                    if resolution_sha != canonical_resolution:
                        raise ConstructExecutionError(
                            "HYDRO_WORKER_SYNTHESIS_RESOLUTION_INVALID",
                            "synthesis worker request lacks the current immutable input resolution", 409,
                        )
                reference_id = f"hydro-worker-request-reference-{_sha({
                    'executionId': execution_id,
                    'assignmentId': scope['assignmentId'],
                    'attemptOrdinal': scope['attemptOrdinal'],
                    'requestHash': worker_request['requestHash'],
                })[:40]}"
                reference_expires_at = min(
                    _time(authorization["expiresAt"], "hydroWorkerRequestAuthorization.expiresAt"),
                    _time(start_permit["expiresAt"], "startPermit.expiresAt"),
                )
                reference_body = {
                    "contract": HYDRO_WORKER_REQUEST_REFERENCE, "authority": AUTHORITY,
                    "referenceId": reference_id,
                    "ownerPrincipalId": owner, "programId": program["programId"], "itemId": program["itemId"],
                    "executionId": execution_id, "graphId": scope["graphId"],
                    "assignmentId": scope["assignmentId"], "workerPrincipalId": scope["workerPrincipalId"],
                    "attemptOrdinal": scope["attemptOrdinal"], "requestId": worker_request["requestId"],
                    "requestHash": worker_request["requestHash"],
                    "startPermitPayloadSha256": scope["startPermitPayloadSha256"],
                    "parentExecutionHeadEventId": scope["parentExecutionHeadEventId"],
                    "parentExecutionHeadSha256": scope["parentExecutionHeadSha256"],
                    "preparedContextReceiptSha256": scope["preparedContextReceiptSha256"],
                    "contextRevisionVectorSha256": scope["contextRevisionVectorSha256"],
                    "providerMessagePayloadSha256": worker_request["providerMessagePayloadSha256"],
                    "providerPayloadSha256": worker_request["providerPayloadSha256"],
                    "authorizationPayloadSha256": authorization["payloadSha256"],
                    "containsProviderMessages": False, "issuedAt": _iso(current),
                    "expiresAt": _iso(reference_expires_at),
                }
                reference = _signed(reference_body, self.private_key_pem)
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_hydro_worker_requests
                      (owner_user_id,program_id,item_id,execution_id,graph_id,assignment_id,step_id,
                       worker_principal_id,attempt_ordinal,reference_id,request_id,request_hash,
                       start_permit_payload_sha256,parent_execution_head_event_id,
                       parent_execution_head_sha256,prepared_context_receipt_sha256,
                       context_revision_vector_sha256,provider_message_payload_sha256,
                       provider_payload_sha256,synthesis_input_resolution_sha256,
                       authorization_payload_sha256,request_reference,request_reference_sha256,expires_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                      ON CONFLICT (owner_user_id,execution_id,step_id,attempt_ordinal) DO NOTHING""",
                    (owner, program["programId"], program["itemId"], execution_id, scope["graphId"],
                     scope["assignmentId"], step["stepId"], scope["workerPrincipalId"], scope["attemptOrdinal"],
                     reference_id, worker_request["requestId"], worker_request["requestHash"],
                     scope["startPermitPayloadSha256"], scope["parentExecutionHeadEventId"],
                     scope["parentExecutionHeadSha256"], scope["preparedContextReceiptSha256"],
                     scope["contextRevisionVectorSha256"], worker_request["providerMessagePayloadSha256"],
                     worker_request["providerPayloadSha256"], resolution_sha, authorization["payloadSha256"],
                     json.dumps(reference), reference["payloadSha256"], _iso(reference_expires_at)),
                )
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_capability_consumptions
                      (owner_user_id,program_id,execution_id,graph_id,assignment_id,step_id,capability_kind,
                       capability_id,capability_payload_sha256,event_id,event_sha256,consumed_at)
                      VALUES (%s,%s,%s,%s,%s,%s,'worker_request_authorization',%s,%s,%s,%s,%s)
                      ON CONFLICT (owner_user_id,capability_kind,capability_id) DO NOTHING""",
                    (owner, program["programId"], execution_id, scope["graphId"], scope["assignmentId"], step["stepId"],
                     authorization["authorizationId"], authorization["payloadSha256"], head["eventId"],
                     head["eventSha256"], _iso(current)),
                )
                cur.execute(
                    """SELECT program_id,execution_id,graph_id,assignment_id,step_id,
                              capability_payload_sha256,event_id,event_sha256
                         FROM ovvaults.construct_work_execution_capability_consumptions
                        WHERE owner_user_id=%s AND capability_kind='worker_request_authorization'
                          AND capability_id=%s""",
                    (owner, authorization["authorizationId"]),
                )
                consumed = _row(cur.fetchone())
                if not consumed or any((
                    consumed.get("program_id") != program["programId"],
                    consumed.get("execution_id") != execution_id,
                    consumed.get("graph_id") != scope["graphId"],
                    consumed.get("assignment_id") != scope["assignmentId"],
                    consumed.get("step_id") != step["stepId"],
                    consumed.get("capability_payload_sha256") != authorization["payloadSha256"],
                    consumed.get("event_id") != head["eventId"],
                    consumed.get("event_sha256") != head["eventSha256"],
                )):
                    raise ConstructExecutionError(
                        "HYDRO_WORKER_REQUEST_AUTHORIZATION_CONSUMED",
                        "worker request authorization is one-use and bound to another attempt", 409,
                    )
                cur.execute(
                    """SELECT request_hash,request_reference FROM ovvaults.construct_work_hydro_worker_requests
                        WHERE owner_user_id=%s AND execution_id=%s AND step_id=%s AND attempt_ordinal=%s""",
                    (owner, execution_id, step["stepId"], scope["attemptOrdinal"]),
                )
                stored = _row(cur.fetchone())
                stored_reference = (stored or {}).get("request_reference")
                if isinstance(stored_reference, str): stored_reference = json.loads(stored_reference)
                if not stored or stored.get("request_hash") != worker_request["requestHash"] \
                        or stored_reference != reference:
                    raise ConstructExecutionError(
                        "HYDRO_WORKER_REQUEST_CONFLICT", "worker request attempt is bound to different bytes", 409,
                    )
        return reference

    def get_hydro_worker_request_reference(
        self, owner_user_id: str, execution_id: str, step_id: str, attempt_ordinal: int, *,
        trusted_internal: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read the immutable content-free request reference for crash recovery."""
        if not trusted_internal:
            raise ConstructExecutionError(
                "EXECUTION_SERVICE_AUTH_REQUIRED", "Hydro worker request readback requires trusted Chatty service", 403,
            )
        owner, execution_id, step_id = str(owner_user_id), _id(execution_id, "executionId"), _id(step_id, "stepId")
        if not isinstance(attempt_ordinal, int) or isinstance(attempt_ordinal, bool) or not 1 <= attempt_ordinal <= 2:
            raise ConstructExecutionError("HYDRO_WORKER_ATTEMPT_INVALID", "worker attempt ordinal is invalid", 400)
        with self.connect() as conn:
            with conn.cursor() as cur:
                current = now if now is not None else self._database_now(cur)
                cur.execute(
                    """SELECT request_reference,request_reference_sha256
                         FROM ovvaults.construct_work_hydro_worker_requests
                        WHERE owner_user_id=%s AND execution_id=%s AND step_id=%s AND attempt_ordinal=%s""",
                    (owner, execution_id, step_id, attempt_ordinal),
                )
                row = _row(cur.fetchone())
        if not row:
            raise ConstructExecutionError("HYDRO_WORKER_REQUEST_NOT_FOUND", "worker request reference not found", 404)
        reference = row.get("request_reference")
        if isinstance(reference, str):
            reference = json.loads(reference)
        public_key = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        verified = _verify_signed(
            reference, fields=_HYDRO_WORKER_REQUEST_REFERENCE_FIELDS,
            contract=HYDRO_WORKER_REQUEST_REFERENCE, public_key_pem=public_key,
            expected_key_id=None, now=current,
        )
        if any((
            verified.get("ownerPrincipalId") != owner,
            verified.get("executionId") != execution_id,
            verified.get("assignmentId") != step_id,
            verified.get("attemptOrdinal") != attempt_ordinal,
            verified.get("containsProviderMessages") is not False,
            verified.get("payloadSha256") != row.get("request_reference_sha256"),
        )):
            raise ConstructExecutionError(
                "HYDRO_WORKER_REQUEST_REFERENCE_INVALID",
                "stored worker request reference failed owner-scoped signed readback", 409,
            )
        return verified

    def create(self, owner_user_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"payload", "authorization"}, "executionCreate")
        payload = _exact(request["payload"], {"program", "workExecutionIntent"}, "executionRequestedPayload")
        program, intent = _validate_program(payload["program"], payload["workExecutionIntent"])
        if program["ownerPrincipalId"] != str(owner_user_id) or program["sessionId"] != program["threadId"]:
            raise ConstructExecutionError("EXECUTION_OWNER_SCOPE_INVALID", "execution owner/session scope invalid", 403)
        payload = {"program": program, "workExecutionIntent": intent}
        current = now or datetime.now(timezone.utc)
        auth = _validate_authorization(request["authorization"], owner=str(owner_user_id), payload=payload,
                                       public_key_pem=self.core_public_key_pem, key_id=self.core_key_id, now=current)
        if auth["eventType"] != "execution_requested" or auth["expectedSequence"] != 1 \
                or auth["expectedHeadEventId"] is not None or auth["expectedHeadSha256"] is not None:
            raise ConstructExecutionError("EXECUTION_GENESIS_AUTH_INVALID", "execution genesis authorization invalid", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"execution:{owner_user_id}:{program['executionId']}",))
                cur.execute(
                    """SELECT p.program_id,p.construct_id,p.thread_id,p.session_id,p.branch_id,
                              e.resulting_goal_revision,e.event_id AS head_event_id,e.event_sha256 AS head_event_sha256
                       FROM ovvaults.construct_work_programs p
                       JOIN LATERAL (
                         SELECT resulting_goal_revision,event_id,event_sha256
                           FROM ovvaults.construct_work_events
                          WHERE owner_user_id=p.owner_user_id AND program_id=p.program_id
                          ORDER BY sequence DESC LIMIT 1
                       ) e ON TRUE
                       WHERE p.owner_user_id=%s AND p.program_id=%s FOR SHARE OF p""",
                    (owner_user_id, program["programId"]),
                )
                work = _row(cur.fetchone())
                if not work or any((
                    work.get("construct_id") != program["sourceConstructId"],
                    work.get("thread_id") != program["threadId"], work.get("session_id") != program["sessionId"],
                    work.get("branch_id") != program["branchId"], work.get("resulting_goal_revision") != program["goalRevision"],
                    work.get("head_event_id") != program["workHeadEventId"], work.get("head_event_sha256") != program["workHeadSha256"],
                )):
                    raise ConstructExecutionError("EXECUTION_WORK_SCOPE_INVALID", "execution is not bound to canonical work head", 409)
                self._assert_input_artifacts(cur, owner=str(owner_user_id), program=program, intent=intent)
                self._assert_proposal(cur, owner=str(owner_user_id), program=program, intent=intent)
                if len(program.get("steps", [])) > 1:
                    binding = program["hydroGraphBinding"]
                    cur.execute(
                        """SELECT projection FROM ovvaults.construct_work_hydro_graphs
                            WHERE owner_user_id=%s AND graph_id=%s AND execution_id=%s AND program_id=%s
                              AND graph_payload_sha256=%s AND graph_hash=%s FOR SHARE""",
                        (str(owner_user_id), binding["graphId"], program["executionId"], program["programId"],
                         binding["graphPayloadSha256"], binding["graphHash"]),
                    )
                    graph_row = _row(cur.fetchone())
                    graph_projection = (graph_row or {}).get("projection")
                    if isinstance(graph_projection, str): graph_projection = json.loads(graph_projection)
                    if not graph_projection or any((
                        graph_projection.get("sourceArgumentsSha256") != binding["sourceArgumentsSha256"],
                        [entry.get("assignmentId") for entry in graph_projection.get("assignments", [])]
                        != binding["assignmentIds"],
                        [entry.get("assignmentHash") for entry in graph_projection.get("assignments", [])]
                        != binding["assignmentHashes"],
                        graph_projection.get("maxParallel") != binding["maxParallel"],
                    )):
                        raise ConstructExecutionError(
                            "HYDRO_EXECUTION_GRAPH_NOT_STAGED",
                            "Hydro execution genesis requires the exact VVAULT-signed staged graph", 409,
                        )
                    workers = sorted({step["responsibleConstructId"] for step in program["steps"]})
                    cur.execute(
                        """SELECT construct_id FROM ovvaults.construct_incarnations
                            WHERE owner_user_id=%s AND construct_id=ANY(%s) AND lifecycle_state='active'""",
                        (owner_user_id, workers),
                    )
                    active_workers = {str((_row(value) or {}).get("construct_id")) for value in cur.fetchall()}
                    if active_workers != set(workers):
                        raise ConstructExecutionError(
                            "EXECUTION_HYDRO_WORKER_PRINCIPAL_INVALID",
                            "every Hydro worker must be an active construct for the authenticated owner", 403,
                        )
                cur.execute("SELECT execution_program,definition_hash FROM ovvaults.construct_work_executions WHERE owner_user_id=%s AND execution_id=%s", (owner_user_id, program["executionId"]))
                existing = _row(cur.fetchone())
                if existing:
                    if existing.get("definition_hash") != program["definitionHash"]:
                        raise ConstructExecutionError("EXECUTION_IDEMPOTENCY_CONFLICT", "execution identity has different definition", 409)
                    return self.projection(owner_user_id, program["executionId"])
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_executions
                      (owner_user_id,program_id,execution_id,item_id,source_construct_id,responsible_construct_id,
                       construct_incarnation_id,thread_id,session_id,branch_id,goal_revision,work_head_event_id,
                       work_head_sha256,work_state_receipt_sha256,execution_program,execution_intent,
                       execution_intent_hash,definition_hash,request_sha256,create_idempotency_key)
                      SELECT %s,%s,%s,%s,%s,%s,c.incarnation_id,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s
                      FROM ovvaults.construct_incarnations c
                      WHERE c.owner_user_id=%s AND c.construct_id=%s AND c.lifecycle_state='active'
                      RETURNING *""",
                    (owner_user_id, program["programId"], program["executionId"], program["itemId"],
                     program["sourceConstructId"], program["responsibleConstructId"], program["threadId"],
                     program["sessionId"], program["branchId"], program["goalRevision"], program["workHeadEventId"],
                     program["workHeadSha256"], program["workStateReceiptSha256"], json.dumps(program), json.dumps(intent),
                     intent["intentHash"], program["definitionHash"], _sha(request), auth["idempotencyKey"],
                     owner_user_id, program["responsibleConstructId"]),
                )
                row = _row(cur.fetchone())
                if not row:
                    raise ConstructExecutionError("EXECUTION_RESPONSIBLE_CONSTRUCT_INVALID", "responsible construct is not active for owner", 403)
                envelope = self._append(cur, owner=str(owner_user_id), row=row, payload=payload, authorization=auth,
                                        actor={"principalId": program["responsibleConstructId"], "principalType": "construct", "authority": "chatty-core"}, now=current)
                return {"status": "created", "event": envelope, "executionId": program["executionId"]}

    def append(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
               actor: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"payload", "authorization"}, "executionAppend")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), True)
                current = now if now is not None else self._database_now(cur)
                event_type = request["authorization"].get("eventType") if isinstance(request["authorization"], dict) else None
                if actor is None:
                    if event_type in {"execution_lease_acquired", "execution_lease_renewed", "execution_attempt_started",
                                      "execution_hydro_synthesis_inputs_resolved"}:
                        actor = {"principalId": "vvault", "principalType": "system", "authority": "vvault"}
                    elif event_type == "execution_effect_dispatched":
                        marker = request["payload"].get("dispatchMarker") if isinstance(request["payload"], dict) else None
                        host_id = marker.get("hostId") if isinstance(marker, dict) else None
                        actor = {"principalId": host_id, "principalType": "execution_host", "authority": "execution_host"}
                    elif event_type == "execution_cancel_acknowledged":
                        events = self._events(cur, str(owner_user_id), execution_id)
                        receipt = ((events[-1]["event"].get("payload") or {}).get("hostReceipt") if events else None)
                        host_id = receipt.get("hostId") if isinstance(receipt, dict) else None
                        actor = {"principalId": host_id, "principalType": "execution_host", "authority": "execution_host"}
                    else:
                        program = row.get("execution_program")
                        if isinstance(program, str):
                            program = json.loads(program)
                        step_id = _execution_step_id(event_type, request.get("payload") or {})
                        step = next((entry for entry in program.get("steps", [])
                                     if entry.get("stepId") == step_id), None)
                        actor = {"principalId": (step or {}).get("responsibleConstructId")
                                 or program.get("responsibleConstructId"),
                                 "principalType": "construct", "authority": "chatty-core"}
                return self._append(cur, owner=str(owner_user_id), row=row, payload=request["payload"],
                                    authorization=request["authorization"], actor=actor,
                                    now=current)

    def list(self, owner_user_id: str, *, program_id: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                if program_id is None:
                    cur.execute("""SELECT execution_id,program_id,item_id,source_construct_id,responsible_construct_id,
                                          thread_id,definition_hash,created_at
                                     FROM ovvaults.construct_work_executions WHERE owner_user_id=%s
                                     ORDER BY created_at,execution_id LIMIT 256""", (str(owner_user_id),))
                else:
                    cur.execute("""SELECT execution_id,program_id,item_id,source_construct_id,responsible_construct_id,
                                          thread_id,definition_hash,created_at
                                     FROM ovvaults.construct_work_executions WHERE owner_user_id=%s AND program_id=%s
                                     ORDER BY created_at,execution_id LIMIT 256""", (str(owner_user_id), _id(program_id, "programId")))
                entries = [_row(row) for row in cur.fetchall()]
        body = {"contract": "life-vvault-execution-list/v1", "ownerPrincipalId": str(owner_user_id),
                "programId": program_id, "executions": entries, "issuedAt": _iso(datetime.now(timezone.utc))}
        return _signed(body, self.private_key_pem)

    def recovery_queue(self, request: dict[str, Any], *, trusted_internal: bool = False,
                       now: datetime | None = None) -> dict[str, Any]:
        """Return a bounded, content-free signed wake queue for the trusted coordinator."""
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "recovery queue requires trusted Chatty service", 403)
        request = _exact(request, {"afterOwnerPrincipalId", "afterExecutionId", "limit"}, "executionRecoveryQueueRequest")
        after_owner, after_execution = request["afterOwnerPrincipalId"], request["afterExecutionId"]
        if (after_owner is None) != (after_execution is None):
            raise ConstructExecutionError("EXECUTION_RECOVERY_CURSOR_INVALID", "recovery cursor must be complete")
        if after_owner is not None:
            after_owner = _id(after_owner, "afterOwnerPrincipalId")
            after_execution = _id(after_execution, "afterExecutionId")
        limit = request["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ConstructExecutionError("EXECUTION_RECOVERY_LIMIT_INVALID", "recovery queue limit is invalid")
        params: list[Any] = []
        cursor_sql = ""
        if after_owner is not None:
            cursor_sql = "AND (x.owner_user_id::text,x.execution_id) > (%s,%s)"
            params.extend((after_owner, after_execution))
        params.append(limit + 1)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT x.owner_user_id,x.execution_id,x.program_id,x.item_id,x.source_construct_id,
                               x.responsible_construct_id,x.thread_id,x.session_id,x.branch_id,
                               x.execution_program,
                               e.sequence,e.event_id,e.event_sha256,e.event_type,e.envelope,
                               (SELECT r.payload->'hostReceipt'
                                  FROM ovvaults.construct_work_execution_events r
                                 WHERE r.owner_user_id=x.owner_user_id AND r.execution_id=x.execution_id
                                   AND r.event_type='execution_attempt_outcome_recorded'
                                 ORDER BY r.sequence DESC LIMIT 1) AS latest_host_receipt,
                               EXISTS (
                                 SELECT 1 FROM ovvaults.construct_work_execution_events c
                                  WHERE c.owner_user_id=x.owner_user_id AND c.execution_id=x.execution_id
                                    AND c.event_type='execution_cancel_requested'
                                    AND NOT EXISTS (
                                      SELECT 1 FROM ovvaults.construct_work_execution_events a
                                       WHERE a.owner_user_id=c.owner_user_id AND a.execution_id=c.execution_id
                                         AND a.event_type='execution_cancel_acknowledged' AND a.sequence > c.sequence
                                    )
                               ) AS cancel_pending
                          FROM ovvaults.construct_work_executions x
                          JOIN LATERAL (
                            SELECT sequence,event_id,event_sha256,event_type,envelope
                              FROM ovvaults.construct_work_execution_events
                             WHERE owner_user_id=x.owner_user_id AND execution_id=x.execution_id
                             ORDER BY sequence DESC LIMIT 1
                          ) e ON TRUE
                         WHERE e.event_type NOT IN ('execution_completed','execution_failed','execution_rejected')
                           {cursor_sql}
                         ORDER BY x.owner_user_id::text,x.execution_id
                         LIMIT %s""",
                    tuple(params),
                )
                rows = [_row(value) for value in cur.fetchall()]
        selected, has_more = rows[:limit], len(rows) > limit
        states = {
            "approval_capability_issued": "approval_pending",
            "execution_authorized": "authorized", "execution_lease_acquired": "authorized",
            "execution_lease_renewed": "authorized", "execution_attempt_started": "dispatch_pending",
            "execution_effect_dispatched": "attempt_in_flight",
            "execution_attempt_outcome_recorded": "result_captured",
            "execution_readback_recorded": "readback_recorded",
            "execution_recovery_selected": "authorized", "execution_step_verified": "finalization_pending",
            "execution_step_completed": "finalization_pending", "execution_cancel_requested": "cancel_pending",
            "execution_cancel_acknowledged": "finalization_pending", "execution_outcome_unknown": "outcome_unknown",
            "execution_step_failed": "finalization_pending",
        }
        entries = []
        for row in selected:
            if row.get("event_type") not in states:
                continue
            envelope = row.get("envelope")
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            payload = ((envelope or {}).get("event") or {}).get("payload") or {}
            latest_receipt = row.get("latest_host_receipt")
            if isinstance(latest_receipt, str):
                latest_receipt = json.loads(latest_receipt)
            recovery_state = states[row["event_type"]]
            if row.get("cancel_pending"):
                recovery_state = "cancel_pending"
            elif row.get("event_type") == "execution_attempt_outcome_recorded":
                receipt = payload.get("hostReceipt") or {}
                outcome = receipt.get("outcome")
                execution_program = row.get("execution_program")
                if isinstance(execution_program, str):
                    execution_program = json.loads(execution_program)
                step = next((entry for entry in (execution_program or {}).get("steps", [])
                             if entry.get("stepId") == receipt.get("stepId")), None)
                providers = (step or {}).get("providerCandidates") or []
                provider_index = providers.index(receipt.get("providerId")) if receipt.get("providerId") in providers else -1
                fallback_pending = all((
                    outcome in {"failed", "not_started"}, receipt.get("effectCommitted") == "false",
                    receipt.get("outputArtifacts") == [], receipt.get("outputSha256") is None,
                    receipt.get("providerDraftSha256") is None,
                    provider_index >= 0 and provider_index + 1 < len(providers),
                    int(receipt.get("attemptOrdinal") or 0) < int(((execution_program or {}).get("budgets") or {}).get("maxAttemptsPerStep") or 0),
                ))
                recovery_state = (
                    "provider_fallback_pending" if fallback_pending else
                    {"completed": "result_captured", "unknown": "outcome_unknown"}.get(outcome, "finalization_pending")
                )
            elif row.get("event_type") == "execution_readback_recorded":
                outcome = (payload.get("readback") or {}).get("outcome")
                recovery_state = {"committed": "readback_recorded", "not_committed": "recovery_pending",
                                  "unknown": "outcome_unknown"}.get(outcome, "outcome_unknown")
            elif row.get("event_type") == "execution_cancel_acknowledged":
                outcome = (latest_receipt or {}).get("outcome")
                recovery_state = ({"completed": "result_captured", "unknown": "outcome_unknown"}
                                  .get(outcome, "finalization_pending"))
            entries.append({
                "ownerPrincipalId": str(row["owner_user_id"]), "executionId": row["execution_id"],
                "programId": row["program_id"], "itemId": row["item_id"],
                "sourceConstructId": row["source_construct_id"],
                "responsibleConstructId": row["responsible_construct_id"],
                "threadId": row["thread_id"], "sessionId": row["session_id"], "branchId": row["branch_id"],
                "headSequence": row["sequence"], "headEventId": row["event_id"],
                "headEventSha256": row["event_sha256"], "headEventType": row["event_type"],
                "recoveryState": recovery_state,
            })
        last = selected[-1] if has_more and selected else None
        current = now or datetime.now(timezone.utc)
        return _signed({
            "contract": EXECUTION_RECOVERY_QUEUE, "entries": entries,
            "nextCursor": ({"afterOwnerPrincipalId": str(last["owner_user_id"]),
                            "afterExecutionId": last["execution_id"]} if last else None),
            "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=30)),
        }, self.private_key_pem)

    def owner_control(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"payload", "authorization"}, "executionOwnerControl")
        event_type = request["authorization"].get("eventType") if isinstance(request["authorization"], dict) else None
        if event_type not in {"execution_cancel_requested", "execution_recovery_selected", "execution_rejected"}:
            raise ConstructExecutionError("EXECUTION_OWNER_CONTROL_INVALID", "owner control event is not allowed", 403)
        actor = None if event_type == "execution_recovery_selected" else {
            "principalId": str(owner_user_id), "principalType": "human", "authority": "owner_authenticated",
        }
        return self.append(owner_user_id, execution_id, request, actor=actor, now=now)

    def _store_artifact(self, cur: Any, *, owner: str, row: dict[str, Any], artifact_type: str,
                        content_sha256: str, source_receipt: dict[str, Any], attempt_id: str | None,
                        media_type: str | None = None, content_bytes: bytes | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
        artifact_id = f"execution-artifact-{_sha({'executionId': row['execution_id'], 'type': artifact_type, 'content': content_sha256})[:40]}"
        body = {"contract": "life-vvault-execution-artifact/v1", "artifactId": artifact_id,
                "ownerPrincipalId": owner, "programId": row["program_id"], "executionId": row["execution_id"],
                "artifactType": artifact_type, "contentSha256": content_sha256,
                "canonicalLocator": f"execution:{row['execution_id']}:{artifact_id}",
                "sourceReceiptPayloadSha256": source_receipt["payloadSha256"],
                "issuedAt": _iso(now or datetime.now(timezone.utc))}
        receipt = _signed(body, self.private_key_pem)
        cur.execute(
            """INSERT INTO ovvaults.construct_work_execution_artifacts
               (owner_user_id,program_id,execution_id,artifact_id,attempt_id,artifact_type,content_sha256,
                media_type,content_bytes,byte_length,canonical_locator,privacy_class,metadata,receipt,receipt_sha256,signature_algorithm,
                signature_key_id,signature)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'owner_private',%s::jsonb,%s::jsonb,%s,%s,%s,%s)
               ON CONFLICT (owner_user_id,execution_id,artifact_type,content_sha256) DO NOTHING""",
            (owner, row["program_id"], row["execution_id"], artifact_id, attempt_id, artifact_type,
             content_sha256, media_type, content_bytes, len(content_bytes) if content_bytes is not None else None,
             body["canonicalLocator"], json.dumps({"sourceReceiptId": source_receipt.get("receiptId") or source_receipt.get("readbackId")}),
             json.dumps(receipt), receipt["payloadSha256"], receipt["algorithm"], receipt["keyId"], receipt["signature"]),
        )
        if content_bytes is None:
            return receipt
        cur.execute(
            """SELECT artifact_id,content_sha256,media_type,content_bytes,byte_length,receipt
                 FROM ovvaults.construct_work_execution_artifacts
                WHERE owner_user_id=%s AND execution_id=%s AND artifact_type=%s AND content_sha256=%s
                FOR SHARE""",
            (owner, row["execution_id"], artifact_type, content_sha256),
        )
        stored = _row(cur.fetchone())
        if not stored:
            raise ConstructExecutionError("EXECUTION_ARTIFACT_STORE_FAILED", "canonical artifact was not stored", 503)
        stored_bytes = stored.get("content_bytes")
        if isinstance(stored_bytes, memoryview):
            stored_bytes = stored_bytes.tobytes()
        if any((
            stored.get("artifact_id") != artifact_id,
            stored.get("content_sha256") != content_sha256,
            stored.get("media_type") != media_type,
            stored_bytes != content_bytes,
            stored.get("byte_length") != (len(content_bytes) if content_bytes is not None else None),
        )):
            raise ConstructExecutionError("EXECUTION_ARTIFACT_IDEMPOTENCY_CONFLICT", "canonical artifact bytes differ", 409)
        stored_receipt = stored.get("receipt")
        if isinstance(stored_receipt, str):
            stored_receipt = json.loads(stored_receipt)
        if not isinstance(stored_receipt, dict) or any((
            stored_receipt.get("artifactId") != artifact_id,
            stored_receipt.get("contentSha256") != content_sha256,
            stored_receipt.get("sourceReceiptPayloadSha256") != source_receipt.get("payloadSha256"),
        )):
            raise ConstructExecutionError("EXECUTION_ARTIFACT_RECEIPT_INVALID", "canonical artifact receipt is invalid", 409)
        return stored_receipt

    def _verified_result_artifact(self, *, program: dict[str, Any], receipt: dict[str, Any],
                                  result_artifact: Any) -> tuple[dict[str, Any], bytes]:
        artifact = _exact(result_artifact, _RESULT_ARTIFACT_FIELDS, "executionResultArtifact")
        if artifact.get("contract") != RESULT_ARTIFACT or artifact.get("mediaType") != "application/json" \
                or not isinstance(artifact.get("content"), dict):
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_INVALID", "result artifact must be canonical JSON", 400)
        content_bytes = _bytes(artifact["content"])
        content_sha256 = _sha(artifact["content"])
        if not content_bytes or len(content_bytes) > _MAX_RESULT_BYTES:
            raise ConstructExecutionError("EXECUTION_RESULT_CAPACITY_EXCEEDED", "result artifact exceeds the hard size limit", 413)
        step = next((entry for entry in program.get("steps", []) if entry.get("stepId") == receipt.get("stepId")), None)
        if not step or any((
            receipt.get("programId") != program.get("programId"),
            receipt.get("itemId") != program.get("itemId"),
            receipt.get("responsibleConstructId") != step.get("responsibleConstructId"),
            receipt.get("operation") != step.get("operation"),
            receipt.get("stepHash") != step.get("stepHash"),
            receipt.get("argumentsSha256") != step.get("argumentsSha256"),
        )):
            raise ConstructExecutionError("EXECUTION_HOST_RECEIPT_SCOPE_INVALID", "host receipt does not bind the canonical step", 403)
        if len(content_bytes) > int(step.get("maxOutputBytes") or 0):
            raise ConstructExecutionError("EXECUTION_RESULT_CAPACITY_EXCEEDED", "result artifact exceeds the canonical step budget", 413)
        descriptors = receipt.get("outputArtifacts")
        expected_id = f"execution-artifact-{_sha({'executionId': program['executionId'], 'type': 'result', 'content': content_sha256})[:40]}"
        expected_descriptor = {"artifactId": expected_id, "sha256": content_sha256, "mediaType": "application/json"}
        if any((
            artifact.get("artifactId") != expected_id,
            artifact.get("contentSha256") != content_sha256,
            receipt.get("outputSha256") != content_sha256,
            descriptors != [expected_descriptor],
            step.get("operation") == "provider.generate" and not _provider_result_matches_draft(artifact.get("content"), receipt),
            step.get("operation") == "hydro.graph.dispatch"
            and not _hydro_worker_result_matches_receipt(artifact.get("content"), receipt),
            step.get("operation") not in {"provider.generate", "hydro.graph.dispatch"}
            and receipt.get("providerDraftSha256") is not None,
        )):
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_MISMATCH", "result bytes do not match the signed host receipt", 409)
        return artifact, content_bytes

    def prepare_approval_disclosure(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a prose-free owner view independently derived from canonical argument bytes."""
        _exact(request, set(), "executionApprovalDisclosurePrepare")
        owner, execution_id = str(owner_user_id), _id(execution_id, "executionId")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, owner, execution_id, True)
                events = self._events(cur, owner, execution_id)
                current = now if now is not None else self._database_now(cur)
                head = events[-1]["event"] if events else None
                if not head or head.get("eventType") != "execution_requested":
                    raise ConstructExecutionError(
                        "EXECUTION_APPROVAL_DISCLOSURE_STATE_INVALID",
                        "approval disclosure is allowed only at the canonical approval head", 409,
                    )
                disclosure = self._canonical_approval_disclosure(cur, owner=owner, row=row)
                body = {
                    "contract": APPROVAL_DISCLOSURE_ENVELOPE, "authority": AUTHORITY,
                    "ownerPrincipalId": owner, "executionId": execution_id,
                    "programId": disclosure["programId"], "itemId": disclosure["itemId"],
                    "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
                    "approvalDisclosure": disclosure,
                    "approvalDisclosureSha256": disclosure["approvalDisclosureSha256"],
                    "canonicalArgumentsVerified": True, "containsRawArguments": False,
                    "containsCredentials": False, "containsPrivateReasoning": False,
                    "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(minutes=5)),
                }
                envelope = _signed(body, self.private_key_pem)
                disclosure_id = f"execution-approval-disclosure-{_sha({'executionId': execution_id, 'head': head['eventSha256'], 'issuedAt': body['issuedAt']})[:40]}"
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_approval_disclosures
                      (owner_user_id,program_id,execution_id,disclosure_id,approval_disclosure_sha256,
                       expected_head_event_id,expected_head_sha256,disclosure,envelope,envelope_sha256,
                       issued_at,expires_at,signature_algorithm,signature_key_id,signature)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT (owner_user_id,disclosure_id) DO NOTHING""",
                    (owner, disclosure["programId"], execution_id, disclosure_id,
                     disclosure["approvalDisclosureSha256"], head["eventId"], head["eventSha256"],
                     json.dumps(disclosure), json.dumps(envelope), envelope["payloadSha256"],
                     body["issuedAt"], body["expiresAt"], envelope["algorithm"], envelope["keyId"], envelope["signature"]),
                )
                cur.execute(
                    """SELECT disclosure,envelope FROM ovvaults.construct_work_execution_approval_disclosures
                        WHERE owner_user_id=%s AND disclosure_id=%s FOR SHARE""",
                    (owner, disclosure_id),
                )
                stored = _row(cur.fetchone())
                stored_disclosure = (stored or {}).get("disclosure")
                stored_envelope = (stored or {}).get("envelope")
                if isinstance(stored_disclosure, str): stored_disclosure = json.loads(stored_disclosure)
                if isinstance(stored_envelope, str): stored_envelope = json.loads(stored_envelope)
                if stored_disclosure != disclosure or stored_envelope != envelope:
                    raise ConstructExecutionError(
                        "EXECUTION_APPROVAL_DISCLOSURE_IDEMPOTENCY_CONFLICT",
                        "stored approval disclosure bytes differ", 409,
                    )
        return envelope

    def issue_approval(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"approval", "authorization"}, "executionApprovalRequest")
        owner_public = self.owner_public_key_pem or canonical_projection_signing.public_key_document(private_key_pem=self.private_key_pem)["publicKeyPem"]
        approval = _verify_signed(request["approval"], fields=_APPROVAL_FIELDS, contract=APPROVAL_CAPABILITY,
                                  public_key_pem=owner_public, expected_key_id=self.owner_key_id,
                                  now=now or datetime.now(timezone.utc))
        if approval.get("ownerPrincipalId") != str(owner_user_id) or approval.get("executionId") != execution_id \
                or approval.get("authority") != "chatty-owner-authorization" or approval.get("oneUse") is not True:
            raise ConstructExecutionError("EXECUTION_APPROVAL_SCOPE_INVALID", "approval scope invalid", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                approval_row = self._program_row(cur, str(owner_user_id), execution_id, True)
                events = self._events(cur, str(owner_user_id), execution_id)
                current = now if now is not None else self._database_now(cur)
                head = events[-1]["event"] if events else None
                canonical_disclosure = self._canonical_approval_disclosure(
                    cur, owner=str(owner_user_id), row=approval_row,
                )
                self._approval_disclosure_row(
                    cur, owner=str(owner_user_id), execution_id=execution_id,
                    disclosure_sha256=canonical_disclosure["approvalDisclosureSha256"],
                    expected_head_event_id=(head or {}).get("eventId"),
                    expected_head_sha256=(head or {}).get("eventSha256"), current=current,
                )
        if approval.get("workExecutionIntentHash") != approval_row.get("execution_intent_hash") \
                or approval.get("approvalDisclosureSha256") != canonical_disclosure["approvalDisclosureSha256"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_SCOPE_INVALID", "approval intent hash mismatch", 403)
        return self.append(owner_user_id, execution_id,
                           {"payload": {"approval": approval, "approvalDisclosure": canonical_disclosure},
                            "authorization": request["authorization"]},
                           actor={"principalId": str(owner_user_id), "principalType": "human", "authority": "owner_authenticated"}, now=now)

    def prepare_owner_control_attestation(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Issue immutable, one-use owner evidence for exactly one current-head control."""
        request = _exact(
            request, {"action", "reasonSha256", "idempotencyKey", "ttlSeconds"},
            "executionOwnerControlAttestationPrepare",
        )
        action = request.get("action")
        if action not in {"cancel", "reject"}:
            raise ConstructExecutionError("EXECUTION_OWNER_CONTROL_INVALID", "owner control action is invalid", 403)
        reason_sha256 = _digest(request.get("reasonSha256"), "reasonSha256")
        idempotency_key = _id(request.get("idempotencyKey"), "idempotencyKey")
        ttl = request.get("ttlSeconds")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 300:
            raise ConstructExecutionError("EXECUTION_OWNER_CONTROL_TTL_INVALID", "owner control TTL invalid", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, True)
                events = self._events(cur, str(owner_user_id), execution_id)
                current = now if now is not None else self._database_now(cur)
                program = row["execution_program"]
                if isinstance(program, str):
                    program = json.loads(program)
                head = events[-1]["event"] if events else None
                if not head or (action == "reject" and head.get("eventType") != "execution_requested") \
                        or head.get("eventType") in {"execution_completed", "execution_failed", "execution_rejected"}:
                    raise ConstructExecutionError(
                        "EXECUTION_OWNER_CONTROL_STATE_INVALID", "owner control is invalid for the current execution head", 409,
                    )
                fact_kind = "execution_cancelled" if action == "cancel" else "execution_rejected"
                evidence_id = f"execution-owner-control-{_sha({'executionId': execution_id, 'action': action, 'head': head['eventSha256'], 'reason': reason_sha256})[:40]}"
                scope = {
                    "ownerPrincipalId": str(owner_user_id), "programId": program["programId"],
                    "constructId": program["sourceConstructId"], "itemId": program["itemId"],
                    "threadId": program["threadId"], "sessionId": program["sessionId"],
                }
                attestation_payload = {
                    "ownerPrincipalId": str(owner_user_id), "executionId": execution_id,
                    "programId": program["programId"], "itemId": program["itemId"],
                    "action": action, "expectedHeadEventId": head["eventId"],
                    "expectedHeadSha256": head["eventSha256"], "reasonSha256": reason_sha256,
                    "idempotencyKey": idempotency_key, "issuedAt": _iso(current),
                    "expiresAt": _iso(current + timedelta(seconds=ttl)),
                }
                reference = {
                    "contract": "chatty-work-evidence-reference/v1", "evidenceId": evidence_id,
                    "evidenceType": "owner_attestation", "authority": "ovvaults.construct_work_execution_owner_controls",
                    "scope": scope, "payloadSha256": _sha(attestation_payload),
                    "receiptSha256": _sha({"attestation": attestation_payload, "evidenceId": evidence_id}),
                    "verifiedFactKinds": [fact_kind], "issuedAt": attestation_payload["issuedAt"],
                    "cryptographicallyVerified": True, "advancementAuthority": True,
                }
                body = {
                    "contract": OWNER_CONTROL_ATTESTATION, **attestation_payload,
                    "evidenceReference": reference, "oneUse": True,
                }
                envelope = _signed(body, self.private_key_pem)
                cur.execute(
                    """INSERT INTO ovvaults.construct_work_execution_owner_controls
                      (owner_user_id,program_id,execution_id,evidence_id,action,expected_head_event_id,
                       expected_head_sha256,reason_sha256,idempotency_key,evidence_reference,
                       evidence_reference_sha256,envelope,envelope_sha256,expires_at,
                       signature_algorithm,signature_key_id,signature)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s)
                      ON CONFLICT (owner_user_id,execution_id,idempotency_key) DO NOTHING""",
                    (str(owner_user_id), program["programId"], execution_id, evidence_id, action,
                     head["eventId"], head["eventSha256"], reason_sha256, idempotency_key,
                     json.dumps(reference), _sha(reference), json.dumps(envelope), envelope["payloadSha256"],
                     attestation_payload["expiresAt"], envelope["algorithm"], envelope["keyId"], envelope["signature"]),
                )
                cur.execute(
                    """SELECT action,expected_head_event_id,expected_head_sha256,reason_sha256,
                              evidence_reference,envelope,expires_at
                         FROM ovvaults.construct_work_execution_owner_controls
                        WHERE owner_user_id=%s AND execution_id=%s AND idempotency_key=%s FOR SHARE""",
                    (str(owner_user_id), execution_id, idempotency_key),
                )
                stored = _row(cur.fetchone())
                stored_reference = (stored or {}).get("evidence_reference")
                stored_envelope = (stored or {}).get("envelope")
                if isinstance(stored_reference, str):
                    stored_reference = json.loads(stored_reference)
                if isinstance(stored_envelope, str):
                    stored_envelope = json.loads(stored_envelope)
                if not stored or any((
                    stored.get("action") != action,
                    stored.get("expected_head_event_id") != head["eventId"],
                    stored.get("expected_head_sha256") != head["eventSha256"],
                    stored.get("reason_sha256") != reason_sha256,
                    stored_reference != reference,
                    stored_envelope != envelope,
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_OWNER_CONTROL_IDEMPOTENCY_CONFLICT", "owner control attestation bytes differ", 409,
                    )
        return envelope

    def prepare_approval(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"approvalDisclosureSha256", "approvedStepIds", "riskCeiling", "maxAttemptsPerStep", "ttlSeconds"}, "executionApprovalPrepare")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, True)
                events = self._events(cur, str(owner_user_id), execution_id)
                current = now if now is not None else self._database_now(cur)
                head = events[-1]["event"] if events else None
                if not head or head.get("eventType") != "execution_requested":
                    raise ConstructExecutionError(
                        "EXECUTION_APPROVAL_STATE_INVALID", "approval is not valid at the current execution head", 409,
                    )
                canonical_disclosure = self._canonical_approval_disclosure(
                    cur, owner=str(owner_user_id), row=row,
                )
                disclosure_row = self._approval_disclosure_row(
                    cur, owner=str(owner_user_id), execution_id=execution_id,
                    disclosure_sha256=_digest(request["approvalDisclosureSha256"], "approvalDisclosureSha256"),
                    expected_head_event_id=head["eventId"], expected_head_sha256=head["eventSha256"],
                    current=current,
                )
        program = row["execution_program"] if isinstance(row["execution_program"], dict) else json.loads(row["execution_program"])
        step_ids = [step["stepId"] for step in program["steps"]]
        if request["approvedStepIds"] != step_ids or request["riskCeiling"] not in {"low", "moderate", "high", "critical"}:
            raise ConstructExecutionError("EXECUTION_APPROVAL_SCOPE_INVALID", "approval must cover the exact canonical step list", 403)
        attempts, ttl = request["maxAttemptsPerStep"], request["ttlSeconds"]
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= program["budgets"]["maxAttemptsPerStep"]:
            raise ConstructExecutionError("EXECUTION_APPROVAL_ATTEMPTS_INVALID", "approval attempts exceed canonical budget", 403)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 300:
            raise ConstructExecutionError("EXECUTION_APPROVAL_TTL_INVALID", "approval TTL invalid")
        if request["approvalDisclosureSha256"] != canonical_disclosure["approvalDisclosureSha256"] \
                or disclosure_row["disclosure"] != canonical_disclosure \
                or current + timedelta(seconds=ttl) > _time(disclosure_row["expires_at"], "approvalDisclosure.expiresAt"):
            raise ConstructExecutionError(
                "EXECUTION_APPROVAL_DISCLOSURE_MISMATCH",
                "approval disclosure is stale or differs from canonical arguments", 409,
            )
        body = {"contract": APPROVAL_CAPABILITY,
                "approvalId": f"execution-approval-{_sha({'executionId': execution_id, 'owner': str(owner_user_id), 'issuedAt': _iso(current)})[:40]}",
                "authority": "chatty-owner-authorization", "ownerPrincipalId": str(owner_user_id),
                "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
                "definitionHash": program["definitionHash"], "workExecutionIntentHash": row["execution_intent_hash"],
                "approvalDisclosureSha256": canonical_disclosure["approvalDisclosureSha256"],
                "approvedStepIds": step_ids,
                "riskCeiling": request["riskCeiling"], "maxAttemptsPerStep": attempts, "oneUse": True,
                "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=ttl))}
        return _signed(body, self.private_key_pem)

    def prepare_recovery_capability(self, owner_user_id: str, execution_id: str,
                                    request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(
            request,
            {"stepId", "attemptOrdinal", "selection", "readbackPayloadSha256", "ttlSeconds"},
            "executionRecoveryCapabilityPrepare",
        )
        current = now or datetime.now(timezone.utc)
        if request.get("selection") != "retry_not_committed":
            raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_SELECTION_INVALID", "only proven not-committed retry is capability eligible", 403)
        ttl = request.get("ttlSeconds")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 300:
            raise ConstructExecutionError("EXECUTION_RECOVERY_CAPABILITY_TTL_INVALID", "recovery capability TTL invalid")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, False)
                events = self._events(cur, str(owner_user_id), execution_id)
        program = row["execution_program"]
        if isinstance(program, str):
            program = json.loads(program)
        head = events[-1]["event"] if events else None
        approval_disclosure_sha256 = next((
            ((entry["event"].get("payload") or {}).get("approval") or {}).get("approvalDisclosureSha256")
            for entry in reversed(events)
            if entry["event"].get("eventType") == "approval_capability_issued"
        ), None)
        _digest(approval_disclosure_sha256, "approvalDisclosureSha256")
        readback = ((head or {}).get("payload") or {}).get("readback")
        if not isinstance(readback, dict) or any((
            (head or {}).get("eventType") != "execution_readback_recorded",
            (readback or {}).get("outcome") != "not_committed",
            (readback or {}).get("payloadSha256") != request.get("readbackPayloadSha256"),
            (readback or {}).get("stepId") != request.get("stepId"),
            (readback or {}).get("attemptOrdinal") != request.get("attemptOrdinal"),
        )):
            raise ConstructExecutionError("EXECUTION_RECOVERY_NOT_PROVEN", "recovery capability requires current authoritative not-committed readback", 409)
        body = {
            "contract": RECOVERY_CAPABILITY,
            "capabilityId": f"execution-recovery-capability-{_sha({'executionId': execution_id, 'head': head['eventSha256'], 'readback': readback['payloadSha256']})[:40]}",
            "authority": "chatty-owner-authorization", "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
            "stepId": request["stepId"], "attemptOrdinal": request["attemptOrdinal"],
            "selection": "retry_not_committed", "definitionHash": program["definitionHash"],
            "workExecutionIntentHash": row["execution_intent_hash"],
            "approvalDisclosureSha256": approval_disclosure_sha256,
            "readbackPayloadSha256": request["readbackPayloadSha256"],
            "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
            "oneUse": True, "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=ttl)),
        }
        step = next((entry for entry in program.get("steps", []) if entry.get("stepId") == request["stepId"]), None)
        if (step or {}).get("hydroScope"):
            if readback.get("hydroScope") != step["hydroScope"]:
                raise ConstructExecutionError("HYDRO_EXECUTION_SIGNED_SCOPE_INVALID", "recovery readback Hydro scope mismatch", 409)
            body["hydroScope"] = step["hydroScope"]
        return _signed(body, self.private_key_pem)

    def prepare_provider_fallback_capability(
        self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
        trusted_internal: bool = False, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Issue a one-use fallback only for a canonical no-output provider failure."""
        if not trusted_internal:
            raise ConstructExecutionError(
                "EXECUTION_SERVICE_AUTH_REQUIRED", "provider fallback capability requires trusted Chatty service", 403,
            )
        request = _exact(
            request,
            {"stepId", "attemptOrdinal", "hostReceiptPayloadSha256", "nextProviderId", "ttlSeconds"},
            "executionProviderFallbackCapabilityPrepare",
        )
        step_id = _id(request["stepId"], "stepId")
        next_provider_id = _id(request["nextProviderId"], "nextProviderId")
        receipt_sha = _digest(request["hostReceiptPayloadSha256"], "hostReceiptPayloadSha256")
        attempt_ordinal = request["attemptOrdinal"]
        ttl = request["ttlSeconds"]
        if isinstance(attempt_ordinal, bool) or not isinstance(attempt_ordinal, int) or attempt_ordinal < 1:
            raise ConstructExecutionError("EXECUTION_PROVIDER_FALLBACK_ATTEMPT_INVALID", "provider fallback attempt is invalid", 403)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 300:
            raise ConstructExecutionError("EXECUTION_PROVIDER_FALLBACK_TTL_INVALID", "provider fallback TTL invalid", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, True)
                events = self._events(cur, str(owner_user_id), execution_id)
                cur.execute(
                    """SELECT 1 FROM ovvaults.construct_work_execution_artifacts
                         WHERE owner_user_id=%s AND execution_id=%s AND artifact_type='result' LIMIT 1""",
                    (str(owner_user_id), execution_id),
                )
                immutable_result_exists = cur.fetchone() is not None
                current = now if now is not None else self._database_now(cur)
        program = row["execution_program"]
        if isinstance(program, str):
            program = json.loads(program)
        head = events[-1]["event"] if events else None
        receipt = ((head or {}).get("payload") or {}).get("hostReceipt")
        approval_disclosure_sha256 = next((
            ((entry["event"].get("payload") or {}).get("approval") or {}).get("approvalDisclosureSha256")
            for entry in reversed(events)
            if entry["event"].get("eventType") == "approval_capability_issued"
        ), None)
        _digest(approval_disclosure_sha256, "approvalDisclosureSha256")
        step = next((entry for entry in program.get("steps", []) if entry.get("stepId") == step_id), None)
        if not isinstance(receipt, dict) or not step:
            raise ConstructExecutionError(
                "EXECUTION_PROVIDER_FALLBACK_NOT_PROVEN", "provider fallback requires a current host outcome", 409,
            )
        host_key = self._host_key(receipt.get("hostId"))
        if not host_key:
            raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
        receipt = _verify_signed(
            receipt, fields=_host_signed_fields(receipt), contract=HOST_RECEIPT,
            public_key_pem=host_key[0], expected_key_id=host_key[1], now=None,
        )
        providers = step.get("providerCandidates") or []
        current_provider = receipt.get("providerId")
        next_index = providers.index(current_provider) + 1 if current_provider in providers else -1
        if any((
            (head or {}).get("eventType") != "execution_attempt_outcome_recorded",
            receipt.get("payloadSha256") != receipt_sha,
            receipt.get("ownerPrincipalId") != str(owner_user_id),
            receipt.get("executionId") != execution_id,
            receipt.get("programId") != program.get("programId"),
            receipt.get("itemId") != program.get("itemId"),
            receipt.get("stepId") != step_id,
            receipt.get("attemptOrdinal") != attempt_ordinal,
            receipt.get("outcome") not in {"failed", "not_started"},
            receipt.get("effectCommitted") != "false",
            receipt.get("outputArtifacts") != [],
            receipt.get("outputSha256") is not None,
            receipt.get("providerDraftSha256") is not None,
            immutable_result_exists,
            next_index <= 0 or next_index >= len(providers),
            next_index > 0 and providers[next_index] != next_provider_id,
            attempt_ordinal >= int((program.get("budgets") or {}).get("maxAttemptsPerStep") or 0),
        )):
            raise ConstructExecutionError(
                "EXECUTION_PROVIDER_FALLBACK_NOT_PROVEN",
                "provider fallback requires an approved next provider and no output or committed effect", 409,
            )
        body = {
            "contract": PROVIDER_FALLBACK_CAPABILITY,
            "capabilityId": f"execution-provider-fallback-{_sha({'executionId': execution_id, 'head': head['eventSha256'], 'nextProviderId': next_provider_id})[:40]}",
            "authority": AUTHORITY, "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
            "stepId": step_id, "attemptOrdinal": attempt_ordinal, "selection": "provider_fallback",
            "definitionHash": program["definitionHash"], "workExecutionIntentHash": row["execution_intent_hash"],
            "approvalDisclosureSha256": approval_disclosure_sha256,
            "hostReceiptPayloadSha256": receipt_sha, "nextProviderId": next_provider_id,
            "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
            "oneUse": True, "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=ttl)),
        }
        if step.get("hydroScope"):
            if receipt.get("hydroScope") != step["hydroScope"]:
                raise ConstructExecutionError("HYDRO_EXECUTION_SIGNED_SCOPE_INVALID", "fallback receipt Hydro scope mismatch", 409)
            body["hydroScope"] = step["hydroScope"]
        return _signed(body, self.private_key_pem)

    def prepare_lease(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        fields = {"stepId", "attemptOrdinal", "providerId", "hostId", "responsibleConstructId", "resourceKeys", "idempotencyKey", "renewalOrdinal", "ttlSeconds"}
        request = _exact(request, fields, "executionLeaseRequest")
        current = now or datetime.now(timezone.utc)
        ttl = request["ttlSeconds"]
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 5 <= ttl <= 120:
            raise ConstructExecutionError("EXECUTION_LEASE_TTL_INVALID", "lease TTL invalid")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, False)
                events = self._events(cur, str(owner_user_id), execution_id)
        program = row["execution_program"] if isinstance(row["execution_program"], dict) else json.loads(row["execution_program"])
        step = next((s for s in program["steps"] if s["stepId"] == request["stepId"]), None)
        if not step or request["hostId"] != step["hostId"] or request["responsibleConstructId"] != step["responsibleConstructId"] \
                or request["resourceKeys"] != step["resourceKeys"]:
            raise ConstructExecutionError("EXECUTION_LEASE_SCOPE_INVALID", "lease does not match canonical step", 403)
        completed_steps = {
            ((entry["event"].get("payload") or {}).get("stepId"))
            for entry in events if entry["event"].get("eventType") == "execution_step_completed"
        }
        if any(dependency not in completed_steps for dependency in step.get("dependencyStepIds", [])):
            raise ConstructExecutionError(
                "HYDRO_EXECUTION_DEPENDENCY_NOT_COMPLETE",
                "Hydro assignment cannot lease before its canonical dependencies complete", 409,
            )
        if step.get("kind") == "hydro_synthesis" and not any(
            entry["event"].get("eventType") == "execution_hydro_synthesis_inputs_resolved"
            and ((entry["event"].get("payload") or {}).get("resolution") or {}).get("synthesisStepId") == step["stepId"]
            for entry in events
        ):
            raise ConstructExecutionError(
                "HYDRO_SYNTHESIS_INPUT_RESOLUTION_REQUIRED",
                "Hydro synthesis cannot lease before immutable dependency results are resolved", 409,
            )
        head = events[-1]["event"]
        body = {"contract": EXECUTION_LEASE, "leaseId": f"execution-lease-{_sha(request)[:40]}", "authority": AUTHORITY,
                "ownerPrincipalId": str(owner_user_id), "executionId": execution_id, "programId": program["programId"],
                "itemId": program["itemId"], "stepId": request["stepId"], "attemptOrdinal": request["attemptOrdinal"],
                "providerId": request["providerId"], "hostId": request["hostId"], "responsibleConstructId": request["responsibleConstructId"],
                "resourceKeys": request["resourceKeys"], "expectedHeadEventId": head["eventId"], "expectedHeadSha256": head["eventSha256"],
                "idempotencyKey": request["idempotencyKey"], "renewalOrdinal": request["renewalOrdinal"],
                "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=ttl))}
        if step.get("hydroScope"):
            body["hydroScope"] = step["hydroScope"]
        return _signed(body, self.private_key_pem)

    def prepare_start_permit(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"lease", "approvalPayloadSha256", "idempotencyKey", "ttlSeconds"}, "executionStartRequest")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, False)
                events = self._events(cur, str(owner_user_id), execution_id)
                current = now if now is not None else self._database_now(cur)
        lease = _verify_signed(request["lease"], fields=_hydro_signed_fields(request["lease"], _LEASE_FIELDS), contract=EXECUTION_LEASE,
                               public_key_pem=canonical_projection_signing.public_key_document(private_key_pem=self.private_key_pem)["publicKeyPem"],
                               expected_key_id=None, now=current)
        program = row["execution_program"] if isinstance(row["execution_program"], dict) else json.loads(row["execution_program"])
        head = events[-1]["event"]
        current_lease = next((
            (entry["event"].get("payload") or {}).get("lease")
            for entry in reversed(events)
            if entry["event"].get("eventType") in {"execution_lease_acquired", "execution_lease_renewed"}
            and ((entry["event"].get("payload") or {}).get("lease") or {}).get("stepId") == lease.get("stepId")
        ), None)
        if lease["ownerPrincipalId"] != str(owner_user_id) or lease["executionId"] != execution_id \
                or not isinstance(current_lease, dict) or current_lease.get("payloadSha256") != lease["payloadSha256"]:
            raise ConstructExecutionError("EXECUTION_STALE_LEASE", "lease is not the latest committed lease event", 409)
        step = next(s for s in program["steps"] if s["stepId"] == lease["stepId"])
        ttl = request["ttlSeconds"]
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 5 <= ttl <= 60:
            raise ConstructExecutionError("EXECUTION_START_TTL_INVALID", "start permit TTL invalid")
        body = {"contract": START_PERMIT, "permitId": f"execution-permit-{_sha(request)[:40]}", "authority": AUTHORITY,
                "ownerPrincipalId": str(owner_user_id), "executionId": execution_id, "programId": program["programId"],
                "itemId": program["itemId"], "stepId": step["stepId"], "attemptOrdinal": lease["attemptOrdinal"],
                "providerId": lease["providerId"], "hostId": lease["hostId"], "responsibleConstructId": step["responsibleConstructId"],
                "leasePayloadSha256": lease["payloadSha256"], "approvalPayloadSha256": _digest(request["approvalPayloadSha256"], "approvalPayloadSha256"),
                "stepHash": step["stepHash"], "argumentsSha256": step["argumentsSha256"], "idempotencyKey": request["idempotencyKey"],
                "preStartHeadEventId": head["eventId"], "preStartHeadSha256": head["eventSha256"],
                "issuedAt": _iso(current), "expiresAt": _iso(current + timedelta(seconds=ttl))}
        if step.get("hydroScope"):
            if lease.get("hydroScope") != step["hydroScope"]:
                raise ConstructExecutionError("HYDRO_EXECUTION_SIGNED_SCOPE_INVALID", "lease Hydro scope mismatch", 409)
            body["hydroScope"] = step["hydroScope"]
        return _signed(body, self.private_key_pem)

    def preflight_inspect(self, owner_user_id: str, execution_id: str) -> dict[str, Any]:
        """Return signed, content-free execution scope/readiness evidence."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                events = self._events(cur, str(owner_user_id), execution_id)
                program = row["execution_program"]
                intent = row["execution_intent"]
                if isinstance(program, str):
                    program = json.loads(program)
                if isinstance(intent, str):
                    intent = json.loads(intent)
                argument = intent["argumentsArtifact"]
                cur.execute(
                    """SELECT program_id,media_type,content_sha256
                         FROM ovvaults.construct_work_execution_inputs
                        WHERE owner_user_id=%s AND artifact_id=%s""",
                    (str(owner_user_id), argument["artifactId"]),
                )
                artifact = _row(cur.fetchone())
        head = events[-1]["event"] if events else None
        step = program["steps"][0]
        key_info = self._host_key(step["hostId"])
        argument_available = bool(artifact and artifact.get("program_id") == program["programId"]
                                  and artifact.get("media_type") == argument["mediaType"]
                                  and artifact.get("content_sha256") == argument["sha256"])
        instant = datetime.now(timezone.utc)
        body = {
            "contract": EXECUTION_PREFLIGHT, "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
            "sourceConstructId": program["sourceConstructId"], "responsibleConstructId": program["responsibleConstructId"],
            "threadId": program["threadId"], "sessionId": program["sessionId"], "branchId": program["branchId"],
            "definitionHash": program["definitionHash"], "workExecutionIntentHash": intent["intentHash"],
            "proposalArtifactId": intent["proposalArtifactId"], "proposalPayloadSha256": intent["proposalPayloadSha256"],
            "argumentArtifact": argument, "canonicalArgumentAvailable": argument_available,
            "hostId": step["hostId"], "hostKeyId": key_info[1] if key_info else None,
            "hostKeyConfigured": key_info is not None,
            "head": ({"eventId": head["eventId"], "eventSha256": head["eventSha256"],
                      "sequence": head["sequence"], "eventType": head["eventType"]} if head else None),
            "eventCount": len(events), "ready": bool(argument_available and key_info),
            "containsArguments": False, "containsCredentials": False, "containsPrivateReasoning": False,
            "issuedAt": _iso(instant), "expiresAt": _iso(instant + timedelta(seconds=60)),
        }
        return _signed(body, self.private_key_pem)

    def sign_context_projection(self, owner_user_id: str, execution_id: str,
                                request: dict[str, Any], *, trusted_internal: bool = False) -> dict[str, Any]:
        if not trusted_internal:
            raise ConstructExecutionError("EXECUTION_SERVICE_AUTH_REQUIRED", "context countersign requires trusted Chatty service", 403)
        request = _exact(request, {"contextProjection"}, "executionContextRequest")
        context = _exact(request["contextProjection"], _EXECUTION_CONTEXT_FIELDS, "executionContextProjection")
        if any((
            context.get("contract") != EXECUTION_CONTEXT_PROJECTION,
            context.get("derivationAuthority") != "chatty-core",
            context.get("persistenceAuthority") != AUTHORITY,
            context.get("contextPolicyVersion") != "chatty-context-sea-policy/v1.2",
            context.get("containsArguments") is not False,
            context.get("containsCredentials") is not False,
            context.get("containsPrivateReasoning") is not False,
            context.get("projectionSha256") != _sha({key: value for key, value in context.items() if key != "projectionSha256"}),
        )):
            raise ConstructExecutionError("EXECUTION_CONTEXT_INVALID", "execution context authority, minimization, or hash is invalid", 409)
        steps = context.get("steps")
        commands = context.get("nextCommands")
        if not isinstance(steps, list) or len(steps) > 32 or not isinstance(commands, list) or len(commands) > 32:
            raise ConstructExecutionError("EXECUTION_CONTEXT_CAPACITY_INVALID", "execution context exceeds bounded projection", 409)
        step_fields = {
            "stepId", "actionId", "ordinal", "operation", "required", "responsibleConstructId",
            "dependencyStepIds", "resourceKeys", "risk", "status", "attemptCount",
            "immutableResultSha256", "evidenceId",
        }
        for step in steps:
            _exact(step, step_fields, "executionContextStep")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                events = self._events(cur, str(owner_user_id), execution_id)
        program = row["execution_program"]
        intent = row["execution_intent"]
        if isinstance(program, str):
            program = json.loads(program)
        if isinstance(intent, str):
            intent = json.loads(intent)
        head = events[-1]["event"] if events else None
        receipt = _exact(context.get("executionStateReceipt"), _EXECUTION_STATE_RECEIPT_FIELDS, "executionStateReceipt")
        if receipt.get("receiptSha256") != _sha({key: value for key, value in receipt.items() if key != "receiptSha256"}):
            raise ConstructExecutionError("EXECUTION_CONTEXT_STATE_HASH_INVALID", "execution state receipt hash is invalid", 409)
        expected = {
            "ownerPrincipalId": str(owner_user_id), "executionId": execution_id,
            "programId": program["programId"], "itemId": program["itemId"],
            "sourceConstructId": program["sourceConstructId"], "responsibleConstructId": program["responsibleConstructId"],
            "threadId": program["threadId"], "sessionId": program["sessionId"], "branchId": program["branchId"],
            "goalRevision": program["goalRevision"], "definitionHash": program["definitionHash"],
            "workExecutionIntentHash": intent["intentHash"],
            "preparedContextReceiptSha256": program["preparedContextReceiptSha256"],
        }
        if any(context.get(key) != value for key, value in expected.items()) or any((
            receipt.get("contract") != "chatty-execution-state-receipt/v1",
            receipt.get("executionId") != execution_id,
            receipt.get("definitionHash") != program["definitionHash"],
            receipt.get("status") != context.get("status"),
            receipt.get("sequence") != (head or {}).get("sequence"),
            receipt.get("headEventId") != (head or {}).get("eventId"),
            receipt.get("headEventSha256") != (head or {}).get("eventSha256"),
        )):
            raise ConstructExecutionError("EXECUTION_CONTEXT_SCOPE_INVALID", "execution context does not bind canonical execution head", 409)
        signature = canonical_projection_signing.sign_canonical_payload(
            context, private_key_pem=self.private_key_pem
        )
        return {**context, **signature}

    def prepare_vvault_readback(self, owner_user_id: str, execution_id: str,
                                request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """Issue readback only where VVAULT owns every asserted artifact byte/hash."""
        request = _exact(request, {"stepId", "attemptOrdinal", "idempotencyKey"}, "executionVvaultReadbackPrepare")
        current = now or datetime.now(timezone.utc)
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, False)
                events = self._events(cur, str(owner_user_id), execution_id)
                program = row["execution_program"]
                if isinstance(program, str):
                    program = json.loads(program)
                step = next((entry for entry in program["steps"] if entry.get("stepId") == request["stepId"]), None)
                head = events[-1]["event"] if events else None
                host_receipt = next((
                    (entry["event"].get("payload") or {}).get("hostReceipt")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                    and ((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("stepId") == request["stepId"]
                ), None)
                head_payload = (head or {}).get("payload") or {}
                head_binds_receipt = ((head or {}).get("eventType") == "execution_attempt_outcome_recorded"
                                      and head_payload.get("hostReceipt") == host_receipt) or (
                    (head or {}).get("eventType") == "execution_cancel_acknowledged"
                    and head_payload.get("stepId") == request["stepId"]
                    and head_payload.get("hostReceiptPayloadSha256") == (host_receipt or {}).get("payloadSha256")
                )
                if not step or step.get("operation") != "artifact.readback.verify":
                    raise ConstructExecutionError(
                        "EXECUTION_READBACK_AUTHORITY_UNSUPPORTED",
                        "workspace, provider, and external effects require their registered host authority", 409,
                    )
                if not isinstance(host_receipt, dict) or any((
                    not head_binds_receipt,
                    host_receipt.get("outcome") != "completed",
                    host_receipt.get("effectCommitted") != "not_applicable",
                    host_receipt.get("stepId") != step["stepId"],
                    host_receipt.get("attemptOrdinal") != request["attemptOrdinal"],
                    host_receipt.get("operation") != step["operation"],
                )):
                    raise ConstructExecutionError("EXECUTION_READBACK_SOURCE_INVALID", "canonical completed artifact readback result is required", 409)
                _assert_hydro_document_scope(program, host_receipt)
                host_key = self._host_key(host_receipt.get("hostId"))
                if not host_key:
                    raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
                _verify_signed(host_receipt, fields=_host_signed_fields(host_receipt), contract=HOST_RECEIPT,
                               public_key_pem=host_key[0], expected_key_id=host_key[1])
                references = host_receipt.get("outputArtifacts") or []
                if not references:
                    raise ConstructExecutionError("EXECUTION_READBACK_ARTIFACT_REQUIRED", "VVAULT readback requires canonical output artifact evidence", 409)
                verified_ids = []
                for reference in references:
                    _exact(reference, {"artifactId", "sha256", "mediaType"}, "executionReadbackArtifact")
                    cur.execute(
                        """SELECT artifact_id,content_sha256
                             FROM ovvaults.construct_work_execution_artifacts
                            WHERE owner_user_id=%s AND execution_id=%s AND artifact_id=%s
                           UNION ALL
                           SELECT artifact_id,content_sha256
                             FROM ovvaults.construct_work_execution_inputs
                            WHERE owner_user_id=%s AND program_id=%s AND artifact_id=%s""",
                        (str(owner_user_id), execution_id, reference["artifactId"],
                         str(owner_user_id), program["programId"], reference["artifactId"]),
                    )
                    matches = [_row(entry) for entry in cur.fetchall()]
                    if len(matches) != 1 or matches[0].get("content_sha256") != reference["sha256"]:
                        raise ConstructExecutionError("EXECUTION_READBACK_ARTIFACT_NOT_VERIFIED", "output artifact is not canonical VVAULT evidence", 409)
                    verified_ids.append(reference["artifactId"])
        body = {
            "contract": EXECUTION_READBACK,
            "readbackId": f"execution-readback-{_sha({'executionId': execution_id, 'stepId': step['stepId'], 'attempt': request['attemptOrdinal'], 'hostReceipt': host_receipt['payloadSha256']})[:40]}",
            "authority": AUTHORITY, "ownerPrincipalId": str(owner_user_id), "executionId": execution_id,
            "programId": program["programId"], "itemId": program["itemId"], "stepId": step["stepId"],
            "attemptOrdinal": request["attemptOrdinal"], "hostId": step["hostId"],
            "idempotencyKey": _id(request["idempotencyKey"], "idempotencyKey"), "outcome": "committed",
            "expectedResultSha256": host_receipt["outputSha256"],
            "observedResultSha256": host_receipt["outputSha256"],
            "evidenceArtifactIds": verified_ids, "observedAt": _iso(current),
        }
        if step.get("hydroScope"):
            body["hydroScope"] = step["hydroScope"]
        return _signed(body, self.private_key_pem)

    def resolve_started_arguments(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *,
                                  now: datetime | None = None) -> dict[str, Any]:
        """Return only the canonical arguments for an already-started fenced attempt.

        The trusted transport credential is not effect authority.  The VVAULT-signed
        start permit must still be live, must be the exact permit durably consumed by
        the current execution head, and must bind the canonical argument artifact.
        """
        request = _exact(request, {"startPermit"}, "executionArgumentResolve")
        current = now or datetime.now(timezone.utc)
        vvault_public = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        permit = _verify_signed(
            request["startPermit"], fields=_hydro_signed_fields(request["startPermit"], _PERMIT_FIELDS), contract=START_PERMIT,
            public_key_pem=vvault_public, expected_key_id=None, now=current,
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                events = self._events(cur, str(owner_user_id), execution_id)
                program = row["execution_program"]
                if isinstance(program, str):
                    program = json.loads(program)
                step = next((entry for entry in program.get("steps", [])
                             if entry.get("stepId") == permit.get("stepId")), None)
                permit_event = next((
                    entry["event"] for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_attempt_started"
                    and ((entry["event"].get("payload") or {}).get("startPermit") or {}).get("stepId") == permit.get("stepId")
                    and ((entry["event"].get("payload") or {}).get("startPermit") or {}).get("attemptOrdinal") == permit.get("attemptOrdinal")
                ), None)
                consumed = ((permit_event or {}).get("payload") or {}).get("startPermit")
                later_for_step = [entry["event"] for entry in events
                                  if (permit_event and entry["event"].get("sequence", 0) > permit_event.get("sequence", 0)
                                      and _execution_step_id(entry["event"].get("eventType"), entry["event"].get("payload") or {})
                                      == permit.get("stepId"))]
                if not step or any((
                    permit.get("ownerPrincipalId") != str(owner_user_id),
                    permit.get("executionId") != execution_id,
                    permit.get("programId") != program.get("programId"),
                    permit.get("itemId") != program.get("itemId"),
                    permit.get("responsibleConstructId") != step.get("responsibleConstructId"),
                    permit.get("hostId") != step.get("hostId"),
                    permit.get("stepHash") != step.get("stepHash"),
                    permit.get("argumentsSha256") != step.get("argumentsSha256"),
                    not isinstance(permit_event, dict),
                    bool(later_for_step),
                    not isinstance(consumed, dict),
                    _sha(consumed) != _sha(permit),
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_ARGUMENT_ACCESS_NOT_AUTHORIZED",
                        "arguments require the exact current committed start permit", 409,
                    )
                reference = step.get("argumentsArtifact") or {}
                _exact(reference, {"artifactId", "sha256", "mediaType"}, "executionArgumentsArtifact")
                cur.execute(
                    """SELECT program_id,media_type,content,content_sha256
                         FROM ovvaults.construct_work_execution_inputs
                        WHERE owner_user_id=%s AND artifact_id=%s FOR SHARE""",
                    (str(owner_user_id), reference["artifactId"]),
                )
                stored = _row(cur.fetchone())
        content = stored.get("content") if stored else None
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, dict) or not stored or any((
            stored.get("program_id") != program["programId"],
            stored.get("media_type") != "application/json",
            stored.get("content_sha256") != reference["sha256"],
            reference.get("mediaType") != "application/json",
            reference.get("sha256") != permit["argumentsSha256"],
            _sha(content) != permit["argumentsSha256"],
        )):
            raise ConstructExecutionError(
                "EXECUTION_ARGUMENT_ARTIFACT_NOT_VERIFIED",
                "canonical arguments do not match the started attempt", 409,
            )
        body = {
            "contract": ARGUMENT_RESOLUTION,
            "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id,
            "programId": program["programId"],
            "itemId": program["itemId"],
            "stepId": step["stepId"],
            "attemptOrdinal": permit["attemptOrdinal"],
            "hostId": permit["hostId"],
            "permitPayloadSha256": permit["payloadSha256"],
            "artifactId": reference["artifactId"],
            "mediaType": reference["mediaType"],
            "content": content,
            "contentSha256": permit["argumentsSha256"],
            "resolvedAt": _iso(current),
            "expiresAt": permit["expiresAt"],
        }
        return _signed(body, self.private_key_pem)

    def record_host_receipt(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"hostReceipt", "resultArtifact", "authorization"}, "executionResultRequest")
        host_id = request["hostReceipt"].get("hostId") if isinstance(request["hostReceipt"], dict) else None
        key_info = self._host_key(host_id)
        if not key_info:
            raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
        receipt = _verify_signed(request["hostReceipt"], fields=_host_signed_fields(request["hostReceipt"]), contract=HOST_RECEIPT,
                                 public_key_pem=key_info[0], expected_key_id=key_info[1])
        expected_invocations = 0 if receipt.get("outcome") in {"failed", "not_started"} \
            and receipt.get("dispatchMarkerPayloadSha256") is None else 1
        if receipt.get("authority") != host_id or receipt.get("ownerPrincipalId") != str(owner_user_id) \
                or receipt.get("executionId") != execution_id or receipt.get("invocationCount") != expected_invocations:
            raise ConstructExecutionError("EXECUTION_HOST_RECEIPT_SCOPE_INVALID", "host receipt scope invalid", 403)
        no_effect = receipt.get("operation") in {"workspace.file.read", "network.https.fetch", "artifact.readback.verify"}
        expected_committed = "not_applicable" if no_effect else "true"
        if (receipt["outcome"] == "completed" and receipt["effectCommitted"] != expected_committed) or \
                (receipt["outcome"] == "not_started" and receipt["effectCommitted"] != "false"):
            raise ConstructExecutionError("EXECUTION_HOST_RECEIPT_OUTCOME_INVALID", "host outcome is inconsistent", 409)
        if receipt["outcome"] != "completed" and any((
            request["resultArtifact"] is not None,
            receipt.get("outputArtifacts") != [],
            receipt.get("outputSha256") is not None,
            receipt.get("providerDraftSha256") is not None,
        )):
            raise ConstructExecutionError("EXECUTION_HOST_RECEIPT_OUTCOME_INVALID", "non-completed outcome cannot carry result bytes", 409)
        current = now or datetime.now(timezone.utc)
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, True)
                program = row.get("execution_program")
                if isinstance(program, str):
                    program = json.loads(program)
                _assert_hydro_document_scope(program, receipt)
                events = self._events(cur, str(owner_user_id), execution_id)
                last_event = events[-1]["event"] if events else None
                dispatch_marker = next((
                    (entry["event"].get("payload") or {}).get("dispatchMarker")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_effect_dispatched"
                    and ((entry["event"].get("payload") or {}).get("dispatchMarker") or {}).get("stepId") == receipt.get("stepId")
                ), None)
                if dispatch_marker is None:
                    if receipt.get("outcome") not in {"failed", "not_started"} \
                            or receipt.get("dispatchMarkerPayloadSha256") is not None:
                        raise ConstructExecutionError("EXECUTION_DISPATCH_MARKER_REQUIRED", "post-dispatch outcome requires the committed dispatch marker", 409)
                elif receipt.get("dispatchMarkerPayloadSha256") != dispatch_marker.get("payloadSha256"):
                    raise ConstructExecutionError("EXECUTION_DISPATCH_MARKER_MISMATCH", "host receipt does not bind the committed dispatch marker", 409)
                step = next((entry for entry in program.get("steps", [])
                             if entry.get("stepId") == receipt.get("stepId")), None)
                if (step or {}).get("hydroScope"):
                    cur.execute(
                        """SELECT request_hash FROM ovvaults.construct_work_hydro_worker_requests
                            WHERE owner_user_id=%s AND execution_id=%s AND step_id=%s
                              AND attempt_ordinal=%s FOR SHARE""",
                        (str(owner_user_id), execution_id, receipt.get("stepId"),
                         receipt.get("attemptOrdinal")),
                    )
                    staged_request = _row(cur.fetchone())
                    if not staged_request or any((
                        receipt.get("hydroWorkerRequestSha256") != staged_request.get("request_hash"),
                        (dispatch_marker or {}).get("hydroWorkerRequestSha256")
                        != staged_request.get("request_hash"),
                    )):
                        raise ConstructExecutionError(
                            "HYDRO_WORKER_RESULT_REQUEST_MISMATCH",
                            "Hydro result does not bind the immutable prepared request reference", 409,
                        )
                if (last_event or {}).get("eventType") == "execution_cancel_requested" \
                        and receipt.get("outcome") not in {"cancelled", "unknown", "completed", "failed", "not_started"}:
                    raise ConstructExecutionError("EXECUTION_CANCEL_OUTCOME_INVALID", "cancel-pending receipt outcome is invalid", 409)
                artifact = None
                if receipt["outcome"] == "completed":
                    verified, content_bytes = self._verified_result_artifact(
                        program=program, receipt=receipt, result_artifact=request["resultArtifact"],
                    )
                    artifact = self._store_artifact(
                        cur, owner=str(owner_user_id), row=row, artifact_type="result",
                        content_sha256=verified["contentSha256"], source_receipt=receipt,
                        attempt_id=receipt["idempotencyKey"], media_type=verified["mediaType"],
                        content_bytes=content_bytes, now=current,
                    )
                event = self._append(cur, owner=str(owner_user_id), row=row, payload={"hostReceipt": receipt},
                                     authorization=request["authorization"],
                                     actor={"principalId": host_id, "principalType": "execution_host", "authority": "execution_host"},
                                     now=current)
                return {"event": event, "artifact": artifact}

    def resolve_result_artifact(self, owner_user_id: str, execution_id: str,
                                request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"stepId", "artifactId", "hostReceiptPayloadSha256"}, "executionResultResolve")
        step_id = _id(request["stepId"], "stepId")
        artifact_id = _id(request["artifactId"], "artifactId")
        host_sha = _digest(request["hostReceiptPayloadSha256"], "hostReceiptPayloadSha256")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                program = row["execution_program"]
                if isinstance(program, str):
                    program = json.loads(program)
                events = self._events(cur, str(owner_user_id), execution_id)
                host_receipt = next((
                    (entry["event"].get("payload") or {}).get("hostReceipt")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                    and ((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("stepId") == step_id
                    and ((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("payloadSha256") == host_sha
                ), None)
                cur.execute(
                    """SELECT artifact_id,content_sha256,media_type,content_bytes,byte_length,receipt,receipt_sha256
                         FROM ovvaults.construct_work_execution_artifacts
                        WHERE owner_user_id=%s AND execution_id=%s AND artifact_id=%s AND artifact_type='result'
                        FOR SHARE""",
                    (str(owner_user_id), execution_id, artifact_id),
                )
                artifact = _row(cur.fetchone())
        if not isinstance(host_receipt, dict) or host_receipt.get("outcome") != "completed" or not artifact:
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_NOT_FOUND", "canonical result artifact not found", 404)
        content_bytes = artifact.get("content_bytes")
        if isinstance(content_bytes, memoryview):
            content_bytes = content_bytes.tobytes()
        try:
            content = json.loads(bytes(content_bytes).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_INVALID", "canonical result bytes are invalid", 409) from exc
        receipt_document = artifact.get("receipt")
        if isinstance(receipt_document, str):
            receipt_document = json.loads(receipt_document)
        vvault_public = canonical_projection_signing.public_key_document(private_key_pem=self.private_key_pem)["publicKeyPem"]
        verified_receipt = _verify_signed(
            receipt_document, fields=_ARTIFACT_RECEIPT_FIELDS, contract="life-vvault-execution-artifact/v1",
            public_key_pem=vvault_public, expected_key_id=None,
        )
        descriptor = {"artifactId": artifact_id, "sha256": artifact["content_sha256"], "mediaType": artifact["media_type"]}
        if any((
            artifact.get("media_type") != "application/json",
            artifact.get("byte_length") != len(content_bytes),
            _bytes(content) != bytes(content_bytes),
            _sha(content) != artifact.get("content_sha256"),
            host_receipt.get("outputArtifacts") != [descriptor],
            host_receipt.get("outputSha256") != artifact.get("content_sha256"),
            verified_receipt.get("sourceReceiptPayloadSha256") != host_sha,
            verified_receipt.get("artifactId") != artifact_id,
            verified_receipt.get("contentSha256") != artifact.get("content_sha256"),
            artifact.get("receipt_sha256") != verified_receipt.get("payloadSha256"),
        )):
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_INVALID", "canonical result artifact failed verification", 409)
        current = now or datetime.now(timezone.utc)
        return _signed({
            "contract": RESULT_RESOLUTION, "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
            "stepId": step_id, "artifactId": artifact_id, "mediaType": "application/json",
            "content": content, "contentSha256": artifact["content_sha256"],
            "byteLength": len(content_bytes), "hostReceiptPayloadSha256": host_sha,
            "resolvedAt": _iso(current),
        }, self.private_key_pem)

    def record_readback(self, owner_user_id: str, execution_id: str, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        request = _exact(request, {"readback", "authorization"}, "executionReadbackRequest")
        raw_readback = request["readback"] if isinstance(request["readback"], dict) else {}
        host_id = raw_readback.get("hostId")
        if raw_readback.get("authority") == AUTHORITY:
            key_info = (
                canonical_projection_signing.public_key_document(
                    private_key_pem=self.private_key_pem
                )["publicKeyPem"],
                None,
            )
        else:
            key_info = self._host_key(host_id)
        if not key_info:
            raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
        readback = _verify_signed(request["readback"], fields=_hydro_signed_fields(request["readback"], _READBACK_FIELDS), contract=EXECUTION_READBACK,
                                  public_key_pem=key_info[0], expected_key_id=key_info[1])
        if readback.get("ownerPrincipalId") != str(owner_user_id) or readback.get("executionId") != execution_id \
                or readback.get("outcome") not in {"committed", "not_committed", "unknown"} \
                or readback.get("authority") not in {AUTHORITY, host_id}:
            raise ConstructExecutionError("EXECUTION_READBACK_SCOPE_INVALID", "readback scope invalid", 403)
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), execution_id, True)
                program = row.get("execution_program")
                if isinstance(program, str):
                    program = json.loads(program)
                events = self._events(cur, str(owner_user_id), execution_id)
                host_receipt = next((
                    (entry["event"].get("payload") or {}).get("hostReceipt")
                    for entry in reversed(events)
                    if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                    and ((entry["event"].get("payload") or {}).get("hostReceipt") or {}).get("stepId") == readback.get("stepId")
                ), None)
                step = next((entry for entry in program.get("steps", [])
                             if entry.get("stepId") == readback.get("stepId")), None)
                _assert_hydro_document_scope(program, readback)
                expected_result = ((host_receipt or {}).get("outputSha256")
                                   or (host_receipt or {}).get("providerDraftSha256"))
                if not step or not isinstance(host_receipt, dict) or any((
                    readback.get("programId") != program.get("programId"),
                    readback.get("itemId") != program.get("itemId"),
                    readback.get("hostId") != step.get("hostId"),
                    readback.get("attemptOrdinal") != host_receipt.get("attemptOrdinal"),
                    readback.get("idempotencyKey") != host_receipt.get("idempotencyKey"),
                    readback.get("expectedResultSha256") != expected_result,
                    readback.get("outcome") == "committed" and readback.get("observedResultSha256") != expected_result,
                    readback.get("outcome") == "not_committed" and readback.get("observedResultSha256") is not None,
                )):
                    raise ConstructExecutionError("EXECUTION_READBACK_SCOPE_INVALID", "readback does not bind the canonical host outcome", 409)
                readback_actor = ({"principalId": "vvault", "principalType": "system", "authority": "vvault"}
                                  if readback.get("authority") == AUTHORITY else
                                  {"principalId": host_id, "principalType": "execution_host", "authority": "execution_host"})
                event = self._append(cur, owner=str(owner_user_id), row=row, payload={"readback": readback},
                                     authorization=request["authorization"],
                                     actor=readback_actor,
                                     now=now or datetime.now(timezone.utc))
                artifact = self._store_artifact(cur, owner=str(owner_user_id), row=row, artifact_type="effect_readback",
                                                content_sha256=readback["payloadSha256"], source_receipt=readback,
                                                attempt_id=readback["idempotencyKey"], now=now)
                return {"event": event, "artifact": artifact}

    def issue_execution_evidence(self, owner_user_id: str, execution_id: str,
                                 request: dict[str, Any]) -> dict[str, Any]:
        """Resolve immutable result + committed readback into canonical advancement evidence."""
        request = _exact(
            request,
            {"stepId", "hostReceiptPayloadSha256", "readbackPayloadSha256"},
            "executionEvidenceResolve",
        )
        step_id = _id(request["stepId"], "stepId")
        host_sha = _digest(request["hostReceiptPayloadSha256"], "hostReceiptPayloadSha256")
        readback_sha = _digest(request["readbackPayloadSha256"], "readbackPayloadSha256")
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                events = self._events(cur, str(owner_user_id), execution_id)
                program = row["execution_program"]
                if isinstance(program, str):
                    program = json.loads(program)
                step = next((entry for entry in program.get("steps", []) if entry.get("stepId") == step_id), None)
                host_receipts = [
                    entry["event"]["payload"].get("hostReceipt")
                    for entry in events
                    if entry["event"].get("eventType") == "execution_attempt_outcome_recorded"
                    and (entry["event"].get("payload") or {}).get("hostReceipt", {}).get("stepId") == step_id
                ]
                readbacks = [
                    entry["event"]["payload"].get("readback")
                    for entry in events
                    if entry["event"].get("eventType") == "execution_readback_recorded"
                    and (entry["event"].get("payload") or {}).get("readback", {}).get("stepId") == step_id
                ]
                host_receipt = next((value for value in reversed(host_receipts)
                                     if isinstance(value, dict) and value.get("payloadSha256") == host_sha), None)
                readback = next((value for value in reversed(readbacks)
                                 if isinstance(value, dict) and value.get("payloadSha256") == readback_sha), None)
                result_sha = (host_receipt or {}).get("outputSha256")
                if not step or not host_receipt or not readback or any((
                    host_receipt.get("outcome") != "completed",
                    host_receipt.get("operation") != step.get("operation"),
                    host_receipt.get("argumentsSha256") != step.get("argumentsSha256"),
                    readback.get("outcome") != "committed",
                    readback.get("expectedResultSha256") != result_sha,
                    readback.get("observedResultSha256") != result_sha,
                )):
                    raise ConstructExecutionError(
                        "EXECUTION_EVIDENCE_NOT_VERIFIED",
                        "execution evidence requires a completed result and authoritative committed readback", 409,
                    )
                host_key = self._host_key(host_receipt.get("hostId"))
                if readback.get("authority") == AUTHORITY:
                    readback_key = (
                        canonical_projection_signing.public_key_document(
                            private_key_pem=self.private_key_pem
                        )["publicKeyPem"],
                        None,
                    )
                else:
                    readback_key = self._host_key(readback.get("hostId"))
                if not host_key or not readback_key:
                    raise ConstructExecutionError("EXECUTION_HOST_KEY_UNAVAILABLE", "host key unavailable", 503)
                _verify_signed(host_receipt, fields=_host_signed_fields(host_receipt), contract=HOST_RECEIPT,
                               public_key_pem=host_key[0], expected_key_id=host_key[1])
                _verify_signed(readback, fields=_hydro_signed_fields(readback, _READBACK_FIELDS), contract=EXECUTION_READBACK,
                               public_key_pem=readback_key[0], expected_key_id=readback_key[1])
                cur.execute(
                    """SELECT artifact_id,content_sha256,media_type,content_bytes,byte_length,receipt_sha256,receipt
                         FROM ovvaults.construct_work_execution_artifacts
                        WHERE owner_user_id=%s AND execution_id=%s
                          AND artifact_type='result' AND content_sha256=%s""",
                    (str(owner_user_id), execution_id, result_sha),
                )
                artifact = _row(cur.fetchone())
        artifact_receipt = artifact.get("receipt") if artifact else None
        if isinstance(artifact_receipt, str):
            artifact_receipt = json.loads(artifact_receipt)
        vvault_public = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        if not artifact or not isinstance(artifact_receipt, dict):
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_NOT_FOUND", "immutable result artifact not found", 409)
        content_bytes = artifact.get("content_bytes")
        if isinstance(content_bytes, memoryview):
            content_bytes = content_bytes.tobytes()
        try:
            content = json.loads(bytes(content_bytes).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_INVALID", "immutable result bytes are invalid", 409) from exc
        verified_artifact = _verify_signed(
            artifact_receipt, fields=_ARTIFACT_RECEIPT_FIELDS,
            contract="life-vvault-execution-artifact/v1", public_key_pem=vvault_public,
            expected_key_id=None,
        )
        if any((
            verified_artifact.get("ownerPrincipalId") != str(owner_user_id),
            verified_artifact.get("executionId") != execution_id,
            verified_artifact.get("programId") != program["programId"],
            verified_artifact.get("artifactId") != artifact.get("artifact_id"),
            verified_artifact.get("artifactType") != "result",
            verified_artifact.get("contentSha256") != result_sha,
            verified_artifact.get("sourceReceiptPayloadSha256") != host_sha,
            artifact.get("receipt_sha256") != verified_artifact.get("payloadSha256"),
            artifact.get("media_type") != "application/json",
            artifact.get("byte_length") != len(content_bytes),
            _bytes(content) != bytes(content_bytes),
            _sha(content) != result_sha,
            host_receipt.get("outputArtifacts") != [{"artifactId": artifact.get("artifact_id"),
                                                       "sha256": result_sha, "mediaType": "application/json"}],
            step.get("operation") == "provider.generate" and not _provider_result_matches_draft(content, host_receipt),
            step.get("operation") not in {"provider.generate", "hydro.graph.dispatch"}
            and host_receipt.get("providerDraftSha256") is not None,
        )):
            raise ConstructExecutionError("EXECUTION_RESULT_ARTIFACT_INVALID", "immutable result artifact failed verification", 409)
        issued_at = verified_artifact["issuedAt"]
        verified_fact_kinds = sorted(set([
            "execution_readback_verified", "execution_result_immutable",
            *step.get("completionFactKinds", []),
        ]))
        work_reference = {
            "contract": "chatty-work-evidence-reference/v1",
            "evidenceId": verified_artifact["artifactId"],
            "evidenceType": "test_result",
            "authority": "ovvaults.construct_work_execution_artifacts",
            "scope": {
                "ownerPrincipalId": str(owner_user_id), "programId": program["programId"],
                "constructId": program["sourceConstructId"], "itemId": program["itemId"],
                "threadId": program["threadId"], "sessionId": program["sessionId"],
            },
            "payloadSha256": result_sha,
            "receiptSha256": verified_artifact["payloadSha256"],
            "verifiedFactKinds": verified_fact_kinds,
            "issuedAt": issued_at, "cryptographicallyVerified": True, "advancementAuthority": True,
        }
        body = {
            "contract": EXECUTION_EVIDENCE,
            "evidenceId": f"execution-evidence-{_sha({'executionId': execution_id, 'stepId': step_id, 'host': host_sha, 'readback': readback_sha})[:40]}",
            "authority": AUTHORITY, "ownerPrincipalId": str(owner_user_id),
            "executionId": execution_id, "programId": program["programId"], "itemId": program["itemId"],
            "stepId": step_id, "hostReceiptPayloadSha256": host_sha,
            "verifiedFactKinds": work_reference["verifiedFactKinds"],
            "workEvidenceReference": work_reference, "issuedAt": issued_at,
        }
        return _signed(body, self.private_key_pem)

    def finalize_execution_work(self, owner_user_id: str, execution_id: str,
                                request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """Atomically append the Core-authorized Plan4 and Plan3 completion deltas."""
        request = _exact(
            request, {"batch", "executionContextProjection", "workContextProjection"},
            "executionWorkFinalizationRequest",
        )
        current = now or datetime.now(timezone.utc)
        batch, authorization = _validate_finalization_batch(
            request["batch"], owner=str(owner_user_id), public_key_pem=self.core_public_key_pem,
            expected_key_id=self.core_key_id, now=current,
        )
        if authorization.get("executionId") != execution_id:
            raise ConstructExecutionError("EXECUTION_FINALIZATION_ROUTE_SCOPE_INVALID", "finalization execution route mismatch", 403)
        if authorization.get("transcriptBindingSha256") is not None:
            raise ConstructExecutionError(
                "EXECUTION_FINALIZATION_TRANSCRIPT_ATOMICITY_REQUIRED",
                "transcript-bound finalization must use the canonical transcript atomic operation", 409,
            )
        execution_context = _exact(
            request["executionContextProjection"],
            set(_EXECUTION_CONTEXT_FIELDS) | {"algorithm", "keyId", "signature"},
            "signedExecutionContextProjection",
        )
        work_context = _exact(
            request["workContextProjection"],
            set(_WORK_CONTEXT_FIELDS) | {"algorithm", "keyId", "signature"},
            "signedWorkContextProjection",
        )
        vvault_public = canonical_projection_signing.public_key_document(
            private_key_pem=self.private_key_pem
        )["publicKeyPem"]
        try:
            for document in (execution_context, work_context):
                unsigned = {key: value for key, value in document.items() if key not in {"algorithm", "keyId", "signature"}}
                canonical_projection_signing.verify_canonical_payload(
                    unsigned,
                    {key: document[key] for key in ("algorithm", "keyId", "signature")},
                    public_key_pem=vvault_public,
                )
        except ValueError as exc:
            raise ConstructExecutionError("EXECUTION_FINALIZATION_CONTEXT_SIGNATURE_INVALID", "finalization context signature is invalid", 403) from exc
        execution_receipt = _exact(
            execution_context.get("executionStateReceipt"), _EXECUTION_STATE_RECEIPT_FIELDS,
            "executionStateReceipt",
        )
        work_receipt = _exact(work_context.get("stateReceipt"), _WORK_STATE_RECEIPT_FIELDS, "workStateReceipt")
        if execution_receipt.get("receiptSha256") != authorization.get("executionExpectedStateReceiptSha256") \
                or work_receipt.get("receiptSha256") != authorization.get("workExpectedStateReceiptSha256"):
            raise ConstructExecutionError("EXECUTION_FINALIZATION_STATE_RECEIPT_MISMATCH", "finalization state receipts mismatch", 409)
        work_service = canonical_work_loop_service
        try:
            with self.connect() as conn:
                try:
                    with conn.cursor() as cur:
                        execution_row = self._program_row(cur, str(owner_user_id), execution_id, True)
                        work_program = work_service._program(
                            cur, str(owner_user_id), authorization["programId"], for_update=True
                        )
                        execution_program = execution_row["execution_program"]
                        if isinstance(execution_program, str):
                            execution_program = json.loads(execution_program)
                        execution_events = self._events(cur, str(owner_user_id), execution_id)
                        execution_head = execution_events[-1]["event"] if execution_events else None
                        work_head = work_service._head(cur, str(owner_user_id), authorization["programId"])
                        scope_pairs = {
                            "ownerPrincipalId": str(owner_user_id), "executionId": execution_id,
                            "programId": execution_program["programId"], "itemId": execution_program["itemId"],
                            "sourceConstructId": execution_program["sourceConstructId"],
                            "responsibleConstructId": execution_program["responsibleConstructId"],
                            "threadId": execution_program["threadId"], "sessionId": execution_program["sessionId"],
                            "executionBranchId": execution_program["branchId"], "workBranchId": work_program["branch_id"],
                        }
                        cur.execute(
                            """SELECT envelope FROM ovvaults.construct_work_execution_events
                                WHERE owner_user_id=%s AND execution_id=%s AND core_authorization_hash=%s
                                ORDER BY sequence""",
                            (str(owner_user_id), execution_id, authorization["payloadSha256"]),
                        )
                        existing_execution = [(_row(value) or {}).get("envelope") for value in cur.fetchall()]
                        cur.execute(
                            """SELECT envelope FROM ovvaults.construct_work_events
                                WHERE owner_user_id=%s AND program_id=%s AND core_authorization_hash=%s
                                ORDER BY sequence""",
                            (str(owner_user_id), authorization["programId"], authorization["payloadSha256"]),
                        )
                        existing_work = [(_row(value) or {}).get("envelope") for value in cur.fetchall()]
                        replay = bool(existing_execution or existing_work)
                        if any(authorization.get(key) != value for key, value in scope_pairs.items()) or (not replay and any((
                            authorization.get("goalRevision") != work_head.get("resulting_goal_revision"),
                            authorization.get("executionExpectedSequence") != len(execution_events) + 1,
                            authorization.get("executionExpectedHeadEventId") != (execution_head or {}).get("eventId"),
                            authorization.get("executionExpectedHeadSha256") != (execution_head or {}).get("eventSha256"),
                            authorization.get("workExpectedSequence") != int(work_head.get("sequence")) + 1,
                            authorization.get("workExpectedHeadEventId") != work_head.get("event_id"),
                            authorization.get("workExpectedHeadSha256") != work_head.get("event_sha256"),
                            execution_receipt.get("headEventSha256") != (execution_head or {}).get("eventSha256"),
                            work_receipt.get("headEventSha256") != work_head.get("event_sha256"),
                        ))):
                            raise ConstructExecutionError("EXECUTION_FINALIZATION_HEAD_CONFLICT", "finalization dual compare-and-swap failed", 409)
                        if existing_execution or existing_work:
                            if len(existing_execution) != len(batch["executionEvents"]) or len(existing_work) != len(batch["workEvents"]):
                                raise ConstructExecutionError("EXECUTION_FINALIZATION_PARTIAL_STATE", "partial finalization state detected", 409)
                            execution_envelopes = [value if isinstance(value, dict) else json.loads(value) for value in existing_execution]
                            work_envelopes = [value if isinstance(value, dict) else json.loads(value) for value in existing_work]
                            if any((
                                [value["event"].get("eventType") for value in execution_envelopes]
                                != [value["eventType"] for value in batch["executionEvents"]],
                                [value["event"].get("payloadSha256") for value in execution_envelopes]
                                != [value["eventPayloadSha256"] for value in batch["executionEvents"]],
                                [value["event"].get("eventType") for value in work_envelopes]
                                != [value["eventType"] for value in batch["workEvents"]],
                                [value["event"].get("payloadSha256") for value in work_envelopes]
                                != [value["eventPayloadSha256"] for value in batch["workEvents"]],
                            )):
                                raise ConstructExecutionError(
                                    "EXECUTION_FINALIZATION_IDEMPOTENCY_CONFLICT",
                                    "finalization readback differs from authorized event bytes", 409,
                                )
                            status = "idempotent_readback"
                        else:
                            first_evidence = batch["executionEvents"][0]["eventPayload"].get("evidence")
                            evidence = _verify_signed(
                                first_evidence, fields=_EXECUTION_EVIDENCE_FIELDS, contract=EXECUTION_EVIDENCE,
                                public_key_pem=vvault_public, expected_key_id=None,
                            )
                            if any((
                                evidence.get("ownerPrincipalId") != str(owner_user_id),
                                evidence.get("executionId") != execution_id,
                                evidence.get("programId") != execution_program["programId"],
                                evidence.get("itemId") != execution_program["itemId"],
                                evidence.get("stepId") != batch["executionEvents"][0]["eventPayload"].get("stepId"),
                                batch["workEvents"][0]["eventPayload"].get("evidence") != evidence.get("workEvidenceReference"),
                            )):
                                raise ConstructExecutionError("EXECUTION_FINALIZATION_EVIDENCE_SCOPE_INVALID", "finalization evidence scope mismatch", 409)
                            execution_envelopes = []
                            previous_execution = execution_head
                            working_events = list(execution_events)
                            for descriptor in batch["executionEvents"]:
                                payload = descriptor["eventPayload"]
                                _assert_transition(working_events, descriptor["eventType"], payload)
                                control = {
                                    "eventType": descriptor["eventType"],
                                    "expectedSequence": int(previous_execution["sequence"]) + 1,
                                    "expectedHeadEventId": previous_execution["eventId"],
                                    "expectedHeadSha256": previous_execution["eventSha256"],
                                    "idempotencyKey": f"execution-finalization-{_sha({'authorization': authorization['payloadSha256'], 'ordinal': descriptor['ordinal']})[:40]}",
                                    "payloadSha256": authorization["payloadSha256"],
                                }
                                event = _event(execution_program, control, payload, descriptor["actor"],
                                               work_service._database_now(cur), evidence.get("payloadSha256"))
                                envelope = _envelope(event, self.private_key_pem)
                                cur.execute(
                                    """INSERT INTO ovvaults.construct_work_execution_events
                                      (owner_user_id,program_id,execution_id,item_id,source_construct_id,responsible_construct_id,
                                       thread_id,session_id,branch_id,event_id,sequence,parent_event_id,parent_event_sha256,event_type,
                                       idempotency_key,request_digest,core_authorization_hash,evidence_digest,occurred_at,
                                       actor_principal_id,actor_principal_type,actor_authority,payload,payload_sha256,event_sha256,
                                       envelope,signature_algorithm,signature_key_id,signature)
                                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s)""",
                                    (str(owner_user_id), event["programId"], event["executionId"], event["itemId"],
                                     event["sourceConstructId"], event["responsibleConstructId"], event["threadId"],
                                     event["sessionId"], event["branchId"], event["eventId"], event["sequence"],
                                     event["parentEventId"], event["parentEventSha256"], event["eventType"],
                                     event["idempotencyKey"], event["requestDigest"], authorization["payloadSha256"],
                                     evidence["payloadSha256"], event["occurredAt"], descriptor["actor"]["principalId"],
                                     descriptor["actor"]["principalType"], descriptor["actor"]["authority"],
                                     json.dumps(payload), event["payloadSha256"], event["eventSha256"], json.dumps(envelope),
                                     envelope["algorithm"], envelope["keyId"], envelope["signature"]),
                                )
                                execution_envelopes.append(envelope)
                                working_events.append(envelope)
                                previous_execution = event
                            work_envelopes = []
                            previous_work = work_head
                            for descriptor in batch["workEvents"]:
                                payload = work_service._validate_payload_shape(descriptor["eventType"], descriptor["eventPayload"])
                                evidence_refs = work_service._validate_event_evidence(
                                    cur, owner_user_id=str(owner_user_id), program=work_program,
                                    event_type=descriptor["eventType"], payload=payload,
                                )
                                control = {
                                    "expectedSequence": int(previous_work.get("sequence")) + 1,
                                    "expectedHeadEventId": previous_work.get("eventId") or previous_work.get("event_id"),
                                    "expectedHeadSha256": previous_work.get("eventSha256") or previous_work.get("event_sha256"),
                                    "eventType": descriptor["eventType"], "eventPayloadSha256": descriptor["eventPayloadSha256"],
                                    "idempotencyKey": f"work-finalization-{_sha({'authorization': authorization['payloadSha256'], 'ordinal': descriptor['ordinal']})[:40]}",
                                    "goalRevision": authorization["goalRevision"], "resultingGoalRevision": authorization["goalRevision"],
                                    "payloadSha256": authorization["payloadSha256"],
                                }
                                envelope = work_service._insert_event(
                                    cur, owner_user_id=str(owner_user_id), program=work_program, authorization=control,
                                    payload=payload, actor=descriptor["actor"], occurred_at=work_service._database_now(cur),
                                    request_digest=_sha({"batch": batch["batchSha256"], "stream": "work", "ordinal": descriptor["ordinal"]}),
                                    evidence_digest=_sha(evidence_refs) if evidence_refs else None,
                                    stored_authorization=authorization,
                                )
                                work_envelopes.append(envelope)
                                previous_work = envelope["event"]
                            status = "committed"
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        except ConstructWorkLoopError as exc:
            raise ConstructExecutionError(exc.code, str(exc), exc.status) from exc
        final_execution = execution_envelopes[-1]["event"]
        final_work = work_envelopes[-1]["event"]
        receipt_body = {
            "contract": FINALIZATION_RECEIPT, "status": status,
            "ownerPrincipalId": str(owner_user_id), "executionId": execution_id,
            "programId": authorization["programId"], "itemId": authorization["itemId"],
            "authorizationPayloadSha256": authorization["payloadSha256"], "batchSha256": batch["batchSha256"],
            "executionEvents": [{"eventId": value["event"]["eventId"], "eventSha256": value["event"]["eventSha256"],
                                  "sequence": value["event"]["sequence"], "eventType": value["event"]["eventType"]}
                                 for value in execution_envelopes],
            "workEvents": [{"eventId": value["event"]["eventId"], "eventSha256": value["event"]["eventSha256"],
                             "sequence": value["event"]["sequence"], "eventType": value["event"]["eventType"]}
                            for value in work_envelopes],
            "resultingExecutionHeadSha256": final_execution["eventSha256"],
            "resultingWorkHeadSha256": final_work["eventSha256"], "atomic": True,
            "readbackVerified": True, "issuedAt": _iso(datetime.now(timezone.utc)),
        }
        return _signed(receipt_body, self.private_key_pem)

    def projection(self, owner_user_id: str, execution_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                row = self._program_row(cur, str(owner_user_id), _id(execution_id, "executionId"), False)
                events = self._events(cur, str(owner_user_id), execution_id)
        head = events[-1]["event"]
        program = row["execution_program"] if isinstance(row["execution_program"], dict) else json.loads(row["execution_program"])
        now = datetime.now(timezone.utc)
        body = {"contract": EXECUTION_PROJECTION, "executionId": execution_id, "ownerPrincipalId": str(owner_user_id),
                "programId": program["programId"], "itemId": program["itemId"], "sourceConstructId": program["sourceConstructId"],
                "responsibleConstructId": program["responsibleConstructId"], "threadId": program["threadId"],
                "sessionId": program["sessionId"], "branchId": program["branchId"], "definitionHash": program["definitionHash"],
                "revision": head["eventSha256"], "head": {"eventId": head["eventId"], "eventSha256": head["eventSha256"], "sequence": head["sequence"]},
                "events": events, "issuedAt": _iso(now), "expiresAt": _iso(now + timedelta(seconds=60))}
        return _signed(body, self.private_key_pem)


construct_execution_service = ConstructExecutionService(host_key_resolver=_configured_host_key)
