"""Canonical profile registration and owner-bound continuity for AUTO.

VVAULT stores and signs evidence for AUTO but never interprets a prompt.  The
service deliberately uses a dedicated owner-qualified transcript path instead
of the construct transcript helpers, whose legacy selectors are callsign-only.
"""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from time import monotonic
from typing import Any, Callable, Protocol

from vvault.server import (
    account_context_service,
    canonical_data_contract,
    canonical_projection_signing,
    chatty_body_service,
    knowledge_activation_service,
    knowledge_contract,
)
from vvault.server.system_runtime_registry import (
    AUTO_PROFILE_COMBINED_SHA256,
    AUTO_PROFILE_CONTRACT,
    AUTO_PROFILE_REVISION,
    AUTO_RUNTIME_PRINCIPAL_ID,
    load_bundled_auto_profile,
)
from vvault.server.code_project_repository import CodeProjectRepository
from vvault.server.vvault_file_repository import SYSTEM_USER_EMAIL, VVaultFileRepository


REGISTRATION_REQUEST_CONTRACT = "chatty-auto-system-runtime-registration-request/v1"
REGISTRATION_RECEIPT_CONTRACT = "chatty-auto-system-runtime-registration-receipt/v1"
REGISTRATION_PREFLIGHT_REQUEST_CONTRACT = "chatty-auto-registration-preflight-request/v1"
REGISTRATION_PREFLIGHT_PROJECTION_CONTRACT = "chatty-auto-registration-preflight-projection/v1"
CONTEXT_REQUEST_CONTRACT = "chatty-auto-context-request/v1"
CONTEXT_PROJECTION_CONTRACT = "chatty-auto-context-projection/v1"
EXCHANGE_REQUEST_CONTRACT = "chatty-auto-exchange-request/v1"
EXCHANGE_RECEIPT_CONTRACT = "chatty-auto-exchange-receipt/v1"
DECISION_CONTEXT_CONTRACT = "chatty-auto-decision-context/v1"
CONTINUATION_BASIS_CONTRACT = "chatty-auto-continuation-basis/v1"
SIGNED_PROJECTION_CONTRACT = "life-vvault-signed-projection/v1"
HYDRO_LIFECYCLE_APPEND_REQUEST_CONTRACT = "chatty-auto-hydro-lifecycle-append-request/v1"
HYDRO_LIFECYCLE_EVENT_CONTRACT = "chatty-auto-hydro-lifecycle-event/v1"
HYDRO_LIFECYCLE_RECEIPT_CONTRACT = "chatty-auto-hydro-lifecycle-receipt/v1"
HYDRO_LIFECYCLE_QUARANTINE_RECEIPT_CONTRACT = "chatty-auto-hydro-lifecycle-quarantine-receipt/v1"
THREAD_INDEX_REQUEST_CONTRACT = "chatty-auto-thread-index-request/v1"
THREAD_INDEX_PROJECTION_CONTRACT = "chatty-auto-thread-index-projection/v1"
HYDRO_CATALOG_REQUEST_CONTRACT = "chatty-auto-hydro-catalog-request/v1"
HYDRO_CATALOG_CONTRACT = "chatty-auto-hydro-worker-catalog/v1"
HYDRO_RECOVERY_INDEX_REQUEST_CONTRACT = "chatty-auto-hydro-recovery-index-request/v1"
HYDRO_RECOVERY_INDEX_CONTRACT = "chatty-auto-hydro-recovery-index/v1"
HYDRO_DISPATCH_REQUEST_CONTRACT = "chatty-auto-hydro-dispatch-request/v1"
HYDRO_DISPATCH_RECEIPT_CONTRACT = "chatty-auto-hydro-dispatch-receipt/v1"
HYDRO_CANCELLATION_REQUEST_CONTRACT = "chatty-auto-hydro-cancellation-request/v1"
HYDRO_CANCELLATION_RECEIPT_CONTRACT = "chatty-auto-hydro-cancellation-receipt/v1"
HYDRO_WORKER_RECEIPT_REQUEST_CONTRACT = "chatty-auto-hydro-worker-receipt-request/v1"
HYDRO_WORKER_RECEIPT_CONTRACT = "chatty-auto-hydro-worker-receipt/v1"
ACTION_GRANT_REQUEST_CONTRACT = "chatty-auto-host-action-grant-request/v2"
ACTION_GRANT_RECEIPT_CONTRACT = "chatty-auto-host-action-grant-receipt/v2"
ACTION_EXECUTION_GRANT_CONTRACT = "chatty-auto-host-action-execution-grant/v2"
ACTION_EVENT_REQUEST_CONTRACT = "chatty-auto-host-action-event-request/v2"
ACTION_EVENT_CONTRACT = "chatty-auto-host-action-execution-event/v1"
ACTION_EVENT_RECEIPT_CONTRACT = "chatty-auto-host-action-event-receipt/v2"
ACTION_HOST_BINDING_CONTRACT = "chatty-auto-cli-host-binding/v1"
CODE_PROJECT_BINDING_REQUEST_CONTRACT = "chatty-auto-code-project-binding-request/v1"
CODE_PROJECT_BINDING_PROJECTION_CONTRACT = "chatty-auto-code-project-binding-projection/v1"
CODE_THREAD_HISTORY_REQUEST_CONTRACT = "chatty-auto-code-thread-history-request/v1"
CODE_THREAD_HISTORY_PROJECTION_CONTRACT = "chatty-auto-code-thread-history-projection/v1"
CODE_PROPOSAL_CONTEXT_REQUEST_CONTRACT = "chatty-auto-code-proposal-context-request/v1"
CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT = "chatty-auto-code-proposal-context-receipt/v1"
CODE_HOST_BINDING_CONTRACT = "chatty-auto-code-host-binding/v1"
CODE_ACTION_GRANT_REQUEST_CONTRACT = "chatty-auto-host-action-grant-request/v3"
CODE_ACTION_GRANT_RECEIPT_CONTRACT = "chatty-auto-host-action-grant-receipt/v3"
CODE_ACTION_EXECUTION_GRANT_CONTRACT = "chatty-auto-host-action-execution-grant/v3"
AUTO_PLAN6_RULESET_VERSION = "chatty-auto-ruleset/v1"
AUTO_PLAN6_RULESET_REVISION = "rules-v1.2.0"
AUTO_PLAN6_RULESET_SHA256 = "c2b99742b7d6a6dff2b052d3718ee7f6b00a26e91c6ae140675561b2391e66a0"
AUTO_ACTIVE_RULESET_VERSION = "chatty-auto-ruleset/v1"
AUTO_ACTIVE_RULESET_REVISION = "rules-v1.3.0"
# Replaced with the reviewed Chatty Core digest before focused validation.
AUTO_ACTIVE_RULESET_SHA256 = "5504ebb963427f5d8db044b86299540878ef8e22485cf6f031d5558ac5f90dae"
_LEGACY_HYDRO_RULESETS = ({
    "version": "chatty-auto-ruleset/v1",
    "revision": "rules-v1.1.0",
    "sha256": "0bce41495200be92592b5ded72a0a51e6a931662806e134e52c3c1911067e86b",
}, {
    "version": AUTO_PLAN6_RULESET_VERSION,
    "revision": AUTO_PLAN6_RULESET_REVISION,
    "sha256": AUTO_PLAN6_RULESET_SHA256,
})

DEFAULT_TTL_SECONDS = 30
MAX_TTL_SECONDS = 60
MAX_CACHE_ENTRIES = 256
CACHE_TTL_SECONDS = 30
CONTEXT_DEADLINE_MS = 600
CONTEXT_STATEMENT_TIMEOUT_MS = 600
MAX_INPUT_CHARS = 16_384
MAX_OUTPUT_CHARS = 65_536
MAX_EVENT_BYTES = 512 * 1024
MAX_HYDRO_EVENT_BYTES = 512 * 1024
MAX_HYDRO_STREAM_BYTES = 8 * 1024 * 1024
MAX_HYDRO_INSTANCES = 1_024
MAX_EVIDENCE_ITEMS = 32
MAX_EVIDENCE_CHARS = 65_536
MAX_CODE_HISTORY_ITEMS = 50
MAX_CODE_HISTORY_CHARS = 256 * 1024
MAX_CODE_PROPOSAL_CONTEXT_BYTES = 128 * 1024
MAX_ACTION_BYTES = 256 * 1024
ACTION_GRANT_TTL_SECONDS = 300

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"[a-z0-9]+")
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_HYDRO_GRAPH_TYPES = {
    "graph.proposed": "proposed",
    "graph.authorization_recorded": "authorization_recorded",
    "graph.authorization_verified": "authorization_verified",
    "graph.dispatch_accepted": "dispatch_accepted",
    "graph.running": "running",
    "graph.completed": "completed",
    "graph.completed_with_failures": "completed_with_failures",
    "graph.failed": "failed",
    "graph.cancelled": "cancelled",
    "graph.conflict": "conflict",
    "graph.unknown": "unknown",
    "graph.rejected": "rejected",
}
_HYDRO_WORKER_TYPES = {
    "worker.queued": "queued",
    "worker.started": "started",
    "worker.completed": "completed",
    "worker.failed": "failed",
    "worker.timed_out": "timed_out",
    "worker.cancellation_requested": "cancellation_requested",
    "worker.cancelled": "cancelled",
    "worker.unknown": "unknown",
    "worker.blocked": "blocked",
}
_HYDRO_GRAPH_TERMINAL = {
    "completed", "completed_with_failures", "failed", "cancelled", "conflict", "unknown", "rejected"
}
_HYDRO_WORKER_TERMINAL = {"completed", "failed", "timed_out", "cancelled", "unknown", "blocked"}
_HYDRO_GRAPH_TRANSITIONS = {
    None: {"proposed"},
    "proposed": {"authorization_recorded", "rejected"},
    "authorization_recorded": {"authorization_verified"},
    "authorization_verified": {"dispatch_accepted"},
    "dispatch_accepted": {"running", "failed", "cancelled", "unknown"},
    "running": set(_HYDRO_GRAPH_TERMINAL),
}
_HYDRO_WORKER_TRANSITIONS = {
    None: {"queued"},
    "queued": {"started", "cancellation_requested", "cancelled", "blocked"},
    "started": {"completed", "failed", "timed_out", "cancellation_requested", "unknown", "blocked"},
    "cancellation_requested": {"cancelled", "completed", "failed", "unknown"},
}

_HYDRO_PHASES = ("inspect", "analyze", "compare", "propose", "verify")
_ACTION_STATES = {
    "authorization_verified", "started", "completed", "failed", "cancelled", "unknown"
}
_ACTION_TERMINAL_STATES = {"completed", "failed", "cancelled", "unknown"}
_ACTION_HOST_CAPABILITY_LIMIT = 64
_ACTION_AUTHORITY_REVISION_ROW_LIMIT = 4096
_ACTION_OPERATIONS = {
    "read_file", "search_text", "inspect_workspace", "create_file", "append_line",
    "exact_replacement", "rename_symbol", "run_command", "run_tests", "run_build",
    "run_check", "web_search", "fetch_url",
}
_ACTION_CAPABILITIES = {
    "read_file": "workspace.read",
    "search_text": "workspace.search",
    "inspect_workspace": "workspace.inspect",
    "create_file": "workspace.write",
    "append_line": "workspace.write",
    "exact_replacement": "workspace.write",
    "rename_symbol": "workspace.write",
    "run_command": "command.execute",
    "run_tests": "command.execute",
    "run_build": "command.execute",
    "run_check": "command.execute",
    "web_search": "web.search",
    "fetch_url": "web.fetch",
}
_ACTION_SLOT_POLICIES = {
    "read_file": ("read_file", {"targetPath"}, {"targetPath"}),
    "search_text": ("search_text", {"needle"}, {"needle"}),
    "list_files": ("inspect_workspace", {"targetPath"}, {"targetPath"}),
    "find_symbol_definition": ("inspect_workspace", {"symbol"}, {"symbol"}),
    "find_symbol_usages": ("inspect_workspace", {"symbol"}, {"symbol"}),
    "explain_symbol": ("inspect_workspace", {"symbol"}, {"symbol"}),
    "explain_file": ("inspect_workspace", {"targetPath"}, {"targetPath"}),
    "inspect_conventions": ("inspect_workspace", set(), set()),
    "inspect_project_stack": ("inspect_workspace", set(), set()),
    "inspect_dependencies": ("inspect_workspace", set(), set()),
    "inspect_module_imports": ("inspect_workspace", {"targetPath"}, {"targetPath"}),
    "inspect_ci_workflow": ("inspect_workspace", {"targetPath"}, {"targetPath"}),
    "inspect_git_ci": ("inspect_workspace", set(), set()),
    "create_file": ("create_file", {"targetPath", "content"}, {"targetPath", "content"}),
    "create_node_test": (
        "create_file",
        {"symbol", "sourcePath", "targetPath", "arguments", "expected"},
        {"symbol", "sourcePath", "targetPath", "arguments", "expected"},
    ),
    "create_python_unittest": (
        "create_file",
        {"symbol", "sourcePath", "targetPath", "arguments", "expected"},
        {"symbol", "sourcePath", "targetPath", "arguments", "expected"},
    ),
    "append_line": ("append_line", {"targetPath", "line"}, {"targetPath", "line"}),
    "exact_replacement": (
        "exact_replacement", {"targetPath", "before", "after"},
        {"targetPath", "before", "after"},
    ),
    "workspace_exact_replacement": (
        "exact_replacement", {"before", "after"}, {"before", "after"},
    ),
    "rename_symbol": (
        "rename_symbol", {"targetPath", "before", "after"},
        {"targetPath", "before", "after"},
    ),
    "run_command": ("run_command", {"commandLine"}, {"commandLine"}),
    "run_tests": ("run_tests", {"diagnose"}, {"diagnose"}),
    "run_package_script": ("run_check", {"scriptName"}, {"scriptName"}),
    "web_search": ("web_search", {"query"}, {"query"}),
    "fetch_url": ("fetch_url", {"url"}, {"url"}),
}
_ACTION_FORBIDDEN_TRANSPORT_KEYS = {
    "attachment", "attachments", "conditioning", "credential", "credentials",
    "definition", "instructions", "model", "permission", "permissions", "prompt",
    "apikey", "profileartifacts", "provider", "skippersistence", "token", "secret", "password",
}


def _hydro_descriptor(
    *, worker_id: str, display_name: str, aliases: list[str], kind: str,
    phase: str, worker_principal_id: str | None, provider: str | None,
    model: str | None, routing_revision: str | None, capabilities: list[str],
    memory_bytes: int,
) -> dict[str, Any]:
    descriptor = {
        "workerId": worker_id,
        "displayName": display_name,
        "aliases": aliases,
        "kind": kind,
        "phase": phase,
        "workerPrincipalId": worker_principal_id,
        "provider": provider,
        "model": model,
        "routingRevision": routing_revision,
        "capabilities": sorted(capabilities),
        "memoryBytes": memory_bytes,
    }
    return {**descriptor, "descriptorSha256": _sha256_value(descriptor)}


def _validate_hydro_executor_attestation(
    value: Any, *, owner_user_id: str, thread_id: str
) -> dict[str, Any]:
    fields = {
        "contract", "ownerUserId", "threadId", "executorHostId", "workerRef",
        "kind", "workerPrincipalId", "provider", "model", "routingRevision",
        "expressionRevision", "memoryBytes", "capabilities", "readiness",
        "attestationSha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
            "executor attestation fields are invalid",
        )
    body = copy.deepcopy(value)
    attestation_sha = _sha256_field(
        body.pop("attestationSha256"), "executorAttestation.attestationSha256"
    )
    worker_ref = str(body.get("workerRef") or "").strip().lower()
    kind = str(body.get("kind") or "").strip().lower()
    provider = str(body.get("provider") or "").strip().lower()
    model = str(body.get("model") or "").strip()
    principal = body.get("workerPrincipalId")
    capabilities = body.get("capabilities")
    if (
        value.get("contract") != "chatty-auto-hydro-executor-attestation/v1"
        or value.get("ownerUserId") != owner_user_id
        or value.get("threadId") != thread_id
        or value.get("executorHostId") != "chatty-service"
        or value.get("readiness") != "ready"
        or not _SAFE_ID.fullmatch(worker_ref)
        or kind not in {"construct", "model"}
        or provider not in {"ollama", "openai", "openrouter"}
        or not model or len(model) > 256 or "\x00" in model
        or not _SHA256.fullmatch(str(value.get("routingRevision") or ""))
        or not isinstance(capabilities, list) or not capabilities
        or capabilities != sorted(set(capabilities))
        or any(not isinstance(item, str) or not item or len(item) > 64 for item in capabilities)
        or not isinstance(value.get("memoryBytes"), int)
        or not 0 < value["memoryBytes"] <= 8_000_000_000
        or attestation_sha != _sha256_value(body)
    ):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
            "executor attestation is inconsistent or unverifiable",
        )
    if kind == "construct":
        principal = str(principal or "").strip().lower()
        if not _SAFE_ID.fullmatch(principal) or principal == AUTO_RUNTIME_PRINCIPAL_ID \
                or not _SHA256.fullmatch(str(value.get("expressionRevision") or "")):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
                "construct executor attestation lacks exact expression evidence",
            )
    elif principal is not None or value.get("expressionRevision") is not None:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
            "model executor attestation may not claim construct identity",
        )
    return {**body, "attestationSha256": attestation_sha}


def _deterministic_hydro_workers() -> list[dict[str, Any]]:
    return [
        _hydro_descriptor(
            worker_id=f"auto-deterministic-{phase}-v1",
            display_name=f"Deterministic {phase}",
            aliases=[f"deterministic-{phase}"],
            kind="deterministic",
            phase=phase,
            worker_principal_id=None,
            provider=None,
            model=None,
            routing_revision=None,
            capabilities=[phase],
            memory_bytes=1_000_000,
        )
        for phase in _HYDRO_PHASES
    ]


class AutoRuntimeContractError(ValueError):
    """Stable service error mapped by the HTTP adapter."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _canonical_bytes(value: Any) -> bytes:
    return canonical_projection_signing.canonical_json_bytes(value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID",
            "signed projection time evidence is invalid",
            status=401,
        ) from exc
    if parsed.tzinfo is None:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID",
            "signed projection time evidence must be UTC-bound",
            status=401,
        )
    return parsed.astimezone(timezone.utc)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _stored_content_bytes(value: Any) -> bytes:
    """Return the exact persisted content bytes used for authority digests."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    return str(value or "").encode("utf-8")


def _validate_profile_schemas(prompt: Any, definition: Any) -> None:
    schemas = canonical_data_contract.load_schemas()
    errors = [
        *canonical_data_contract.validate_json_document(
            prompt, schemas.get("life.vvault.system-runtime.prompt") or {}
        ),
        *canonical_data_contract.validate_json_document(
            definition, schemas.get("life.vvault.system-runtime.definition") or {}
        ),
    ]
    if errors:
        raise AutoRuntimeContractError(
            "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
            f"AUTO profile schema validation failed: {errors[:4]}",
            status=503,
        )


def _safe_identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized):
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST",
            f"{field} must be a safe identifier of at most 128 characters",
        )
    return normalized


def _sha256_field(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", f"{field} must be a SHA-256 digest"
        )
    return normalized


def _validate_action_profile_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "contract", "profileContract", "revision", "combinedSha256", "promptSha256",
        "definitionSha256", "conditioningSha256", "provenanceSources", "verificationState",
    }:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action profile evidence fields are invalid"
        )
    if (
        value.get("contract") != "chatty-auto-system-runtime-profile-evidence/v1"
        or value.get("revision") != AUTO_PROFILE_REVISION
        or value.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
        or value.get("verificationState") != "canonical_verified"
        or not isinstance(value.get("provenanceSources"), list)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action profile evidence is not canonical AUTO 1.0.0"
        )
    for field in (
        "combinedSha256", "promptSha256", "definitionSha256", "conditioningSha256"
    ):
        _sha256_field(value.get(field), f"action.profileEvidence.{field}")
    return copy.deepcopy(value)


def _validate_action_ruleset_evidence(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"version", "revision", "sha256"}:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action ruleset evidence fields are invalid"
        )
    expected = {
        "version": AUTO_ACTIVE_RULESET_VERSION,
        "revision": AUTO_ACTIVE_RULESET_REVISION,
        "sha256": AUTO_ACTIVE_RULESET_SHA256,
    }
    if value != expected:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action ruleset evidence is unsupported"
        )
    return copy.deepcopy(expected)


def _validate_interaction_policy(value: Any) -> dict[str, Any]:
    expected = {
        "contract", "mode", "readOnly", "architectureRequired",
        "architectureBasis",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("contract") != "chatty-auto-interaction-policy/v1"
    ):
        raise AutoRuntimeContractError(
            "AUTO_INTERACTION_POLICY_INVALID",
            "AUTO interaction policy evidence fields are invalid",
        )
    mode = value.get("mode")
    flags = {
        "default": (False, False),
        "chat": (True, False),
        "plan": (True, True),
    }
    if mode not in flags or (
        value.get("readOnly"), value.get("architectureRequired")
    ) != flags[mode]:
        raise AutoRuntimeContractError(
            "AUTO_INTERACTION_POLICY_INVALID",
            "AUTO interaction policy flags do not match its mode",
        )
    basis = value.get("architectureBasis")
    if basis is not None:
        if mode != "default" or not isinstance(basis, dict) or set(basis) != {
            "planTurnId", "planResultSha256", "approvalTurnId",
            "approvalReceiptSha256",
        }:
            raise AutoRuntimeContractError(
                "AUTO_INTERACTION_POLICY_INVALID",
                "AUTO architecture basis is invalid for this policy",
            )
        _safe_identifier(basis.get("planTurnId"), "interactionPolicy.architectureBasis.planTurnId")
        _safe_identifier(basis.get("approvalTurnId"), "interactionPolicy.architectureBasis.approvalTurnId")
        _sha256_field(basis.get("planResultSha256"), "interactionPolicy.architectureBasis.planResultSha256")
        _sha256_field(basis.get("approvalReceiptSha256"), "interactionPolicy.architectureBasis.approvalReceiptSha256")
    return copy.deepcopy(value)


def _validate_hydro_delegation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "contract", "required", "workerCount", "delegatesAllExecution",
        "directExecution",
    } or value.get("contract") != "chatty-auto-hydro-delegation/v1":
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_DELEGATION_INVALID",
            "AUTO Hydro delegation evidence fields are invalid",
        )
    required = value.get("required")
    worker_count = value.get("workerCount")
    if (
        not isinstance(required, bool)
        or isinstance(worker_count, bool)
        or not isinstance(worker_count, int)
        or worker_count < 0
        or worker_count > MAX_HYDRO_INSTANCES
        or value.get("directExecution") is not False
    ):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_DELEGATION_INVALID",
            "AUTO Hydro delegation evidence is invalid",
        )
    if required:
        valid = worker_count >= 1 and value.get("delegatesAllExecution") is True
    else:
        valid = worker_count == 0 and value.get("delegatesAllExecution") is False
    if not valid:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_DELEGATION_INVALID",
            "Hydro must delegate all substantive work to at least one worker",
        )
    return copy.deepcopy(value)


_READ_ONLY_ACTION_OPERATIONS = {
    "read_file", "search_text", "inspect_workspace", "web_search", "fetch_url",
}


def _validate_action_interaction_authority(
    action: dict[str, Any], *, allow_read_only_chat: bool = True
) -> dict[str, Any]:
    policy = _validate_interaction_policy(action.get("interactionPolicy"))
    operation = str(action.get("operation") or "")
    mutating = operation not in _READ_ONLY_ACTION_OPERATIONS
    if policy["mode"] == "plan" or policy["architectureRequired"]:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_PLAN_AUTHORITY_REQUIRED",
            "Plan architecture cannot authorize execution before approval",
            status=409,
        )
    if policy["mode"] == "chat" and (
        mutating or not allow_read_only_chat
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_CHAT_READ_ONLY",
            "Chat policy cannot authorize a mutating host action",
            status=409,
        )
    if mutating and policy["readOnly"]:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_READ_ONLY",
            "read-only policy cannot authorize a mutating host action",
            status=409,
        )
    return policy


def _contains_forbidden_action_transport_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).replace("_", "").casefold() in _ACTION_FORBIDDEN_TRANSPORT_KEYS
            or _contains_forbidden_action_transport_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_action_transport_field(item) for item in value)
    return False


def _validate_host_action(
    value: Any, *, owner_user_id: str, thread_id: str, require_approval: bool,
    allow_legacy_readback: bool = False,
) -> dict[str, Any]:
    legacy_fields = {
        "contract", "actionId", "canonicalSha256", "ownerId", "runtimePrincipalId",
        "threadId", "proposalTurnId", "approvalTurnId", "attemptId", "hypothesisId",
        "operation", "slots", "risk", "requiredHostCapability", "profileEvidence",
        "rulesetEvidence",
    }
    expected_fields = legacy_fields | {"interactionPolicy"}
    legacy = isinstance(value, dict) and set(value) == legacy_fields
    if not isinstance(value, dict) or (
        set(value) != expected_fields
        and not (allow_legacy_readback and legacy)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action descriptor fields are invalid"
        )
    action_id = _safe_identifier(value.get("actionId"), "action.actionId")
    _safe_identifier(value.get("proposalTurnId"), "action.proposalTurnId")
    approval_turn_id = value.get("approvalTurnId")
    if approval_turn_id is not None:
        _safe_identifier(approval_turn_id, "action.approvalTurnId")
    if require_approval and approval_turn_id is None:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_APPROVAL_UNVERIFIABLE",
            "host action execution requires a canonical approval turn",
            status=409,
        )
    _safe_identifier(value.get("attemptId"), "action.attemptId")
    _safe_identifier(value.get("hypothesisId"), "action.hypothesisId")
    operation = _safe_identifier(value.get("operation"), "action.operation")
    if operation not in _ACTION_OPERATIONS:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_OPERATION_UNSUPPORTED",
            "host action operation is not registered for CLI execution",
            status=409,
        )
    capability = _safe_identifier(
        value.get("requiredHostCapability"), "action.requiredHostCapability"
    )
    slots = value.get("slots")
    if (
        not isinstance(slots, dict)
        or len(_canonical_bytes(slots)) > 64 * 1024
        or _contains_forbidden_action_transport_field(slots)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action slots are invalid or oversized"
        )
    recipe = str(slots.get("recipe") or operation)
    policy = _ACTION_SLOT_POLICIES.get(recipe)
    expected_operation = policy[0] if policy else None
    if recipe == "run_package_script":
        expected_operation = "run_build" if slots.get("scriptName") == "build" else "run_check"
    slot_keys = set(slots) - {"recipe"}
    if (
        policy is None
        or expected_operation != operation
        or not policy[1] <= slot_keys
        or not slot_keys <= policy[2]
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action slots do not match the registered operation recipe"
        )
    if value.get("risk") not in {"low", "moderate", "high", "critical"}:
        raise AutoRuntimeContractError("AUTO_ACTION_INVALID", "host action risk is invalid")
    if (
        value.get("contract") != "chatty-auto-host-action/v1"
        or value.get("ownerId") != owner_user_id
        or value.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or value.get("threadId") != thread_id
        or capability != _ACTION_CAPABILITIES[operation]
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_BINDING_INVALID", "host action owner, runtime, or thread binding is invalid",
            status=403,
        )
    _validate_action_profile_evidence(value.get("profileEvidence"))
    if legacy:
        if value.get("rulesetEvidence") != {
            "version": AUTO_PLAN6_RULESET_VERSION,
            "revision": AUTO_PLAN6_RULESET_REVISION,
            "sha256": AUTO_PLAN6_RULESET_SHA256,
        }:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_INVALID",
                "legacy host action ruleset evidence is unsupported",
            )
    else:
        _validate_action_ruleset_evidence(value.get("rulesetEvidence"))
        _validate_interaction_policy(value.get("interactionPolicy"))
    declared_sha = _sha256_field(value.get("canonicalSha256"), "action.canonicalSha256")
    basis = copy.deepcopy(value)
    basis.pop("canonicalSha256", None)
    id_basis = copy.deepcopy(basis)
    id_basis.pop("actionId", None)
    expected_action_id = f"auto-action:{_sha256_value(id_basis)[:32]}"
    if action_id != expected_action_id or declared_sha != _sha256_value(basis):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_INVALID", "host action stable ID or canonical hash does not match its bytes"
        )
    return copy.deepcopy(value)


def _validate_cli_host_binding(
    value: Any, *, required_capability: str | None = None
) -> tuple[dict[str, Any], str]:
    expected_fields = {
        "contract", "hostType", "hostSessionId", "workspaceContextId",
        "workspaceRootSha256", "capabilities",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_HOST_BINDING_INVALID",
            "CLI host binding fields are invalid",
        )
    host_session_id = _safe_identifier(
        value.get("hostSessionId"), "hostBinding.hostSessionId"
    )
    workspace_context_id = _sha256_field(
        value.get("workspaceContextId"), "hostBinding.workspaceContextId"
    )
    workspace_root_sha256 = _sha256_field(
        value.get("workspaceRootSha256"), "hostBinding.workspaceRootSha256"
    )
    capabilities = value.get("capabilities")
    if (
        value.get("contract") != ACTION_HOST_BINDING_CONTRACT
        or value.get("hostType") != "chatty-cli"
        or not isinstance(capabilities, list)
        or not capabilities
        or len(capabilities) > _ACTION_HOST_CAPABILITY_LIMIT
        or any(not isinstance(item, str) or not item for item in capabilities)
        or capabilities != sorted(set(capabilities))
        or any(not _SAFE_ID.fullmatch(item) for item in capabilities)
        or (required_capability is not None and required_capability not in capabilities)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_HOST_BINDING_INVALID",
            "CLI host binding is not canonical or lacks the required capability",
            status=403,
        )
    binding = {
        "contract": ACTION_HOST_BINDING_CONTRACT,
        "hostType": "chatty-cli",
        "hostSessionId": host_session_id,
        "workspaceContextId": workspace_context_id,
        "workspaceRootSha256": workspace_root_sha256,
        "capabilities": list(capabilities),
    }
    return binding, _sha256_value(binding)


def _code_thread_id(project_instance_id: str) -> str:
    project_id = _safe_identifier(project_instance_id, "projectInstanceId")
    digest = hashlib.sha256(
        b"chatty-auto-code-thread/v1\0" + project_id.encode("utf-8")
    ).hexdigest()
    return f"code-auto-{digest}"


def _validate_code_project_binding_payload(
    value: Any,
    *,
    owner_user_id: str,
    project_instance_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    expected_fields = {
        "contract", "ownerId", "runtimePrincipalId", "projectInstanceId",
        "threadId", "projectName", "canonicalRootPath", "projectRecordSha256",
        "projectRevision", "storagePath", "bindingSha256", "issuedAt", "expiresAt",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AutoRuntimeContractError(
            "AUTO_CODE_PROJECT_BINDING_INVALID",
            "Code project binding fields are invalid",
        )
    project_id = _safe_identifier(value.get("projectInstanceId"), "projectInstanceId")
    projected_thread_id = _safe_identifier(value.get("threadId"), "threadId")
    if (
        value.get("contract") != CODE_PROJECT_BINDING_PROJECTION_CONTRACT
        or value.get("ownerId") != owner_user_id
        or value.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or (project_instance_id is not None and project_id != project_instance_id)
        or projected_thread_id != _code_thread_id(project_id)
        or (thread_id is not None and projected_thread_id != thread_id)
        or not isinstance(value.get("projectName"), str)
        or not value.get("projectName")
        or len(value["projectName"]) > 256
        or not isinstance(value.get("canonicalRootPath"), str)
        or not value.get("canonicalRootPath")
        or len(value["canonicalRootPath"]) > 1024
        or not isinstance(value.get("storagePath"), str)
        or not value.get("storagePath")
        or len(value["storagePath"]) > 1024
    ):
        raise AutoRuntimeContractError(
            "AUTO_CODE_PROJECT_BINDING_INVALID",
            "Code project binding owner, project, or thread is invalid",
            status=403,
        )
    _sha256_field(value.get("projectRecordSha256"), "projectRecordSha256")
    _sha256_field(value.get("projectRevision"), "projectRevision")
    binding_basis = {
        key: copy.deepcopy(value[key])
        for key in (
            "contract", "ownerId", "runtimePrincipalId", "projectInstanceId",
            "threadId", "projectName", "canonicalRootPath",
            "projectRecordSha256", "projectRevision", "storagePath",
        )
    }
    if _sha256_field(value.get("bindingSha256"), "bindingSha256") != _sha256_value(binding_basis):
        raise AutoRuntimeContractError(
            "AUTO_CODE_PROJECT_BINDING_INVALID",
            "Code project binding hash does not match its canonical fields",
        )
    issued_at = _parse_iso(value.get("issuedAt"))
    expires_at = _parse_iso(value.get("expiresAt"))
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=MAX_TTL_SECONDS)
        or instant < issued_at - timedelta(seconds=30)
        or instant >= expires_at
    ):
        raise AutoRuntimeContractError(
            "AUTO_CODE_PROJECT_BINDING_EXPIRED",
            "Code project binding lifetime is invalid or expired",
            status=409,
        )
    return copy.deepcopy(value)


def _validate_code_host_binding(
    value: Any,
    *,
    required_capability: str,
    project_binding: dict[str, Any],
    proposal_context_receipt: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    expected_fields = {
        "contract", "hostType", "hostSessionId", "projectInstanceId",
        "projectRevision", "projectRecordSha256", "workspaceContextId",
        "workspaceRootSha256", "capabilities",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_HOST_BINDING_INVALID", "Code host binding fields are invalid"
        )
    host_session_id = _safe_identifier(value.get("hostSessionId"), "hostBinding.hostSessionId")
    project_instance_id = _safe_identifier(
        value.get("projectInstanceId"), "hostBinding.projectInstanceId"
    )
    project_revision = _sha256_field(
        value.get("projectRevision"), "hostBinding.projectRevision"
    )
    project_record_sha256 = _sha256_field(
        value.get("projectRecordSha256"), "hostBinding.projectRecordSha256"
    )
    workspace_context_id = _sha256_field(
        value.get("workspaceContextId"), "hostBinding.workspaceContextId"
    )
    workspace_root_sha256 = _sha256_field(
        value.get("workspaceRootSha256"), "hostBinding.workspaceRootSha256"
    )
    capabilities = value.get("capabilities")
    if (
        value.get("contract") != CODE_HOST_BINDING_CONTRACT
        or value.get("hostType") != "code-ide"
        or not isinstance(capabilities, list)
        or not capabilities
        or len(capabilities) > _ACTION_HOST_CAPABILITY_LIMIT
        or capabilities != sorted(set(capabilities))
        or any(not isinstance(item, str) or not _SAFE_ID.fullmatch(item) for item in capabilities)
        or required_capability not in capabilities
        or project_instance_id != project_binding.get("projectInstanceId")
        or project_revision != project_binding.get("projectRevision")
        or project_record_sha256 != project_binding.get("projectRecordSha256")
        or workspace_context_id != proposal_context_receipt.get("workspaceContextId")
        or workspace_root_sha256 != proposal_context_receipt.get("workspaceRootSha256")
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_HOST_BINDING_INVALID",
            "Code host binding is noncanonical or does not match project proposal evidence",
            status=403,
        )
    binding = {
        "contract": CODE_HOST_BINDING_CONTRACT,
        "hostType": "code-ide",
        "hostSessionId": host_session_id,
        "projectInstanceId": project_instance_id,
        "projectRevision": project_revision,
        "projectRecordSha256": project_record_sha256,
        "workspaceContextId": workspace_context_id,
        "workspaceRootSha256": workspace_root_sha256,
        "capabilities": list(capabilities),
    }
    return binding, _sha256_value(binding)


def _thread_title(thread_id: str) -> str:
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/chatty/threads/{digest}.jsonl"


def _receipt_storage_path(owner_user_id: str, thread_id: str, turn_id: str) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    turn_hash = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"threads/{thread_hash}/receipts/{turn_hash}.json"
    )


def _hydro_lifecycle_title(thread_id: str) -> str:
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/hydro/threads/{digest}.jsonl"


def _hydro_receipt_storage_path(
    owner_user_id: str, thread_id: str, event_id: str
) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    event_hash = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"hydro/threads/{thread_hash}/receipts/{event_hash}.json"
    )


def _hydro_receipt_prefix(owner_user_id: str, thread_id: str) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"hydro/threads/{thread_hash}/receipts/"
    )


def _hydro_quarantine_storage_path(
    owner_user_id: str, thread_id: str, event_sha256: str
) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"hydro/threads/{thread_hash}/quarantine/{event_sha256}.json"
    )


def _hydro_authority_storage_path(
    owner_user_id: str, thread_id: str, record_type: str, record_id: str
) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    record_hash = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"hydro/threads/{thread_hash}/{record_type}/{record_hash}.json"
    )


def _action_authority_storage_path(
    owner_user_id: str, thread_id: str, record_type: str, record_id: str
) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    record_hash = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"threads/{thread_hash}/actions/{record_type}/{record_hash}.json"
    )


def _action_authority_prefix(
    owner_user_id: str, thread_id: str, record_type: str
) -> str:
    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    thread_hash = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return (
        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/{owner_hash}/"
        f"threads/{thread_hash}/actions/{record_type}/"
    )


def _action_event_record_type(action_id: str) -> str:
    return f"{hashlib.sha256(action_id.encode('utf-8')).hexdigest()}/events"


def _hydro_event_state(event_type: str) -> tuple[str, str]:
    if event_type in _HYDRO_GRAPH_TYPES:
        return "graph", _HYDRO_GRAPH_TYPES[event_type]
    if event_type in _HYDRO_WORKER_TYPES:
        return "worker", _HYDRO_WORKER_TYPES[event_type]
    raise AutoRuntimeContractError(
        "AUTO_HYDRO_EVENT_INVALID",
        "Hydro lifecycle event type is unsupported",
    )


def _validate_hydro_authorization(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "turnId", "exchangePayloadSha256", "receiptSha256"
    }:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro authorization fields are invalid"
        )
    return {
        "turnId": _safe_identifier(value.get("turnId"), "authorization.turnId"),
        "exchangePayloadSha256": _sha256_field(
            value.get("exchangePayloadSha256"), "authorization.exchangePayloadSha256"
        ),
        "receiptSha256": _sha256_field(
            value.get("receiptSha256"), "authorization.receiptSha256"
        ),
    }


def _validate_hydro_event(value: Any, *, thread_id: str) -> dict[str, Any]:
    expected_fields = {
        "contract", "eventId", "parentEventId", "sequence",
        "runtimePrincipalId", "threadId", "graphId", "executionId", "type",
        "graphRevision", "graphSha256", "profileEvidence", "rulesetEvidence",
        "authorization", "payload", "payloadSha256", "idempotencyKey",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro lifecycle event fields are invalid"
        )
    event_id = _safe_identifier(value.get("eventId"), "event.eventId")
    parent_event_id = value.get("parentEventId")
    if parent_event_id is not None:
        parent_event_id = _safe_identifier(parent_event_id, "event.parentEventId")
    graph_id = _safe_identifier(value.get("graphId"), "event.graphId")
    execution_id = value.get("executionId")
    if execution_id is not None:
        execution_id = _safe_identifier(execution_id, "event.executionId")
    event_type = str(value.get("type") or "")
    state_kind, state = _hydro_event_state(event_type)
    sequence = value.get("sequence")
    graph_revision = value.get("graphRevision")
    if (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
        or isinstance(graph_revision, bool)
        or not isinstance(graph_revision, int)
        or graph_revision < 1
    ):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro sequence and graph revision must be positive integers"
        )
    profile = value.get("profileEvidence")
    if not isinstance(profile, dict) or (
        profile.get("revision") != AUTO_PROFILE_REVISION
        or profile.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
        or profile.get("verificationState") != "canonical_verified"
    ):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro profile evidence is not canonical AUTO 1.0.0"
        )
    ruleset = value.get("rulesetEvidence")
    if not isinstance(ruleset, dict) or set(ruleset) != {
        "version", "revision", "sha256"
    }:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro ruleset evidence is invalid"
        )
    for field in ("version", "revision"):
        text = str(ruleset.get(field) or "")
        if not text or len(text) > 256 or "\x00" in text:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", f"Hydro ruleset {field} is invalid"
            )
    _sha256_field(ruleset.get("sha256"), "event.rulesetEvidence.sha256")
    current_ruleset = {
        "version": AUTO_ACTIVE_RULESET_VERSION,
        "revision": AUTO_ACTIVE_RULESET_REVISION,
        "sha256": AUTO_ACTIVE_RULESET_SHA256,
    }
    if ruleset != current_ruleset and ruleset not in _LEGACY_HYDRO_RULESETS:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID",
            "Hydro ruleset evidence is not a recognized immutable executable ruleset",
        )
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro lifecycle payload must be an object"
        )
    payload_sha256 = _sha256_field(value.get("payloadSha256"), "event.payloadSha256")
    if payload_sha256 != _sha256_value(payload):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro payload hash does not match canonical bytes"
        )
    authorization = _validate_hydro_authorization(value.get("authorization"))
    if state == "proposed":
        if authorization is not None or execution_id is not None:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "proposed graph events cannot carry authorization or execution IDs"
            )
        active_graph = payload.get("graph")
        instances = active_graph.get("instances") if isinstance(active_graph, dict) else None
        active_graph_body = copy.deepcopy(active_graph) if isinstance(active_graph, dict) else {}
        declared_graph_sha256 = active_graph_body.pop("canonicalSha256", None)
        if (
            not isinstance(active_graph, dict)
            or active_graph.get("contract") != "chatty-auto-hydro-task-graph/v1"
            or active_graph.get("graphId") != graph_id
            or active_graph.get("threadId") != thread_id
            or active_graph.get("revision") != graph_revision
            or active_graph.get("canonicalSha256") != value.get("graphSha256")
            or declared_graph_sha256 != _sha256_value(active_graph_body)
            or active_graph.get("profile") != {
                "revision": profile.get("revision"),
                "combinedSha256": profile.get("combinedSha256"),
            }
            or active_graph.get("ruleset") != ruleset
            or not isinstance(instances, list)
            or len(instances) > MAX_HYDRO_INSTANCES
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "proposed task graph is inconsistent or oversized"
            )
        execution_ids: list[str] = []
        for index, instance in enumerate(instances):
            if not isinstance(instance, dict):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_EVENT_INVALID", "proposed task graph instance is invalid"
                )
            execution_ids.append(
                _safe_identifier(instance.get("executionId"), f"graph.instances[{index}].executionId")
            )
            _safe_identifier(instance.get("workerId"), f"graph.instances[{index}].workerId")
        if len(set(execution_ids)) != len(execution_ids):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "proposed task graph execution IDs must be unique"
            )
    elif state == "rejected":
        rejected_graph_ids = payload.get("rejectedGraphIds")
        if (
            authorization is not None
            or execution_id is not None
            or not isinstance(rejected_graph_ids, list)
            or not rejected_graph_ids
            or len(rejected_graph_ids) > MAX_HYDRO_INSTANCES
            or graph_id not in rejected_graph_ids
            or not isinstance(payload.get("rejectionEvidence"), dict)
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID",
                "rejected graph event requires bounded rejection evidence without dispatcher authorization",
            )
        for index, rejected_id in enumerate(rejected_graph_ids):
            _safe_identifier(rejected_id, f"event.payload.rejectedGraphIds[{index}]")
        if len(set(rejected_graph_ids)) != len(rejected_graph_ids):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "rejected graph IDs must be unique"
            )
    elif authorization is None:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "post-proposal Hydro events require authorization evidence"
        )
    if state_kind == "graph" and state in (_HYDRO_GRAPH_TERMINAL - {"rejected"}):
        aggregate = payload.get("aggregate")
        if not isinstance(aggregate, dict):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "terminal graph events require a structured aggregate"
            )
        aggregate_body = copy.deepcopy(aggregate)
        aggregate_sha256 = aggregate_body.pop("aggregateSha256", None)
        if (
            aggregate.get("contract") != "chatty-auto-hydro-aggregate/v1"
            or aggregate.get("graphId") != graph_id
            or aggregate.get("graphCanonicalSha256") != value.get("graphSha256")
            or aggregate.get("status") != state
            or not isinstance(aggregate_sha256, str)
            or aggregate_sha256 != _sha256_value(aggregate_body)
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "terminal graph aggregate is inconsistent"
            )
    if state_kind == "graph" and execution_id is not None:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "graph lifecycle events cannot carry an execution ID"
        )
    if state_kind == "worker" and execution_id is None:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "worker lifecycle events require an execution ID"
        )
    normalized = copy.deepcopy(value)
    normalized.update({
        "eventId": event_id,
        "parentEventId": parent_event_id,
        "threadId": thread_id,
        "graphId": graph_id,
        "executionId": execution_id,
        "authorization": authorization,
    })
    if (
        value.get("contract") != HYDRO_LIFECYCLE_EVENT_CONTRACT
        or value.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or value.get("threadId") != thread_id
    ):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro event contract, runtime, or thread binding is invalid"
        )
    _sha256_field(value.get("graphSha256"), "event.graphSha256")
    _safe_identifier(value.get("idempotencyKey"), "event.idempotencyKey")
    if len(_canonical_bytes(normalized)) > MAX_HYDRO_EVENT_BYTES:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_INVALID", "Hydro lifecycle event is oversized", status=413
        )
    return normalized


def _hydro_canonical_event(
    *, owner_user_id: str, event: dict[str, Any], event_sha256: str, recorded_at: str
) -> dict[str, Any]:
    return {
        "contract": "chatty-auto-hydro-canonical-lifecycle-event/v1",
        "ownerId": owner_user_id,
        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
        "threadId": event["threadId"],
        "graphId": event["graphId"],
        "eventId": event["eventId"],
        "eventSha256": event_sha256,
        "recordedAt": recorded_at,
        "event": copy.deepcopy(event),
    }


def _parse_hydro_stream(content: str, *, owner_user_id: str, thread_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in content.splitlines():
        if not raw.strip():
            continue
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_INVALID", "canonical Hydro lifecycle is malformed", status=503
            ) from exc
        event = stored.get("event") if isinstance(stored, dict) else None
        if (
            stored.get("contract") != "chatty-auto-hydro-canonical-lifecycle-event/v1"
            or stored.get("ownerId") != owner_user_id
            or stored.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
            or stored.get("threadId") != thread_id
            or not isinstance(event, dict)
            or stored.get("graphId") != event.get("graphId")
            or stored.get("eventId") != event.get("eventId")
            or stored.get("eventSha256") != _sha256_value(event)
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_INVALID", "canonical Hydro lifecycle evidence is inconsistent", status=503
            )
        events.append(stored)
    return events


def _validate_hydro_transition(
    prior_events: list[dict[str, Any]], event: dict[str, Any]
) -> None:
    graph_id = event["graphId"]
    graph_events = [item["event"] for item in prior_events if item["event"]["graphId"] == graph_id]
    latest = graph_events[-1] if graph_events else None
    if any(item["eventId"] == event["eventId"] for item in graph_events):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_EVENT_CONFLICT", "Hydro eventId is already bound", status=409
        )
    if event["sequence"] != (int((latest or {}).get("sequence") or 0) + 1):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_SEQUENCE_CONFLICT", "Hydro event sequence is not contiguous", status=409
        )
    if event["parentEventId"] != ((latest or {}).get("eventId") if latest else None):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_SEQUENCE_CONFLICT", "Hydro parent event does not match canonical head", status=409
        )
    if latest:
        for field in ("graphRevision", "graphSha256", "profileEvidence", "rulesetEvidence"):
            if _canonical_bytes(event[field]) != _canonical_bytes(latest[field]):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_GRAPH_CHANGED", f"Hydro {field} changed after proposal", status=409
                )
        if latest.get("authorization") is not None and (
            _canonical_bytes(event.get("authorization"))
            != _canonical_bytes(latest.get("authorization"))
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_AUTHORIZATION_CHANGED", "Hydro authorization changed after binding", status=409
            )
    active_other = None
    graph_heads: dict[str, dict[str, Any]] = {}
    for stored in prior_events:
        graph_heads[stored["event"]["graphId"]] = stored["event"]
    for other_id, head in graph_heads.items():
        if other_id == graph_id:
            continue
        graph_types = [
            stored["event"] for stored in prior_events
            if stored["event"]["graphId"] == other_id
            and stored["event"]["type"] in _HYDRO_GRAPH_TYPES
        ]
        if graph_types and _HYDRO_GRAPH_TYPES[graph_types[-1]["type"]] not in _HYDRO_GRAPH_TERMINAL:
            active_other = other_id
            break
    kind, state = _hydro_event_state(event["type"])
    if not graph_events and (kind != "graph" or state != "proposed"):
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_TRANSITION_CONFLICT", "Hydro graph must begin with proposed", status=409
        )
    if not graph_events and active_other:
        raise AutoRuntimeContractError(
            "AUTO_HYDRO_ACTIVE_GRAPH_CONFLICT", "another Hydro graph is active for this owner/thread", status=409
        )
    graph_state = None
    worker_states: dict[str, str] = {}
    proposed_graph = None
    for prior in graph_events:
        prior_kind, prior_state = _hydro_event_state(prior["type"])
        if prior_kind == "graph":
            graph_state = prior_state
            if prior_state == "proposed":
                proposed_graph = prior["payload"].get("graph")
        else:
            worker_states[str(prior["executionId"])] = prior_state
    if kind == "graph":
        allowed = _HYDRO_GRAPH_TRANSITIONS.get(graph_state, set())
        if state not in allowed:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_TRANSITION_CONFLICT", "Hydro graph transition is invalid", status=409
            )
    else:
        if graph_state not in {"dispatch_accepted", "running"}:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_TRANSITION_CONFLICT", "worker transition is invalid for graph state", status=409
            )
        prior_state = worker_states.get(str(event["executionId"]))
        instances = (
            proposed_graph.get("instances")
            if isinstance(proposed_graph, dict)
            else None
        )
        instance = next(
            (
                item for item in (instances or [])
                if isinstance(item, dict)
                and item.get("executionId") == event["executionId"]
            ),
            None,
        )
        if instance is None:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EXECUTION_UNAUTHORIZED",
                "worker execution is not present in the approved task graph",
                status=409,
            )
        if prior_state in _HYDRO_WORKER_TERMINAL:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_TERMINAL_CONFLICT", "first verified worker terminal state already won", status=409
            )
        if state not in _HYDRO_WORKER_TRANSITIONS.get(prior_state, set()):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_TRANSITION_CONFLICT", "Hydro worker transition is invalid", status=409
            )
        if state in _HYDRO_WORKER_TERMINAL:
            receipt = event["payload"].get("receipt")
            expected_status = {
                "completed": "completed",
                "failed": "failed",
                "timed_out": "timed_out",
                "cancelled": "cancelled",
                "unknown": "unknown",
            }[state]
            if (
                not isinstance(receipt, dict)
                or receipt.get("contract") != "chatty-auto-hydro-worker-result/v1"
                or receipt.get("graphId") != graph_id
                or receipt.get("executionId") != event["executionId"]
                or receipt.get("workerId") != instance.get("workerId")
                or receipt.get("phase") != instance.get("phase")
                or receipt.get("rosterIndex") != instance.get("rosterIndex")
                or receipt.get("retryOrdinal", 0) != instance.get("retryOrdinal", 0)
                or receipt.get("status") != expected_status
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_CHILD_RECEIPT_INVALID",
                    "terminal child receipt does not match its approved graph execution",
                    status=409,
                )
            entry_index = instance.get("entryIndex")
            roster_entries = proposed_graph.get("rosterEntries") or []
            descriptor = (
                roster_entries[entry_index].get("descriptor")
                if isinstance(entry_index, int)
                and 0 <= entry_index < len(roster_entries)
                and isinstance(roster_entries[entry_index], dict)
                else None
            )
            child_invocation = receipt.get("childInvocation")
            if child_invocation is not None and (
                not isinstance(child_invocation, dict)
                or not isinstance(descriptor, dict)
                or child_invocation.get("provider") != descriptor.get("provider")
                or child_invocation.get("model") != descriptor.get("model")
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_CHILD_RECEIPT_INVALID",
                    "child provider/model attribution does not match approved disclosure",
                    status=409,
                )
            if (
                state == "completed"
                and isinstance(descriptor, dict)
                and descriptor.get("provider") is not None
                and child_invocation is None
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_CHILD_RECEIPT_INVALID",
                    "completed model child receipt lacks exact provider/model attribution",
                    status=409,
                )


def _project_hydro(events: list[dict[str, Any]], revision: str) -> dict[str, Any]:
    graph_heads: OrderedDict[str, dict[str, Any]] = OrderedDict()
    graph_states: dict[str, str] = {}
    terminal_receipts: dict[str, OrderedDict[str, dict[str, Any]]] = {}
    worker_heads: dict[str, OrderedDict[str, dict[str, Any]]] = {}
    latest_aggregate: dict[str, Any] | None = None
    for stored in events:
        event = stored["event"]
        graph_id = event["graphId"]
        graph_heads[graph_id] = event
        kind, state = _hydro_event_state(event["type"])
        if kind == "graph":
            graph_states[graph_id] = state
            aggregate = event["payload"].get("aggregate")
            if isinstance(aggregate, dict):
                latest_aggregate = copy.deepcopy(aggregate)
        else:
            graph_workers = worker_heads.setdefault(graph_id, OrderedDict())
            graph_workers[str(event["executionId"])] = {
                "event": event,
                "eventSha256": stored["eventSha256"],
                "recordedAt": stored["recordedAt"],
            }
            if state in _HYDRO_WORKER_TERMINAL:
                receipt = event["payload"].get("workerResult") or event["payload"].get("receipt")
                if isinstance(receipt, dict):
                    graph_receipts = terminal_receipts.setdefault(graph_id, OrderedDict())
                    graph_receipts[str(event["executionId"])] = copy.deepcopy(receipt)
    active_id = next(
        (
            graph_id for graph_id in reversed(graph_heads)
            if graph_states.get(graph_id) not in _HYDRO_GRAPH_TERMINAL
        ),
        None,
    )
    def runtime_state(graph_id: str) -> dict[str, Any]:
        graph_head = graph_heads[graph_id]
        proposed = next(
            stored["event"] for stored in events
            if stored["event"]["graphId"] == graph_id
            and stored["event"]["type"] == "graph.proposed"
        )
        instances = proposed["payload"]["graph"].get("instances") or []
        graph_worker_heads = worker_heads.get(graph_id, OrderedDict())
        worker_states = []
        for instance in instances[:MAX_HYDRO_INSTANCES]:
            execution_id = str(instance.get("executionId") or "")
            worker_head = graph_worker_heads.get(execution_id)
            if worker_head is None:
                worker_states.append({
                    "executionId": execution_id,
                    "workerId": str(instance.get("workerId") or ""),
                    "state": "planned",
                    "eventId": None,
                    "sequence": None,
                    "eventSha256": None,
                    "updatedAt": None,
                })
                continue
            worker_event = worker_head["event"]
            _kind, worker_state = _hydro_event_state(worker_event["type"])
            worker_states.append({
                "executionId": execution_id,
                "workerId": str(instance.get("workerId") or ""),
                "state": worker_state,
                "eventId": worker_event["eventId"],
                "sequence": worker_event["sequence"],
                "eventSha256": worker_head["eventSha256"],
                "updatedAt": worker_head["recordedAt"],
            })
        return {
            "contract": "chatty-auto-hydro-runtime-state/v1",
            "activeGraph": copy.deepcopy(proposed["payload"]["graph"]),
            "graphState": graph_states.get(graph_id),
            "authorization": copy.deepcopy(graph_head.get("authorization")),
            "rejectedGraphIds": copy.deepcopy(
                graph_head["payload"].get(
                    "rejectedGraphIds",
                    proposed["payload"].get("rejectedGraphIds", []),
                )
            ),
            "terminalChildReceipts": list(
                terminal_receipts.get(graph_id, OrderedDict()).values()
            )[:MAX_HYDRO_INSTANCES],
            "workerStates": worker_states,
            "latestAggregate": copy.deepcopy(graph_head["payload"].get("aggregate")),
        }
    active_graph = runtime_state(active_id) if active_id is not None else None
    latest_id = next(reversed(graph_heads), None) if graph_heads else None
    latest_graph = runtime_state(latest_id) if latest_id is not None else None
    return {
        "revision": revision,
        "lifecycleHead": (
            {
                "eventId": events[-1]["event"]["eventId"],
                "sequence": events[-1]["event"]["sequence"],
                "eventSha256": events[-1]["eventSha256"],
            }
            if events
            else None
        ),
        "activeGraph": active_graph,
        "latestGraph": latest_graph,
        "latestAggregate": latest_aggregate,
    }


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row or {})


def _fetchone(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    return _row_dict(row) if row else None


def _validate_stored_exchange_event(
    event: Any, *, transcript_title: str
) -> dict[str, Any]:
    """Validate opaque Core state before it can re-enter a signed projection."""
    if not isinstance(event, dict) or event.get("contract") != "chatty-auto-canonical-exchange/v1":
        raise AutoRuntimeContractError(
            "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
            "latest AUTO transcript event is invalid",
            status=503,
        )
    thread_id = str(event.get("threadId") or "")
    result = event.get("result")
    profile = event.get("profileEvidence")
    ruleset = event.get("rulesetEvidence")
    dialogue_revision = str(event.get("dialogueRevision") or "")
    decision_context = event.get("decisionContext")
    if (
        not _SAFE_ID.fullmatch(thread_id)
        or _thread_title(thread_id) != transcript_title
        or event.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or not isinstance(result, dict)
        or event.get("resultSha256") != _sha256_value(result)
        or result.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or result.get("intrinsicIdentity") is not False
        or result.get("provider") is not None
        or result.get("model") is not None
        or result.get("output") != event.get("output")
        or _canonical_bytes(result.get("profile")) != _canonical_bytes(profile)
        or _canonical_bytes(result.get("ruleset")) != _canonical_bytes(ruleset)
        or (
            decision_context is not None
            and (
                not isinstance(decision_context, dict)
                or decision_context.get("contract") != DECISION_CONTEXT_CONTRACT
                or decision_context.get("threadId") != thread_id
                or decision_context.get("turnId") != event.get("turnId")
                or decision_context.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
                or decision_context.get("externalEvidenceSha256")
                != _sha256_value(decision_context.get("externalEvidence"))
                or _canonical_bytes(decision_context.get("profileEvidence"))
                != _canonical_bytes(profile)
                or _canonical_bytes(decision_context.get("rulesetEvidence"))
                != _canonical_bytes(ruleset)
            )
        )
    ):
        raise AutoRuntimeContractError(
            "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
            "latest AUTO transcript event evidence is inconsistent",
            status=503,
        )
    dialogue_state = result.get("dialogueState")
    if (
        not isinstance(dialogue_state, dict)
        or dialogue_state.get("threadId") != thread_id
        or str(dialogue_state.get("revision")) != dialogue_revision
    ):
        raise AutoRuntimeContractError(
            "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
            "latest AUTO dialogue evidence is inconsistent",
            status=503,
        )
    return event


def _validate_decision_context(
    value: Any,
    *,
    owner_user_id: str,
    thread_id: str,
    turn_id: str,
    profile_evidence: dict[str, Any],
    ruleset_evidence: dict[str, Any],
    interaction_policy: dict[str, Any],
    hydro_delegation: dict[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "contract", "ownerId", "runtimePrincipalId", "threadId", "turnId",
        "contextEvidenceRevision", "sourceRevisions", "kernelEvidenceRevision",
        "knowledgeReferences", "externalEvidence", "externalEvidenceSha256", "profileEvidence",
        "rulesetEvidence", "interactionPolicy", "hydroDelegation",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision context fields are invalid"
        )
    source_revisions = value.get("sourceRevisions")
    base_revision_fields = {
        "profile", "transcript", "knowledge", "accountContext",
        "revisionVector", "knowledgeSelection",
    }
    if (
        not isinstance(source_revisions, dict)
        or set(source_revisions) not in (
            base_revision_fields,
            base_revision_fields | {"actionAuthority"},
        )
    ):
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision source revisions are invalid"
        )
    for field in source_revisions:
        _sha256_field(source_revisions.get(field), f"sourceRevisions.{field}")
    revision_components = {
        field: source_revisions[field]
        for field in ("profile", "transcript", "knowledge", "accountContext")
    }
    if "actionAuthority" in source_revisions:
        revision_components["actionAuthority"] = source_revisions["actionAuthority"]
    evidence = value.get("externalEvidence")
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_ITEMS:
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision evidence is invalid"
        )
    evidence_chars = 0
    for item in evidence:
        reference = item.get("reference") if isinstance(item, dict) else None
        if not isinstance(reference, dict):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "decision evidence reference is invalid"
            )
        available = reference.get("available") is True
        content = item.get("content")
        if (
            reference.get("contract") != "chatty-auto-evidence-reference/v1"
            or (available and not isinstance(content, str))
            or (not available and content is not None)
            or (
                available
                and reference.get("contentHash")
                != _sha256_bytes(content.encode("utf-8"))
            )
        ):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "decision evidence is inconsistent"
            )
        evidence_chars += len(content or "")
    if evidence_chars > MAX_EVIDENCE_CHARS:
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision evidence is oversized"
        )
    knowledge_references = value.get("knowledgeReferences")
    if (
        not isinstance(knowledge_references, list)
        or len(knowledge_references) > MAX_EVIDENCE_ITEMS
    ):
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision knowledge references are invalid"
        )
    expected_context_revision = _sha256_value(
        {
            "ownerId": owner_user_id,
            "threadId": thread_id,
            "sourceRevisions": source_revisions,
            "evidence": [item["reference"] for item in evidence],
            "knowledgeReferences": knowledge_references,
        }
    )
    if (
        value.get("contract") != DECISION_CONTEXT_CONTRACT
        or value.get("ownerId") != owner_user_id
        or value.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or value.get("threadId") != thread_id
        or value.get("turnId") != turn_id
        or _sha256_field(value.get("contextEvidenceRevision"), "contextEvidenceRevision")
        != expected_context_revision
        or _sha256_field(value.get("kernelEvidenceRevision"), "kernelEvidenceRevision")
        != _sha256_value(evidence)
        or value.get("externalEvidenceSha256") != _sha256_value(evidence)
        or source_revisions.get("revisionVector") != _sha256_value(revision_components)
        or _canonical_bytes(value.get("profileEvidence"))
        != _canonical_bytes(profile_evidence)
        or _canonical_bytes(value.get("rulesetEvidence"))
        != _canonical_bytes(ruleset_evidence)
        or _canonical_bytes(value.get("interactionPolicy"))
        != _canonical_bytes(interaction_policy)
        or _canonical_bytes(value.get("hydroDelegation"))
        != _canonical_bytes(hydro_delegation)
    ):
        raise AutoRuntimeContractError(
            "AUTO_RUNTIME_INVALID_REQUEST", "decision context is inconsistent"
        )
    return copy.deepcopy(value)


def _continuation_basis(
    *,
    owner_user_id: str,
    thread_id: str,
    transcript: dict[str, Any],
    source_revisions: dict[str, str],
) -> dict[str, Any] | None:
    latest = transcript.get("latestEvent")
    if not isinstance(latest, dict):
        return None
    decision = latest.get("decisionContext")
    result = latest.get("result")
    if not isinstance(decision, dict) or not isinstance(result, dict):
        return None
    receipt = transcript.get("latestReceipt")
    if (
        not isinstance(receipt, dict)
        or receipt.get("contract") != EXCHANGE_RECEIPT_CONTRACT
        or receipt.get("ownerId") != owner_user_id
        or receipt.get("threadId") != thread_id
        or receipt.get("turnId") != latest.get("turnId")
        or receipt.get("payloadSha256") != _sha256_value(latest)
        or receipt.get("transcriptSha256") != transcript.get("sha256")
    ):
        return None
    if (
        decision.get("ownerId") != owner_user_id
        or decision.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID
        or decision.get("threadId") != thread_id
        or decision.get("turnId") != latest.get("turnId")
    ):
        return None
    prior = decision.get("sourceRevisions")
    if not isinstance(prior, dict):
        return None
    for field in ("profile", "knowledge", "accountContext", "knowledgeSelection"):
        if prior.get(field) != source_revisions.get(field):
            return None
    if (
        "actionAuthority" in prior
        and prior.get("actionAuthority") != source_revisions.get("actionAuthority")
    ):
        return None
    transcript_sha = str(transcript.get("sha256") or "")
    result_sha = _sha256_value(result)
    return {
        "contract": CONTINUATION_BASIS_CONTRACT,
        "ownerId": owner_user_id,
        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
        "threadId": thread_id,
        "priorTurnId": latest.get("turnId"),
        "priorResultSha256": result_sha,
        "latestResultSha256": result_sha,
        "priorExchangePayloadSha256": receipt.get("payloadSha256"),
        "priorContextEvidenceRevision": decision.get("contextEvidenceRevision"),
        "priorSourceRevisions": copy.deepcopy(prior),
        "transcriptRevisionAfterAppend": transcript_sha,
        "kernelEvidenceRevision": decision.get("kernelEvidenceRevision"),
        "externalEvidence": copy.deepcopy(decision.get("externalEvidence") or []),
        "externalEvidenceSha256": decision.get("externalEvidenceSha256"),
        "selfAppendOnly": transcript_sha == source_revisions.get("transcript"),
    }


def _project_action_lifecycle(
    repository: "AutoRuntimeRepository",
    *,
    owner_user_id: str,
    thread_id: str,
    revision: str,
    timeout_ms: int,
) -> dict[str, Any]:
    grants = repository.list_action_authority_records(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        record_type="grants",
        timeout_ms=timeout_ms,
    )
    if not grants:
        return {"revision": revision, "activeAction": None}
    grants.sort(
        key=lambda item: (
            str(item.get("recordedAt") or ""),
            str(item.get("actionId") or ""),
        )
    )
    grant_record = grants[-1]
    execution_grant = grant_record.get("executionGrant")
    supported_pair = (
        grant_record.get("contract") == "chatty-auto-host-action-grant-record/v2"
        and isinstance(execution_grant, dict)
        and execution_grant.get("contract") == ACTION_EXECUTION_GRANT_CONTRACT
    ) or (
        grant_record.get("contract") == "chatty-auto-host-action-grant-record/v3"
        and isinstance(execution_grant, dict)
        and execution_grant.get("contract") == CODE_ACTION_EXECUTION_GRANT_CONTRACT
    )
    if not supported_pair:
        raise AutoRuntimeContractError(
            "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
            "latest AUTO action grant is malformed or unsupported",
            status=503,
        )
    descriptor = _validate_host_action(
        execution_grant.get("action"),
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        require_approval=True,
        allow_legacy_readback=True,
    )
    approval_event = repository.read_exchange_event(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        turn_id=descriptor["approvalTurnId"],
        timeout_ms=timeout_ms,
    )
    if (
        not isinstance(approval_event, dict)
        or _canonical_bytes(approval_event.get("hostAction"))
        != _canonical_bytes(descriptor)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
            "latest AUTO action does not match its canonical approval exchange",
            status=503,
        )
    events = repository.list_action_authority_records(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        record_type=_action_event_record_type(descriptor["actionId"]),
        timeout_ms=timeout_ms,
    )
    if not events:
        return {"revision": revision, "activeAction": None}
    states = [str(item.get("state") or "") for item in events]
    if (
        len(events) > 3
        or states[:1] != ["authorization_verified"]
        or (len(states) >= 2 and states[1] != "started")
        or (len(states) == 3 and states[2] not in _ACTION_TERMINAL_STATES)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
            "AUTO action lifecycle is not a valid linear transition",
            status=503,
        )
    latest = events[-1]
    event = latest.get("event")
    state = states[-1]
    if (
        latest.get("contract") != "chatty-auto-host-action-event-record/v2"
        or latest.get("actionId") != descriptor["actionId"]
        or latest.get("actionCanonicalSha256") != descriptor["canonicalSha256"]
        or latest.get("hostBindingSha256") != execution_grant.get("hostBindingSha256")
        or not isinstance(event, dict)
        or event.get("state") != state
        or latest.get("eventSha256") != _sha256_value(event)
    ):
        raise AutoRuntimeContractError(
            "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
            "latest AUTO action lifecycle evidence is inconsistent",
            status=503,
        )
    return {
        "revision": revision,
        "activeAction": {
            "descriptor": copy.deepcopy(descriptor),
            "state": state,
            "event": copy.deepcopy(event),
            "restoredState": "unknown" if state == "started" else state,
        },
    }


class AutoRuntimeRepository(Protocol):
    def registration_preflight(self) -> dict[str, Any]: ...
    def register_profile(self, *, idempotency_key: str, registered_at: str) -> dict[str, Any]: ...
    def load_canonical_profile(self, *, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any]: ...
    def source_revisions(self, *, owner_user_id: str, transcript_title: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, str]: ...
    def context_snapshot(self, *, owner_user_id: str, thread_id: str, transcript_title: str, memory_limit: int, character_budget: int, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any]: ...
    def revision_snapshot(self, *, owner_user_id: str, thread_id: str, transcript_title: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, str]: ...
    def read_thread(self, *, owner_user_id: str, transcript_title: str, memory_limit: int, character_budget: int, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any]: ...
    def read_exchange_receipt(self, *, owner_user_id: str, thread_id: str, turn_id: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any] | None: ...
    def read_exchange_event(self, *, owner_user_id: str, thread_id: str, turn_id: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any] | None: ...
    def append_exchange(self, *, owner_user_id: str, transcript_title: str, thread_id: str, turn_id: str, event_bytes: bytes, payload_sha256: str, appended_at: str, expected_transcript_sha256: str | None = None) -> dict[str, Any]: ...
    def read_hydro_lifecycle(self, *, owner_user_id: str, thread_id: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any]: ...
    def append_hydro_event(self, *, owner_user_id: str, thread_id: str, event: dict[str, Any], event_sha256: str, recorded_at: str) -> dict[str, Any]: ...
    def quarantine_hydro_event(self, *, owner_user_id: str, thread_id: str, event: dict[str, Any], event_sha256: str, reason_code: str, lifecycle_sha256: str, quarantined_at: str) -> dict[str, Any]: ...
    def list_auto_threads(self, *, owner_user_id: str, limit: int, offset: int, timeout_ms: int = CONTEXT_DEADLINE_MS) -> list[dict[str, Any]]: ...
    def store_hydro_authority_record(self, *, owner_user_id: str, thread_id: str, record_type: str, record_id: str, request_sha256: str, record: dict[str, Any], recorded_at: str) -> dict[str, Any]: ...
    def read_hydro_authority_record(self, *, owner_user_id: str, thread_id: str, record_type: str, record_id: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any] | None: ...
    def list_hydro_recovery_records(self, *, limit: int, offset: int, timeout_ms: int = CONTEXT_DEADLINE_MS) -> list[dict[str, Any]]: ...
    def store_action_authority_record(self, *, owner_user_id: str, thread_id: str, record_type: str, record_id: str, request_sha256: str, record: dict[str, Any], recorded_at: str) -> dict[str, Any]: ...
    def read_action_authority_record(self, *, owner_user_id: str, thread_id: str, record_type: str, record_id: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> dict[str, Any] | None: ...
    def list_action_authority_records(self, *, owner_user_id: str, thread_id: str, record_type: str, timeout_ms: int = CONTEXT_DEADLINE_MS) -> list[dict[str, Any]]: ...


def _actual_content_sha256(row: dict[str, Any]) -> str:
    projected = str(row.get("actual_sha256") or "").lower()
    if projected:
        if not _SHA256.fullmatch(projected):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_CONTEXT_UNAVAILABLE",
                "canonical revision digest is malformed",
                status=503,
            )
        return projected
    return _sha256_bytes(_stored_content_bytes(row.get("content")))


def _revision_vector_from_rows(
    *,
    profile_rows: list[dict[str, Any]],
    registration_rows: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    lifecycle_rows: list[dict[str, Any]],
    action_rows: list[dict[str, Any]],
    activation_rows: list[dict[str, Any]],
    knowledge_artifact_rows: list[dict[str, Any]],
    account: dict[str, Any] | None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    activated_references: list[dict[str, Any]] = []
    for row in activation_rows:
        try:
            receipt = json.loads(_stored_content_bytes(row.get("content")).decode("utf-8"))
            reference = receipt["knowledgeReference"]
            artifact_id = str(reference.get("artifact_id") or reference.get("artifactId") or "").strip()
            revision = str(reference.get("revision") or "").strip()
            digest = str(reference.get("sha256") or reference.get("contentHash") or "").strip().lower()
            required = reference.get("required") is not False
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared activation evidence is malformed",
                status=503,
            ) from exc
        if not artifact_id or not revision or not _SHA256.fullmatch(digest):
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared activation evidence is incomplete",
                status=503,
            )
        activated_references.append({
            "artifact_id": artifact_id,
            "revision": revision,
            "sha256": digest,
            "required": required,
        })
    profile_revision = _sha256_value({
        "artifacts": [{
            "path": row.get("storage_path"),
            "declaredSha256": row.get("sha256"),
            "actualSha256": _actual_content_sha256(row),
            "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
            "isSystem": row.get("is_system") is True,
            "updatedAt": str(row.get("updated_at") or ""),
        } for row in profile_rows],
        "registrationReceipts": [{
            "path": row.get("storage_path"),
            "declaredSha256": row.get("sha256"),
            "actualSha256": _actual_content_sha256(row),
            "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
            "isSystem": row.get("is_system") is True,
            "updatedAt": str(row.get("updated_at") or ""),
        } for row in registration_rows],
    })
    knowledge_revision = _sha256_value({
        "activatedReferences": [{
            "artifactId": item["artifact_id"],
            "revision": item["revision"],
            "sha256": item["sha256"],
        } for item in activated_references],
        "activationReceipts": [{
            "declaredSha256": row.get("sha256"),
            "actualSha256": _actual_content_sha256(row),
            "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
            "updatedAt": str(row.get("updated_at") or ""),
        } for row in activation_rows],
        "canonicalArtifacts": [{
            "id": row.get("id"),
            "path": row.get("storage_path"),
            "declaredSha256": row.get("sha256"),
            "actualSha256": _actual_content_sha256(row),
            "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
            "updatedAt": str(row.get("updated_at") or ""),
        } for row in sorted(
            knowledge_artifact_rows,
            key=lambda item: (str(item.get("id") or ""), str(item.get("storage_path") or "")),
        )],
    })
    account_revision = _sha256_value({
        "sha256": account.get("sha256"),
        "actualSha256": _actual_content_sha256(account),
        "metadataSha256": _sha256_value(_metadata(account.get("metadata"))),
        "updatedAt": str(account.get("updated_at") or ""),
    }) if account else _EMPTY_SHA256
    action_revision = _sha256_value([{
        "path": row.get("storage_path"),
        "sha256": row.get("sha256"),
        "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
        "updatedAt": str(row.get("updated_at") or ""),
    } for row in action_rows]) if action_rows else _EMPTY_SHA256
    return ({
        "profile": profile_revision,
        "transcript": str((transcript_rows[0] if transcript_rows else {}).get("source_hash") or _EMPTY_SHA256),
        "hydroLifecycle": str((lifecycle_rows[0] if lifecycle_rows else {}).get("source_hash") or _EMPTY_SHA256),
        "knowledge": knowledge_revision,
        "accountContext": account_revision,
        "actionAuthority": action_revision,
    }, activated_references)


class PostgresAutoRuntimeRepository:
    """OVVAULTS repository with exact owner/title predicates and atomic writes."""

    _ARTIFACTS = (
        ("prompt", "system-runtime/prompt.json", "application/json"),
        ("definition", "system-runtime/definition.json", "application/json"),
        ("conditioning", "system-runtime/conditioning.txt", "text/plain"),
    )

    def __init__(self, *, file_repository: VVaultFileRepository | None = None) -> None:
        self.file_repository = file_repository or VVaultFileRepository()

    def _connect(self, *, timeout_ms: int | None = None):
        return chatty_body_service._connect(
            timeout_seconds=(timeout_ms / 1000) if timeout_ms is not None else None
        )

    @staticmethod
    def _set_statement_timeout(cur: Any, timeout_ms: int) -> None:
        bounded_ms = max(1, min(int(timeout_ms), CONTEXT_STATEMENT_TIMEOUT_MS))
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{bounded_ms}ms",),
        )

    @staticmethod
    def _context_deadline(timeout_ms: int) -> float:
        return monotonic() + (max(1, min(int(timeout_ms), CONTEXT_DEADLINE_MS)) / 1000)

    @classmethod
    def _execute_before_deadline(
        cls,
        cur: Any,
        deadline: float,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> None:
        remaining_ms = int((deadline - monotonic()) * 1000)
        if remaining_ms <= 0:
            raise AutoRuntimeContractError(
                "AUTO_CONTEXT_DEADLINE_EXCEEDED",
                "canonical context exceeded its bounded deadline",
                status=503,
            )
        cls._set_statement_timeout(cur, remaining_ms)
        cur.execute(sql, params)

    def _system_user_id(self) -> str:
        return self.file_repository._system_user_id()

    @classmethod
    def _read_system_user_id(cls, cur: Any, deadline: float) -> str:
        cls._execute_before_deadline(
            cur,
            deadline,
            "SELECT id::text AS id FROM ovvaults.users WHERE lower(email)=lower(%s)",
            (SYSTEM_USER_EMAIL,),
        )
        row = _fetchone(cur)
        system_user_id = str((row or {}).get("id") or "")
        if not system_user_id:
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNAVAILABLE",
                "VVAULT system owner is unavailable",
                status=503,
            )
        return system_user_id

    @staticmethod
    def _artifact_path(relative_path: str) -> str:
        return f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/{relative_path}"

    def _read_profile_rows(
        self, cur: Any, system_user_id: str, *, deadline: float | None = None
    ) -> list[dict[str, Any]]:
        paths = [self._artifact_path(item[1]) for item in self._ARTIFACTS]
        sql = """SELECT id::text AS id,user_id::text AS user_id,storage_path,content,
                      sha256,metadata,is_system,updated_at
                 FROM ovvaults.vault_files
                WHERE user_id=%s AND coalesce(is_system,false)=true
                  AND storage_path = ANY(%s)
                ORDER BY storage_path"""
        if deadline is None:
            cur.execute(sql, (system_user_id, paths))
        else:
            self._execute_before_deadline(cur, deadline, sql, (system_user_id, paths))
        return [_row_dict(row) for row in cur.fetchall()]

    def _read_registration_receipts(
        self, cur: Any, system_user_id: str, *, deadline: float | None = None
    ) -> list[dict[str, Any]]:
        prefix = f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/registrations/"
        sql = """SELECT id::text AS id,object_key,storage_path,content,sha256,metadata,is_system
                 FROM ovvaults.vault_files
                WHERE user_id=%s AND coalesce(is_system,false)=true
                  AND storage_path LIKE %s
                ORDER BY created_at DESC,object_key DESC
                LIMIT 64"""
        params = (system_user_id, f"{prefix}%")
        if deadline is None:
            cur.execute(sql, params)
        else:
            self._execute_before_deadline(cur, deadline, sql, params)
        return [_row_dict(row) for row in cur.fetchall()]

    @staticmethod
    def _registration_receipt_valid(
        row: dict[str, Any], artifact_ids: list[str]
    ) -> bool:
        raw = str(row.get("content") or "")
        if not raw or str(row.get("sha256") or "") != _sha256_bytes(raw.encode("utf-8")):
            return False
        try:
            receipt = json.loads(raw)
        except json.JSONDecodeError:
            return False
        metadata = _metadata(row.get("metadata"))
        return bool(
            isinstance(receipt, dict)
            and receipt.get("contract") == REGISTRATION_RECEIPT_CONTRACT
            and receipt.get("runtimePrincipalId") == AUTO_RUNTIME_PRINCIPAL_ID
            and receipt.get("status") in {"created", "idempotent_readback"}
            and receipt.get("principalType") == "system_runtime"
            and receipt.get("profileRevision") == AUTO_PROFILE_REVISION
            and receipt.get("combinedSha256") == AUTO_PROFILE_COMBINED_SHA256
            and receipt.get("artifactIds") == artifact_ids
            and metadata.get("contract") == REGISTRATION_RECEIPT_CONTRACT
            and metadata.get("combinedSha256") == AUTO_PROFILE_COMBINED_SHA256
            and metadata.get("principalType") == "system_runtime"
            and metadata.get("intrinsicIdentity") is False
            and "provider" in metadata
            and metadata.get("provider") is None
            and "model" in metadata
            and metadata.get("model") is None
            and metadata.get("appendOnly") is True
            and receipt.get("idempotencyKey") == metadata.get("idempotencyKey")
            and receipt.get("receiptSha256") == metadata.get("receiptSha256")
            and row.get("is_system") is True
            and receipt.get("receiptSha256")
            == _sha256_value(
                {key: value for key, value in receipt.items() if key != "receiptSha256"}
            )
        )

    def _validate_registered_profile_rows(
        self,
        rows: list[dict[str, Any]],
        bundled: dict[str, Any],
        *,
        conflict_status: int,
    ) -> list[str]:
        """Verify exact artifact bytes and security metadata after every readback."""
        expected_by_path = {
            self._artifact_path(relative): (
                bundled["canonicalBytes"][name],
                bundled["hashes"][f"{name}Sha256"],
            )
            for name, relative, _content_type in self._ARTIFACTS
        }
        if len(rows) != len(expected_by_path):
            raise AutoRuntimeContractError(
                "AUTO_PROFILE_REGISTRATION_CONFLICT"
                if conflict_status == 409
                else "AUTO_PROFILE_REGISTRATION_FAILED",
                "AUTO canonical profile readback is incomplete",
                status=conflict_status,
            )
        expected_provenance = [
            *copy.deepcopy(bundled["provenanceSources"]),
            *[
                {
                    "sourceId": f"artifact:system-runtime-{artifact_name}",
                    "kind": "canonical_artifact",
                    "revision": AUTO_PROFILE_REVISION,
                    "sha256": bundled["hashes"][f"{artifact_name}Sha256"],
                }
                for artifact_name, _relative, _type in self._ARTIFACTS
            ],
        ]
        for row in rows:
            path = str(row.get("storage_path") or "")
            expected = expected_by_path.get(path)
            has_content = "content" in row
            raw = _stored_content_bytes(row.get("content")) if has_content else b""
            actual_digest = (
                _actual_content_sha256(row)
                if row.get("actual_sha256") is not None
                else _sha256_bytes(raw)
            )
            metadata = _metadata(row.get("metadata"))
            if (
                expected is None
                or (has_content and raw != expected[0])
                or str(row.get("sha256") or "") != expected[1]
                or actual_digest != expected[1]
                or row.get("is_system") is not True
                or metadata.get("profileRevision") != AUTO_PROFILE_REVISION
                or metadata.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
                or metadata.get("principalType") != "system_runtime"
                or metadata.get("intrinsicIdentity") is not False
                or "provider" not in metadata
                or metadata.get("provider") is not None
                or "model" not in metadata
                or metadata.get("model") is not None
                or metadata.get("provenanceSources") != expected_provenance
            ):
                raise AutoRuntimeContractError(
                    "AUTO_PROFILE_REGISTRATION_CONFLICT"
                    if conflict_status == 409
                    else "AUTO_PROFILE_REGISTRATION_FAILED",
                    "AUTO revision 1.0.0 differs from the reviewed profile",
                    status=conflict_status,
                )
        return [str(row["id"]) for row in rows]

    def registration_preflight(self) -> dict[str, Any]:
        """Inspect canonical AUTO registration without acquiring a write lock."""
        bundled = load_bundled_auto_profile()
        with self._connect(timeout_ms=CONTEXT_DEADLINE_MS) as conn:
            with conn.cursor() as cur:
                system_user_id = self._read_system_user_id(
                    cur, self._context_deadline(CONTEXT_DEADLINE_MS)
                )
                rows = self._read_profile_rows(cur, system_user_id)
                receipts = self._read_registration_receipts(cur, system_user_id)

        base = {
            "contract": "chatty-auto-registration-preflight/v1",
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "profileRevision": AUTO_PROFILE_REVISION,
            "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
            "systemOwnerId": system_user_id,
            "artifactCount": len(rows),
            "registrationReceiptCount": len(receipts),
        }
        if not rows and not receipts:
            return {**base, "status": "absent", "safeToRegister": True}
        if len(rows) != len(self._ARTIFACTS):
            return {
                **base,
                "status": "conflict",
                "safeToRegister": False,
                "reasonCode": "AUTO_PROFILE_ROWS_PARTIAL",
            }
        try:
            artifact_ids = self._validate_registered_profile_rows(
                rows, bundled, conflict_status=409
            )
        except AutoRuntimeContractError as exc:
            return {
                **base,
                "status": "conflict",
                "safeToRegister": False,
                "reasonCode": exc.code,
            }
        valid_receipts = [
            row for row in receipts if self._registration_receipt_valid(row, artifact_ids)
        ]
        if not valid_receipts:
            return {
                **base,
                "status": "conflict",
                "safeToRegister": False,
                "artifactIds": artifact_ids,
                "reasonCode": "AUTO_REGISTRATION_RECEIPT_UNVERIFIABLE",
            }
        return {
            **base,
            "status": "registered",
            "safeToRegister": True,
            "artifactIds": artifact_ids,
            "registrationReceiptSha256": [
                str(_metadata(row.get("metadata")).get("receiptSha256") or "")
                for row in valid_receipts
            ],
        }

    def register_profile(self, *, idempotency_key: str, registered_at: str) -> dict[str, Any]:
        bundled = load_bundled_auto_profile()
        _validate_profile_schemas(bundled["prompt"], bundled["definition"])
        system_user_id = self._system_user_id()
        idempotency_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        receipt_path = (
            f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/registrations/"
            f"{idempotency_hash}.json"
        )
        receipt_key = f"system/{receipt_path}"
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        ("system-runtime:auto-001:profile:1.0.0",),
                    )
                    cur.execute(
                        """SELECT id::text AS id,object_key,storage_path,content,sha256,metadata,is_system
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND object_key=%s
                            ORDER BY id FOR UPDATE""",
                        (system_user_id, receipt_key),
                    )
                    existing_receipt_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(existing_receipt_rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_PROFILE_REGISTRATION_CONFLICT",
                            "AUTO registration idempotency authority is ambiguous",
                            status=409,
                        )
                    existing_receipt = (
                        existing_receipt_rows[0] if existing_receipt_rows else None
                    )
                    rows = self._read_profile_rows(cur, system_user_id)
                    profile_preexisting = bool(rows)
                    if rows:
                        artifact_ids = self._validate_registered_profile_rows(
                            rows, bundled, conflict_status=409
                        )
                    else:
                        artifact_ids = []
                    registration_receipts = self._read_registration_receipts(cur, system_user_id)
                    valid_registration_exists = any(
                        self._registration_receipt_valid(row, artifact_ids)
                        for row in registration_receipts
                    )
                    if rows and not valid_registration_exists:
                        raise AutoRuntimeContractError(
                            "AUTO_PROFILE_REGISTRATION_CONFLICT",
                            "AUTO profile rows exist without a valid append-only registration receipt",
                            status=409,
                        )
                    if existing_receipt:
                        metadata = _metadata(existing_receipt.get("metadata"))
                        if (
                            metadata.get("idempotencyKey") != idempotency_key
                            or metadata.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
                            or not self._registration_receipt_valid(existing_receipt, artifact_ids)
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_PROFILE_REGISTRATION_CONFLICT",
                                "AUTO registration idempotency key is bound to different bytes",
                                status=409,
                            )
                        if len(rows) != 3:
                            raise AutoRuntimeContractError(
                                "AUTO_PROFILE_REGISTRATION_CONFLICT",
                                "AUTO registration receipt exists without a complete profile",
                                status=409,
                            )
                        conn.commit()
                        return {
                            "status": "idempotent_readback",
                            "artifactIds": artifact_ids,
                            "registeredAt": str(metadata.get("registeredAt") or registered_at),
                        }
                    if not rows:
                        for name, relative, content_type in self._ARTIFACTS:
                            storage_path = self._artifact_path(relative)
                            raw = bundled["canonicalBytes"][name]
                            content = raw.decode("utf-8")
                            digest = bundled["hashes"][f"{name}Sha256"]
                            provenance_sources = [
                                *copy.deepcopy(bundled["provenanceSources"]),
                                *[
                                    {
                                        "sourceId": f"artifact:system-runtime-{artifact_name}",
                                        "kind": "canonical_artifact",
                                        "revision": AUTO_PROFILE_REVISION,
                                        "sha256": bundled["hashes"][f"{artifact_name}Sha256"],
                                    }
                                    for artifact_name, _relative, _type in self._ARTIFACTS
                                ],
                            ]
                            metadata = {
                                "artifactName": name,
                                "artifactId": f"life.vvault.system-runtime.{name}",
                                "profileContract": AUTO_PROFILE_CONTRACT,
                                "profileRevision": AUTO_PROFILE_REVISION,
                                "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
                                "principalType": "system_runtime",
                                "intrinsicIdentity": False,
                                "provider": None,
                                "model": None,
                                "provenanceSources": provenance_sources,
                            }
                            cur.execute(
                                """INSERT INTO ovvaults.vault_files
                                   (user_id,bucket,object_key,filename,storage_path,
                                    content_type,file_type,size_bytes,sha256,content,
                                    metadata,construct_id,is_system,created_at,updated_at)
                                   VALUES (%s,'vvault-local',%s,%s,%s,%s,%s,%s,%s,%s,
                                           %s::jsonb,%s,true,%s,%s)""",
                                (
                                    system_user_id,
                                    f"system/{storage_path}",
                                    storage_path,
                                    storage_path,
                                    content_type,
                                    "json" if name != "conditioning" else "text",
                                    len(raw),
                                    digest,
                                    content,
                                    json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                                    AUTO_RUNTIME_PRINCIPAL_ID,
                                    registered_at,
                                    registered_at,
                                ),
                            )
                        rows = self._read_profile_rows(cur, system_user_id)
                    artifact_ids = self._validate_registered_profile_rows(
                        rows, bundled, conflict_status=503
                    )
                    registration = {
                        "contract": REGISTRATION_RECEIPT_CONTRACT,
                        "status": "created" if not profile_preexisting else "idempotent_readback",
                        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                        "principalType": "system_runtime",
                        "profileRevision": AUTO_PROFILE_REVISION,
                        "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
                        "idempotencyKey": idempotency_key,
                        "artifactIds": artifact_ids,
                    }
                    registration["receiptSha256"] = _sha256_value(registration)
                    receipt_raw = _canonical_bytes(registration)
                    stored_content_sha = _sha256_bytes(receipt_raw)
                    receipt_metadata = {
                        "contract": REGISTRATION_RECEIPT_CONTRACT,
                        "idempotencyKey": idempotency_key,
                        "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
                        "registeredAt": registered_at,
                        "receiptSha256": registration["receiptSha256"],
                        "principalType": "system_runtime",
                        "intrinsicIdentity": False,
                        "provider": None,
                        "model": None,
                        "appendOnly": True,
                    }
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,
                            content_type,file_type,size_bytes,sha256,content,metadata,
                            construct_id,is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,true,%s,%s)""",
                        (
                            system_user_id,
                            receipt_key,
                            receipt_path,
                            receipt_path,
                            len(receipt_raw),
                            stored_content_sha,
                            receipt_raw.decode("utf-8"),
                            json.dumps(receipt_metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID,
                            registered_at,
                            registered_at,
                        ),
                    )
                    receipt_rows = self._read_registration_receipts(cur, system_user_id)
                    inserted_receipt = next(
                        (
                            row
                            for row in receipt_rows
                            if str(row.get("object_key") or "") == receipt_key
                        ),
                        None,
                    )
                    if not inserted_receipt or not self._registration_receipt_valid(
                        inserted_receipt, artifact_ids
                    ):
                        raise AutoRuntimeContractError(
                            "AUTO_PROFILE_REGISTRATION_FAILED",
                            "AUTO registration receipt readback was unverifiable",
                            status=503,
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "status": "idempotent_readback" if profile_preexisting else "created",
            "artifactIds": [str(row["id"]) for row in rows],
            "registeredAt": registered_at,
        }

    def load_canonical_profile(
        self, *, timeout_ms: int = CONTEXT_DEADLINE_MS
    ) -> dict[str, Any]:
        bundled = load_bundled_auto_profile()
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                system_user_id = self._read_system_user_id(cur, deadline)
                rows = self._read_profile_rows(cur, system_user_id, deadline=deadline)
                registration_receipts = self._read_registration_receipts(
                    cur, system_user_id, deadline=deadline
                )
        if len(rows) != 3:
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNAVAILABLE",
                "AUTO canonical profile is not registered",
                status=503,
            )
        by_path = {str(row.get("storage_path") or ""): row for row in rows}
        artifact_ids = [str(row["id"]) for row in rows]
        if not any(
            self._registration_receipt_valid(receipt, artifact_ids)
            for receipt in registration_receipts
        ):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                "AUTO canonical profile has no valid append-only registration receipt",
                status=503,
            )
        artifacts: dict[str, Any] = {}
        artifact_sources: list[dict[str, str]] = []
        for name, relative, _content_type in self._ARTIFACTS:
            path = self._artifact_path(relative)
            row = by_path.get(path)
            if not row or row.get("is_system") is not True:
                raise AutoRuntimeContractError(
                    "AUTO_CANONICAL_PROFILE_UNAVAILABLE",
                    f"AUTO canonical {name} artifact is missing",
                    status=503,
                )
            raw = str(row.get("content") or "")
            expected = bundled["hashes"][f"{name}Sha256"]
            metadata = _metadata(row.get("metadata"))
            expected_provenance = [
                *copy.deepcopy(bundled["provenanceSources"]),
                *[
                    {
                        "sourceId": f"artifact:system-runtime-{artifact_name}",
                        "kind": "canonical_artifact",
                        "revision": AUTO_PROFILE_REVISION,
                        "sha256": bundled["hashes"][f"{artifact_name}Sha256"],
                    }
                    for artifact_name, _relative, _type in self._ARTIFACTS
                ],
            ]
            if (
                _sha256_bytes(raw.encode("utf-8")) != expected
                or row.get("sha256") != expected
                or metadata.get("profileRevision") != AUTO_PROFILE_REVISION
                or metadata.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
                or metadata.get("principalType") != "system_runtime"
                or metadata.get("intrinsicIdentity") is not False
                or "provider" not in metadata
                or metadata.get("provider") is not None
                or "model" not in metadata
                or metadata.get("model") is not None
                or metadata.get("provenanceSources") != expected_provenance
            ):
                raise AutoRuntimeContractError(
                    "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                    f"AUTO canonical {name} artifact hash is invalid",
                    status=503,
                )
            if name == "conditioning":
                artifacts[name] = raw
            else:
                try:
                    artifacts[name] = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise AutoRuntimeContractError(
                        "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                        f"AUTO canonical {name} artifact is malformed",
                        status=503,
                    ) from exc
            artifact_sources.append(
                {
                    "sourceId": f"artifact:system-runtime-{name}",
                    "kind": "canonical_artifact",
                    "revision": AUTO_PROFILE_REVISION,
                    "sha256": expected,
                }
            )
        # Recompute the combined digest from canonical database bytes, not the template.
        combined = _sha256_value(
            {
                "conditioning": artifacts["conditioning"],
                "definition": artifacts["definition"],
                "prompt": artifacts["prompt"],
            }
        )
        if combined != AUTO_PROFILE_COMBINED_SHA256:
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                "AUTO canonical combined profile hash is invalid",
                status=503,
            )
        _validate_profile_schemas(artifacts["prompt"], artifacts["definition"])
        manifest = bundled["manifest"]
        return {
            "contract": AUTO_PROFILE_CONTRACT,
            "revision": AUTO_PROFILE_REVISION,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "profileType": "non_intrinsic_system_runtime",
            "intrinsicIdentity": False,
            "behaviorAuthority": "chatty-core",
            "canonicalAuthority": "vvault/ovvaults",
            "provider": None,
            "model": None,
            "verificationState": "canonical_verified",
            "canonicalPersistence": True,
            "provenanceSources": [
                *copy.deepcopy(manifest["provenanceSources"]),
                *artifact_sources,
            ],
            "artifacts": artifacts,
            "hashes": copy.deepcopy(bundled["hashes"]),
        }

    @staticmethod
    def _json_rows(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise AutoRuntimeContractError(
                    "AUTO_CANONICAL_CONTEXT_UNAVAILABLE",
                    "canonical context bundle is malformed",
                    status=503,
                ) from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_CONTEXT_UNAVAILABLE",
                "canonical context bundle rows are malformed",
                status=503,
            )
        return [dict(item) for item in value]

    def _profile_from_snapshot_rows(
        self,
        profile_rows: list[dict[str, Any]],
        registration_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bundled = load_bundled_auto_profile()
        artifact_ids = self._validate_registered_profile_rows(
            profile_rows, bundled, conflict_status=503
        )
        if not any(
            self._registration_receipt_valid(receipt, artifact_ids)
            for receipt in registration_rows
        ):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                "AUTO canonical profile has no valid append-only registration receipt",
                status=503,
            )
        by_path = {str(row.get("storage_path") or ""): row for row in profile_rows}
        artifacts: dict[str, Any] = {
            "prompt": copy.deepcopy(bundled["prompt"]),
            "definition": copy.deepcopy(bundled["definition"]),
            "conditioning": bundled["conditioning"],
        }
        artifact_sources: list[dict[str, str]] = []
        for name, relative, _content_type in self._ARTIFACTS:
            if self._artifact_path(relative) not in by_path:
                raise AutoRuntimeContractError(
                    "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                    f"AUTO canonical {name} artifact is unavailable",
                    status=503,
                )
            artifact_sources.append({
                "sourceId": f"artifact:system-runtime-{name}",
                "kind": "canonical_artifact",
                "revision": AUTO_PROFILE_REVISION,
                "sha256": bundled["hashes"][f"{name}Sha256"],
            })
        if _sha256_value({
            "conditioning": artifacts["conditioning"],
            "definition": artifacts["definition"],
            "prompt": artifacts["prompt"],
        }) != AUTO_PROFILE_COMBINED_SHA256:
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNVERIFIABLE",
                "AUTO canonical combined profile hash is invalid",
                status=503,
            )
        _validate_profile_schemas(artifacts["prompt"], artifacts["definition"])
        return {
            "contract": AUTO_PROFILE_CONTRACT,
            "revision": AUTO_PROFILE_REVISION,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "profileType": "non_intrinsic_system_runtime",
            "intrinsicIdentity": False,
            "behaviorAuthority": "chatty-core",
            "canonicalAuthority": "vvault/ovvaults",
            "provider": None,
            "model": None,
            "verificationState": "canonical_verified",
            "canonicalPersistence": True,
            "provenanceSources": [
                *copy.deepcopy(bundled["manifest"]["provenanceSources"]),
                *artifact_sources,
            ],
            "artifacts": artifacts,
            "hashes": copy.deepcopy(bundled["hashes"]),
        }

    @staticmethod
    def _thread_from_snapshot_rows(
        rows: list[dict[str, Any]],
        *,
        transcript_title: str,
        memory_limit: int,
        character_budget: int,
    ) -> dict[str, Any]:
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_TRANSCRIPT_AUTHORITY_CONFLICT",
                "multiple canonical AUTO transcript rows exist for one owner/thread",
                status=503,
            )
        if not rows:
            return {
                "title": transcript_title,
                "sha256": _EMPTY_SHA256,
                "events": [],
                "latestResult": None,
                "dialogueState": None,
                "latestEvent": None,
                "truncated": False,
            }
        row = rows[0]
        content = str(row.get("content") or "")
        lines = content.splitlines()
        truncated = int(row.get("total_chars") or 0) > len(content)
        if truncated and lines:
            lines = lines[1:]
        events = []
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AutoRuntimeContractError(
                    "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
                    "AUTO transcript tail contains malformed canonical evidence",
                    status=503,
                ) from exc
            events.append(_validate_stored_exchange_event(event, transcript_title=transcript_title))
        selected: list[dict[str, Any]] = []
        selected_chars = 0
        if memory_limit:
            for event in reversed(events[-memory_limit:]):
                event_chars = len(_canonical_bytes(event))
                if selected_chars + event_chars <= character_budget:
                    selected.append(event)
                    selected_chars += event_chars
            selected.reverse()
        latest = events[-1] if events else None
        latest_result = latest.get("result") if isinstance(latest, dict) else None
        return {
            "title": transcript_title,
            "sha256": str(row.get("source_hash") or _sha256_bytes(content.encode("utf-8"))),
            "events": selected,
            "latestResult": latest_result,
            "dialogueState": latest_result.get("dialogueState") if isinstance(latest_result, dict) else None,
            "latestEvent": latest,
            "truncated": truncated,
        }

    @staticmethod
    def _hydro_from_snapshot_rows(
        rows: list[dict[str, Any]], *, owner_user_id: str, thread_id: str
    ) -> dict[str, Any]:
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                "multiple canonical Hydro lifecycle rows exist for one owner/thread",
                status=503,
            )
        if not rows:
            return _project_hydro([], _EMPTY_SHA256)
        row = rows[0]
        content = str(row.get("content") or "")
        if int(row.get("total_chars") or 0) > MAX_HYDRO_STREAM_BYTES:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_OVERSIZED",
                "canonical Hydro lifecycle exceeds the bounded projection size",
                status=503,
            )
        revision = str(row.get("source_hash") or _sha256_bytes(content.encode("utf-8")))
        return _project_hydro(
            _parse_hydro_stream(content, owner_user_id=owner_user_id, thread_id=thread_id),
            revision,
        )

    def context_snapshot(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        transcript_title: str,
        memory_limit: int,
        character_budget: int,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any]:
        """Read one transactionally consistent AUTO context bundle in one query.

        A second call remains mandatory before projection release. This removes
        remote PostgreSQL round trips without weakening revision revalidation.
        """
        paths = [self._artifact_path(item[1]) for item in self._ARTIFACTS]
        registration_prefix = f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/registrations/%"
        lifecycle_title = _hydro_lifecycle_title(thread_id)
        action_prefix = _action_authority_prefix(owner_user_id, thread_id, "grants").rsplit(
            "grants/", 1
        )[0] + "%"
        receipt_prefix = _receipt_storage_path(
            owner_user_id, thread_id, "placeholder"
        ).rsplit("/", 1)[0] + "/%"
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """WITH system_owner AS MATERIALIZED (
                           SELECT id FROM ovvaults.users WHERE lower(email)=lower(%s) LIMIT 1
                         ), activations AS MATERIALIZED (
                           SELECT DISTINCT ON (metadata->>'artifact_id')
                                  content,sha256,metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s
                              AND metadata->>'contract_version'=%s
                              AND metadata->>'activation_status'='active'
                            ORDER BY metadata->>'artifact_id',updated_at DESC
                         ), knowledge_ids AS MATERIALIZED (
                           SELECT coalesce(
                                    (content::jsonb->'knowledgeReference'->>'artifact_id'),
                                    (content::jsonb->'knowledgeReference'->>'artifactId')
                                  ) AS artifact_id
                             FROM activations
                         ), uuid_knowledge_ids AS MATERIALIZED (
                           SELECT artifact_id::uuid AS artifact_id
                             FROM knowledge_ids
                            WHERE artifact_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                         ), symbolic_knowledge_ids AS MATERIALIZED (
                           SELECT artifact_id
                             FROM knowledge_ids
                            WHERE artifact_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                         )
                         SELECT
                           (SELECT id::text FROM system_owner) AS system_user_id,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT id::text AS id,user_id::text AS user_id,
                                          storage_path,sha256,
                                          encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                          metadata,is_system,
                                          updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=(SELECT id FROM system_owner)
                                      AND coalesce(is_system,false)=true
                                      AND storage_path=ANY(%s)) row_data),'[]'::jsonb) AS profile_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT id::text AS id,object_key,storage_path,content,sha256,
                                          metadata,is_system,updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=(SELECT id FROM system_owner)
                                      AND coalesce(is_system,false)=true
                                      AND storage_path LIKE %s) row_data),'[]'::jsonb) AS registration_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id)
                             FROM (SELECT id::text AS id,right(content,%s) AS content,
                                          length(content) AS total_chars,source_hash,
                                          materialized_at::text AS materialized_at,
                                          created_at::text AS created_at
                                     FROM ovvaults.transcripts
                                    WHERE user_id=%s AND title=%s) row_data),'[]'::jsonb) AS transcript_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id)
                             FROM (SELECT id::text AS id,right(content,%s) AS content,
                                          length(content) AS total_chars,source_hash,
                                          materialized_at::text AS materialized_at,
                                          created_at::text AS created_at
                                     FROM ovvaults.transcripts
                                    WHERE user_id=%s AND title=%s) row_data),'[]'::jsonb) AS lifecycle_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT storage_path,content,sha256,metadata,
                                          updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s AND storage_path LIKE %s
                                    ORDER BY storage_path
                                    LIMIT %s) row_data),'[]'::jsonb) AS action_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(activations) ORDER BY metadata->>'artifact_id')
                                      FROM activations),'[]'::jsonb) AS activation_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id,storage_path)
                             FROM (SELECT id::text AS id,storage_path,content,sha256,metadata,
                                          updated_at::text AS updated_at,user_id::text AS user_id
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s
                                      AND id IN (SELECT artifact_id FROM uuid_knowledge_ids)
                                   UNION ALL
                                   SELECT id::text AS id,storage_path,content,sha256,metadata,
                                          updated_at::text AS updated_at,user_id::text AS user_id
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s
                                      AND metadata->>'artifact_id' IN (
                                        SELECT artifact_id FROM symbolic_knowledge_ids
                                      )) row_data),'[]'::jsonb) AS knowledge_artifact_rows,
                           (SELECT to_jsonb(row_data) FROM (
                              SELECT content,sha256,metadata,updated_at::text AS updated_at
                                FROM ovvaults.vault_files
                               WHERE user_id=%s
                                 AND metadata->>'contract_version'=%s
                                 AND metadata->>'publication_status'='approved'
                               ORDER BY (metadata->>'revision')::int DESC,updated_at DESC
                               LIMIT 1
                           ) row_data) AS account_row,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id DESC)
                             FROM (SELECT id::text AS id,content,sha256,metadata,object_key
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s AND storage_path LIKE %s
                                    ORDER BY id DESC LIMIT 64) row_data),'[]'::jsonb) AS exchange_receipt_rows""",
                    (
                        SYSTEM_USER_EMAIL,
                        owner_user_id,
                        knowledge_activation_service.OWNER_SHARED_ACTIVATION_VERSION,
                        paths,
                        registration_prefix,
                        MAX_EVENT_BYTES + 1,
                        owner_user_id,
                        transcript_title,
                        MAX_HYDRO_STREAM_BYTES + 1,
                        owner_user_id,
                        lifecycle_title,
                        owner_user_id,
                        action_prefix,
                        _ACTION_AUTHORITY_REVISION_ROW_LIMIT + 1,
                        owner_user_id,
                        owner_user_id,
                        owner_user_id,
                        account_context_service.PROJECTION_VERSION,
                        owner_user_id,
                        receipt_prefix,
                    ),
                )
                bundle = _fetchone(cur) or {}
        if not str(bundle.get("system_user_id") or ""):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNAVAILABLE",
                "VVAULT system owner is unavailable",
                status=503,
            )
        profile_rows = self._json_rows(bundle.get("profile_rows"))
        registration_rows = self._json_rows(bundle.get("registration_rows"))
        transcript_rows = self._json_rows(bundle.get("transcript_rows"))
        lifecycle_rows = self._json_rows(bundle.get("lifecycle_rows"))
        action_rows = self._json_rows(bundle.get("action_rows"))
        activation_rows = self._json_rows(bundle.get("activation_rows"))
        knowledge_rows = self._json_rows(bundle.get("knowledge_artifact_rows"))
        receipt_rows = self._json_rows(bundle.get("exchange_receipt_rows"))
        account = bundle.get("account_row")
        if isinstance(account, str):
            account = json.loads(account)
        if account is not None and not isinstance(account, dict):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_CONTEXT_UNAVAILABLE",
                "canonical account context row is malformed",
                status=503,
            )
        if len(action_rows) > _ACTION_AUTHORITY_REVISION_ROW_LIMIT:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_AUTHORITY_UNAVAILABLE",
                "AUTO action authority exceeds the bounded revision projection",
                status=503,
            )
        if len(activation_rows) > MAX_EVIDENCE_ITEMS:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared activation set exceeds AUTO bounds",
                status=503,
            )
        revisions, activated_references = _revision_vector_from_rows(
            profile_rows=profile_rows,
            registration_rows=registration_rows,
            transcript_rows=transcript_rows,
            lifecycle_rows=lifecycle_rows,
            action_rows=action_rows,
            activation_rows=activation_rows,
            knowledge_artifact_rows=knowledge_rows,
            account=account,
        )
        return {
            "revisions": revisions,
            "profile": self._profile_from_snapshot_rows(profile_rows, registration_rows),
            "transcript": self._thread_from_snapshot_rows(
                transcript_rows,
                transcript_title=transcript_title,
                memory_limit=memory_limit,
                character_budget=character_budget,
            ),
            "hydro": self._hydro_from_snapshot_rows(
                lifecycle_rows, owner_user_id=owner_user_id, thread_id=thread_id
            ),
            "actionRowsPresent": bool(action_rows),
            "knowledgeReferences": activated_references,
            "activationRows": activation_rows,
            "knowledgeArtifactRows": knowledge_rows,
            "accountRow": account,
            "exchangeReceiptRows": receipt_rows,
        }

    def revision_snapshot(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        transcript_title: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, str]:
        """Recheck every cache authority with server-computed content hashes.

        This is the warm-path freshness gate. It returns no profile prose,
        transcript content, knowledge documents, or account contents. Actual
        SHA-256 values are computed by PostgreSQL from canonical bytes so a
        stale declared hash cannot preserve a cache hit.
        """
        paths = [self._artifact_path(item[1]) for item in self._ARTIFACTS]
        registration_prefix = f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/registrations/%"
        lifecycle_title = _hydro_lifecycle_title(thread_id)
        action_prefix = _action_authority_prefix(owner_user_id, thread_id, "grants").rsplit(
            "grants/", 1
        )[0] + "%"
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """WITH system_owner AS MATERIALIZED (
                           SELECT id FROM ovvaults.users WHERE lower(email)=lower(%s) LIMIT 1
                         ), activations AS MATERIALIZED (
                           SELECT DISTINCT ON (metadata->>'artifact_id')
                                  content,sha256,
                                  encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                  metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s
                              AND metadata->>'contract_version'=%s
                              AND metadata->>'activation_status'='active'
                            ORDER BY metadata->>'artifact_id',updated_at DESC
                         ), knowledge_ids AS MATERIALIZED (
                           SELECT coalesce(
                                    (content::jsonb->'knowledgeReference'->>'artifact_id'),
                                    (content::jsonb->'knowledgeReference'->>'artifactId')
                                  ) AS artifact_id
                             FROM activations
                         ), uuid_knowledge_ids AS MATERIALIZED (
                           SELECT artifact_id::uuid AS artifact_id
                             FROM knowledge_ids
                            WHERE artifact_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                         ), symbolic_knowledge_ids AS MATERIALIZED (
                           SELECT artifact_id
                             FROM knowledge_ids
                            WHERE artifact_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                         )
                         SELECT
                           (SELECT id::text FROM system_owner) AS system_user_id,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT storage_path,sha256,
                                          encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                          metadata,is_system,updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=(SELECT id FROM system_owner)
                                      AND coalesce(is_system,false)=true
                                      AND storage_path=ANY(%s)) row_data),'[]'::jsonb) AS profile_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT storage_path,sha256,
                                          encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                          metadata,is_system,updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=(SELECT id FROM system_owner)
                                      AND coalesce(is_system,false)=true
                                      AND storage_path LIKE %s) row_data),'[]'::jsonb) AS registration_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id)
                             FROM (SELECT id::text AS id,source_hash,
                                          materialized_at::text AS materialized_at,
                                          created_at::text AS created_at
                                     FROM ovvaults.transcripts
                                    WHERE user_id=%s AND title=%s) row_data),'[]'::jsonb) AS transcript_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id)
                             FROM (SELECT id::text AS id,source_hash,
                                          materialized_at::text AS materialized_at,
                                          created_at::text AS created_at
                                     FROM ovvaults.transcripts
                                    WHERE user_id=%s AND title=%s) row_data),'[]'::jsonb) AS lifecycle_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY storage_path)
                             FROM (SELECT storage_path,sha256,metadata,
                                          updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s AND storage_path LIKE %s
                                    ORDER BY storage_path
                                    LIMIT %s) row_data),'[]'::jsonb) AS action_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(activations) ORDER BY metadata->>'artifact_id')
                                      FROM activations),'[]'::jsonb) AS activation_rows,
                           coalesce((SELECT jsonb_agg(to_jsonb(row_data) ORDER BY id,storage_path)
                             FROM (SELECT id::text AS id,storage_path,sha256,
                                          encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                          metadata,updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s
                                      AND id IN (SELECT artifact_id FROM uuid_knowledge_ids)
                                   UNION ALL
                                   SELECT id::text AS id,storage_path,sha256,
                                          encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                          metadata,updated_at::text AS updated_at
                                     FROM ovvaults.vault_files
                                    WHERE user_id=%s
                                      AND metadata->>'artifact_id' IN (
                                        SELECT artifact_id FROM symbolic_knowledge_ids
                                      )) row_data),'[]'::jsonb) AS knowledge_artifact_rows,
                           (SELECT to_jsonb(row_data) FROM (
                              SELECT sha256,
                                     encode(sha256(convert_to(content,'UTF8')),'hex') AS actual_sha256,
                                     metadata,updated_at::text AS updated_at
                                FROM ovvaults.vault_files
                               WHERE user_id=%s
                                 AND metadata->>'contract_version'=%s
                                 AND metadata->>'publication_status'='approved'
                               ORDER BY (metadata->>'revision')::int DESC,updated_at DESC
                               LIMIT 1
                           ) row_data) AS account_row""",
                    (
                        SYSTEM_USER_EMAIL,
                        owner_user_id,
                        knowledge_activation_service.OWNER_SHARED_ACTIVATION_VERSION,
                        paths,
                        registration_prefix,
                        owner_user_id,
                        transcript_title,
                        owner_user_id,
                        lifecycle_title,
                        owner_user_id,
                        action_prefix,
                        _ACTION_AUTHORITY_REVISION_ROW_LIMIT + 1,
                        owner_user_id,
                        owner_user_id,
                        owner_user_id,
                        account_context_service.PROJECTION_VERSION,
                    ),
                )
                bundle = _fetchone(cur) or {}
        if not str(bundle.get("system_user_id") or ""):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNAVAILABLE",
                "VVAULT system owner is unavailable",
                status=503,
            )
        profile_rows = self._json_rows(bundle.get("profile_rows"))
        registration_rows = self._json_rows(bundle.get("registration_rows"))
        transcript_rows = self._json_rows(bundle.get("transcript_rows"))
        lifecycle_rows = self._json_rows(bundle.get("lifecycle_rows"))
        action_rows = self._json_rows(bundle.get("action_rows"))
        activation_rows = self._json_rows(bundle.get("activation_rows"))
        knowledge_rows = self._json_rows(bundle.get("knowledge_artifact_rows"))
        account = bundle.get("account_row")
        if isinstance(account, str):
            account = json.loads(account)
        if account is not None and not isinstance(account, dict):
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_CONTEXT_UNAVAILABLE",
                "canonical account revision row is malformed",
                status=503,
            )
        if len(transcript_rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_TRANSCRIPT_AUTHORITY_CONFLICT",
                "multiple canonical AUTO transcript rows exist for one owner/thread",
                status=503,
            )
        if len(lifecycle_rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                "multiple canonical Hydro lifecycle rows exist for one owner/thread",
                status=503,
            )
        if len(action_rows) > _ACTION_AUTHORITY_REVISION_ROW_LIMIT:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_AUTHORITY_UNAVAILABLE",
                "AUTO action authority exceeds the bounded revision projection",
                status=503,
            )
        if len(activation_rows) > MAX_EVIDENCE_ITEMS:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared activation set exceeds AUTO bounds",
                status=503,
            )
        revisions, _references = _revision_vector_from_rows(
            profile_rows=profile_rows,
            registration_rows=registration_rows,
            transcript_rows=transcript_rows,
            lifecycle_rows=lifecycle_rows,
            action_rows=action_rows,
            activation_rows=activation_rows,
            knowledge_artifact_rows=knowledge_rows,
            account=account,
        )
        return revisions

    @staticmethod
    def exchange_receipt_from_snapshot(
        rows: list[dict[str, Any]],
        *,
        owner_user_id: str,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        matches = []
        for row in rows:
            raw = str(row.get("content") or "")
            try:
                receipt = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AutoRuntimeContractError(
                    "AUTO_EXCHANGE_READBACK_FAILED",
                    "stored exchange receipt is malformed",
                    status=503,
                ) from exc
            if not isinstance(receipt, dict) or receipt.get("turnId") != turn_id:
                continue
            receipt_base = {key: value for key, value in receipt.items() if key != "receiptSha256"}
            if (
                receipt.get("contract") != EXCHANGE_RECEIPT_CONTRACT
                or receipt.get("ownerId") != owner_user_id
                or receipt.get("threadId") != thread_id
                or receipt.get("receiptSha256") != _sha256_value(receipt_base)
                or str(row.get("sha256") or "") != _sha256_bytes(raw.encode("utf-8"))
            ):
                raise AutoRuntimeContractError(
                    "AUTO_EXCHANGE_READBACK_FAILED",
                    "stored exchange receipt is unverifiable",
                    status=503,
                )
            matches.append(receipt)
        if len(matches) > 1:
            raise AutoRuntimeContractError(
                "AUTO_EXCHANGE_AUTHORITY_CONFLICT",
                "multiple canonical receipts exist for one owner/thread/turn",
                status=503,
            )
        return matches[0] if matches else None

    def source_revisions(
        self,
        *,
        owner_user_id: str,
        transcript_title: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, str]:
        paths = [self._artifact_path(item[1]) for item in self._ARTIFACTS]
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                system_user_id = self._read_system_user_id(cur, deadline)
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT storage_path,content,sha256,metadata,is_system,updated_at
                         FROM ovvaults.vault_files
                        WHERE user_id=%s AND coalesce(is_system,false)=true
                          AND storage_path = ANY(%s)
                        ORDER BY storage_path""",
                    (system_user_id, paths),
                )
                profile_rows = [_row_dict(row) for row in cur.fetchall()]
                registration_prefix = (
                    f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/registrations/"
                )
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT storage_path,content,sha256,metadata,is_system,updated_at
                         FROM ovvaults.vault_files
                        WHERE user_id=%s AND coalesce(is_system,false)=true
                          AND storage_path LIKE %s
                        ORDER BY storage_path""",
                    (system_user_id, f"{registration_prefix}%"),
                )
                registration_rows = [_row_dict(row) for row in cur.fetchall()]
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT id::text AS id,source_hash,materialized_at,created_at
                         FROM ovvaults.transcripts
                        WHERE user_id=%s AND title=%s
                        ORDER BY id""",
                    (owner_user_id, transcript_title),
                )
                transcript_rows = [_row_dict(row) for row in cur.fetchall()]
                if len(transcript_rows) > 1:
                    raise AutoRuntimeContractError(
                        "AUTO_TRANSCRIPT_AUTHORITY_CONFLICT",
                        "multiple canonical AUTO transcript rows exist for one owner/thread",
                        status=503,
                    )
                transcript = transcript_rows[0] if transcript_rows else None
                lifecycle_title = transcript_title.replace(
                    f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/chatty/threads/",
                    f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/hydro/threads/",
                    1,
                )
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT id::text AS id,source_hash,materialized_at,created_at
                         FROM ovvaults.transcripts
                        WHERE user_id=%s AND title=%s
                        ORDER BY id""",
                    (owner_user_id, lifecycle_title),
                )
                lifecycle_rows = [_row_dict(row) for row in cur.fetchall()]
                if len(lifecycle_rows) > 1:
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                        "multiple canonical Hydro lifecycle rows exist for one owner/thread",
                        status=503,
                    )
                lifecycle = lifecycle_rows[0] if lifecycle_rows else None
                thread_hash = transcript_title.rsplit("/", 1)[-1].removesuffix(".jsonl")
                action_rows: list[dict[str, Any]] = []
                # Legacy/non-AUTO transcript titles predate the owner/thread
                # action-authority path.  They have no action authority rather
                # than an unverifiable one.  Canonical AUTO titles always carry
                # the full thread SHA and retain the strict bounded lookup.
                if _SHA256.fullmatch(thread_hash):
                    owner_hash = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
                    action_prefix = (
                        f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/"
                        f"{owner_hash}/threads/{thread_hash}/actions/"
                    )
                    self._execute_before_deadline(
                        cur,
                        deadline,
                        """SELECT storage_path,sha256,metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND storage_path LIKE %s
                            ORDER BY storage_path
                            LIMIT %s""",
                        (
                            owner_user_id,
                            f"{action_prefix}%",
                            _ACTION_AUTHORITY_REVISION_ROW_LIMIT + 1,
                        ),
                    )
                    action_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(action_rows) > _ACTION_AUTHORITY_REVISION_ROW_LIMIT:
                        raise AutoRuntimeContractError(
                            "AUTO_ACTION_AUTHORITY_UNAVAILABLE",
                            "AUTO action authority exceeds the bounded revision projection",
                            status=503,
                        )
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT content,sha256,metadata,updated_at
                         FROM (
                           SELECT DISTINCT ON (metadata->>'artifact_id')
                                  content,sha256,metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s
                              AND metadata->>'contract_version'=%s
                              AND metadata->>'activation_status'='active'
                            ORDER BY metadata->>'artifact_id',updated_at DESC
                         ) active
                        ORDER BY metadata->>'artifact_id'""",
                    (
                        owner_user_id,
                        knowledge_activation_service.OWNER_SHARED_ACTIVATION_VERSION,
                    ),
                )
                activation_rows = [_row_dict(row) for row in cur.fetchall()]
                if len(activation_rows) > MAX_EVIDENCE_ITEMS:
                    raise AutoRuntimeContractError(
                        "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                        "owner-shared activation set exceeds AUTO bounds",
                        status=503,
                    )
                activated_references: list[dict[str, str]] = []
                for row in activation_rows:
                    try:
                        receipt = json.loads(
                            _stored_content_bytes(row.get("content")).decode("utf-8")
                        )
                        reference = receipt["knowledgeReference"]
                        artifact_id = str(
                            reference.get("artifact_id")
                            or reference.get("artifactId")
                            or ""
                        ).strip()
                        revision = str(reference.get("revision") or "").strip()
                        digest = str(
                            reference.get("sha256")
                            or reference.get("contentHash")
                            or ""
                        ).strip().lower()
                    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                        raise AutoRuntimeContractError(
                            "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                            "owner-shared activation evidence is malformed",
                            status=503,
                        ) from exc
                    if not artifact_id or not revision or not _SHA256.fullmatch(digest):
                        raise AutoRuntimeContractError(
                            "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                            "owner-shared activation evidence is incomplete",
                            status=503,
                        )
                    activated_references.append(
                        {"artifactId": artifact_id, "revision": revision, "sha256": digest}
                    )
                uuid_ids: list[str] = []
                symbolic_ids: list[str] = []
                for reference in activated_references:
                    try:
                        uuid_ids.append(str(uuid.UUID(reference["artifactId"])))
                    except (ValueError, AttributeError):
                        symbolic_ids.append(reference["artifactId"])
                knowledge_artifact_rows: list[dict[str, Any]] = []
                if uuid_ids:
                    self._execute_before_deadline(
                        cur,
                        deadline,
                        """SELECT id::text AS id,storage_path,content,sha256,metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND id=ANY(%s::uuid[])
                            ORDER BY id""",
                        (owner_user_id, uuid_ids),
                    )
                    knowledge_artifact_rows.extend(
                        _row_dict(row) for row in cur.fetchall()
                    )
                if symbolic_ids:
                    self._execute_before_deadline(
                        cur,
                        deadline,
                        """SELECT id::text AS id,storage_path,content,sha256,metadata,updated_at
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND metadata->>'artifact_id'=ANY(%s)
                            ORDER BY metadata->>'artifact_id',id""",
                        (owner_user_id, symbolic_ids),
                    )
                    knowledge_artifact_rows.extend(
                        _row_dict(row) for row in cur.fetchall()
                    )
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT content,sha256,metadata,updated_at
                         FROM ovvaults.vault_files
                        WHERE user_id=%s
                          AND metadata->>'contract_version'=%s
                          AND metadata->>'publication_status'='approved'
                        ORDER BY (metadata->>'revision')::int DESC,updated_at DESC
                        LIMIT 1""",
                    (owner_user_id, account_context_service.PROJECTION_VERSION),
                )
                account = _fetchone(cur)
        profile_revision = _sha256_value({
            "artifacts": [
                {
                    "path": row.get("storage_path"),
                    "declaredSha256": row.get("sha256"),
                    "actualSha256": _sha256_bytes(_stored_content_bytes(row.get("content"))),
                    "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
                    "isSystem": row.get("is_system") is True,
                    "updatedAt": str(row.get("updated_at") or ""),
                }
                for row in profile_rows
            ],
            "registrationReceipts": [
                {
                    "path": row.get("storage_path"),
                    "declaredSha256": row.get("sha256"),
                    "actualSha256": _sha256_bytes(_stored_content_bytes(row.get("content"))),
                    "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
                    "isSystem": row.get("is_system") is True,
                    "updatedAt": str(row.get("updated_at") or ""),
                }
                for row in registration_rows
            ],
        })
        knowledge_revision = _sha256_value(
            {
                "activatedReferences": activated_references,
                "activationReceipts": [
                    {
                        "declaredSha256": row.get("sha256"),
                        "actualSha256": _sha256_bytes(
                            _stored_content_bytes(row.get("content"))
                        ),
                        "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
                        "updatedAt": str(row.get("updated_at") or ""),
                    }
                    for row in activation_rows
                ],
                "canonicalArtifacts": [
                    {
                        "id": row.get("id"),
                        "path": row.get("storage_path"),
                        "declaredSha256": row.get("sha256"),
                        "actualSha256": _sha256_bytes(
                            _stored_content_bytes(row.get("content"))
                        ),
                        "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
                        "updatedAt": str(row.get("updated_at") or ""),
                    }
                    for row in sorted(
                        knowledge_artifact_rows,
                        key=lambda item: (str(item.get("id") or ""), str(item.get("storage_path") or "")),
                    )
                ],
            }
        )
        account_revision = (
            _sha256_value(
                {
                    "sha256": account.get("sha256"),
                    "actualSha256": _sha256_bytes(
                        _stored_content_bytes(account.get("content"))
                    ),
                    "metadataSha256": _sha256_value(_metadata(account.get("metadata"))),
                    "updatedAt": str(account.get("updated_at") or ""),
                }
            )
            if account
            else _EMPTY_SHA256
        )
        action_revision = _sha256_value(
            [
                {
                    "path": row.get("storage_path"),
                    "sha256": row.get("sha256"),
                    "metadataSha256": _sha256_value(_metadata(row.get("metadata"))),
                    "updatedAt": str(row.get("updated_at") or ""),
                }
                for row in action_rows
            ]
        ) if action_rows else _EMPTY_SHA256
        return {
            "profile": profile_revision,
            "transcript": str((transcript or {}).get("source_hash") or _EMPTY_SHA256),
            "hydroLifecycle": str((lifecycle or {}).get("source_hash") or _EMPTY_SHA256),
            "knowledge": knowledge_revision,
            "accountContext": account_revision,
            "actionAuthority": action_revision,
        }

    def read_thread(
        self,
        *,
        owner_user_id: str,
        transcript_title: str,
        memory_limit: int,
        character_budget: int,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any]:
        # One complete accepted event must remain available for Plan 3
        # continuation even when the caller asks for a smaller memory budget.
        # The transfer is still strictly bounded and never scans the full row.
        bounded_tail = MAX_EVENT_BYTES + 1
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT id::text AS id,right(content,%s) AS content,
                               length(content) AS total_chars,source_hash,
                               materialized_at,created_at
                         FROM ovvaults.transcripts
                        WHERE user_id=%s AND title=%s
                        ORDER BY id""",
                    (bounded_tail, owner_user_id, transcript_title),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_TRANSCRIPT_AUTHORITY_CONFLICT",
                "multiple canonical AUTO transcript rows exist for one owner/thread",
                status=503,
            )
        row = rows[0] if rows else None
        if not row:
            return {
                "title": transcript_title,
                "sha256": _EMPTY_SHA256,
                "events": [],
                "latestResult": None,
                "dialogueState": None,
                "latestEvent": None,
                "truncated": False,
            }
        content = str(row.get("content") or "")
        lines = content.splitlines()
        tail_was_truncated = int(row.get("total_chars") or 0) > len(content)
        if tail_was_truncated and lines:
            lines = lines[1:]
        events: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AutoRuntimeContractError(
                    "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
                    "AUTO transcript tail contains malformed canonical evidence",
                    status=503,
                ) from exc
            events.append(
                _validate_stored_exchange_event(event, transcript_title=transcript_title)
            )
        selected: list[dict[str, Any]] = []
        selected_chars = 0
        if memory_limit:
            for event in reversed(events[-memory_limit:]):
                event_chars = len(_canonical_bytes(event).decode("utf-8"))
                if selected_chars + event_chars > character_budget:
                    continue
                selected.append(event)
                selected_chars += event_chars
            selected.reverse()
        latest = events[-1] if events else None
        latest_result = latest.get("result") if isinstance(latest, dict) else None
        dialogue_state = (
            latest_result.get("dialogueState")
            if isinstance(latest_result, dict)
            else None
        )
        return {
            "title": transcript_title,
            "sha256": str(row.get("source_hash") or _sha256_bytes(content.encode("utf-8"))),
            "events": selected,
            "latestResult": latest_result,
            "dialogueState": dialogue_state,
            "latestEvent": latest,
            "truncated": tail_was_truncated,
        }

    def append_exchange(
        self,
        *,
        owner_user_id: str,
        transcript_title: str,
        thread_id: str,
        turn_id: str,
        event_bytes: bytes,
        payload_sha256: str,
        appended_at: str,
        expected_transcript_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt_path = _receipt_storage_path(owner_user_id, thread_id, turn_id)
        receipt_key = f"users/{owner_user_id}/{receipt_path}"
        receipt_metadata = {
            "contract": EXCHANGE_RECEIPT_CONTRACT,
            "ownerUserId": owner_user_id,
            "threadId": thread_id,
            "threadTitle": transcript_title,
            "turnId": turn_id,
            "payloadSha256": payload_sha256,
            "appendOnly": True,
        }
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"auto-thread:{owner_user_id}:{transcript_title}",),
                    )
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (receipt_key,))
                    cur.execute(
                        """SELECT id::text AS id,content,sha256,metadata
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND object_key=%s
                            ORDER BY id FOR UPDATE""",
                        (owner_user_id, receipt_key),
                    )
                    existing_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(existing_rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_EXCHANGE_AUTHORITY_CONFLICT",
                            "multiple canonical receipts exist for one owner/thread/turn",
                            status=503,
                        )
                    existing = existing_rows[0] if existing_rows else None
                    if existing:
                        metadata = _metadata(existing.get("metadata"))
                        if metadata.get("payloadSha256") != payload_sha256:
                            raise AutoRuntimeContractError(
                                "AUTO_EXCHANGE_CONFLICT",
                                "turnId is already bound to different canonical bytes",
                                status=409,
                            )
                        try:
                            receipt = json.loads(str(existing.get("content") or ""))
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_EXCHANGE_READBACK_FAILED",
                                "stored exchange receipt is malformed",
                                status=503,
                            ) from exc
                        receipt_base = {
                            key: value for key, value in receipt.items() if key != "receiptSha256"
                        }
                        if (
                            not isinstance(receipt, dict)
                            or receipt.get("contract") != EXCHANGE_RECEIPT_CONTRACT
                            or receipt.get("ownerId") != owner_user_id
                            or receipt.get("threadId") != thread_id
                            or receipt.get("turnId") != turn_id
                            or receipt.get("payloadSha256") != payload_sha256
                            or receipt.get("receiptSha256") != _sha256_value(receipt_base)
                            or str(existing.get("sha256") or "")
                            != _sha256_bytes(str(existing.get("content") or "").encode("utf-8"))
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_EXCHANGE_READBACK_FAILED",
                                "stored exchange receipt is unverifiable",
                                status=503,
                            )
                        conn.commit()
                        return {
                            "status": "idempotent_readback",
                            "transcriptSha256": str(receipt["transcriptSha256"]),
                            "receiptSha256": str(receipt["receiptSha256"]),
                        }
                    cur.execute(
                        """SELECT id::text AS id,source_hash,right(content,1) AS final_character
                             FROM ovvaults.transcripts
                            WHERE user_id=%s AND title=%s
                            ORDER BY id FOR UPDATE""",
                        (owner_user_id, transcript_title),
                    )
                    transcript_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(transcript_rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_TRANSCRIPT_AUTHORITY_CONFLICT",
                            "multiple canonical AUTO transcript rows exist for one owner/thread",
                            status=503,
                        )
                    transcript = transcript_rows[0] if transcript_rows else None
                    current_transcript_sha = str(
                        (transcript or {}).get("source_hash") or _EMPTY_SHA256
                    )
                    if (
                        expected_transcript_sha256 is not None
                        and current_transcript_sha != expected_transcript_sha256
                    ):
                        raise AutoRuntimeContractError(
                            "AUTO_CONTINUATION_SOURCE_CHANGED",
                            "AUTO transcript changed after the signed decision context was loaded",
                            status=409,
                        )
                    separator = "" if not transcript or transcript.get("final_character") in {None, "", "\n"} else "\n"
                    suffix = separator + event_bytes.decode("utf-8") + "\n"
                    if transcript:
                        cur.execute(
                            """UPDATE ovvaults.transcripts
                                  SET content=coalesce(content,'') || %s,
                                      source_hash=encode(digest(convert_to(coalesce(content,'') || %s,'UTF8'),'sha256'),'hex'),
                                      materialized_at=%s
                                WHERE id=%s AND user_id=%s AND title=%s
                                RETURNING id::text AS id,source_hash""",
                            (
                                suffix,
                                suffix,
                                appended_at,
                                transcript["id"],
                                owner_user_id,
                                transcript_title,
                            ),
                        )
                        updated = _fetchone(cur)
                        transcript_sha = str((updated or {}).get("source_hash") or "")
                    else:
                        transcript_sha = _sha256_bytes(suffix.encode("utf-8"))
                        cur.execute(
                            """INSERT INTO ovvaults.transcripts
                               (user_id,title,content,source_hash,materialized_at,created_at)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               RETURNING id::text AS id""",
                            (
                                owner_user_id,
                                transcript_title,
                                suffix,
                                transcript_sha,
                                appended_at,
                                appended_at,
                            ),
                        )
                        inserted = _fetchone(cur)
                        if not inserted:
                            raise AutoRuntimeContractError(
                                "AUTO_EXCHANGE_READBACK_FAILED",
                                "canonical transcript insert returned no row",
                                status=503,
                            )
                        transcript = inserted
                    stored_receipt = {
                        "contract": EXCHANGE_RECEIPT_CONTRACT,
                        "status": "appended",
                        "ownerId": owner_user_id,
                        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "payloadSha256": payload_sha256,
                        "transcriptSha256": transcript_sha,
                    }
                    stored_receipt["receiptSha256"] = _sha256_value(stored_receipt)
                    receipt_raw = _canonical_bytes(stored_receipt)
                    stored_content_sha = _sha256_bytes(receipt_raw)
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,content_type,
                            file_type,size_bytes,sha256,content,metadata,construct_id,
                            is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,false,%s,%s)""",
                        (
                            owner_user_id,
                            receipt_key,
                            receipt_path,
                            receipt_path,
                            len(receipt_raw),
                            stored_content_sha,
                            receipt_raw.decode("utf-8"),
                            json.dumps(receipt_metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID,
                            appended_at,
                            appended_at,
                        ),
                    )
                    cur.execute(
                        """SELECT source_hash FROM ovvaults.transcripts
                            WHERE id=%s AND user_id=%s AND title=%s""",
                        (transcript["id"], owner_user_id, transcript_title),
                    )
                    readback = _fetchone(cur)
                    if not readback or readback.get("source_hash") != transcript_sha:
                        raise AutoRuntimeContractError(
                            "AUTO_EXCHANGE_READBACK_FAILED",
                            "canonical transcript readback did not match the append",
                            status=503,
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "status": "appended",
            "transcriptSha256": transcript_sha,
            "receiptSha256": stored_receipt["receiptSha256"],
        }

    def read_exchange_receipt(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        turn_id: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any] | None:
        receipt_path = _receipt_storage_path(owner_user_id, thread_id, turn_id)
        receipt_key = f"users/{owner_user_id}/{receipt_path}"
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT content,sha256,metadata
                         FROM ovvaults.vault_files
                        WHERE user_id=%s AND object_key=%s
                        ORDER BY id""",
                    (owner_user_id, receipt_key),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_EXCHANGE_AUTHORITY_CONFLICT",
                "multiple canonical receipts exist for one owner/thread/turn",
                status=503,
            )
        if not rows:
            return None
        row = rows[0]
        try:
            receipt = json.loads(str(row.get("content") or ""))
        except json.JSONDecodeError as exc:
            raise AutoRuntimeContractError(
                "AUTO_EXCHANGE_READBACK_FAILED",
                "stored exchange receipt is malformed",
                status=503,
            ) from exc
        receipt_base = {
            key: value for key, value in receipt.items() if key != "receiptSha256"
        }
        if (
            not isinstance(receipt, dict)
            or receipt.get("contract") != EXCHANGE_RECEIPT_CONTRACT
            or receipt.get("ownerId") != owner_user_id
            or receipt.get("threadId") != thread_id
            or receipt.get("turnId") != turn_id
            or receipt.get("receiptSha256") != _sha256_value(receipt_base)
            or str(row.get("sha256") or "")
            != _sha256_bytes(str(row.get("content") or "").encode("utf-8"))
        ):
            raise AutoRuntimeContractError(
                "AUTO_EXCHANGE_READBACK_FAILED",
                "stored exchange receipt is unverifiable",
                status=503,
            )
        return receipt

    def read_exchange_event(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        turn_id: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any] | None:
        transcript_title = _thread_title(thread_id)
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT event_line
                         FROM ovvaults.transcripts transcript
                         CROSS JOIN LATERAL string_to_table(transcript.content,E'\\n') AS lines(event_line)
                        WHERE transcript.user_id=%s AND transcript.title=%s
                          AND event_line<>''
                          AND (event_line::jsonb)->>'turnId'=%s
                        LIMIT 2""",
                    (owner_user_id, transcript_title, turn_id),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_EXCHANGE_AUTHORITY_CONFLICT",
                "multiple canonical AUTO exchanges exist for one owner/thread/turn",
                status=503,
            )
        if not rows:
            return None
        try:
            candidate = json.loads(str(rows[0].get("event_line") or ""))
        except json.JSONDecodeError as exc:
            raise AutoRuntimeContractError(
                "AUTO_TRANSCRIPT_EVIDENCE_INVALID",
                "AUTO transcript contains malformed canonical evidence",
                status=503,
            ) from exc
        candidate = _validate_stored_exchange_event(
            candidate, transcript_title=transcript_title
        )
        return candidate if candidate.get("turnId") == turn_id else None

    def read_hydro_lifecycle(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any]:
        title = _hydro_lifecycle_title(thread_id)
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT content,source_hash,length(content) AS total_chars
                         FROM ovvaults.transcripts
                        WHERE user_id=%s AND title=%s
                        ORDER BY id""",
                    (owner_user_id, title),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                "multiple canonical Hydro lifecycle rows exist for one owner/thread",
                status=503,
            )
        if not rows:
            return _project_hydro([], _EMPTY_SHA256)
        row = rows[0]
        content = str(row.get("content") or "")
        if int(row.get("total_chars") or 0) > MAX_HYDRO_STREAM_BYTES:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_LIFECYCLE_OVERSIZED",
                "canonical Hydro lifecycle exceeds the bounded projection size",
                status=503,
            )
        revision = str(row.get("source_hash") or _sha256_bytes(content.encode("utf-8")))
        return _project_hydro(
            _parse_hydro_stream(
                content, owner_user_id=owner_user_id, thread_id=thread_id
            ),
            revision,
        )

    def append_hydro_event(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        event: dict[str, Any],
        event_sha256: str,
        recorded_at: str,
    ) -> dict[str, Any]:
        title = _hydro_lifecycle_title(thread_id)
        receipt_path = _hydro_receipt_storage_path(
            owner_user_id, thread_id, event["eventId"]
        )
        receipt_key = f"users/{owner_user_id}/{receipt_path}"
        receipt_prefix = _hydro_receipt_prefix(owner_user_id, thread_id)
        idempotency_key = event["idempotencyKey"]
        canonical_event = _hydro_canonical_event(
            owner_user_id=owner_user_id,
            event=event,
            event_sha256=event_sha256,
            recorded_at=recorded_at,
        )
        event_line = _canonical_bytes(canonical_event).decode("utf-8") + "\n"
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"auto-hydro:{owner_user_id}:{title}",),
                    )
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"auto-hydro-idempotency:{owner_user_id}:{thread_id}:{idempotency_key}",),
                    )
                    cur.execute(
                        """SELECT id::text AS id,object_key,content,sha256,metadata
                             FROM ovvaults.vault_files
                            WHERE user_id=%s
                              AND (object_key=%s OR
                                   (storage_path LIKE %s AND metadata->>'idempotencyKey'=%s))
                            ORDER BY id FOR UPDATE""",
                        (
                            owner_user_id,
                            receipt_key,
                            f"{receipt_prefix}%",
                            idempotency_key,
                        ),
                    )
                    existing_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(existing_rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                            "multiple canonical Hydro receipts match one event",
                            status=503,
                        )
                    if existing_rows:
                        existing = existing_rows[0]
                        metadata = _metadata(existing.get("metadata"))
                        try:
                            receipt = json.loads(str(existing.get("content") or ""))
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_READBACK_FAILED",
                                "stored Hydro lifecycle receipt is malformed",
                                status=503,
                            ) from exc
                        receipt_base = {
                            key: value for key, value in receipt.items()
                            if key != "receiptSha256"
                        }
                        if (
                            metadata.get("eventSha256") != event_sha256
                            or metadata.get("eventId") != event["eventId"]
                            or metadata.get("idempotencyKey") != idempotency_key
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_EVENT_CONFLICT",
                                "eventId or idempotencyKey is bound to different canonical bytes",
                                status=409,
                            )
                        if (
                            receipt.get("contract") != HYDRO_LIFECYCLE_RECEIPT_CONTRACT
                            or receipt.get("ownerId") != owner_user_id
                            or receipt.get("threadId") != thread_id
                            or receipt.get("eventId") != event["eventId"]
                            or receipt.get("eventSha256") != event_sha256
                            or receipt.get("receiptSha256") != _sha256_value(receipt_base)
                            or str(existing.get("sha256") or "")
                            != _sha256_bytes(str(existing.get("content") or "").encode("utf-8"))
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_READBACK_FAILED",
                                "stored Hydro lifecycle receipt is unverifiable",
                                status=503,
                            )
                        conn.commit()
                        return {**receipt, "status": "idempotent_readback"}
                    cur.execute(
                        """SELECT id::text AS id,content,source_hash
                             FROM ovvaults.transcripts
                            WHERE user_id=%s AND title=%s
                            ORDER BY id FOR UPDATE""",
                        (owner_user_id, title),
                    )
                    lifecycle_rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(lifecycle_rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_LIFECYCLE_AUTHORITY_CONFLICT",
                            "multiple canonical Hydro lifecycle rows exist for one owner/thread",
                            status=503,
                        )
                    lifecycle = lifecycle_rows[0] if lifecycle_rows else None
                    content = str((lifecycle or {}).get("content") or "")
                    prior_events = _parse_hydro_stream(
                        content, owner_user_id=owner_user_id, thread_id=thread_id
                    )
                    _validate_hydro_transition(prior_events, event)
                    if len((content + event_line).encode("utf-8")) > MAX_HYDRO_STREAM_BYTES:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_LIFECYCLE_OVERSIZED",
                            "canonical Hydro lifecycle stream is full",
                            status=409,
                        )
                    new_content = content + event_line
                    lifecycle_sha256 = _sha256_bytes(new_content.encode("utf-8"))
                    if lifecycle:
                        cur.execute(
                            """UPDATE ovvaults.transcripts
                                  SET content=%s,source_hash=%s,materialized_at=%s
                                WHERE id=%s AND user_id=%s AND title=%s
                                RETURNING id::text AS id,source_hash""",
                            (
                                new_content,
                                lifecycle_sha256,
                                recorded_at,
                                lifecycle["id"],
                                owner_user_id,
                                title,
                            ),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO ovvaults.transcripts
                               (user_id,title,content,source_hash,materialized_at,created_at)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               RETURNING id::text AS id,source_hash""",
                            (
                                owner_user_id,
                                title,
                                new_content,
                                lifecycle_sha256,
                                recorded_at,
                                recorded_at,
                            ),
                        )
                    updated = _fetchone(cur)
                    if not updated or updated.get("source_hash") != lifecycle_sha256:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_READBACK_FAILED",
                            "canonical Hydro lifecycle readback did not match the append",
                            status=503,
                        )
                    receipt = {
                        "contract": HYDRO_LIFECYCLE_RECEIPT_CONTRACT,
                        "status": "appended",
                        "ownerId": owner_user_id,
                        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                        "threadId": thread_id,
                        "graphId": event["graphId"],
                        "eventId": event["eventId"],
                        "eventType": event["type"],
                        "sequence": event["sequence"],
                        "eventSha256": event_sha256,
                        "lifecycleSha256": lifecycle_sha256,
                    }
                    receipt["receiptSha256"] = _sha256_value(receipt)
                    receipt_raw = _canonical_bytes(receipt)
                    receipt_metadata = {
                        "contract": HYDRO_LIFECYCLE_RECEIPT_CONTRACT,
                        "ownerUserId": owner_user_id,
                        "threadId": thread_id,
                        "graphId": event["graphId"],
                        "eventId": event["eventId"],
                        "eventType": event["type"],
                        "eventSha256": event_sha256,
                        "idempotencyKey": idempotency_key,
                        "appendOnly": True,
                    }
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,content_type,
                            file_type,size_bytes,sha256,content,metadata,construct_id,
                            is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,false,%s,%s)""",
                        (
                            owner_user_id,
                            receipt_key,
                            receipt_path,
                            receipt_path,
                            len(receipt_raw),
                            _sha256_bytes(receipt_raw),
                            receipt_raw.decode("utf-8"),
                            json.dumps(receipt_metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID,
                            recorded_at,
                            recorded_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return receipt

    def list_auto_threads(
        self,
        *,
        owner_user_id: str,
        limit: int,
        offset: int,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> list[dict[str, Any]]:
        """Return bounded owner-qualified AUTO thread tails; never scan full transcripts."""
        deadline = self._context_deadline(timeout_ms)
        prefix = f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/chatty/threads/"
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT thread.title,right(thread.content,%s) AS tail,thread.source_hash,
                               thread.materialized_at,thread.id::text AS id,
                               (SELECT right(hydro.content,%s)
                                  FROM ovvaults.transcripts hydro
                                 WHERE hydro.user_id=thread.user_id
                                   AND hydro.title=replace(thread.title,'/chatty/threads/','/hydro/threads/')
                                 ORDER BY hydro.id DESC LIMIT 1) AS hydro_tail
                         FROM ovvaults.transcripts thread
                        WHERE thread.user_id=%s AND thread.title LIKE %s
                        ORDER BY thread.materialized_at DESC NULLS LAST,thread.id DESC
                        LIMIT %s OFFSET %s""",
                    (MAX_EVENT_BYTES, MAX_HYDRO_EVENT_BYTES, owner_user_id, f"{prefix}%", limit, offset),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        projected: list[dict[str, Any]] = []
        for row in rows:
            lines = [line for line in str(row.get("tail") or "").splitlines() if line.strip()]
            if not lines:
                continue
            try:
                latest = json.loads(lines[-1])
            except json.JSONDecodeError:
                continue
            if not isinstance(latest, dict):
                continue
            thread_id = str(latest.get("threadId") or "")
            if not _SAFE_ID.fullmatch(thread_id) or _thread_title(thread_id) != row.get("title"):
                continue
            result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
            dialogue = result.get("dialogueState") if isinstance(result.get("dialogueState"), dict) else {}
            title_source = str(latest.get("input") or "").strip().replace("\n", " ")
            hydro_lines = [line for line in str(row.get("hydro_tail") or "").splitlines() if line.strip()]
            active_graph_id = None
            if hydro_lines:
                try:
                    hydro_latest = json.loads(hydro_lines[-1])
                    hydro_event = hydro_latest.get("event") if isinstance(hydro_latest, dict) else None
                    if isinstance(hydro_event, dict):
                        _kind, hydro_state = _hydro_event_state(str(hydro_event.get("type") or ""))
                        if hydro_state not in _HYDRO_GRAPH_TERMINAL:
                            active_graph_id = hydro_event.get("graphId")
                except (json.JSONDecodeError, AutoRuntimeContractError):
                    active_graph_id = None
            projected.append({
                "threadId": thread_id,
                "title": (title_source[:80] or f"AUTO {thread_id[:8]}"),
                "latestTurnId": latest.get("turnId"),
                "latestResultSha256": latest.get("resultSha256"),
                "dialogueRevision": str(dialogue.get("revision") or ""),
                "transcriptRevision": str(row.get("source_hash") or _EMPTY_SHA256),
                "canonicalUpdatedAt": (
                    row["materialized_at"].isoformat()
                    if hasattr(row.get("materialized_at"), "isoformat")
                    else str(row.get("materialized_at") or "")
                ),
                "persistenceState": "canonical",
                "activeHydroGraphId": active_graph_id,
            })
        return projected

    def store_hydro_authority_record(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        record_type: str,
        record_id: str,
        request_sha256: str,
        record: dict[str, Any],
        recorded_at: str,
    ) -> dict[str, Any]:
        """Append one immutable graph-bound dispatch/cancellation/worker record."""
        storage_path = _hydro_authority_storage_path(
            owner_user_id, thread_id, record_type, record_id
        )
        object_key = f"users/{owner_user_id}/{storage_path}"
        raw = _canonical_bytes(record)
        metadata = {
            "contract": record.get("contract"),
            "recordType": record_type,
            "recordId": record_id,
            "requestSha256": request_sha256,
            "ownerUserId": owner_user_id,
            "threadId": thread_id,
            "graphId": record.get("graphId"),
            "appendOnly": True,
        }
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (object_key,))
                    cur.execute(
                        """SELECT content,sha256,metadata FROM ovvaults.vault_files
                            WHERE user_id=%s AND object_key=%s ORDER BY id FOR UPDATE""",
                        (owner_user_id, object_key),
                    )
                    rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_AUTHORITY_CONFLICT",
                            "multiple canonical Hydro authority records share one key",
                            status=503,
                        )
                    if rows:
                        stored = rows[0]
                        stored_metadata = _metadata(stored.get("metadata"))
                        try:
                            stored_record = json.loads(str(stored.get("content") or ""))
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                                "stored Hydro authority record is malformed",
                                status=503,
                            ) from exc
                        if (
                            stored_metadata.get("requestSha256") != request_sha256
                            or _sha256_bytes(str(stored.get("content") or "").encode("utf-8"))
                            != str(stored.get("sha256") or "")
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_AUTHORITY_CONFLICT",
                                "Hydro authority key is already bound to different bytes",
                                status=409,
                            )
                        conn.commit()
                        return {"created": False, "record": stored_record}
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,content_type,
                            file_type,size_bytes,sha256,content,metadata,construct_id,
                            is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,false,%s,%s)""",
                        (
                            owner_user_id, object_key, storage_path, storage_path,
                            len(raw), _sha256_bytes(raw), raw.decode("utf-8"),
                            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID, recorded_at, recorded_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"created": True, "record": copy.deepcopy(record)}

    def read_hydro_authority_record(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        record_type: str,
        record_id: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any] | None:
        storage_path = _hydro_authority_storage_path(
            owner_user_id, thread_id, record_type, record_id
        )
        object_key = f"users/{owner_user_id}/{storage_path}"
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT content,sha256 FROM ovvaults.vault_files
                        WHERE user_id=%s AND object_key=%s ORDER BY id""",
                    (owner_user_id, object_key),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
        if len(rows) > 1:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_AUTHORITY_CONFLICT",
                "multiple canonical Hydro authority records share one key",
                status=503,
            )
        if not rows:
            return None
        raw = str(rows[0].get("content") or "")
        if _sha256_bytes(raw.encode("utf-8")) != str(rows[0].get("sha256") or ""):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                "stored Hydro authority record hash is invalid",
                status=503,
            )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                "stored Hydro authority record is malformed",
                status=503,
            ) from exc
        return value if isinstance(value, dict) else None

    def list_hydro_recovery_records(
        self, *, limit: int, offset: int, timeout_ms: int = CONTEXT_DEADLINE_MS
    ) -> list[dict[str, Any]]:
        """Return a bounded service recovery window of canonical dispatches.

        This is deliberately not owner-addressable. The service-only HTTP
        route is the sole caller and every projected entry retains its exact
        canonical owner/thread binding.
        """
        bounded_limit = max(1, min(51, int(limit)))
        bounded_offset = max(0, min(1_000_000, int(offset)))
        deadline = self._context_deadline(timeout_ms)
        dispatch_pattern = (
            f"instances/{AUTO_RUNTIME_PRINCIPAL_ID}/system-runtime/owners/%/"
            "hydro/threads/%/dispatches/%.json"
        )
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                self._execute_before_deadline(
                    cur,
                    deadline,
                    """SELECT user_id,content,sha256,created_at
                         FROM ovvaults.vault_files
                         WHERE construct_id=%s AND storage_path LIKE %s
                           AND metadata->>'recordType'='dispatches'
                         ORDER BY created_at,user_id,object_key
                         LIMIT %s OFFSET %s""",
                    (AUTO_RUNTIME_PRINCIPAL_ID, dispatch_pattern, bounded_limit, bounded_offset),
                )
                rows = [_row_dict(row) for row in cur.fetchall()]
                projected = []
                for row in rows:
                    raw = str(row.get("content") or "")
                    if _sha256_bytes(raw.encode("utf-8")) != str(row.get("sha256") or ""):
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                            "stored Hydro dispatch hash is invalid",
                            status=503,
                        )
                    try:
                        dispatch = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                            "stored Hydro dispatch is malformed",
                            status=503,
                        ) from exc
                    owner = str(row.get("user_id") or "")
                    thread_id = str(dispatch.get("threadId") or "")
                    receipt_prefix = _hydro_authority_storage_path(
                        owner, thread_id, "worker-receipts", "placeholder"
                    ).rsplit("/", 1)[0] + "/%"
                    self._execute_before_deadline(
                        cur,
                        deadline,
                        """SELECT content,sha256 FROM ovvaults.vault_files
                             WHERE user_id=%s AND construct_id=%s
                               AND storage_path LIKE %s
                               AND metadata->>'recordType'='worker-receipts'
                             ORDER BY created_at,object_key LIMIT %s""",
                        (owner, AUTO_RUNTIME_PRINCIPAL_ID, receipt_prefix, MAX_HYDRO_INSTANCES * 4),
                    )
                    states: dict[str, str] = {}
                    for receipt_row in [_row_dict(item) for item in cur.fetchall()]:
                        receipt_raw = str(receipt_row.get("content") or "")
                        if _sha256_bytes(receipt_raw.encode("utf-8")) != str(receipt_row.get("sha256") or ""):
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                                "stored Hydro worker receipt hash is invalid",
                                status=503,
                            )
                        try:
                            receipt = json.loads(receipt_raw)
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_AUTHORITY_UNVERIFIABLE",
                                "stored Hydro worker receipt is malformed",
                                status=503,
                            ) from exc
                        result = receipt.get("workerResult") if isinstance(receipt, dict) else None
                        if isinstance(result, dict) and result.get("graphId") == dispatch.get("graphId"):
                            states[str(result.get("executionId") or "")] = str(result.get("status") or "")
                    projected.append({
                        "ownerId": owner,
                        "threadId": thread_id,
                        "recordedAt": (
                            row["created_at"].isoformat()
                            if hasattr(row.get("created_at"), "isoformat")
                            else str(row.get("created_at") or "")
                        ),
                        "dispatch": dispatch,
                        "workerStates": states,
                    })
        return projected

    def store_action_authority_record(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        record_type: str,
        record_id: str,
        request_sha256: str,
        record: dict[str, Any],
        recorded_at: str,
    ) -> dict[str, Any]:
        """Append one immutable owner/thread/action authority record."""
        storage_path = _action_authority_storage_path(
            owner_user_id, thread_id, record_type, record_id
        )
        object_key = f"users/{owner_user_id}/{storage_path}"
        raw = _canonical_bytes(record)
        metadata = {
            "contract": record.get("contract"),
            "recordType": record_type,
            "recordId": record_id,
            "requestSha256": request_sha256,
            "ownerUserId": owner_user_id,
            "threadId": thread_id,
            "actionId": record.get("actionId"),
            "appendOnly": True,
        }
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (object_key,))
                    cur.execute(
                        """SELECT content,sha256,metadata FROM ovvaults.vault_files
                            WHERE user_id=%s AND object_key=%s ORDER BY id FOR UPDATE""",
                        (owner_user_id, object_key),
                    )
                    rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_ACTION_AUTHORITY_CONFLICT",
                            "multiple canonical action authority records share one key",
                            status=503,
                        )
                    if rows:
                        stored = rows[0]
                        stored_metadata = _metadata(stored.get("metadata"))
                        try:
                            stored_record = json.loads(str(stored.get("content") or ""))
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
                                "stored action authority record is malformed",
                                status=503,
                            ) from exc
                        if (
                            stored_metadata.get("requestSha256") != request_sha256
                            or _sha256_bytes(str(stored.get("content") or "").encode("utf-8"))
                            != str(stored.get("sha256") or "")
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_ACTION_AUTHORITY_CONFLICT",
                                "action authority key is already bound to different bytes",
                                status=409,
                            )
                        conn.commit()
                        return {"created": False, "record": stored_record}
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,content_type,
                            file_type,size_bytes,sha256,content,metadata,construct_id,
                            is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,false,%s,%s)""",
                        (
                            owner_user_id, object_key, storage_path, storage_path,
                            len(raw), _sha256_bytes(raw), raw.decode("utf-8"),
                            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID, recorded_at, recorded_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"created": True, "record": copy.deepcopy(record)}

    def read_action_authority_record(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        record_type: str,
        record_id: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> dict[str, Any] | None:
        storage_path = _action_authority_storage_path(
            owner_user_id, thread_id, record_type, record_id
        )
        object_key = f"users/{owner_user_id}/{storage_path}"
        records = self._read_action_rows(
            owner_user_id=owner_user_id,
            object_key=object_key,
            storage_prefix=None,
            timeout_ms=timeout_ms,
        )
        if len(records) > 1:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_AUTHORITY_CONFLICT",
                "multiple canonical action authority records share one key",
                status=503,
            )
        return records[0] if records else None

    def list_action_authority_records(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        record_type: str,
        timeout_ms: int = CONTEXT_DEADLINE_MS,
    ) -> list[dict[str, Any]]:
        records = self._read_action_rows(
            owner_user_id=owner_user_id,
            object_key=None,
            storage_prefix=_action_authority_prefix(
                owner_user_id, thread_id, record_type
            ),
            timeout_ms=timeout_ms,
        )
        order = {"authorization_verified": 0, "started": 1}
        records.sort(key=lambda item: (
            order.get(str(item.get("state") or ""), 2),
            str((item.get("event") or {}).get("eventId") or ""),
        ))
        return records

    def _read_action_rows(
        self,
        *,
        owner_user_id: str,
        object_key: str | None,
        storage_prefix: str | None,
        timeout_ms: int,
    ) -> list[dict[str, Any]]:
        deadline = self._context_deadline(timeout_ms)
        with self._connect(timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                if object_key is not None:
                    sql = """SELECT content,sha256 FROM ovvaults.vault_files
                              WHERE user_id=%s AND object_key=%s ORDER BY created_at,id"""
                    params = (owner_user_id, object_key)
                else:
                    sql = """SELECT content,sha256 FROM ovvaults.vault_files
                              WHERE user_id=%s AND storage_path LIKE %s
                              ORDER BY created_at,id"""
                    params = (owner_user_id, f"{storage_prefix}%")
                self._execute_before_deadline(cur, deadline, sql, params)
                rows = [_row_dict(row) for row in cur.fetchall()]
        records: list[dict[str, Any]] = []
        for row in rows:
            raw = str(row.get("content") or "")
            if _sha256_bytes(raw.encode("utf-8")) != str(row.get("sha256") or ""):
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
                    "stored action authority record hash is invalid",
                    status=503,
                )
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
                    "stored action authority record is malformed",
                    status=503,
                ) from exc
            if not isinstance(value, dict):
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_AUTHORITY_UNVERIFIABLE",
                    "stored action authority record must be an object",
                    status=503,
                )
            records.append(value)
        return records

    def quarantine_hydro_event(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        event: dict[str, Any],
        event_sha256: str,
        reason_code: str,
        lifecycle_sha256: str,
        quarantined_at: str,
    ) -> dict[str, Any]:
        storage_path = _hydro_quarantine_storage_path(
            owner_user_id, thread_id, event_sha256
        )
        object_key = f"users/{owner_user_id}/{storage_path}"
        receipt = {
            "contract": HYDRO_LIFECYCLE_QUARANTINE_RECEIPT_CONTRACT,
            "ownerId": owner_user_id,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": event["graphId"],
            "eventId": event["eventId"],
            "eventType": event["type"],
            "sequence": event["sequence"],
            "eventSha256": event_sha256,
            "reasonCode": str(reason_code or "AUTO_HYDRO_EVENT_CONFLICT")[:128],
            "lifecycleSha256": lifecycle_sha256,
            "quarantinedAt": quarantined_at,
        }
        receipt["receiptSha256"] = _sha256_value(receipt)
        receipt_raw = _canonical_bytes(receipt)
        metadata = {
            "contract": HYDRO_LIFECYCLE_QUARANTINE_RECEIPT_CONTRACT,
            "ownerUserId": owner_user_id,
            "threadId": thread_id,
            "graphId": event["graphId"],
            "eventId": event["eventId"],
            "eventSha256": event_sha256,
            "reasonCode": receipt["reasonCode"],
            "quarantined": True,
            "appendOnly": True,
        }
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))", (object_key,)
                    )
                    cur.execute(
                        """SELECT content,sha256,metadata
                             FROM ovvaults.vault_files
                            WHERE user_id=%s AND object_key=%s
                            ORDER BY id FOR UPDATE""",
                        (owner_user_id, object_key),
                    )
                    rows = [_row_dict(row) for row in cur.fetchall()]
                    if len(rows) > 1:
                        raise AutoRuntimeContractError(
                            "AUTO_HYDRO_QUARANTINE_AUTHORITY_CONFLICT",
                            "multiple Hydro quarantine receipts exist for one attempted event hash",
                            status=503,
                        )
                    if rows:
                        existing = rows[0]
                        try:
                            stored = json.loads(str(existing.get("content") or ""))
                        except json.JSONDecodeError as exc:
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_QUARANTINE_READBACK_FAILED",
                                "stored Hydro quarantine receipt is malformed",
                                status=503,
                            ) from exc
                        stored_base = {
                            key: value for key, value in stored.items()
                            if key != "receiptSha256"
                        }
                        if (
                            stored.get("contract")
                            != HYDRO_LIFECYCLE_QUARANTINE_RECEIPT_CONTRACT
                            or stored.get("ownerId") != owner_user_id
                            or stored.get("threadId") != thread_id
                            or stored.get("eventSha256") != event_sha256
                            or stored.get("receiptSha256") != _sha256_value(stored_base)
                            or str(existing.get("sha256") or "")
                            != _sha256_bytes(str(existing.get("content") or "").encode("utf-8"))
                        ):
                            raise AutoRuntimeContractError(
                                "AUTO_HYDRO_QUARANTINE_READBACK_FAILED",
                                "stored Hydro quarantine receipt is unverifiable",
                                status=503,
                            )
                        conn.commit()
                        return stored
                    cur.execute(
                        """INSERT INTO ovvaults.vault_files
                           (user_id,bucket,object_key,filename,storage_path,content_type,
                            file_type,size_bytes,sha256,content,metadata,construct_id,
                            is_system,created_at,updated_at)
                           VALUES (%s,'vvault-local',%s,%s,%s,'application/json','json',
                                   %s,%s,%s,%s::jsonb,%s,false,%s,%s)""",
                        (
                            owner_user_id,
                            object_key,
                            storage_path,
                            storage_path,
                            len(receipt_raw),
                            _sha256_bytes(receipt_raw),
                            receipt_raw.decode("utf-8"),
                            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                            AUTO_RUNTIME_PRINCIPAL_ID,
                            quarantined_at,
                            quarantined_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return receipt


@dataclass
class _CacheEntry:
    projection: dict[str, Any]
    created_at: float


class AutoRuntimeService:
    def __init__(
        self,
        *,
        repository: AutoRuntimeRepository | None = None,
        signer: Callable[[dict[str, Any]], dict[str, str]] | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        knowledge_reference_loader: Callable[[str], list[dict[str, Any]]] | None = None,
        knowledge_resolver: Callable[..., tuple[dict[str, Any], int]] | None = None,
        account_context_loader: Callable[[str], dict[str, Any] | None] | None = None,
        code_project_repository: CodeProjectRepository | None = None,
    ) -> None:
        self.repository = repository or PostgresAutoRuntimeRepository()
        self.signer = signer or canonical_projection_signing.sign_canonical_payload
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock or monotonic
        self._default_knowledge_reference_loader = knowledge_reference_loader is None
        self._default_knowledge_resolver = knowledge_resolver is None
        self._default_account_context_loader = account_context_loader is None
        self.knowledge_reference_loader = knowledge_reference_loader
        self.knowledge_resolver = knowledge_resolver
        self.account_context_loader = account_context_loader
        self.code_project_repository = code_project_repository or CodeProjectRepository()
        self._cache: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()
        self._cache_lock = RLock()

    @staticmethod
    def _load_optional_account_context(
        owner_user_id: str, *, timeout_ms: int
    ) -> dict[str, Any] | None:
        try:
            return account_context_service.read_projection(
                owner_user_id=owner_user_id,
                statement_timeout_ms=timeout_ms,
                connection_timeout_seconds=timeout_ms / 1000,
            )
        except ValueError as exc:
            if "not published" in str(exc):
                return None
            raise

    def _sign(self, payload: dict[str, Any], *, ttl_seconds: int) -> dict[str, Any]:
        issued = self.now()
        bounded_ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
        signed_payload = {
            **copy.deepcopy(payload),
            "issuedAt": _iso(issued),
            "expiresAt": _iso(issued + timedelta(seconds=bounded_ttl)),
        }
        try:
            fields = self.signer(signed_payload)
        except Exception as exc:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE",
                "canonical projection signing is unavailable",
                status=503,
            ) from exc
        if not isinstance(fields, dict) or set(fields) != {"algorithm", "keyId", "signature"}:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical signer returned invalid evidence", status=503
            )
        try:
            signature_bytes = base64.b64decode(
                str(fields.get("signature") or ""), validate=True
            )
        except (ValueError, TypeError) as exc:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE",
                "canonical signer returned invalid evidence",
                status=503,
            ) from exc
        if (
            fields.get("algorithm") != "Ed25519"
            or not _SHA256.fullmatch(str(fields.get("keyId") or ""))
            or len(signature_bytes) != 64
        ):
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical signer returned invalid evidence", status=503
            )
        return {
            "contract": SIGNED_PROJECTION_CONTRACT,
            "payload": signed_payload,
            **fields,
        }

    def _sign_hydro_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Issue a catalog whose own payload hash includes signer-owned lifetime fields."""
        issued = self.now().astimezone(timezone.utc)
        signed_payload = {
            **copy.deepcopy(payload),
            "issuedAt": _iso(issued),
            "expiresAt": _iso(issued + timedelta(seconds=DEFAULT_TTL_SECONDS)),
        }
        catalog_basis = {
            key: signed_payload[key]
            for key in ("contract", "ownerUserId", "revision", "issuedAt", "expiresAt", "workers")
        }
        signed_payload["payloadSha256"] = _sha256_value(catalog_basis)
        try:
            fields = self.signer(signed_payload)
        except Exception as exc:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical catalog signing is unavailable", status=503
            ) from exc
        if not isinstance(fields, dict) or set(fields) != {"algorithm", "keyId", "signature"}:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical signer returned invalid catalog evidence", status=503
            )
        try:
            signature_bytes = base64.b64decode(str(fields.get("signature") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical signer returned invalid catalog evidence", status=503
            ) from exc
        if fields.get("algorithm") != "Ed25519" or not _SHA256.fullmatch(
            str(fields.get("keyId") or "")
        ) or len(signature_bytes) != 64:
            raise AutoRuntimeContractError(
                "AUTO_SIGNING_UNAVAILABLE", "canonical signer returned invalid catalog evidence", status=503
            )
        return {"contract": SIGNED_PROJECTION_CONTRACT, "payload": signed_payload, **fields}

    def _preflight_signer(self) -> None:
        self._sign({"contract": "life-vvault-signing-preflight/v1"}, ttl_seconds=1)

    def register(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "request must be an object")
        if set(request) != {"contract", "profileRevision", "combinedSha256", "idempotencyKey"}:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "registration request fields are invalid")
        if request.get("contract") != REGISTRATION_REQUEST_CONTRACT:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "registration contract is unsupported")
        if request.get("profileRevision") != AUTO_PROFILE_REVISION:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "profile revision is unsupported")
        if _sha256_field(request.get("combinedSha256"), "combinedSha256") != AUTO_PROFILE_COMBINED_SHA256:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "profile hash is unsupported")
        idempotency_key = _safe_identifier(request.get("idempotencyKey"), "idempotencyKey")
        self._preflight_signer()
        registered_at = _iso(self.now())
        try:
            stored = self.repository.register_profile(
                idempotency_key=idempotency_key, registered_at=registered_at
            )
        except AutoRuntimeContractError as exc:
            if exc.code != "AUTO_PROFILE_REGISTRATION_CONFLICT" or exc.status != 409:
                raise
            conflict = {
                "contract": REGISTRATION_RECEIPT_CONTRACT,
                "status": "conflict",
                "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                "principalType": "system_runtime",
                "profileRevision": AUTO_PROFILE_REVISION,
                "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
                "idempotencyKey": idempotency_key,
                "artifactIds": [],
            }
            conflict["receiptSha256"] = _sha256_value(conflict)
            return self._sign(conflict, ttl_seconds=MAX_TTL_SECONDS)
        payload = {
            "contract": REGISTRATION_RECEIPT_CONTRACT,
            "status": stored["status"],
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "principalType": "system_runtime",
            "profileRevision": AUTO_PROFILE_REVISION,
            "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
            "idempotencyKey": idempotency_key,
            "artifactIds": list(stored["artifactIds"]),
        }
        payload["receiptSha256"] = _sha256_value(payload)
        self.evict_all()
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def registration_preflight(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "contract", "profileRevision", "combinedSha256"
        }:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST",
                "registration preflight request fields are invalid",
            )
        if request.get("contract") != REGISTRATION_PREFLIGHT_REQUEST_CONTRACT:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST",
                "registration preflight contract is unsupported",
            )
        if (
            request.get("profileRevision") != AUTO_PROFILE_REVISION
            or _sha256_field(request.get("combinedSha256"), "combinedSha256")
            != AUTO_PROFILE_COMBINED_SHA256
        ):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST",
                "registration preflight authority is unsupported",
            )
        self._preflight_signer()
        inspected = self.repository.registration_preflight()
        payload = {
            **copy.deepcopy(inspected),
            "contract": REGISTRATION_PREFLIGHT_PROJECTION_CONTRACT,
        }
        payload["preflightSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def _validate_context_request(self, request: dict[str, Any]) -> tuple[str, str, int, int, int]:
        if not isinstance(request, dict):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "request must be an object")
        allowed = {"contract", "threadId", "query", "memoryLimit", "characterBudget", "ttlSeconds"}
        if set(request) - allowed or request.get("contract") != CONTEXT_REQUEST_CONTRACT:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "context request fields are invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        query = request.get("query")
        if not isinstance(query, str) or len(query) > MAX_INPUT_CHARS or "\x00" in query:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "query is invalid or oversized")
        memory_limit = request.get("memoryLimit")
        character_budget = request.get("characterBudget")
        ttl = request.get("ttlSeconds", DEFAULT_TTL_SECONDS)
        if isinstance(memory_limit, bool) or not isinstance(memory_limit, int) or not 0 <= memory_limit <= 12:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "memoryLimit must be between 0 and 12")
        if isinstance(character_budget, bool) or not isinstance(character_budget, int) or not 1024 <= character_budget <= 65_536:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "characterBudget must be between 1024 and 65536")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL_SECONDS:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "ttlSeconds must be between 1 and 60")
        return thread_id, query, memory_limit, character_budget, ttl

    @staticmethod
    def _evidence(reference_id: str, kind: str, source: str, revision: str, content: Any) -> dict[str, Any]:
        content_text = content if isinstance(content, str) else _canonical_bytes(content).decode("utf-8")
        return {
            "reference": {
                "contract": "chatty-auto-evidence-reference/v1",
                "evidenceId": reference_id,
                "kind": kind,
                "source": source,
                "revision": revision,
                "contentHash": _sha256_bytes(content_text.encode("utf-8")),
                "available": True,
                "authority": "vvault/ovvaults",
            },
            "content": content_text,
        }

    @staticmethod
    def _lexical_matches(events: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query_terms = set(_TOKEN.findall(query.lower()))
        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        for index, event in enumerate(events):
            searchable = f"{event.get('input', '')} {event.get('output', '')}".lower()
            score = len(query_terms.intersection(_TOKEN.findall(searchable)))
            if query_terms and score == 0:
                continue
            ranked.append((-score, -index, str(event.get("turnId") or ""), event))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [copy.deepcopy(item[3]) for item in ranked[:limit]]

    def _knowledge(
        self,
        owner_user_id: str,
        query: str,
        limit: int,
        *,
        deadline_started: float,
    ) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
        reference_timeout_ms = self._remaining_context_timeout_ms(deadline_started)
        if self._default_knowledge_reference_loader:
            references = knowledge_activation_service.owner_shared_references(
                owner_user_id=owner_user_id,
                statement_timeout_ms=reference_timeout_ms,
                connection_timeout_seconds=reference_timeout_ms / 1000,
            )
        else:
            references = self.knowledge_reference_loader(owner_user_id)
        if not isinstance(references, list):
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared knowledge references are malformed",
                status=503,
            )
        try:
            reference_bytes = _canonical_bytes(references)
        except (TypeError, ValueError) as exc:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared knowledge references are not canonical JSON",
                status=503,
            ) from exc
        if len(references) > MAX_EVIDENCE_ITEMS or len(reference_bytes) > MAX_EVIDENCE_CHARS:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared knowledge references exceed projection bounds",
                status=503,
            )
        exact_references = copy.deepcopy(references)
        if not references:
            return [], _EMPTY_SHA256, []
        resolver_timeout_ms = self._remaining_context_timeout_ms(deadline_started)
        resolver_kwargs = {
            "owner_user_id": owner_user_id,
            "instance_id": AUTO_RUNTIME_PRINCIPAL_ID,
            "references": references,
            "owner_shared_references": references,
            "require_shared": False,
        }
        if self._default_knowledge_resolver:
            projection, status = knowledge_contract.resolve_knowledge_references(
                **resolver_kwargs,
                statement_timeout_ms=resolver_timeout_ms,
                connection_timeout_seconds=resolver_timeout_ms / 1000,
                deadline_monotonic=monotonic() + (resolver_timeout_ms / 1000),
            )
        else:
            projection, status = self.knowledge_resolver(**resolver_kwargs)
        if status != 200 or projection.get("success") is not True:
            failures = projection.get("failures") if isinstance(projection, dict) else None
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                f"required owner-shared knowledge is unresolved: {failures or 'unknown'}",
                status=503,
            )
        claims = (
            knowledge_contract.select_claims(
                list(projection.get("artifacts") or []), query, limit=limit
            )
            if limit > 0
            else []
        )
        set_hash = str((projection.get("shared_corpus") or {}).get("setHash") or "")
        return (
            claims,
            set_hash if _SHA256.fullmatch(set_hash) else _sha256_value(references),
            exact_references,
        )

    def _knowledge_from_snapshot(
        self,
        owner_user_id: str,
        query: str,
        limit: int,
        *,
        activation_rows: list[dict[str, Any]],
        artifact_rows: list[dict[str, Any]],
        expected_references: list[dict[str, Any]],
        deadline_started: float,
    ) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
        """Resolve canonical knowledge from the verified transaction bundle.

        This is not a cache shortcut. Activation receipts, publication hashes,
        signatures, owner binding, revision, and requested digests receive the
        same verification as the database-backed resolver; only the redundant
        PostgreSQL round trips are removed.
        """
        references = knowledge_activation_service.owner_shared_references_from_rows(
            activation_rows,
            owner_user_id=owner_user_id,
        )
        if _canonical_bytes(references) != _canonical_bytes(expected_references):
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared activation snapshot is inconsistent",
                status=503,
            )
        if len(references) > MAX_EVIDENCE_ITEMS or len(_canonical_bytes(references)) > MAX_EVIDENCE_CHARS:
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                "owner-shared knowledge references exceed projection bounds",
                status=503,
            )
        if not references:
            return [], _EMPTY_SHA256, []
        remaining_ms = self._remaining_context_timeout_ms(deadline_started)
        projection, status = knowledge_contract.resolve_knowledge_references(
            owner_user_id=owner_user_id,
            instance_id=AUTO_RUNTIME_PRINCIPAL_ID,
            references=references,
            owner_shared_references=references,
            require_shared=False,
            deadline_monotonic=monotonic() + (remaining_ms / 1000),
            canonical_rows=artifact_rows,
        )
        if status != 200 or projection.get("success") is not True:
            failures = projection.get("failures") if isinstance(projection, dict) else None
            raise AutoRuntimeContractError(
                "AUTO_REQUIRED_KNOWLEDGE_UNAVAILABLE",
                f"required owner-shared knowledge is unresolved: {failures or 'unknown'}",
                status=503,
            )
        claims = (
            knowledge_contract.select_claims(
                list(projection.get("artifacts") or []), query, limit=limit
            )
            if limit > 0
            else []
        )
        set_hash = str((projection.get("shared_corpus") or {}).get("setHash") or "")
        return (
            claims,
            set_hash if _SHA256.fullmatch(set_hash) else _sha256_value(references),
            copy.deepcopy(references),
        )

    def _cache_get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        instant = self.monotonic_clock()
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if instant - entry.created_at > CACHE_TTL_SECONDS:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            result = copy.deepcopy(entry.projection)
        result["cache"] = {"state": "fresh", "usable": True}
        return result

    def _cache_candidate(
        self, prefix: tuple[Any, ...]
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        """Return a request-matched candidate pending canonical revalidation."""
        instant = self.monotonic_clock()
        with self._cache_lock:
            for key in reversed(list(self._cache)):
                entry = self._cache[key]
                if instant - entry.created_at > CACHE_TTL_SECONDS:
                    self._cache.pop(key, None)
                    continue
                if key[: len(prefix)] != prefix:
                    continue
                projection = copy.deepcopy(entry.projection)
                projection["cache"] = {"state": "fresh", "usable": True}
                return key, projection
        return None

    def _cache_evict_prefix(self, prefix: tuple[Any, ...]) -> None:
        with self._cache_lock:
            for key in list(self._cache):
                if key[: len(prefix)] == prefix:
                    self._cache.pop(key, None)

    def _cache_put(self, key: tuple[Any, ...], projection: dict[str, Any]) -> None:
        stored = copy.deepcopy(projection)
        stored["cache"] = {"state": "fresh", "usable": True}
        with self._cache_lock:
            self._cache[key] = _CacheEntry(stored, self.monotonic_clock())
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def evict_all(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def evict_owner_thread(self, owner_user_id: str, thread_id: str) -> None:
        with self._cache_lock:
            for key in list(self._cache):
                if key[0] == owner_user_id and key[1] == thread_id:
                    self._cache.pop(key, None)

    def _ensure_context_deadline(self, started_at: float) -> None:
        elapsed_ms = (self.monotonic_clock() - started_at) * 1000
        if elapsed_ms > CONTEXT_DEADLINE_MS:
            raise AutoRuntimeContractError(
                "AUTO_CONTEXT_DEADLINE_EXCEEDED",
                "canonical context exceeded its bounded deadline",
                status=503,
            )

    @staticmethod
    def _normalized_context_revisions(
        revisions: dict[str, str]
    ) -> tuple[dict[str, str], str]:
        revision_components = {
            key: revisions[key]
            for key in (
                "profile", "transcript", "knowledge", "accountContext",
                "actionAuthority",
            )
        }
        for field, value in revision_components.items():
            _sha256_field(value, f"{field} revision")
        hydro_revision = str(revisions.get("hydroLifecycle") or _EMPTY_SHA256)
        _sha256_field(hydro_revision, "hydroLifecycle revision")
        return ({
            **revision_components,
            "revisionVector": _sha256_value(revision_components),
        }, hydro_revision)

    def _remaining_context_timeout_ms(self, started_at: float) -> int:
        elapsed_ms = (self.monotonic_clock() - started_at) * 1000
        remaining_ms = CONTEXT_DEADLINE_MS - elapsed_ms
        if remaining_ms <= 0:
            raise AutoRuntimeContractError(
                "AUTO_CONTEXT_DEADLINE_EXCEEDED",
                "canonical context exceeded its bounded deadline",
                status=503,
            )
        return max(1, min(int(remaining_ms), CONTEXT_STATEMENT_TIMEOUT_MS))

    def context(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError("AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403)
        thread_id, query, memory_limit, character_budget, ttl = self._validate_context_request(request)
        self._preflight_signer()
        deadline_started = self.monotonic_clock()
        title = _thread_title(thread_id)
        snapshot_reader = getattr(self.repository, "context_snapshot", None)
        revision_reader = getattr(self.repository, "revision_snapshot", None)
        request_prefix = (
            owner,
            thread_id,
            _sha256_bytes(query.encode("utf-8")),
            memory_limit,
            character_budget,
        )
        snapshot = None
        # A cached projection is only a candidate.  Its exact canonical
        # revision vector is recomputed from PostgreSQL actual-byte hashes on
        # every hit before any cached authority is returned.
        candidate = self._cache_candidate(request_prefix) if callable(revision_reader) else None
        if candidate is not None:
            candidate_key, candidate_projection = candidate
            try:
                candidate_raw = revision_reader(
                    owner_user_id=owner,
                    thread_id=thread_id,
                    transcript_title=title,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
                candidate_revisions, candidate_hydro = self._normalized_context_revisions(
                    candidate_raw
                )
                self._ensure_context_deadline(deadline_started)
                current_key = (
                    *request_prefix,
                    candidate_hydro,
                    tuple(sorted(candidate_revisions.items())),
                )
                if current_key == candidate_key:
                    return self._sign(candidate_projection, ttl_seconds=ttl)
                self._cache_evict_prefix(request_prefix)
            except Exception as exc:
                unavailable_reason = (
                    exc.code
                    if isinstance(exc, AutoRuntimeContractError)
                    else "AUTO_SOURCE_REVISION_UNAVAILABLE"
                )
                payload = self._unavailable_projection(
                    owner, thread_id, unavailable_reason, cache_state="unavailable"
                )
                return self._sign(payload, ttl_seconds=ttl)
        try:
            if callable(snapshot_reader):
                snapshot = snapshot_reader(
                    owner_user_id=owner,
                    thread_id=thread_id,
                    transcript_title=title,
                    memory_limit=memory_limit,
                    character_budget=character_budget,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
                revisions = snapshot["revisions"]
            else:
                revisions = self.repository.source_revisions(
                    owner_user_id=owner,
                    transcript_title=title,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
            self._ensure_context_deadline(deadline_started)
        except Exception as exc:
            unavailable_reason = (
                exc.code
                if isinstance(exc, AutoRuntimeContractError)
                else "AUTO_SOURCE_REVISION_UNAVAILABLE"
            )
            payload = self._unavailable_projection(
                owner, thread_id, unavailable_reason, cache_state="unavailable"
            )
            return self._sign(payload, ttl_seconds=ttl)
        raw_revisions = revisions
        revisions, hydro_revision = self._normalized_context_revisions(raw_revisions)
        revision_components = {
            key: revisions[key]
            for key in (
                "profile", "transcript", "knowledge", "accountContext",
                "actionAuthority",
            )
        }
        cache_key = (
            *request_prefix,
            hydro_revision,
            tuple(sorted(revisions.items())),
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            try:
                self._ensure_context_deadline(deadline_started)
            except AutoRuntimeContractError:
                payload = self._unavailable_projection(
                    owner, thread_id, "AUTO_CONTEXT_DEADLINE_EXCEEDED",
                    source_revisions=revisions, cache_state="unavailable",
                )
                return self._sign(payload, ttl_seconds=ttl)
            return self._sign(cached, ttl_seconds=ttl)
        try:
            profile = (
                snapshot["profile"]
                if snapshot is not None
                else self.repository.load_canonical_profile(
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started)
                )
            )
            self._ensure_context_deadline(deadline_started)
            if profile.get("hashes", {}).get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256:
                raise AutoRuntimeContractError(
                    "AUTO_CANONICAL_PROFILE_UNVERIFIABLE", "AUTO canonical profile hash is unsupported", status=503
                )
            transcript = (
                snapshot["transcript"]
                if snapshot is not None
                else self.repository.read_thread(
                    owner_user_id=owner,
                    transcript_title=title,
                    memory_limit=memory_limit,
                    character_budget=character_budget,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
            )
            self._ensure_context_deadline(deadline_started)
            hydro = (
                snapshot["hydro"]
                if snapshot is not None
                else self.repository.read_hydro_lifecycle(
                    owner_user_id=owner,
                    thread_id=thread_id,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
            )
            self._ensure_context_deadline(deadline_started)
            if hydro.get("revision") != hydro_revision:
                raise AutoRuntimeContractError(
                    "AUTO_CONTEXT_SOURCE_CHANGED",
                    "canonical Hydro lifecycle changed while the projection was being built",
                    status=503,
                )
            action_lifecycle = (
                {"revision": revisions["actionAuthority"], "activeAction": None}
                if snapshot is not None and not snapshot["actionRowsPresent"]
                else _project_action_lifecycle(
                    self.repository,
                    owner_user_id=owner,
                    thread_id=thread_id,
                    revision=revisions["actionAuthority"],
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
            )
            self._ensure_context_deadline(deadline_started)
            latest_event = transcript.get("latestEvent")
            latest_receipt = None
            if isinstance(latest_event, dict) and latest_event.get("decisionContext") is not None:
                latest_receipt = (
                    self.repository.exchange_receipt_from_snapshot(
                        snapshot["exchangeReceiptRows"],
                        owner_user_id=owner,
                        thread_id=thread_id,
                        turn_id=str(latest_event.get("turnId") or ""),
                    )
                    if snapshot is not None
                    else self.repository.read_exchange_receipt(
                        owner_user_id=owner,
                        thread_id=thread_id,
                        turn_id=str(latest_event.get("turnId") or ""),
                        timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                    )
                )
                if latest_receipt is None:
                    raise AutoRuntimeContractError(
                        "AUTO_EXCHANGE_READBACK_FAILED",
                        "decision-bound AUTO exchange has no canonical append receipt",
                        status=503,
                    )
            transcript = {**transcript, "latestReceipt": latest_receipt}
            self._ensure_context_deadline(deadline_started)
            if (
                snapshot is not None
                and self._default_knowledge_reference_loader
                and self._default_knowledge_resolver
            ):
                claims, knowledge_set_hash, knowledge_references = self._knowledge_from_snapshot(
                    owner,
                    query,
                    memory_limit,
                    activation_rows=snapshot["activationRows"],
                    artifact_rows=snapshot["knowledgeArtifactRows"],
                    expected_references=snapshot["knowledgeReferences"],
                    deadline_started=deadline_started,
                )
            elif snapshot is not None and not snapshot["knowledgeReferences"]:
                claims, knowledge_set_hash, knowledge_references = [], _EMPTY_SHA256, []
            else:
                claims, knowledge_set_hash, knowledge_references = self._knowledge(
                    owner, query, memory_limit, deadline_started=deadline_started
                )
            self._ensure_context_deadline(deadline_started)
            account_timeout_ms = self._remaining_context_timeout_ms(deadline_started)
            if snapshot is not None and self._default_account_context_loader:
                account_context = (
                    account_context_service.read_projection_row(
                        snapshot["accountRow"], owner_user_id=owner
                    )
                    if snapshot["accountRow"] is not None
                    else None
                )
            elif self._default_account_context_loader:
                account_context = self._load_optional_account_context(
                    owner, timeout_ms=account_timeout_ms
                )
            else:
                account_context = self.account_context_loader(owner)
            self._ensure_context_deadline(deadline_started)
            if callable(snapshot_reader):
                # The consolidated read is one PostgreSQL MVCC statement: all
                # profile, continuity, Hydro, action, knowledge, and account
                # bytes and revisions are from the same final canonical
                # snapshot. Local validation performs no further authority
                # reads, so a second remote statement would not improve
                # cross-source consistency. Warm reuse is separately guarded
                # by revision_snapshot on every hit.
                verified_revisions = raw_revisions
            else:
                verified_revisions = self.repository.source_revisions(
                    owner_user_id=owner,
                    transcript_title=title,
                    timeout_ms=self._remaining_context_timeout_ms(deadline_started),
                )
            self._ensure_context_deadline(deadline_started)
            if any(
                verified_revisions.get(key) != revision_components[key]
                for key in revision_components
            ) or verified_revisions.get("hydroLifecycle") != hydro_revision:
                raise AutoRuntimeContractError(
                    "AUTO_CONTEXT_SOURCE_CHANGED",
                    "canonical context changed while the projection was being built",
                    status=503,
                )
        except Exception as exc:
            unavailable_reason = (
                exc.code
                if isinstance(exc, AutoRuntimeContractError)
                else "AUTO_CANONICAL_CONTEXT_UNAVAILABLE"
            )
            payload = self._unavailable_projection(
                owner,
                thread_id,
                unavailable_reason,
                source_revisions=revisions,
                cache_state="miss",
            )
            return self._sign(payload, ttl_seconds=ttl)
        lexical = self._lexical_matches(list(transcript.get("events") or []), query, memory_limit)
        evidence: list[dict[str, Any]] = []
        remaining_chars = min(character_budget, MAX_EVIDENCE_CHARS)
        candidates: list[tuple[str, str, str, str, Any]] = []
        for index, event in enumerate(lexical):
            candidates.append(
                (
                    f"transcript:{index + 1}",
                    "dialogue_state",
                    title,
                    str(transcript.get("sha256") or _EMPTY_SHA256),
                    event,
                )
            )
        for index, claim in enumerate(claims):
            candidates.append(
                (
                    f"knowledge:{index + 1}",
                    "knowledge",
                    str(claim.get("document_artifact_id") or "owner-shared"),
                    str(claim.get("document_sha256") or knowledge_set_hash),
                    claim,
                )
            )
        if account_context is not None:
            candidates.append(
                (
                    "account-context:1",
                    "canonical_context",
                    "ovvaults.vault_files/account-context",
                    str(account_context.get("contentHash") or revisions["accountContext"]),
                    account_context,
                )
            )
        for evidence_id, kind, source, revision, content in candidates[:MAX_EVIDENCE_ITEMS]:
            content_text = content if isinstance(content, str) else _canonical_bytes(content).decode("utf-8")
            if len(content_text) > remaining_chars:
                continue
            evidence.append(self._evidence(evidence_id, kind, source, revision, content_text))
            remaining_chars -= len(content_text)
        source_revisions = {
            **revisions,
            "knowledgeSelection": knowledge_set_hash,
        }
        continuation_basis = _continuation_basis(
            owner_user_id=owner,
            thread_id=thread_id,
            transcript=transcript,
            source_revisions=source_revisions,
        )
        evidence_revision = _sha256_value(
            {
                "ownerId": owner,
                "threadId": thread_id,
                "sourceRevisions": source_revisions,
                "evidence": [item["reference"] for item in evidence],
                "knowledgeReferences": knowledge_references,
            }
        )
        projection = {
            "contract": CONTEXT_PROJECTION_CONTRACT,
            "status": "ready",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "profile": profile,
            "transcript": transcript,
            "hydro": hydro,
            "actionLifecycle": action_lifecycle,
            "evidence": evidence,
            "knowledgeReferences": knowledge_references,
            "sourceRevisions": source_revisions,
            "evidenceRevision": evidence_revision,
            "continuationBasis": continuation_basis,
            "cache": {"state": "miss", "usable": True},
            "unavailableReason": None,
        }
        self._cache_put(cache_key, projection)
        return self._sign(projection, ttl_seconds=ttl)

    @staticmethod
    def _unavailable_projection(
        owner_user_id: str,
        thread_id: str,
        reason: str,
        *,
        source_revisions: dict[str, str] | None = None,
        cache_state: str,
    ) -> dict[str, Any]:
        revisions = source_revisions or {
            "profile": _EMPTY_SHA256,
            "transcript": _EMPTY_SHA256,
            "knowledge": _EMPTY_SHA256,
            "accountContext": _EMPTY_SHA256,
            "actionAuthority": _EMPTY_SHA256,
        }
        if "revisionVector" not in revisions:
            revisions = {
                **revisions,
                "revisionVector": _sha256_value(
                    {
                        key: revisions[key]
                        for key in (
                            "profile", "transcript", "knowledge", "accountContext",
                            "actionAuthority",
                        )
                    }
                ),
            }
        return {
            "contract": CONTEXT_PROJECTION_CONTRACT,
            "status": "unavailable",
            "ownerId": owner_user_id,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "profile": None,
            "transcript": {"events": [], "latestResult": None, "dialogueState": None, "latestEvent": None, "latestReceipt": None},
            "hydro": {
                "revision": _EMPTY_SHA256,
                "lifecycleHead": None,
                "activeGraph": None,
                "latestGraph": None,
                "latestAggregate": None,
            },
            "actionLifecycle": {
                "revision": revisions["actionAuthority"],
                "activeAction": None,
            },
            "evidence": [],
            "knowledgeReferences": [],
            "sourceRevisions": revisions,
            "evidenceRevision": _sha256_value(
                {"ownerId": owner_user_id, "threadId": thread_id, "sourceRevisions": revisions}
            ),
            "continuationBasis": None,
            "cache": {"state": cache_state, "usable": False},
            "unavailableReason": str(reason or "canonical evidence unavailable")[:512],
        }

    def thread_index(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403
            )
        if not isinstance(request, dict) or set(request) - {"contract", "limit", "cursor"}:
            raise AutoRuntimeContractError(
                "AUTO_THREAD_INDEX_INVALID", "AUTO thread index request fields are invalid"
            )
        if request.get("contract") != THREAD_INDEX_REQUEST_CONTRACT:
            raise AutoRuntimeContractError(
                "AUTO_THREAD_INDEX_INVALID", "AUTO thread index contract is unsupported"
            )
        limit = request.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise AutoRuntimeContractError(
                "AUTO_THREAD_INDEX_INVALID", "AUTO thread index limit must be from 1 through 50"
            )
        cursor = request.get("cursor")
        offset = 0
        if cursor not in (None, ""):
            try:
                decoded = base64.urlsafe_b64decode(str(cursor) + "===").decode("ascii")
                offset = int(decoded)
            except (ValueError, UnicodeError) as exc:
                raise AutoRuntimeContractError(
                    "AUTO_THREAD_INDEX_INVALID", "AUTO thread index cursor is invalid"
                ) from exc
            if offset < 0 or offset > 1_000_000:
                raise AutoRuntimeContractError(
                    "AUTO_THREAD_INDEX_INVALID", "AUTO thread index cursor is out of range"
                )
        self._preflight_signer()
        rows = self.repository.list_auto_threads(
            owner_user_id=owner, limit=limit + 1, offset=offset
        )
        has_more = len(rows) > limit
        threads = rows[:limit]
        next_cursor = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(str(offset + limit).encode("ascii")).decode("ascii").rstrip("=")
        payload = {
            "contract": THREAD_INDEX_PROJECTION_CONTRACT,
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threads": threads,
            "nextCursor": next_cursor,
            "indexRevision": _sha256_value(threads),
        }
        return self._sign(payload, ttl_seconds=DEFAULT_TTL_SECONDS)

    def hydro_catalog(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403
            )
        allowed = {"contract", "threadId", "executorAttestations"}
        if not isinstance(request, dict) or set(request) - allowed \
                or request.get("contract") != HYDRO_CATALOG_REQUEST_CONTRACT:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_CATALOG_INVALID", "AUTO Hydro catalog request is invalid"
            )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        raw_attestations = request.get("executorAttestations") or []
        if not isinstance(raw_attestations, list) or len(raw_attestations) > MAX_HYDRO_INSTANCES:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_CATALOG_INVALID", "executor attestations are invalid"
            )
        attestations: dict[str, dict[str, Any]] = {}
        for raw_attestation in raw_attestations:
            attestation = _validate_hydro_executor_attestation(
                raw_attestation, owner_user_id=owner, thread_id=thread_id
            )
            worker_ref = attestation["workerRef"]
            if worker_ref in attestations:
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
                    "executor attestations must be unique by worker reference",
                )
            attestations[worker_ref] = attestation
        profile = self.repository.load_canonical_profile()
        if profile.get("hashes", {}).get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256:
            raise AutoRuntimeContractError(
                "AUTO_CANONICAL_PROFILE_UNVERIFIABLE", "AUTO canonical profile is unavailable", status=503
            )
        workers = _deterministic_hydro_workers()
        construct_result = chatty_body_service.list_constructs(owner, include_hidden=True)
        model_result = chatty_body_service.list_byop_models(owner)
        if construct_result.http_status != 200 or model_result.http_status != 200:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_CATALOG_UNAVAILABLE", "canonical worker sources are unavailable", status=503
            )
        construct_basis = []
        for construct in list(construct_result.payload.get("constructs") or []):
            callsign = str(construct.get("callsign") or construct.get("construct_id") or "").strip().lower()
            if not callsign or callsign == AUTO_RUNTIME_PRINCIPAL_ID or not _SAFE_ID.fullmatch(callsign):
                continue
            if construct.get("projectableToChatty") is not True:
                continue
            display = str(construct.get("displayName") or construct.get("name") or callsign).strip()[:128]
            aliases = list(dict.fromkeys([callsign, display]))
            worker_id = f"construct-{_sha256_bytes(callsign.encode('utf-8'))[:24]}"
            attestation = attestations.pop(worker_id, None)
            if attestation is None:
                continue
            identity_result = chatty_body_service.identity(callsign, owner_user_id=owner)
            expression = (
                identity_result.payload.get("expressionProjection")
                if identity_result.http_status == 200 else None
            )
            expression_revision = str((expression or {}).get("revision") or "")
            expected_routing_revision = _sha256_value({
                "expressionRevision": expression_revision,
                "model": attestation["model"],
                "provider": attestation["provider"],
            })
            if (
                attestation.get("kind") != "construct"
                or attestation.get("workerPrincipalId") != callsign
                or attestation.get("expressionRevision") != expression_revision
                or not _SHA256.fullmatch(expression_revision)
                or attestation.get("routingRevision") != expected_routing_revision
                or attestation.get("capabilities") != ["analyze"]
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
                    "construct executor route or expression has drifted",
                    status=409,
                )
            workers.append(_hydro_descriptor(
                worker_id=worker_id, display_name=display, aliases=aliases,
                kind="construct", phase="analyze", worker_principal_id=callsign,
                provider=attestation["provider"], model=attestation["model"],
                routing_revision=attestation["routingRevision"],
                capabilities=["analyze"], memory_bytes=attestation["memoryBytes"],
            ))
            construct_basis.append({
                "callsign": callsign,
                "expressionRevision": expression_revision,
                "provider": attestation["provider"],
                "model": attestation["model"],
                "routingRevision": attestation["routingRevision"],
                "attestationSha256": attestation["attestationSha256"],
            })
        model_basis = []
        for model_entry in list(model_result.payload.get("models") or []):
            provider = str(model_entry.get("provider") or "").strip().lower()
            model = str(model_entry.get("model") or "").strip()
            if not provider or not model:
                continue
            target = f"{provider}:{model}"
            display = str(model_entry.get("name") or target).strip()[:128]
            worker_id = f"model-{_sha256_bytes(target.encode('utf-8'))[:24]}"
            attestation = attestations.pop(worker_id, None)
            if attestation is None:
                continue
            expected_routing_revision = _sha256_value({
                "expressionRevision": None, "model": model, "provider": provider,
            })
            if (
                attestation.get("kind") != "model"
                or attestation.get("workerPrincipalId") is not None
                or attestation.get("provider") != provider
                or attestation.get("model") != model
                or attestation.get("routingRevision") != expected_routing_revision
                or attestation.get("capabilities") != ["analyze"]
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
                    "model executor route has drifted",
                    status=409,
                )
            workers.append(_hydro_descriptor(
                worker_id=worker_id, display_name=display, aliases=list(dict.fromkeys([target, display])),
                kind="model", phase="analyze", worker_principal_id=None,
                provider=provider, model=model, routing_revision=attestation["routingRevision"],
                capabilities=["analyze"], memory_bytes=attestation["memoryBytes"],
            ))
            model_basis.append({
                "provider": provider, "model": model,
                "routingRevision": attestation["routingRevision"],
                "attestationSha256": attestation["attestationSha256"],
            })
        if attestations:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EXECUTOR_ATTESTATION_INVALID",
                "an executor attestation is not eligible in the canonical owner catalog",
                status=409,
            )
        revision = _sha256_value({
            "profile": AUTO_PROFILE_COMBINED_SHA256,
            "constructs": construct_basis,
            "models": model_basis,
            "workers": [worker["descriptorSha256"] for worker in workers],
        })
        return self._sign_hydro_catalog({
            "contract": HYDRO_CATALOG_CONTRACT,
            "ownerUserId": owner,
            "revision": revision,
            "workers": workers,
        })

    def hydro_recovery_index(self, *, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"contract", "cursor", "limit"} \
                or request.get("contract") != HYDRO_RECOVERY_INDEX_REQUEST_CONTRACT:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_RECOVERY_INDEX_INVALID", "Hydro recovery index request is invalid"
            )
        limit = request.get("limit")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_RECOVERY_INDEX_INVALID", "Hydro recovery index limit is invalid"
            )
        cursor = request.get("cursor")
        offset = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 256:
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_RECOVERY_INDEX_INVALID", "Hydro recovery cursor is invalid"
                )
            try:
                decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
                offset = int(decoded)
            except (ValueError, UnicodeError) as exc:
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_RECOVERY_INDEX_INVALID", "Hydro recovery cursor is invalid"
                ) from exc
            if offset < 0 or offset > 1_000_000:
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_RECOVERY_INDEX_INVALID", "Hydro recovery cursor is out of range"
                )
        self._preflight_signer()
        rows = self.repository.list_hydro_recovery_records(
            limit=limit + 1, offset=offset
        )
        has_more = len(rows) > limit
        graphs = []
        terminals = {"completed", "failed", "timed_out", "cancelled", "unknown", "blocked"}
        for row in rows[:limit]:
            dispatch = row.get("dispatch") if isinstance(row, dict) else None
            graph = dispatch.get("graph") if isinstance(dispatch, dict) else None
            grant = dispatch.get("executionGrant") if isinstance(dispatch, dict) else None
            owner = str(row.get("ownerId") or "")
            thread_id = str(row.get("threadId") or "")
            if (
                dispatch.get("contract") != "chatty-auto-hydro-dispatch-record/v1"
                or dispatch.get("ownerId") != owner
                or dispatch.get("threadId") != thread_id
                or not isinstance(graph, dict)
                or graph.get("threadId") != thread_id
                or graph.get("ruleset") != {
                    "version": AUTO_ACTIVE_RULESET_VERSION,
                    "revision": AUTO_ACTIVE_RULESET_REVISION,
                    "sha256": AUTO_ACTIVE_RULESET_SHA256,
                }
                or graph.get("profile", {}).get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
                or not isinstance(grant, dict)
                or grant.get("ownerId") != owner
                or grant.get("threadId") != thread_id
                or grant.get("graphId") != graph.get("graphId")
                or grant.get("graphSha256") != graph.get("canonicalSha256")
                or grant.get("grantSha256") != _sha256_value({
                    key: value for key, value in grant.items() if key != "grantSha256"
                })
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_RECOVERY_INDEX_UNVERIFIABLE",
                    "canonical Hydro recovery dispatch is inconsistent",
                    status=503,
                )
            states = row.get("workerStates") if isinstance(row.get("workerStates"), dict) else {}
            execution_ids = [str(item.get("executionId") or "") for item in graph.get("instances") or []]
            if not execution_ids or all(states.get(execution_id) in terminals for execution_id in execution_ids):
                continue
            unverifiable_started = 0
            roster_entries = graph.get("rosterEntries") or []
            for item in graph.get("instances") or []:
                entry_index = item.get("entryIndex")
                if not isinstance(entry_index, int) or not 0 <= entry_index < len(roster_entries):
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_RECOVERY_INDEX_UNVERIFIABLE",
                        "recovery graph instance is not bound to its roster",
                        status=503,
                    )
                descriptor = roster_entries[entry_index].get("descriptor")
                if not isinstance(descriptor, dict):
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_RECOVERY_INDEX_UNVERIFIABLE",
                        "recovery graph descriptor is malformed",
                        status=503,
                    )
                if states.get(str(item.get("executionId") or "")) == "started" \
                        and descriptor.get("kind") in {"construct", "model"}:
                    unverifiable_started += 1
            graphs.append({
                "ownerId": owner,
                "threadId": thread_id,
                "graph": copy.deepcopy(graph),
                "executionGrant": copy.deepcopy(grant),
                "workerStates": copy.deepcopy(states),
                "maxConcurrent": max(1, min(4, int((dispatch.get("capacity") or {}).get("maxConcurrent") or 4))),
                "unverifiableStartedCount": unverifiable_started,
            })
        next_cursor = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(
                str(offset + limit).encode("ascii")
            ).decode("ascii").rstrip("=")
        payload = {
            "contract": HYDRO_RECOVERY_INDEX_CONTRACT,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "graphs": graphs,
            "nextCursor": next_cursor,
            "indexRevision": _sha256_value(graphs),
        }
        payload["payloadSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=DEFAULT_TTL_SECONDS)

    def _canonical_dispatch_record(
        self, *, owner: str, thread_id: str, request: dict[str, Any]
    ) -> tuple[dict[str, Any], str, str]:
        graph = request.get("graph")
        if not isinstance(graph, dict) or graph.get("contract") != "chatty-auto-hydro-task-graph/v1":
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro graph is invalid")
        graph_id = _safe_identifier(graph.get("graphId"), "graphId")
        graph_sha = _sha256_field(graph.get("canonicalSha256"), "graph.canonicalSha256")
        graph_body = copy.deepcopy(graph)
        graph_body.pop("canonicalSha256", None)
        if _sha256_value(graph_body) != graph_sha or graph.get("threadId") != thread_id:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro graph bytes are inconsistent")
        if graph.get("profile", {}).get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro graph profile is unsupported")
        if graph.get("ruleset") != {
            "version": AUTO_ACTIVE_RULESET_VERSION,
            "revision": AUTO_ACTIVE_RULESET_REVISION,
            "sha256": AUTO_ACTIVE_RULESET_SHA256,
        }:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro graph ruleset is unsupported")
        instances = graph.get("instances")
        if not isinstance(instances, list) or not 1 <= len(instances) <= MAX_HYDRO_INSTANCES:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro instances are invalid")
        interaction_policy = _validate_interaction_policy(
            graph.get("interactionPolicy")
        )
        hydro_delegation = _validate_hydro_delegation(
            graph.get("hydroDelegation")
        )
        if (
            not hydro_delegation["required"]
            or hydro_delegation["workerCount"] != len(instances)
            or not hydro_delegation["delegatesAllExecution"]
            or hydro_delegation["directExecution"]
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_DISPATCH_INVALID",
                "Hydro graph does not prove mandatory complete delegation",
            )
        roster_entries = graph.get("rosterEntries")
        if not isinstance(roster_entries, list) or not roster_entries:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro roster is invalid")
        for entry in roster_entries:
            descriptor = entry.get("descriptor") if isinstance(entry, dict) else None
            if not isinstance(descriptor, dict):
                raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro worker descriptor is invalid")
            kind = descriptor.get("kind")
            if kind in {"construct", "model"}:
                if (
                    descriptor.get("provider") not in {"ollama", "openai", "openrouter"}
                    or not str(descriptor.get("model") or "").strip()
                    or not _SHA256.fullmatch(str(descriptor.get("routingRevision") or ""))
                    or (kind == "construct" and not _SAFE_ID.fullmatch(str(descriptor.get("workerPrincipalId") or "")))
                    or (kind == "model" and descriptor.get("workerPrincipalId") is not None)
                ):
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_DISPATCH_INVALID",
                        "external Hydro workers require exact approved provider/model routing evidence",
                    )
            elif kind == "deterministic":
                if descriptor.get("provider") is not None or descriptor.get("model") is not None:
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_DISPATCH_INVALID",
                        "deterministic Hydro workers may not use a provider or model",
                    )
            else:
                raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro worker kind is unsupported")
        authorization = request.get("authorization")
        authorization_receipt = request.get("authorizationReceipt")
        if not isinstance(authorization, dict) or not isinstance(authorization_receipt, dict):
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro authorization is invalid")
        if authorization.get("graphId") != graph_id or authorization.get("graphCanonicalSha256") != graph_sha:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro authorization graph is inconsistent")
        if authorization_receipt.get("ownerId") != owner or authorization_receipt.get("threadId") != thread_id \
                or authorization_receipt.get("graphId") != graph_id \
                or authorization_receipt.get("eventType") != "graph.authorization_verified":
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro authorization receipt is invalid")
        expires = self.now().astimezone(timezone.utc) + timedelta(hours=1)
        grant = {
            "contract": "chatty-auto-hydro-execution-grant/v1",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": graph_id,
            "graphSha256": graph_sha,
            "authorizationReceiptSha256": authorization_receipt.get("receiptSha256"),
            "executionIds": [instance.get("executionId") for instance in instances],
            "capabilitySha256": graph.get("capabilitySha256"),
            "interactionPolicy": interaction_policy,
            "hydroDelegation": hydro_delegation,
            "expiresAt": _iso(expires),
        }
        grant["grantSha256"] = _sha256_value(grant)
        record = {
            "contract": "chatty-auto-hydro-dispatch-record/v1",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": graph_id,
            "graphSha256": graph_sha,
            "graph": copy.deepcopy(graph),
            "authorization": copy.deepcopy(authorization),
            "authorizationReceipt": copy.deepcopy(authorization_receipt),
            "capacity": copy.deepcopy(request.get("capacity")),
            "executionGrant": grant,
        }
        return record, graph_id, graph_sha

    def hydro_dispatch(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        expected = {"contract", "threadId", "graph", "authorization", "authorizationReceipt", "capacity", "idempotencyKey"}
        if not owner or not isinstance(request, dict) or set(request) != expected \
                or request.get("contract") != HYDRO_DISPATCH_REQUEST_CONTRACT:
            raise AutoRuntimeContractError("AUTO_HYDRO_DISPATCH_INVALID", "Hydro dispatch request is invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        idempotency_key = _safe_identifier(request.get("idempotencyKey"), "idempotencyKey")
        record, graph_id, graph_sha = self._canonical_dispatch_record(
            owner=owner, thread_id=thread_id, request=request
        )
        request_sha = _sha256_value(request)
        stored = self.repository.store_hydro_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="dispatches",
            record_id=graph_id, request_sha256=request_sha, record=record,
            recorded_at=_iso(self.now()),
        )
        canonical = stored["record"]
        payload = {
            "contract": HYDRO_DISPATCH_RECEIPT_CONTRACT,
            "status": "accepted" if stored["created"] else "idempotent_readback",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": graph_id,
            "graphSha256": graph_sha,
            "idempotencyKey": idempotency_key,
            "executionGrant": canonical["executionGrant"],
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def hydro_cancellation(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        required = {"contract", "threadId", "graphId", "graphSha256", "executionId", "cancellationEventId", "cancellationEventSha256", "lifecycleReceiptSha256", "idempotencyKey"}
        if not owner or not isinstance(request, dict) or set(request) != required \
                or request.get("contract") != HYDRO_CANCELLATION_REQUEST_CONTRACT:
            raise AutoRuntimeContractError("AUTO_HYDRO_CANCELLATION_INVALID", "Hydro cancellation request is invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        graph_id = _safe_identifier(request.get("graphId"), "graphId")
        execution_id = _safe_identifier(request.get("executionId"), "executionId")
        dispatch = self.repository.read_hydro_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="dispatches", record_id=graph_id
        )
        if not dispatch or dispatch.get("graphSha256") != request.get("graphSha256") \
                or execution_id not in dispatch.get("executionGrant", {}).get("executionIds", []):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_CANCELLATION_UNAUTHORIZED", "Hydro cancellation is not graph-authorized", status=409
            )
        record = {**copy.deepcopy(request), "ownerId": owner, "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID}
        stored = self.repository.store_hydro_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="cancellations",
            record_id=request["idempotencyKey"], request_sha256=_sha256_value(request),
            record=record, recorded_at=_iso(self.now()),
        )
        payload = {
            "contract": HYDRO_CANCELLATION_RECEIPT_CONTRACT,
            "status": "cancellation_requested" if stored["created"] else "idempotent_readback",
            "workerState": "cancellation_requested",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": graph_id,
            "graphSha256": request["graphSha256"],
            "executionId": execution_id,
            "cancellationEventId": request["cancellationEventId"],
            "cancellationEventSha256": request["cancellationEventSha256"],
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def hydro_worker_receipt(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        required = {"contract", "threadId", "graphId", "graphSha256", "workerEventId", "executionId", "workerResult", "workerResultSha256", "executionGrant", "idempotencyKey"}
        if not owner or not isinstance(request, dict) or set(request) != required \
                or request.get("contract") != HYDRO_WORKER_RECEIPT_REQUEST_CONTRACT:
            raise AutoRuntimeContractError("AUTO_HYDRO_WORKER_RECEIPT_INVALID", "Hydro worker receipt request is invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        graph_id = _safe_identifier(request.get("graphId"), "graphId")
        worker_event_id = _safe_identifier(request.get("workerEventId"), "workerEventId")
        execution_id = _safe_identifier(request.get("executionId"), "executionId")
        worker_result = request.get("workerResult")
        if not isinstance(worker_result, dict) or worker_result.get("executionId") != execution_id \
                or worker_result.get("graphId") != graph_id \
                or request.get("workerResultSha256") != _sha256_value(worker_result):
            raise AutoRuntimeContractError("AUTO_HYDRO_WORKER_RECEIPT_INVALID", "Hydro worker result bytes are inconsistent")
        dispatch = self.repository.read_hydro_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="dispatches", record_id=graph_id
        )
        grant = request.get("executionGrant")
        if not dispatch or _canonical_bytes(grant) != _canonical_bytes(dispatch.get("executionGrant")) \
                or grant.get("grantSha256") != _sha256_value({key: value for key, value in grant.items() if key != "grantSha256"}) \
                or execution_id not in grant.get("executionIds", []) \
                or self.now().astimezone(timezone.utc) >= _parse_iso(grant.get("expiresAt")):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_WORKER_UNAUTHORIZED", "Hydro worker execution grant is invalid or expired", status=403
            )
        record = {**copy.deepcopy(request), "ownerId": owner, "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID}
        stored = self.repository.store_hydro_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="worker-receipts",
            record_id=worker_event_id, request_sha256=_sha256_value(request),
            record=record, recorded_at=_iso(self.now()),
        )
        payload = {
            "contract": HYDRO_WORKER_RECEIPT_CONTRACT,
            "status": "accepted" if stored["created"] else "idempotent_readback",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "graphId": graph_id,
            "graphSha256": request["graphSha256"],
            "workerEventId": worker_event_id,
            "executionId": execution_id,
            "workerResult": copy.deepcopy(worker_result),
            "workerResultSha256": request["workerResultSha256"],
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def _current_code_project_binding(
        self, *, owner_user_id: str, project_instance_id: str
    ) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403
            )
        project_id = _safe_identifier(project_instance_id, "projectInstanceId")
        try:
            authority = self.code_project_repository.get_project_authority(
                user_id=owner, project_instance_id=project_id
            )
        except ValueError as exc:
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROJECT_UNVERIFIABLE",
                "canonical Code project evidence is inconsistent",
                status=503,
            ) from exc
        if not authority:
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROJECT_NOT_FOUND",
                "canonical Code project is unavailable for this owner",
                status=404,
            )
        body = {
            "contract": CODE_PROJECT_BINDING_PROJECTION_CONTRACT,
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "projectInstanceId": project_id,
            "threadId": _code_thread_id(project_id),
            "projectName": str(authority.get("projectName") or project_id)[:256],
            "canonicalRootPath": str(authority.get("canonicalRootPath") or "")[:1024],
            "projectRecordSha256": _sha256_field(
                authority.get("projectRecordSha256"), "projectRecordSha256"
            ),
            "projectRevision": _sha256_field(
                authority.get("projectRevision"), "projectRevision"
            ),
            "storagePath": str(authority.get("storagePath") or "")[:1024],
        }
        if (
            not body["canonicalRootPath"]
            or not body["storagePath"]
            or body["canonicalRootPath"] != f".hydro/workspaces/{project_id}"
            or body["storagePath"] != f"code/projects/{project_id}/project.json"
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROJECT_UNVERIFIABLE",
                "canonical Code project paths do not match the project identity",
                status=503,
            )
        body["bindingSha256"] = _sha256_value(body)
        return body

    def code_project_binding(
        self, *, owner_user_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        if (
            not isinstance(request, dict)
            or set(request) != {"contract", "projectInstanceId"}
            or request.get("contract") != CODE_PROJECT_BINDING_REQUEST_CONTRACT
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROJECT_BINDING_INVALID",
                "Code project binding request is invalid",
            )
        body = self._current_code_project_binding(
            owner_user_id=owner_user_id,
            project_instance_id=request.get("projectInstanceId"),
        )
        return self._sign(body, ttl_seconds=DEFAULT_TTL_SECONDS)

    def code_thread_history(
        self, *, owner_user_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "contract", "projectInstanceId", "threadId", "limit", "characterBudget"
        }
        if (
            not isinstance(request, dict)
            or not required.issubset(request)
            or bool(set(request) - required - {"cursor"})
            or request.get("contract") != CODE_THREAD_HISTORY_REQUEST_CONTRACT
            or not isinstance(request.get("limit"), int)
            or not 1 <= request["limit"] <= MAX_CODE_HISTORY_ITEMS
            or not isinstance(request.get("characterBudget"), int)
            or not 1_024 <= request["characterBudget"] <= MAX_CODE_HISTORY_CHARS
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_THREAD_HISTORY_INVALID", "Code thread history request is invalid"
            )
        project = self._current_code_project_binding(
            owner_user_id=owner_user_id,
            project_instance_id=request.get("projectInstanceId"),
        )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        if thread_id != project["threadId"]:
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROJECT_THREAD_MISMATCH",
                "Code thread is not bound to the canonical project",
                status=403,
            )
        transcript = self.repository.read_thread(
            owner_user_id=str(owner_user_id),
            transcript_title=_thread_title(thread_id),
            memory_limit=request["limit"],
            character_budget=request["characterBudget"],
        )
        transcript_revision = str(transcript.get("sha256") or _EMPTY_SHA256)
        cursor = request.get("cursor")
        if cursor is not None and cursor != transcript_revision:
            raise AutoRuntimeContractError(
                "AUTO_CODE_HISTORY_REVISION_DRIFT",
                "Code AUTO history cursor no longer matches the canonical transcript revision",
                status=409,
            )
        events = []
        receipt_fields = (
            "contract", "ownerId", "runtimePrincipalId", "threadId", "turnId",
            "status", "payloadSha256", "transcriptSha256", "receiptSha256",
            "issuedAt", "expiresAt",
        )
        for event in copy.deepcopy(transcript.get("events") or []):
            if isinstance(event, dict) and isinstance(event.get("turnId"), str):
                receipt = self.repository.read_exchange_receipt(
                    owner_user_id=str(owner_user_id), thread_id=thread_id,
                    turn_id=event["turnId"],
                )
                if isinstance(receipt, dict):
                    event["persistenceEvidence"] = {
                        field: receipt.get(field) for field in receipt_fields
                    }
            events.append(event)
        source_revisions = self.repository.source_revisions(
            owner_user_id=str(owner_user_id),
            transcript_title=_thread_title(thread_id),
        )
        action_projection = _project_action_lifecycle(
            self.repository,
            owner_user_id=str(owner_user_id),
            thread_id=thread_id,
            revision=str(source_revisions.get("actionAuthority") or _EMPTY_SHA256),
            timeout_ms=CONTEXT_DEADLINE_MS,
        )
        active_action = action_projection.get("activeAction")
        action_state = None
        if isinstance(active_action, dict):
            descriptor = active_action.get("descriptor")
            latest_action_event = active_action.get("event")
            if isinstance(descriptor, dict) and isinstance(latest_action_event, dict):
                action_state = {
                    "actionId": descriptor.get("actionId"),
                    "actionCanonicalSha256": descriptor.get("canonicalSha256"),
                    "state": active_action.get("state"),
                    "restoredState": active_action.get("restoredState"),
                    "eventId": latest_action_event.get("eventId"),
                    "eventSha256": _sha256_value(latest_action_event),
                }
        hydro_projection = self.repository.read_hydro_lifecycle(
            owner_user_id=str(owner_user_id),
            thread_id=thread_id,
            timeout_ms=CONTEXT_DEADLINE_MS,
        )
        active_hydro = hydro_projection.get("activeGraph")
        hydro_state = None
        if isinstance(active_hydro, dict):
            graph = active_hydro.get("activeGraph")
            if isinstance(graph, dict):
                hydro_state = {
                    "graphId": graph.get("graphId"),
                    "graphSha256": graph.get("canonicalSha256"),
                    "graphState": active_hydro.get("graphState"),
                    "lifecycleHead": copy.deepcopy(hydro_projection.get("lifecycleHead")),
                }
        payload = {
            "contract": CODE_THREAD_HISTORY_PROJECTION_CONTRACT,
            "ownerId": str(owner_user_id),
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "projectInstanceId": project["projectInstanceId"],
            "projectRevision": project["projectRevision"],
            "projectRecordSha256": project["projectRecordSha256"],
            "threadId": thread_id,
            "transcriptRevision": transcript_revision,
            "events": events,
            "actionState": action_state,
            "hydroState": hydro_state,
            "truncated": bool(transcript.get("truncated")),
            "nextCursor": transcript_revision if bool(transcript.get("truncated")) else None,
        }
        payload["historySha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=DEFAULT_TTL_SECONDS)

    def code_proposal_context(
        self, *, owner_user_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        expected = {
            "contract", "threadId", "projectBinding", "proposalContextId",
            "workspaceContextId", "workspaceRootSha256", "context",
            "contextSha256", "idempotencyKey",
        }
        if (
            not owner
            or not isinstance(request, dict)
            or set(request) != expected
            or request.get("contract") != CODE_PROPOSAL_CONTEXT_REQUEST_CONTRACT
            or len(_canonical_bytes(request)) > MAX_CODE_PROPOSAL_CONTEXT_BYTES
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROPOSAL_CONTEXT_INVALID",
                "Code proposal context request is invalid or oversized",
            )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        proposal_context_id = _safe_identifier(
            request.get("proposalContextId"), "proposalContextId"
        )
        idempotency_key = _safe_identifier(
            request.get("idempotencyKey"), "idempotencyKey"
        )
        project_binding = _validate_code_project_binding_payload(
            request.get("projectBinding"),
            owner_user_id=owner,
            thread_id=thread_id,
            now=self.now(),
        )
        current_project = self._current_code_project_binding(
            owner_user_id=owner,
            project_instance_id=project_binding["projectInstanceId"],
        )
        for field in (
            "threadId", "projectRecordSha256", "projectRevision", "canonicalRootPath",
            "storagePath", "bindingSha256",
        ):
            if project_binding.get(field) != current_project.get(field):
                raise AutoRuntimeContractError(
                    "AUTO_CODE_PROJECT_BINDING_STALE",
                    "Code project binding no longer matches canonical project evidence",
                    status=409,
                )
        workspace_context_id = _sha256_field(
            request.get("workspaceContextId"), "workspaceContextId"
        )
        workspace_root_sha256 = _sha256_field(
            request.get("workspaceRootSha256"), "workspaceRootSha256"
        )
        context = request.get("context")
        if (
            not isinstance(context, dict)
            or _contains_forbidden_action_transport_field(context)
            or _sha256_field(request.get("contextSha256"), "contextSha256")
            != _sha256_value(context)
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROPOSAL_CONTEXT_INVALID",
                "Code proposal context bytes are invalid or unsafe",
            )
        request_sha256 = _sha256_value(request)
        recorded_at = _iso(self.now())
        record = {
            "contract": "chatty-auto-code-proposal-context-record/v1",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "projectInstanceId": project_binding["projectInstanceId"],
            "proposalContextId": proposal_context_id,
            "projectBinding": copy.deepcopy(project_binding),
            "projectRevision": project_binding["projectRevision"],
            "projectRecordSha256": project_binding["projectRecordSha256"],
            "workspaceContextId": workspace_context_id,
            "workspaceRootSha256": workspace_root_sha256,
            "context": copy.deepcopy(context),
            "contextSha256": request["contextSha256"],
            "idempotencyKey": idempotency_key,
            "requestSha256": request_sha256,
            "recordedAt": recorded_at,
        }
        stored = self.repository.store_action_authority_record(
            owner_user_id=owner,
            thread_id=thread_id,
            record_type="proposal-contexts",
            record_id=proposal_context_id,
            request_sha256=request_sha256,
            record=record,
            recorded_at=recorded_at,
        )
        canonical = stored["record"]
        payload = {
            "contract": CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT,
            "status": "accepted" if stored["created"] else "idempotent_readback",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "projectInstanceId": canonical["projectInstanceId"],
            "proposalContextId": proposal_context_id,
            "projectRevision": canonical["projectRevision"],
            "projectRecordSha256": canonical["projectRecordSha256"],
            "workspaceContextId": canonical["workspaceContextId"],
            "workspaceRootSha256": canonical["workspaceRootSha256"],
            "contextSha256": canonical["contextSha256"],
            "idempotencyKey": idempotency_key,
            "recordedAt": canonical["recordedAt"],
        }
        payload["receiptSha256"] = _sha256_value(payload)
        self.evict_owner_thread(owner, thread_id)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def action_grant(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if isinstance(request, dict) and request.get("contract") == CODE_ACTION_GRANT_REQUEST_CONTRACT:
            return self._code_action_grant(owner_user_id=owner_user_id, request=request)
        owner = str(owner_user_id or "").strip()
        expected = {
            "contract", "threadId", "action", "actionSha256", "approvalReceipt",
            "hostBinding", "idempotencyKey",
        }
        if (
            not owner
            or not isinstance(request, dict)
            or set(request) != expected
            or request.get("contract") != ACTION_GRANT_REQUEST_CONTRACT
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_GRANT_INVALID", "host action grant request is invalid"
            )
        if len(_canonical_bytes(request)) > MAX_ACTION_BYTES:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_GRANT_INVALID", "host action grant request is oversized"
            )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        idempotency_key = _safe_identifier(
            request.get("idempotencyKey"), "idempotencyKey"
        )
        action = _validate_host_action(
            request.get("action"),
            owner_user_id=owner,
            thread_id=thread_id,
            require_approval=True,
        )
        interaction_policy = _validate_action_interaction_authority(action)
        action_sha = _sha256_field(request.get("actionSha256"), "actionSha256")
        if action_sha != action["canonicalSha256"]:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_GRANT_INVALID", "actionSha256 does not match the descriptor"
            )
        host_binding, host_binding_sha256 = _validate_cli_host_binding(
            request.get("hostBinding"),
            required_capability=action["requiredHostCapability"],
        )
        approval_receipt = request.get("approvalReceipt")
        if not isinstance(approval_receipt, dict):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_APPROVAL_UNVERIFIABLE",
                "canonical approval receipt is required",
                status=409,
            )
        approval_turn_id = action["approvalTurnId"]
        stored_receipt = self.repository.read_exchange_receipt(
            owner_user_id=owner,
            thread_id=thread_id,
            turn_id=approval_turn_id,
        )
        approval_event = self.repository.read_exchange_event(
            owner_user_id=owner,
            thread_id=thread_id,
            turn_id=approval_turn_id,
        )
        approval_result = (
            approval_event.get("result") if isinstance(approval_event, dict) else None
        )
        decision = (
            approval_result.get("decision") if isinstance(approval_result, dict) else None
        )
        hypotheses = (
            approval_result.get("hypotheses") if isinstance(approval_result, dict) else None
        )
        selected_id = (
            decision.get("selectedHypothesisId") if isinstance(decision, dict) else None
        )
        selected = next(
            (
                item for item in (hypotheses or [])
                if isinstance(item, dict) and item.get("hypothesisId") == selected_id
            ),
            None,
        )
        receipt_fields = {
            "contract", "ownerId", "runtimePrincipalId", "threadId", "turnId",
            "status", "payloadSha256", "transcriptSha256", "receiptSha256",
            "issuedAt", "expiresAt",
        }
        if (
            not isinstance(stored_receipt, dict)
            or set(approval_receipt) != receipt_fields
            or any(
                approval_receipt.get(field) != stored_receipt.get(field)
                for field in receipt_fields - {"issuedAt", "expiresAt"}
            )
            or stored_receipt.get("ownerId") != owner
            or stored_receipt.get("threadId") != thread_id
            or stored_receipt.get("turnId") != approval_turn_id
            or not isinstance(approval_event, dict)
            or _sha256_value(approval_event) != stored_receipt.get("payloadSha256")
            or not isinstance(approval_result, dict)
            or not isinstance(selected, dict)
            or selected.get("intent") != "approval_recorded"
            or _canonical_bytes(approval_event.get("hostAction")) != _canonical_bytes(action)
            or _canonical_bytes(approval_event.get("profileEvidence"))
            != _canonical_bytes(action.get("profileEvidence"))
            or _canonical_bytes(approval_event.get("rulesetEvidence"))
            != _canonical_bytes(action.get("rulesetEvidence"))
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_APPROVAL_UNVERIFIABLE",
                "canonical approval exchange does not authorize the exact host action",
                status=409,
            )
        self._preflight_signer()
        issued = self.now().astimezone(timezone.utc)
        execution_grant = {
            "contract": ACTION_EXECUTION_GRANT_CONTRACT,
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha,
            "action": copy.deepcopy(action),
            "operation": action["operation"],
            "slotsSha256": _sha256_value(action["slots"]),
            "requiredHostCapability": action["requiredHostCapability"],
            "risk": action["risk"],
            "profileEvidence": copy.deepcopy(action["profileEvidence"]),
            "rulesetEvidence": copy.deepcopy(action["rulesetEvidence"]),
            "approvalTurnId": approval_turn_id,
            "approvalPayloadSha256": stored_receipt["payloadSha256"],
            "approvalReceiptSha256": stored_receipt["receiptSha256"],
            "idempotencyKey": idempotency_key,
            "hostBinding": host_binding,
            "hostBindingSha256": host_binding_sha256,
            "issuedAt": _iso(issued),
            "expiresAt": _iso(issued + timedelta(seconds=ACTION_GRANT_TTL_SECONDS)),
        }
        execution_grant["grantSha256"] = _sha256_value(execution_grant)
        record = {
            "contract": "chatty-auto-host-action-grant-record/v2",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha,
            "idempotencyKey": idempotency_key,
            "requestSha256": _sha256_value(request),
            "executionGrant": execution_grant,
            "recordedAt": _iso(self.now()),
        }
        stored = self.repository.store_action_authority_record(
            owner_user_id=owner,
            thread_id=thread_id,
            record_type="grants",
            record_id=action["actionId"],
            request_sha256=record["requestSha256"],
            record=record,
            recorded_at=_iso(self.now()),
        )
        canonical = stored["record"]
        payload = {
            "contract": ACTION_GRANT_RECEIPT_CONTRACT,
            "status": "accepted" if stored["created"] else "idempotent_readback",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha,
            "idempotencyKey": idempotency_key,
            "hostBindingSha256": host_binding_sha256,
            "executionGrant": copy.deepcopy(canonical["executionGrant"]),
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def _code_action_grant(
        self, *, owner_user_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        expected = {
            "contract", "threadId", "action", "actionSha256", "approvalReceipt",
            "projectBinding", "proposalContextReceipt", "hostBinding", "idempotencyKey",
        }
        if not owner or set(request) != expected or len(_canonical_bytes(request)) > MAX_ACTION_BYTES:
            raise AutoRuntimeContractError("AUTO_CODE_ACTION_GRANT_INVALID", "Code host action grant request is invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        idempotency_key = _safe_identifier(request.get("idempotencyKey"), "idempotencyKey")
        action = _validate_host_action(
            request.get("action"), owner_user_id=owner, thread_id=thread_id, require_approval=True
        )
        interaction_policy = _validate_action_interaction_authority(action)
        action_sha = _sha256_field(request.get("actionSha256"), "actionSha256")
        if action_sha != action["canonicalSha256"]:
            raise AutoRuntimeContractError("AUTO_CODE_ACTION_GRANT_INVALID", "actionSha256 does not match the descriptor")
        project_binding = _validate_code_project_binding_payload(
            request.get("projectBinding"), owner_user_id=owner, thread_id=thread_id, now=self.now()
        )
        current_project = self._current_code_project_binding(
            owner_user_id=owner, project_instance_id=project_binding["projectInstanceId"]
        )
        for field in ("threadId", "projectRevision", "projectRecordSha256", "bindingSha256"):
            if project_binding.get(field) != current_project.get(field):
                raise AutoRuntimeContractError(
                    "AUTO_CODE_PROJECT_BINDING_STALE", "Code project binding is stale", status=409
                )
        proposal_receipt = request.get("proposalContextReceipt")
        if (
            not isinstance(proposal_receipt, dict)
            or proposal_receipt.get("contract") != CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT
            or proposal_receipt.get("ownerId") != owner
            or proposal_receipt.get("threadId") != thread_id
            or proposal_receipt.get("projectInstanceId") != project_binding["projectInstanceId"]
            or proposal_receipt.get("receiptSha256")
            != _sha256_value({key: value for key, value in proposal_receipt.items() if key != "receiptSha256"})
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROPOSAL_CONTEXT_UNVERIFIABLE", "signed proposal context receipt is invalid", status=409
            )
        proposal_context_id = _safe_identifier(
            proposal_receipt.get("proposalContextId"), "proposalContextReceipt.proposalContextId"
        )
        proposal_record = self.repository.read_action_authority_record(
            owner_user_id=owner, thread_id=thread_id,
            record_type="proposal-contexts", record_id=proposal_context_id,
        )
        context = proposal_record.get("context") if isinstance(proposal_record, dict) else None
        proposal_action = None
        if isinstance(context, dict):
            try:
                proposal_action = _validate_host_action(
                    context.get("proposalAction"), owner_user_id=owner,
                    thread_id=thread_id, require_approval=False,
                )
            except AutoRuntimeContractError:
                proposal_action = None
        if (
            not isinstance(proposal_record, dict)
            or proposal_record.get("contract") != "chatty-auto-code-proposal-context-record/v1"
            or proposal_record.get("contextSha256") != proposal_receipt.get("contextSha256")
            or proposal_record.get("workspaceContextId") != proposal_receipt.get("workspaceContextId")
            or proposal_record.get("workspaceRootSha256") != proposal_receipt.get("workspaceRootSha256")
            or not isinstance(context, dict)
            or not isinstance(proposal_action, dict)
            or proposal_action.get("approvalTurnId") is not None
            or proposal_action.get("proposalTurnId") != action["proposalTurnId"]
            or any(
                _canonical_bytes(proposal_action.get(field)) != _canonical_bytes(action.get(field))
                for field in (
                    "ownerId", "runtimePrincipalId", "threadId", "operation", "slots",
                    "risk", "requiredHostCapability", "profileEvidence", "rulesetEvidence",
                    "interactionPolicy",
                )
            )
        ):
            raise AutoRuntimeContractError(
                "AUTO_CODE_PROPOSAL_CONTEXT_UNVERIFIABLE",
                "proposal context does not bind the exact persisted action",
                status=409,
            )
        host_binding, host_binding_sha256 = _validate_code_host_binding(
            request.get("hostBinding"), required_capability=action["requiredHostCapability"],
            project_binding=project_binding, proposal_context_receipt=proposal_receipt,
        )
        approval_receipt = request.get("approvalReceipt")
        approval_turn_id = action["approvalTurnId"]
        stored_receipt = self.repository.read_exchange_receipt(
            owner_user_id=owner, thread_id=thread_id, turn_id=approval_turn_id
        )
        approval_event = self.repository.read_exchange_event(
            owner_user_id=owner, thread_id=thread_id, turn_id=approval_turn_id
        )
        approval_result = approval_event.get("result") if isinstance(approval_event, dict) else None
        decision = approval_result.get("decision") if isinstance(approval_result, dict) else None
        hypotheses = approval_result.get("hypotheses") if isinstance(approval_result, dict) else None
        selected_id = decision.get("selectedHypothesisId") if isinstance(decision, dict) else None
        selected = next((item for item in (hypotheses or []) if isinstance(item, dict) and item.get("hypothesisId") == selected_id), None)
        thread_snapshot = self.repository.read_thread(
            owner_user_id=owner, transcript_title=_thread_title(thread_id),
            memory_limit=50, character_budget=MAX_CODE_HISTORY_CHARS,
        )
        canonical_events = thread_snapshot.get("events") if isinstance(thread_snapshot, dict) else None
        proposal_index = next((
            index for index, event in enumerate(canonical_events or [])
            if isinstance(event, dict) and event.get("turnId") == action["proposalTurnId"]
        ), -1)
        approval_index = next((
            index for index, event in enumerate(canonical_events or [])
            if isinstance(event, dict) and event.get("turnId") == approval_turn_id
        ), -1)
        receipt_fields = {
            "contract", "ownerId", "runtimePrincipalId", "threadId", "turnId", "status",
            "payloadSha256", "transcriptSha256", "receiptSha256", "issuedAt", "expiresAt",
        }
        if (
            not isinstance(approval_receipt, dict)
            or not isinstance(stored_receipt, dict)
            or set(approval_receipt) != receipt_fields
            or any(approval_receipt.get(field) != stored_receipt.get(field) for field in receipt_fields - {"issuedAt", "expiresAt"})
            or stored_receipt.get("ownerId") != owner
            or stored_receipt.get("threadId") != thread_id
            or stored_receipt.get("turnId") != approval_turn_id
            or not isinstance(approval_event, dict)
            or _sha256_value(approval_event) != stored_receipt.get("payloadSha256")
            or not isinstance(selected, dict) or selected.get("intent") != "approval_recorded"
            or _canonical_bytes(approval_event.get("hostAction")) != _canonical_bytes(action)
            or _canonical_bytes(approval_event.get("profileEvidence")) != _canonical_bytes(action.get("profileEvidence"))
            or _canonical_bytes(approval_event.get("rulesetEvidence")) != _canonical_bytes(action.get("rulesetEvidence"))
            or proposal_index < 0 or approval_index <= proposal_index
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_APPROVAL_UNVERIFIABLE", "canonical approval does not authorize this Code action", status=409
            )
        self._preflight_signer()
        issued = self.now().astimezone(timezone.utc)
        execution_grant = {
            "contract": CODE_ACTION_EXECUTION_GRANT_CONTRACT,
            "ownerId": owner, "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id, "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha, "action": copy.deepcopy(action),
            "operation": action["operation"], "slotsSha256": _sha256_value(action["slots"]),
            "requiredHostCapability": action["requiredHostCapability"], "risk": action["risk"],
            "profileEvidence": copy.deepcopy(action["profileEvidence"]),
            "rulesetEvidence": copy.deepcopy(action["rulesetEvidence"]),
            "approvalTurnId": approval_turn_id,
            "approvalPayloadSha256": stored_receipt["payloadSha256"],
            "approvalReceiptSha256": stored_receipt["receiptSha256"],
            "projectBinding": copy.deepcopy(project_binding),
            "projectBindingSha256": project_binding["bindingSha256"],
            "proposalContextReceipt": copy.deepcopy(proposal_receipt),
            "proposalContextReceiptSha256": proposal_receipt["receiptSha256"],
            "idempotencyKey": idempotency_key, "hostBinding": host_binding,
            "hostBindingSha256": host_binding_sha256,
            "issuedAt": _iso(issued),
            "expiresAt": _iso(issued + timedelta(seconds=ACTION_GRANT_TTL_SECONDS)),
        }
        execution_grant["grantSha256"] = _sha256_value(execution_grant)
        record = {
            "contract": "chatty-auto-host-action-grant-record/v3",
            "ownerId": owner, "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id, "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha, "idempotencyKey": idempotency_key,
            "requestSha256": _sha256_value(request), "executionGrant": execution_grant,
            "recordedAt": _iso(self.now()),
        }
        stored = self.repository.store_action_authority_record(
            owner_user_id=owner, thread_id=thread_id, record_type="grants",
            record_id=action["actionId"], request_sha256=record["requestSha256"],
            record=record, recorded_at=record["recordedAt"],
        )
        canonical = stored["record"]
        payload = {
            "contract": CODE_ACTION_GRANT_RECEIPT_CONTRACT,
            "status": "accepted" if stored["created"] else "idempotent_readback",
            "ownerId": owner, "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id, "actionId": action["actionId"],
            "actionCanonicalSha256": action_sha, "idempotencyKey": idempotency_key,
            "hostBindingSha256": host_binding_sha256,
            "projectBindingSha256": project_binding["bindingSha256"],
            "proposalContextReceiptSha256": proposal_receipt["receiptSha256"],
            "executionGrant": copy.deepcopy(canonical["executionGrant"]),
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def action_event(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        expected = {
            "contract", "threadId", "actionId", "actionCanonicalSha256",
            "hostBindingSha256", "executionGrant", "event", "idempotencyKey",
        }
        if (
            not owner
            or not isinstance(request, dict)
            or set(request) != expected
            or request.get("contract") != ACTION_EVENT_REQUEST_CONTRACT
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_EVENT_INVALID", "host action event request is invalid"
            )
        if len(_canonical_bytes(request)) > MAX_ACTION_BYTES:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_EVENT_INVALID", "host action event request is oversized"
            )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        action_id = _safe_identifier(request.get("actionId"), "actionId")
        action_sha = _sha256_field(
            request.get("actionCanonicalSha256"), "actionCanonicalSha256"
        )
        idempotency_key = _safe_identifier(
            request.get("idempotencyKey"), "idempotencyKey"
        )
        host_binding_sha256 = _sha256_field(
            request.get("hostBindingSha256"), "hostBindingSha256"
        )
        grant_record = self.repository.read_action_authority_record(
            owner_user_id=owner,
            thread_id=thread_id,
            record_type="grants",
            record_id=action_id,
        )
        grant = request.get("executionGrant")
        if (
            not isinstance(grant_record, dict)
            or not isinstance(grant, dict)
            or _canonical_bytes(grant) != _canonical_bytes(grant_record.get("executionGrant"))
            or grant.get("contract") not in {
                ACTION_EXECUTION_GRANT_CONTRACT, CODE_ACTION_EXECUTION_GRANT_CONTRACT
            }
            or grant_record.get("contract") not in {
                "chatty-auto-host-action-grant-record/v2",
                "chatty-auto-host-action-grant-record/v3",
            }
            or (
                grant_record.get("contract") == "chatty-auto-host-action-grant-record/v2"
                and grant.get("contract") != ACTION_EXECUTION_GRANT_CONTRACT
            )
            or (
                grant_record.get("contract") == "chatty-auto-host-action-grant-record/v3"
                and grant.get("contract") != CODE_ACTION_EXECUTION_GRANT_CONTRACT
            )
            or grant.get("ownerId") != owner
            or grant.get("threadId") != thread_id
            or grant.get("actionId") != action_id
            or grant.get("actionCanonicalSha256") != action_sha
            or grant.get("hostBindingSha256") != host_binding_sha256
            or _sha256_value(grant.get("hostBinding")) != host_binding_sha256
            or grant.get("grantSha256")
            != _sha256_value({key: value for key, value in grant.items() if key != "grantSha256"})
            or self.now().astimezone(timezone.utc) >= _parse_iso(grant.get("expiresAt"))
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_EXECUTION_UNAUTHORIZED",
                "host action execution grant is invalid or expired",
                status=403,
            )
        event = request.get("event")
        if not isinstance(event, dict) or set(event) != {
            "contract", "eventId", "state", "evidence", "evidenceSha256"
        }:
            raise AutoRuntimeContractError(
                "AUTO_ACTION_EVENT_INVALID", "host action event fields are invalid"
            )
        event_id = _safe_identifier(event.get("eventId"), "event.eventId")
        state = str(event.get("state") or "")
        evidence = event.get("evidence")
        if (
            event.get("contract") != ACTION_EVENT_CONTRACT
            or state not in _ACTION_STATES
            or not isinstance(evidence, dict)
            or len(_canonical_bytes(evidence)) > 128 * 1024
            or _contains_forbidden_action_transport_field(evidence)
            or _sha256_field(event.get("evidenceSha256"), "event.evidenceSha256")
            != _sha256_value(evidence)
        ):
            raise AutoRuntimeContractError(
                "AUTO_ACTION_EVENT_INVALID", "host action event evidence is invalid"
            )
        request_sha = _sha256_value(request)
        record_type = _action_event_record_type(action_id)
        prior = self.repository.list_action_authority_records(
            owner_user_id=owner,
            thread_id=thread_id,
            record_type=record_type,
        )
        replay = next(
            (item for item in prior if item.get("idempotencyKey") == idempotency_key),
            None,
        )
        if replay is not None:
            if replay.get("requestSha256") != request_sha:
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_EVENT_CONFLICT",
                    "host action event idempotency key is bound to different bytes",
                    status=409,
                )
            canonical_event = replay["event"]
            created = False
        else:
            prior_states = [str(item.get("state") or "") for item in prior]
            prior_event_ids = {str(item.get("event", {}).get("eventId") or "") for item in prior}
            if event_id in prior_event_ids:
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_EVENT_CONFLICT",
                    "host action event ID is already bound",
                    status=409,
                )
            expected_state = (
                "authorization_verified" if not prior_states
                else "started" if prior_states == ["authorization_verified"]
                else None
            )
            valid_terminal = (
                len(prior_states) == 2
                and prior_states[0] == "authorization_verified"
                and prior_states[1] == "started"
                and state in _ACTION_TERMINAL_STATES
            )
            if state != expected_state and not valid_terminal:
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_TRANSITION_CONFLICT",
                    "host action lifecycle transition is invalid",
                    status=409,
                )
            if any(item in _ACTION_TERMINAL_STATES for item in prior_states):
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_TERMINAL_CONFLICT",
                    "host action already has a canonical terminal state",
                    status=409,
                )
            self._preflight_signer()
            record = {
                "contract": "chatty-auto-host-action-event-record/v2",
                "ownerId": owner,
                "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                "threadId": thread_id,
                "actionId": action_id,
                "actionCanonicalSha256": action_sha,
                "hostBindingSha256": host_binding_sha256,
                "event": copy.deepcopy(event),
                "state": state,
                "eventSha256": _sha256_value(event),
                "idempotencyKey": idempotency_key,
                "requestSha256": request_sha,
            }
            try:
                stored = self.repository.store_action_authority_record(
                    owner_user_id=owner,
                    thread_id=thread_id,
                    record_type=record_type,
                    # All contenders for the same lifecycle transition share one
                    # immutable authority slot.  The repository lock therefore
                    # makes the first terminal outcome win even across hosts.
                    record_id=(state if state in {"authorization_verified", "started"} else "terminal"),
                    request_sha256=request_sha,
                    record=record,
                    recorded_at=_iso(self.now()),
                )
            except AutoRuntimeContractError as exc:
                if exc.code != "AUTO_ACTION_AUTHORITY_CONFLICT" or exc.status != 409:
                    raise
                raise AutoRuntimeContractError(
                    "AUTO_ACTION_EVENT_CONFLICT",
                    "host action lifecycle slot is already bound to different bytes",
                    status=409,
                ) from exc
            canonical_event = stored["record"]["event"]
            created = stored["created"]
        payload = {
            "contract": ACTION_EVENT_RECEIPT_CONTRACT,
            "status": "accepted" if created else "idempotent_readback",
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "actionId": action_id,
            "actionCanonicalSha256": action_sha,
            "hostBindingSha256": host_binding_sha256,
            "event": copy.deepcopy(canonical_event),
            "state": canonical_event["state"],
            "eventSha256": _sha256_value(canonical_event),
        }
        payload["receiptSha256"] = _sha256_value(payload)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def append(self, *, owner_user_id: str, request: dict[str, Any]) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError("AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403)
        if not isinstance(request, dict):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "request must be an object")
        required_fields = {
            "contract", "threadId", "turnId", "input", "output", "result",
            "resultSha256", "profileEvidence", "rulesetEvidence", "dialogueRevision",
            "interactionPolicy", "hydroDelegation",
        }
        allowed_fields = {*required_fields, "decisionContext", "hostAction"}
        if (
            not required_fields <= set(request)
            or set(request) - allowed_fields
            or request.get("contract") != EXCHANGE_REQUEST_CONTRACT
        ):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "exchange request fields are invalid")
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        turn_id = _safe_identifier(request.get("turnId"), "turnId")
        user_input = request.get("input")
        output = request.get("output")
        result = request.get("result")
        if not isinstance(user_input, str) or len(user_input) > MAX_INPUT_CHARS or "\x00" in user_input:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "input is invalid or oversized")
        if not isinstance(output, str) or len(output) > MAX_OUTPUT_CHARS or "\x00" in output:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "output is invalid or oversized")
        if not isinstance(result, dict):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "result must be an object")
        result_sha = _sha256_field(request.get("resultSha256"), "resultSha256")
        if result_sha != _sha256_value(result):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "resultSha256 does not match canonical result bytes")
        profile_evidence = request.get("profileEvidence")
        ruleset_evidence = request.get("rulesetEvidence")
        interaction_policy = _validate_interaction_policy(
            request.get("interactionPolicy")
        )
        hydro_delegation = _validate_hydro_delegation(
            request.get("hydroDelegation")
        )
        if not isinstance(profile_evidence, dict) or not isinstance(ruleset_evidence, dict):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "profile and ruleset evidence are required")
        if (
            profile_evidence.get("combinedSha256") != AUTO_PROFILE_COMBINED_SHA256
            or profile_evidence.get("revision") != AUTO_PROFILE_REVISION
            or profile_evidence.get("verificationState") != "canonical_verified"
        ):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "profile evidence is not canonical AUTO 1.0.0")
        _validate_action_ruleset_evidence(ruleset_evidence)
        if result.get("runtimePrincipalId") != AUTO_RUNTIME_PRINCIPAL_ID:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "result runtime principal is invalid")
        if (
            result.get("intrinsicIdentity") is not False
            or result.get("provider") is not None
            or result.get("model") is not None
        ):
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "AUTO result must remain provider-free")
        dialogue_revision = _safe_identifier(request.get("dialogueRevision"), "dialogueRevision")
        if result.get("output") != output:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result output does not match exchange output"
            )
        if _canonical_bytes(result.get("profile")) != _canonical_bytes(profile_evidence):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result profile evidence does not match the exchange"
            )
        if _canonical_bytes(result.get("ruleset")) != _canonical_bytes(ruleset_evidence):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result ruleset evidence does not match the exchange"
            )
        if _canonical_bytes(result.get("interactionPolicy")) != _canonical_bytes(
            interaction_policy
        ) or _canonical_bytes(result.get("hydroDelegation")) != _canonical_bytes(
            hydro_delegation
        ):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST",
                "result policy or delegation evidence does not match the exchange",
            )
        dialogue_state = result.get("dialogueState")
        if not isinstance(dialogue_state, dict) or dialogue_state.get("threadId") != thread_id:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result dialogue state does not match the thread"
            )
        state_revision = dialogue_state.get("revision")
        if isinstance(state_revision, bool) or str(state_revision) != dialogue_revision:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result dialogue revision does not match the exchange"
            )
        if result.get("contract") != "chatty-auto-turn/v1" or result.get("runtimeMode") != "auto":
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST", "result contract or runtime mode is invalid"
            )
        host_action = request.get("hostAction")
        if host_action is not None:
            if not isinstance(host_action, dict):
                raise AutoRuntimeContractError(
                    "AUTO_RUNTIME_INVALID_REQUEST",
                    "host action must be an object",
                )
            host_action = _validate_host_action(
                host_action,
                owner_user_id=owner,
                thread_id=thread_id,
                require_approval=host_action.get("approvalTurnId") is not None,
            )
            if host_action["approvalTurnId"] not in {None, turn_id}:
                raise AutoRuntimeContractError(
                    "AUTO_RUNTIME_INVALID_REQUEST",
                    "approved host action does not bind the current canonical turn",
                )
        canonical_profile = self.repository.load_canonical_profile()
        expected_profile_evidence = {
            "contract": "chatty-auto-system-runtime-profile-evidence/v1",
            "profileContract": canonical_profile["contract"],
            "revision": canonical_profile["revision"],
            "combinedSha256": canonical_profile["hashes"]["combinedSha256"],
            "promptSha256": canonical_profile["hashes"]["promptSha256"],
            "definitionSha256": canonical_profile["hashes"]["definitionSha256"],
            "conditioningSha256": canonical_profile["hashes"]["conditioningSha256"],
            "provenanceSources": canonical_profile["provenanceSources"],
            "verificationState": "canonical_verified",
        }
        if _canonical_bytes(profile_evidence) != _canonical_bytes(expected_profile_evidence):
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_INVALID_REQUEST",
                "profile evidence does not match the registered canonical profile",
            )
        decision_context = request.get("decisionContext")
        expected_transcript_sha256 = None
        if decision_context is not None:
            decision_context = _validate_decision_context(
                decision_context,
                owner_user_id=owner,
                thread_id=thread_id,
                turn_id=turn_id,
                profile_evidence=profile_evidence,
                ruleset_evidence=ruleset_evidence,
                interaction_policy=interaction_policy,
                hydro_delegation=hydro_delegation,
            )
            expected_transcript_sha256 = decision_context["sourceRevisions"]["transcript"]
        self._preflight_signer()
        appended_at = _iso(self.now())
        event = {
            "contract": "chatty-auto-canonical-exchange/v1",
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "turnId": turn_id,
            "input": user_input,
            "output": output,
            "result": result,
            "resultSha256": result_sha,
            "profileEvidence": profile_evidence,
            "rulesetEvidence": ruleset_evidence,
            "interactionPolicy": interaction_policy,
            "hydroDelegation": hydro_delegation,
            "dialogueRevision": dialogue_revision,
        }
        if decision_context is not None:
            event["decisionContext"] = decision_context
        if host_action is not None:
            event["hostAction"] = host_action
        event_bytes = _canonical_bytes(event)
        if len(event_bytes) > MAX_EVENT_BYTES:
            raise AutoRuntimeContractError("AUTO_RUNTIME_INVALID_REQUEST", "canonical exchange is oversized")
        payload_sha = _sha256_bytes(event_bytes)
        try:
            stored = self.repository.append_exchange(
                owner_user_id=owner,
                transcript_title=_thread_title(thread_id),
                thread_id=thread_id,
                turn_id=turn_id,
                event_bytes=event_bytes,
                payload_sha256=payload_sha,
                appended_at=appended_at,
                expected_transcript_sha256=expected_transcript_sha256,
            )
        except AutoRuntimeContractError as exc:
            if exc.code != "AUTO_EXCHANGE_CONFLICT" or exc.status != 409:
                raise
            conflict = {
                "contract": EXCHANGE_RECEIPT_CONTRACT,
                "status": "conflict",
                "ownerId": owner,
                "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                "threadId": thread_id,
                "turnId": turn_id,
                "payloadSha256": payload_sha,
                "transcriptSha256": _EMPTY_SHA256,
            }
            conflict["receiptSha256"] = _sha256_value(conflict)
            return self._sign(conflict, ttl_seconds=MAX_TTL_SECONDS)
        payload = {
            "contract": EXCHANGE_RECEIPT_CONTRACT,
            "status": stored["status"],
            "ownerId": owner,
            "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
            "threadId": thread_id,
            "turnId": turn_id,
            "payloadSha256": payload_sha,
            "transcriptSha256": stored["transcriptSha256"],
            "receiptSha256": stored["receiptSha256"],
        }
        self.evict_owner_thread(owner, thread_id)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)

    def append_hydro_lifecycle_event(
        self, *, owner_user_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip()
        if not owner:
            raise AutoRuntimeContractError(
                "AUTO_RUNTIME_OWNER_REQUIRED", "authenticated owner is required", status=403
            )
        if not isinstance(request, dict) or set(request) != {
            "contract", "threadId", "event", "eventSha256"
        }:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "Hydro lifecycle append request fields are invalid"
            )
        if request.get("contract") != HYDRO_LIFECYCLE_APPEND_REQUEST_CONTRACT:
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "Hydro lifecycle append contract is unsupported"
            )
        thread_id = _safe_identifier(request.get("threadId"), "threadId")
        event = _validate_hydro_event(request.get("event"), thread_id=thread_id)
        event_sha256 = _sha256_field(request.get("eventSha256"), "eventSha256")
        if event_sha256 != _sha256_value(event):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID", "eventSha256 does not match canonical event bytes"
            )
        self._preflight_signer()
        canonical_profile = self.repository.load_canonical_profile()
        expected_profile_evidence = {
            "contract": "chatty-auto-system-runtime-profile-evidence/v1",
            "profileContract": canonical_profile["contract"],
            "revision": canonical_profile["revision"],
            "combinedSha256": canonical_profile["hashes"]["combinedSha256"],
            "promptSha256": canonical_profile["hashes"]["promptSha256"],
            "definitionSha256": canonical_profile["hashes"]["definitionSha256"],
            "conditioningSha256": canonical_profile["hashes"]["conditioningSha256"],
            "provenanceSources": canonical_profile["provenanceSources"],
            "verificationState": "canonical_verified",
        }
        if _canonical_bytes(event["profileEvidence"]) != _canonical_bytes(
            expected_profile_evidence
        ):
            raise AutoRuntimeContractError(
                "AUTO_HYDRO_EVENT_INVALID",
                "Hydro profile evidence does not match the registered canonical profile",
            )
        authorization = event.get("authorization")
        if authorization is not None:
            exchange_receipt = self.repository.read_exchange_receipt(
                owner_user_id=owner,
                thread_id=thread_id,
                turn_id=authorization["turnId"],
            )
            if (
                not isinstance(exchange_receipt, dict)
                or exchange_receipt.get("ownerId") != owner
                or exchange_receipt.get("threadId") != thread_id
                or exchange_receipt.get("turnId") != authorization["turnId"]
                or exchange_receipt.get("payloadSha256")
                != authorization["exchangePayloadSha256"]
                or exchange_receipt.get("receiptSha256")
                != authorization["receiptSha256"]
            ):
                raise AutoRuntimeContractError(
                    "AUTO_HYDRO_AUTHORIZATION_UNVERIFIABLE",
                    "Hydro authorization does not match a canonical owner/thread exchange receipt",
                    status=409,
                )
            if event["type"] in {
                "graph.authorization_recorded",
                "graph.authorization_verified",
                "graph.dispatch_accepted",
            }:
                approval_exchange = self.repository.read_exchange_event(
                    owner_user_id=owner,
                    thread_id=thread_id,
                    turn_id=authorization["turnId"],
                )
                approval_result = (
                    approval_exchange.get("result")
                    if isinstance(approval_exchange, dict)
                    else None
                )
                hydro_state = (
                    approval_result.get("hydro")
                    if isinstance(approval_result, dict)
                    else None
                )
                approved_graph = (
                    hydro_state.get("activeGraph")
                    if isinstance(hydro_state, dict)
                    else None
                )
                approved_graph_body = (
                    copy.deepcopy(approved_graph)
                    if isinstance(approved_graph, dict)
                    else {}
                )
                approved_graph_declared_sha256 = approved_graph_body.pop(
                    "canonicalSha256", None
                )
                if (
                    not isinstance(approval_result, dict)
                    or not isinstance(hydro_state, dict)
                    or not isinstance(approved_graph, dict)
                ):
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_AUTHORIZATION_UNVERIFIABLE",
                        "canonical AUTO exchange has no mandatory Hydro graph",
                        status=409,
                    )
                interaction_policy = (
                    approval_result.get("interactionPolicy")
                    if isinstance(approval_result, dict)
                    else None
                )
                hydro_delegation = (
                    approval_result.get("hydroDelegation")
                    if isinstance(approval_result, dict)
                    else None
                )
                instances = (
                    approved_graph.get("instances")
                    if isinstance(approved_graph, dict)
                    else None
                )
                if (
                    not isinstance(approval_exchange, dict)
                    or _sha256_value(approval_exchange)
                    != authorization["exchangePayloadSha256"]
                    or approval_result.get("ruleset") != {
                        "version": AUTO_ACTIVE_RULESET_VERSION,
                        "revision": AUTO_ACTIVE_RULESET_REVISION,
                        "sha256": AUTO_ACTIVE_RULESET_SHA256,
                    }
                    or _canonical_bytes(approval_exchange.get("interactionPolicy"))
                    != _canonical_bytes(interaction_policy)
                    or _canonical_bytes(approval_exchange.get("hydroDelegation"))
                    != _canonical_bytes(hydro_delegation)
                    or _canonical_bytes(_validate_interaction_policy(interaction_policy))
                    != _canonical_bytes(approved_graph.get("interactionPolicy"))
                    or _canonical_bytes(_validate_hydro_delegation(hydro_delegation))
                    != _canonical_bytes(approved_graph.get("hydroDelegation"))
                    or hydro_delegation.get("required") is not True
                    or hydro_delegation.get("delegatesAllExecution") is not True
                    or hydro_delegation.get("directExecution") is not False
                    or not isinstance(instances, list)
                    or hydro_delegation.get("workerCount") != len(instances)
                    or len(instances) < 1
                    or approved_graph.get("graphId") != event["graphId"]
                    or approved_graph.get("canonicalSha256") != event["graphSha256"]
                    or approved_graph_declared_sha256
                    != _sha256_value(approved_graph_body)
                ):
                    raise AutoRuntimeContractError(
                        "AUTO_HYDRO_AUTHORIZATION_UNVERIFIABLE",
                        "canonical AUTO exchange does not authorize exact mandatory Hydro delegation",
                        status=409,
                    )
        recorded_at = _iso(self.now())
        try:
            payload = self.repository.append_hydro_event(
                owner_user_id=owner,
                thread_id=thread_id,
                event=event,
                event_sha256=event_sha256,
                recorded_at=recorded_at,
            )
        except AutoRuntimeContractError as exc:
            if exc.status != 409:
                raise
            try:
                lifecycle = self.repository.read_hydro_lifecycle(
                    owner_user_id=owner, thread_id=thread_id
                )
                lifecycle_sha256 = str(lifecycle.get("revision") or _EMPTY_SHA256)
            except Exception:
                lifecycle_sha256 = _EMPTY_SHA256
            self.repository.quarantine_hydro_event(
                owner_user_id=owner,
                thread_id=thread_id,
                event=event,
                event_sha256=event_sha256,
                reason_code=exc.code,
                lifecycle_sha256=lifecycle_sha256,
                quarantined_at=recorded_at,
            )
            payload = {
                "contract": HYDRO_LIFECYCLE_RECEIPT_CONTRACT,
                "status": "conflict",
                "ownerId": owner,
                "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
                "threadId": thread_id,
                "graphId": event["graphId"],
                "eventId": event["eventId"],
                "eventType": event["type"],
                "sequence": event["sequence"],
                "eventSha256": event_sha256,
                "lifecycleSha256": lifecycle_sha256,
            }
            payload["receiptSha256"] = _sha256_value(payload)
        self.evict_owner_thread(owner, thread_id)
        return self._sign(payload, ttl_seconds=MAX_TTL_SECONDS)


_DEFAULT_SERVICE: AutoRuntimeService | None = None


def _service(value: AutoRuntimeService | None = None) -> AutoRuntimeService:
    global _DEFAULT_SERVICE
    if value is not None:
        return value
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = AutoRuntimeService()
    return _DEFAULT_SERVICE


def register_auto_profile(
    request: dict[str, Any], *, service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).register(request)


def inspect_auto_registration(
    *, repository: PostgresAutoRuntimeRepository | None = None
) -> dict[str, Any]:
    """Return read-only canonical registration preflight evidence."""
    return (repository or PostgresAutoRuntimeRepository()).registration_preflight()


def project_auto_registration_preflight(
    request: dict[str, Any], *, service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).registration_preflight(request)


def project_auto_context(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).context(owner_user_id=owner_user_id, request=request)


def append_auto_exchange(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).append(owner_user_id=owner_user_id, request=request)


def append_auto_hydro_lifecycle_event(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).append_hydro_lifecycle_event(
        owner_user_id=owner_user_id, request=request
    )


def project_auto_thread_index(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).thread_index(owner_user_id=owner_user_id, request=request)


def project_auto_hydro_catalog(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).hydro_catalog(owner_user_id=owner_user_id, request=request)


def project_auto_hydro_recovery_index(
    *, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).hydro_recovery_index(request=request)


def register_auto_hydro_dispatch(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).hydro_dispatch(owner_user_id=owner_user_id, request=request)


def register_auto_hydro_cancellation(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).hydro_cancellation(owner_user_id=owner_user_id, request=request)


def attest_auto_hydro_worker_receipt(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).hydro_worker_receipt(owner_user_id=owner_user_id, request=request)


def project_auto_code_project_binding(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).code_project_binding(
        owner_user_id=owner_user_id, request=request
    )


def project_auto_code_thread_history(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).code_thread_history(
        owner_user_id=owner_user_id, request=request
    )


def append_auto_code_proposal_context(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).code_proposal_context(
        owner_user_id=owner_user_id, request=request
    )


def grant_auto_host_action(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).action_grant(owner_user_id=owner_user_id, request=request)


def append_auto_host_action_event(
    *, owner_user_id: str, request: dict[str, Any], service: AutoRuntimeService | None = None
) -> dict[str, Any]:
    return _service(service).action_event(owner_user_id=owner_user_id, request=request)


def verify_auto_signed_projection(
    envelope: dict[str, Any],
    *,
    public_key_pem: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify exact envelope bytes and enforce the bounded projection lifetime."""

    if not isinstance(envelope, dict) or set(envelope) != {
        "contract", "payload", "algorithm", "keyId", "signature"
    }:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID", "signed projection envelope is invalid", status=401
        )
    if envelope.get("contract") != SIGNED_PROJECTION_CONTRACT:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID", "signed projection contract is unsupported", status=401
        )
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("contract") not in {
        CONTEXT_PROJECTION_CONTRACT,
        REGISTRATION_RECEIPT_CONTRACT,
        REGISTRATION_PREFLIGHT_PROJECTION_CONTRACT,
        EXCHANGE_RECEIPT_CONTRACT,
        HYDRO_LIFECYCLE_RECEIPT_CONTRACT,
        THREAD_INDEX_PROJECTION_CONTRACT,
        HYDRO_CATALOG_CONTRACT,
        HYDRO_RECOVERY_INDEX_CONTRACT,
        HYDRO_DISPATCH_RECEIPT_CONTRACT,
        HYDRO_CANCELLATION_RECEIPT_CONTRACT,
        HYDRO_WORKER_RECEIPT_CONTRACT,
        ACTION_GRANT_RECEIPT_CONTRACT,
        CODE_ACTION_GRANT_RECEIPT_CONTRACT,
        ACTION_EVENT_RECEIPT_CONTRACT,
        CODE_PROJECT_BINDING_PROJECTION_CONTRACT,
        CODE_THREAD_HISTORY_PROJECTION_CONTRACT,
        CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT,
    }:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID", "signed projection payload contract is unsupported", status=401
        )
    try:
        canonical_projection_signing.verify_canonical_payload(
            payload,
            {
                "algorithm": envelope.get("algorithm"),
                "keyId": envelope.get("keyId"),
                "signature": envelope.get("signature"),
            },
            public_key_pem=public_key_pem,
        )
    except ValueError as exc:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID", "signed projection verification failed", status=401
        ) from exc
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = _parse_iso(payload.get("issuedAt"))
    expires_at = _parse_iso(payload.get("expiresAt"))
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=MAX_TTL_SECONDS):
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_INVALID", "signed projection lifetime is invalid", status=401
        )
    if instant < issued_at - timedelta(seconds=30):
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_NOT_YET_VALID", "signed projection is not yet valid", status=401
        )
    if instant >= expires_at:
        raise AutoRuntimeContractError(
            "AUTO_SIGNED_PROJECTION_EXPIRED", "signed projection has expired", status=410
        )
    return copy.deepcopy(payload)


__all__ = [
    "AUTO_PROFILE_COMBINED_SHA256",
    "AUTO_PLAN6_RULESET_REVISION",
    "AUTO_PLAN6_RULESET_SHA256",
    "AUTO_PLAN6_RULESET_VERSION",
    "AUTO_ACTIVE_RULESET_REVISION",
    "AUTO_ACTIVE_RULESET_SHA256",
    "AUTO_ACTIVE_RULESET_VERSION",
    "AUTO_RUNTIME_PRINCIPAL_ID",
    "CONTINUATION_BASIS_CONTRACT",
    "DECISION_CONTEXT_CONTRACT",
    "HYDRO_LIFECYCLE_APPEND_REQUEST_CONTRACT",
    "HYDRO_LIFECYCLE_EVENT_CONTRACT",
    "HYDRO_LIFECYCLE_QUARANTINE_RECEIPT_CONTRACT",
    "HYDRO_LIFECYCLE_RECEIPT_CONTRACT",
    "THREAD_INDEX_PROJECTION_CONTRACT",
    "HYDRO_CATALOG_CONTRACT",
    "HYDRO_RECOVERY_INDEX_CONTRACT",
    "HYDRO_DISPATCH_RECEIPT_CONTRACT",
    "HYDRO_CANCELLATION_RECEIPT_CONTRACT",
    "HYDRO_WORKER_RECEIPT_CONTRACT",
    "ACTION_GRANT_REQUEST_CONTRACT",
    "ACTION_GRANT_RECEIPT_CONTRACT",
    "ACTION_EXECUTION_GRANT_CONTRACT",
    "ACTION_EVENT_REQUEST_CONTRACT",
    "ACTION_EVENT_CONTRACT",
    "ACTION_EVENT_RECEIPT_CONTRACT",
    "CODE_PROJECT_BINDING_REQUEST_CONTRACT",
    "CODE_PROJECT_BINDING_PROJECTION_CONTRACT",
    "CODE_THREAD_HISTORY_REQUEST_CONTRACT",
    "CODE_THREAD_HISTORY_PROJECTION_CONTRACT",
    "CODE_PROPOSAL_CONTEXT_REQUEST_CONTRACT",
    "CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT",
    "CODE_HOST_BINDING_CONTRACT",
    "CODE_ACTION_GRANT_REQUEST_CONTRACT",
    "CODE_ACTION_GRANT_RECEIPT_CONTRACT",
    "CODE_ACTION_EXECUTION_GRANT_CONTRACT",
    "REGISTRATION_PREFLIGHT_REQUEST_CONTRACT",
    "REGISTRATION_PREFLIGHT_PROJECTION_CONTRACT",
    "AutoRuntimeContractError",
    "AutoRuntimeService",
    "PostgresAutoRuntimeRepository",
    "append_auto_exchange",
    "append_auto_code_proposal_context",
    "inspect_auto_registration",
    "project_auto_registration_preflight",
    "append_auto_hydro_lifecycle_event",
    "append_auto_host_action_event",
    "attest_auto_hydro_worker_receipt",
    "project_auto_hydro_catalog",
    "project_auto_code_project_binding",
    "project_auto_code_thread_history",
    "project_auto_hydro_recovery_index",
    "project_auto_thread_index",
    "register_auto_hydro_cancellation",
    "register_auto_hydro_dispatch",
    "grant_auto_host_action",
    "project_auto_context",
    "register_auto_profile",
    "verify_auto_signed_projection",
]
