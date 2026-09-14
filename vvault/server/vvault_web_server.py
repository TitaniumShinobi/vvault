#!/usr/bin/env python3
"""
VVAULT Web Server
Flask-based web server for the VVAULT system running on port 8000.

This server provides a REST API for the VVAULT web interface and serves
as the backend for the React frontend running on port 7784.

Author: Devon Allen Woodson
Date: 2025-10-28
Version: 1.0.0
"""

# Load environment variables from repo root .env
from pathlib import Path
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"Loaded .env from {env_path}")
except ImportError:
    pass  # dotenv not installed, rely on system env vars

import os
import sys
import json
import copy
import re
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from uuid import UUID, uuid4

from flask import Flask, request, jsonify, send_from_directory, Response, has_request_context
from flask_cors import CORS
import hashlib
import hmac
import threading
import zipfile
import io
import mimetypes
import time
import secrets
import base64
import jwt
from datetime import datetime, timedelta, timezone
import requests  # For Turnstile verification
from oauthlib.oauth2 import WebApplicationClient
from urllib.parse import urlparse, urlencode
import smtplib
import ssl
from email.message import EmailMessage

_server_dir = os.path.dirname(os.path.abspath(__file__))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
from vxrunner_baseline import convert_capsule_to_baseline
from continuity_parser import ContinuityParser
from vvault.boot.vvault_boot import boot_sequence
from vvault.audit.audit_compliance import (
    AuditLogger,
    AuditLevel,
    get_privileged_event_severity,
)
from vvault.security.pocketverse_guard import (
    enforce_pocketverse_authority,
    PocketverseAuthorityError,
)
from vvault.server import chatty_body_service
from vvault.server import conversation_thread_service as conversation_thread_service_module
from vvault.server import singleton_construct_authorization as singleton_construct_authorization_module
from vvault.server.conversation_thread_service import (
    ConversationContractError,
    conversation_thread_service,
)
from vvault.server.singleton_construct_authorization import (
    singleton_construct_authorization_service,
)
from vvault.server import offline_snapshot_service
from vvault.server import canonical_projection_signing
from vvault.server import auto_runtime_service
from vvault.server import canonical_data_contract
from vvault.server import canonical_context_service
from vvault.server import construct_work_loop_service as construct_work_loop_service_module
from vvault.server.construct_work_loop_service import (
    ConstructWorkLoopError,
    construct_work_loop_service,
)
from vvault.server import construct_execution_service as construct_execution_service_module
from vvault.server.construct_execution_service import (
    ConstructExecutionError,
    construct_execution_service,
)
from vvault.server import knowledge_contract
from vvault.server import account_context_service, human_context_service, knowledge_activation_service, knowledge_publication_service
from vvault.server import vvault_access_assertion
from vvault.server import life_capsule_resolver
from vvault.server import marketplace_service
from vvault.server import cleanhouse_files_evidence
from vvault.server import vvault_enrollment
from vvault.server.vault_drive_repository import VAULT_DRIVE_REPOSITORY
from vvault.server.avatar_canonicalization import (
    AvatarCanonicalizationError,
    normalize_avatar_payload_to_png,
)
from vvault.server.construct_taxonomy import (
    canonical_category,
    canonical_category_for_scope,
    taxonomy_payload,
)
from vvault.server.projection_classification import row_is_projection_excluded
from vvault.server.upload_path_contract import (
    preserved_upload_path,
    safe_upload_relative_path,
)
import vvault_auth_repository
import vvault_file_repository
from code_project_repository import CodeProjectRepository, is_internal_code_project_path


def _pocketverse_request_context():
    """Build request context for Pocketverse guard from current request."""
    cu = getattr(request, "current_user", None) or {}
    return {
        "email": cu.get("email"),
        "user_id": cu.get("id") or cu.get("user_id"),
        "session_user": cu,
        "metadata_loader": _load_pocketverse_metadata_from_body,
    }

def _is_uuid(value: Optional[str]) -> bool:
    return bool(
        isinstance(value, str)
        and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', value.strip(), re.I)
    )


_CONSTRUCT_OWNER_CACHE_TTL_SECONDS = 30.0
_construct_owner_cache_lock = threading.Lock()
_construct_owner_cache: dict[str, tuple[float, str]] = {}
_construct_projectability_cache_lock = threading.Lock()
_construct_projectability_cache: dict[tuple[str, str], tuple[float, bool]] = {}
CONSTRUCT_PROJECTABILITY_CACHE_TTL_SECONDS = 60.0
CONSTRUCT_EDITOR_CACHE_TTL_SECONDS = 30.0
CONSTRUCT_EDITOR_CACHE_LKG_SECONDS = 120.0
_construct_editor_cache_lock = threading.Lock()
_construct_editor_cache: dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}
_construct_editor_inflight: dict[tuple[str, str], threading.Event] = {}
AVATAR_CACHE_TTL_SECONDS = 60.0
AVATAR_CACHE_LKG_SECONDS = 300.0
AVATAR_CACHE_MAX_ENTRIES = 32
AVATAR_CACHE_MAX_BYTES = 128 * 1024 * 1024
_avatar_cache_lock = threading.Lock()
_avatar_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_avatar_descriptor_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_avatar_cache_inflight: dict[tuple[str, str], threading.Event] = {}
_avatar_hydration_slots = threading.BoundedSemaphore(2)


def _provided_service_token() -> str:
    """Read the bounded service credential from the supported transports."""
    chatty_key = str(request.headers.get("X-Chatty-Key") or "")
    service_key = str(request.headers.get("X-Service-Token") or "")
    if chatty_key and service_key and not hmac.compare_digest(
        chatty_key.encode("utf-8"), service_key.encode("utf-8")
    ):
        return ""
    provided = chatty_key or service_key
    if provided:
        return provided
    auth_header = str(request.headers.get("Authorization") or "")
    for prefix in ("Bearer ", "ServiceToken "):
        if auth_header.startswith(prefix):
            return auth_header[len(prefix):].strip()
    return ""


def _configured_service_token() -> str:
    """Resolve the current service credential without freezing env selection."""
    imported = str(globals().get("_IMPORTED_VVAULT_SERVICE_TOKEN") or "")
    current = str(globals().get("VVAULT_SERVICE_TOKEN") or "")
    if current != imported:
        return current
    if "VVAULT_SERVICE_TOKEN" in os.environ:
        return str(os.environ.get("VVAULT_SERVICE_TOKEN") or "")
    return ""


def _service_token_matches(expected: str | None = None) -> bool:
    configured = str(
        _configured_service_token()
        if expected is None
        else expected or ""
    )
    provided = _provided_service_token()
    return bool(
        configured
        and provided
        and hmac.compare_digest(
            configured.encode("utf-8"), provided.encode("utf-8")
        )
    )


def _trusted_service_identity_cache_allowed() -> bool:
    return _service_token_matches()


def _invalidate_construct_owner_cache(construct_id: str) -> None:
    callsign = _normalize_callsign(construct_id)
    with _construct_owner_cache_lock:
        _construct_owner_cache.pop(callsign, None)
    with _construct_editor_cache_lock:
        for key in list(_construct_editor_cache):
            if key[1] == callsign:
                _construct_editor_cache.pop(key, None)
    with _construct_projectability_cache_lock:
        for key in list(_construct_projectability_cache):
            if key[1] == callsign:
                _construct_projectability_cache.pop(key, None)


def _construct_is_projectable_cached(user_id: str, callsign: str) -> bool:
    """Bound the repeated owner/callsign projection guard to one DB lookup."""
    key = (str(user_id), _normalize_callsign(callsign))
    now = time.monotonic()
    with _construct_projectability_cache_lock:
        cached = _construct_projectability_cache.get(key)
        if cached and now - cached[0] <= CONSTRUCT_PROJECTABILITY_CACHE_TTL_SECONDS:
            return cached[1]
    result = VAULT_FILE_REPOSITORY.construct_is_projectable(
        user_id=key[0], callsign=key[1]
    )
    with _construct_projectability_cache_lock:
        _construct_projectability_cache[key] = (time.monotonic(), bool(result))
        if len(_construct_projectability_cache) > 1024:
            oldest = min(
                _construct_projectability_cache,
                key=lambda item: _construct_projectability_cache[item][0],
            )
            _construct_projectability_cache.pop(oldest, None)
    return bool(result)


def _invalidate_avatar_cache(
    construct_id: str, owner_user_id: str | None = None
) -> None:
    callsign = _normalize_callsign(construct_id)
    with _avatar_cache_lock:
        for key in list(_avatar_cache):
            if key[1] == callsign and (
                owner_user_id is None or key[0] == str(owner_user_id)
            ):
                _avatar_cache.pop(key, None)
        for key in list(_avatar_descriptor_cache):
            if key[1] == callsign and (
                owner_user_id is None or key[0] == str(owner_user_id)
            ):
                _avatar_descriptor_cache.pop(key, None)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
_POCKETVERSE_BOOT_LOCK = threading.Lock()
_POCKETVERSE_BOOT_STATE: Dict[str, Any] = {
    "mode": "idle",
    "status": "not_started",
    "started_at": None,
    "completed_at": None,
    "error": None,
}

VAULT_PREVIEW_ROUTE_BUDGET_MS = max(0, int(os.environ.get("VVAULT_PREVIEW_ROUTE_BUDGET_MS", "1800")))
VAULT_FAST_CAPSULE_PREVIEW_BUDGET_MS = max(0, int(os.environ.get("VVAULT_FAST_CAPSULE_PREVIEW_BUDGET_MS", "900")))
VAULT_PREVIEW_MAX_TRANSCRIPTS = max(1, int(os.environ.get("VVAULT_PREVIEW_MAX_TRANSCRIPTS", "6")))
SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()
SERVER_STARTED_MONOTONIC = time.perf_counter()


def _loaded_source_provenance(module_file: str) -> Dict[str, Any]:
    """Capture the exact source bytes loaded by this runtime at startup."""
    resolved = str(Path(module_file).resolve())
    try:
        source = Path(resolved).read_bytes()
        source_sha256 = hashlib.sha256(source).hexdigest()
        byte_length = len(source)
    except OSError:
        source_sha256 = None
        byte_length = None
    return {
        "moduleFile": resolved,
        "sourceSha256": source_sha256,
        "byteLength": byte_length,
        "loadedAt": SERVER_STARTED_AT,
    }


_SERVER_SOURCE_PROVENANCE = _loaded_source_provenance(__file__)
_AUTO_RUNTIME_SOURCE_PROVENANCE = _loaded_source_provenance(auto_runtime_service.__file__)
_AUTO_ACCESS_ASSERTION_SOURCE_PROVENANCE = _loaded_source_provenance(
    vvault_access_assertion.__file__
)
_AUTO_PROJECTION_SIGNING_SOURCE_PROVENANCE = _loaded_source_provenance(
    canonical_projection_signing.__file__
)
_CHATTY_BODY_SOURCE_PROVENANCE = _loaded_source_provenance(chatty_body_service.__file__)
_ACCOUNT_CONTEXT_SOURCE_PROVENANCE = _loaded_source_provenance(
    account_context_service.__file__
)
_CONVERSATION_THREAD_SOURCE_PROVENANCE = _loaded_source_provenance(
    conversation_thread_service_module.__file__
)
_SINGLETON_CONSTRUCT_AUTHORIZATION_SOURCE_PROVENANCE = _loaded_source_provenance(
    singleton_construct_authorization_module.__file__
)
_KNOWLEDGE_CONTRACT_SOURCE_PROVENANCE = _loaded_source_provenance(knowledge_contract.__file__)
_KNOWLEDGE_ACTIVATION_SOURCE_PROVENANCE = _loaded_source_provenance(
    knowledge_activation_service.__file__
)
_KNOWLEDGE_PUBLICATION_SOURCE_PROVENANCE = _loaded_source_provenance(
    knowledge_publication_service.__file__
)
_CANONICAL_CONTEXT_SOURCE_PROVENANCE = _loaded_source_provenance(
    canonical_context_service.__file__
)
_CANONICAL_CONTEXT_UNIT_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/canonical-context-unit.schema.json")
)
_CANONICAL_CONTEXT_MANIFEST_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/canonical-context-manifest.schema.json")
)
_CONSTRUCT_WORK_LOOP_SOURCE_PROVENANCE = _loaded_source_provenance(
    construct_work_loop_service_module.__file__
)
_CONSTRUCT_EXECUTION_SOURCE_PROVENANCE = _loaded_source_provenance(
    construct_execution_service_module.__file__
)
_CONSTRUCT_EXECUTION_PROGRAM_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/execution-program.schema.json")
)
_CONSTRUCT_EXECUTION_EVENT_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/execution-event-envelope.schema.json")
)
_CONSTRUCT_WORK_PROGRAM_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-program.schema.json")
)
_CONSTRUCT_WORK_EVENT_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-event-envelope.schema.json")
)
_CONSTRUCT_WORK_EVENT_BATCH_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-event-batch.schema.json")
)
_CONSTRUCT_WORK_CREATE_AUTH_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-program-create-authorization.schema.json")
)
_CONSTRUCT_WORK_SCOPE_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-scope-resolution.schema.json")
)
_CONSTRUCT_WORK_ACTIVE_SCOPE_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-active-scope-resolution.schema.json")
)
_CONSTRUCT_WORK_EVIDENCE_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-evidence-resolution.schema.json")
)
_CONSTRUCT_WORK_HANDOFF_AUTH_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-handoff-authorization.schema.json")
)
_CONSTRUCT_WORK_PREFLIGHT_SCHEMA_PROVENANCE = _loaded_source_provenance(
    str(_repo_root / "contracts/v1/schemas/work-preflight-inspection.schema.json")
)
_startup_timings: Dict[str, int] = {}
_projection_warm_state_lock = threading.Lock()
_projection_warm_state: Dict[str, Any] = {
    "contract": "life.vvault.chatty-capability-readiness/v1",
    "ready": False,
    "status": "not_started",
    "generation": 0,
    "verifiedAt": None,
    "expiresAt": None,
    "ownersPrimed": 0,
    "communityStorePrimed": False,
    "probeConstructId": None,
    "capabilities": {
        "construct_registry": {"ready": False, "status": "not_started"},
        "identity_projection": {"ready": False, "status": "not_started"},
        "knowledge_context_projection": {"ready": False, "status": "not_started"},
    },
    "durationMs": None,
    "failures": [],
}


def _prime_mandatory_projection_caches() -> Dict[str, Any]:
    """Establish the leased Chatty capability state before advertising readiness."""
    owner_ids: list[str] = []
    emails = [
        value.strip()
        for value in str(os.environ.get("VVAULT_ADMIN_EMAILS") or "").split(",")
        if value.strip()
    ]
    for user_id in AUTH_REPOSITORY.readiness_owner_ids(emails):
        if _is_uuid(user_id):
            owner_ids.append(user_id)
    previous = _current_projection_warm_state()
    generation = int(previous.get("generation") or 0) + 1
    result = chatty_body_service.prime_startup_projection_caches(owner_ids)
    capabilities = {
        "construct_registry": {
            "ready": bool(result.get("ready")),
            "status": "ready" if result.get("ready") else "unavailable",
        },
        "identity_projection": {"ready": False, "status": "not_started"},
        "knowledge_context_projection": {"ready": False, "status": "not_started"},
    }
    failures = list(result.get("failures") or [])
    probe_construct_id = None
    if result.get("ready") and owner_ids:
        owner_id = owner_ids[0]
        try:
            catalog = chatty_body_service.list_constructs(
                owner_id, include_hidden=True, _force_refresh=True
            )
            records = list(catalog.payload.get("constructs") or [])
            if catalog.status != "body_native" or not records:
                raise RuntimeError("actor-scoped construct registry has no probe construct")
            preferred_probe = str(
                os.environ.get("VVAULT_READINESS_PROBE_CONSTRUCT")
                or "zenithrouteproof-001"
            ).strip()
            probe_record = next(
                (
                    record
                    for record in records
                    if str(
                        record.get("callsign") or record.get("construct_id") or ""
                    ).strip()
                    == preferred_probe
                ),
                records[0],
            )
            probe_construct_id = str(
                probe_record.get("callsign")
                or probe_record.get("construct_id")
                or ""
            ).strip()
            identity = life_capsule_resolver.resolve_identity(probe_construct_id)
            if identity.status != "body_native" or identity.http_status >= 300:
                raise RuntimeError(f"identity projection returned {identity.status}")
            capabilities["identity_projection"] = {"ready": True, "status": "ready"}

            profile = chatty_body_service.construct_profile(probe_construct_id)
            if profile.status != "body_native" or profile.http_status >= 300:
                raise RuntimeError(f"construct profile returned {profile.status}")
            profile_payload = profile.payload.get("profile") or {}
            references = list(profile_payload.get("canonRefs") or []) + list(
                profile_payload.get("knowledgeRefs") or []
            )
            _projection, projection_status = knowledge_contract.resolve_knowledge_references(
                owner_user_id=owner_id,
                instance_id=probe_construct_id,
                references=references,
            )
            if projection_status >= 500:
                raise RuntimeError(
                    f"knowledge-context projection returned {projection_status}"
                )
            # Empty or construct-invalid knowledge is a data state, not a service outage.
            capabilities["knowledge_context_projection"] = {
                "ready": True,
                "status": "ready",
            }
        except Exception as exc:
            missing = next(
                (name for name, state in capabilities.items() if not state.get("ready")),
                "identity_projection",
            )
            capabilities[missing] = {
                "ready": False,
                "status": "unavailable",
                "errorCode": type(exc).__name__,
            }
            failures.append({"projection": missing, "status": type(exc).__name__})
    elif not owner_ids:
        failures.append({"projection": "actor_context", "status": "owner_unavailable"})

    now = datetime.now(timezone.utc)
    result.update({
        "contract": "life.vvault.chatty-capability-readiness/v1",
        "generation": generation,
        "verifiedAt": now.isoformat(),
        "expiresAt": (now + timedelta(seconds=READINESS_LEASE_SECONDS)).isoformat(),
        "probeConstructId": probe_construct_id,
        "capabilities": capabilities,
        "failures": failures,
    })
    result["ready"] = bool(capabilities) and all(
        state.get("ready") is True for state in capabilities.values()
    )
    result["status"] = "ready" if result.get("ready") else "failed"
    with _projection_warm_state_lock:
        _projection_warm_state.clear()
        _projection_warm_state.update(copy.deepcopy(result))
    return result


def _current_projection_warm_state() -> Dict[str, Any]:
    with _projection_warm_state_lock:
        return copy.deepcopy(_projection_warm_state)


def _projection_lease_expiry(projection: Dict[str, Any]) -> datetime:
    try:
        expiry = datetime.fromisoformat(
            str(projection.get("expiresAt") or "1970-01-01T00:00:00+00:00")
        )
    except (TypeError, ValueError):
        expiry = datetime.fromtimestamp(0, timezone.utc)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry


def _projection_lease_is_fresh(
    projection: Dict[str, Any], *, now: Optional[datetime] = None
) -> bool:
    checked_at = now or datetime.now(timezone.utc)
    return bool(projection.get("ready")) and _projection_lease_expiry(projection) > checked_at


def _projection_lease_needs_refresh(
    projection: Dict[str, Any], *, now: Optional[datetime] = None
) -> bool:
    checked_at = now or datetime.now(timezone.utc)
    refresh_by = checked_at + timedelta(seconds=READINESS_RENEWAL_MARGIN_SECONDS)
    return not projection.get("ready") or _projection_lease_expiry(projection) <= refresh_by


def _renew_projection_capability_lease(
    projection: Dict[str, Any], *, now: Optional[datetime] = None
) -> Dict[str, Any] | None:
    """Renew a healthy capability lease without replaying every projection.

    The expensive projection probes are startup/recovery work. Re-running them
    on every lease interval competes with canonical transcript writes and can
    make an otherwise healthy VVAULT fail its own readiness contract. Runtime
    route failures still invalidate the relevant capability explicitly; an
    invalid or incomplete capability set is therefore never renewed here.
    """
    capabilities = copy.deepcopy(projection.get("capabilities") or {})
    required = {
        "construct_registry",
        "identity_projection",
        "knowledge_context_projection",
    }
    if not projection.get("ready") or not required.issubset(capabilities):
        return None
    if any(capabilities[name].get("ready") is not True for name in required):
        return None

    renewed_at = now or datetime.now(timezone.utc)
    with _projection_warm_state_lock:
        current_generation = int(_projection_warm_state.get("generation") or 0)
        source_generation = int(projection.get("generation") or 0)
        generation = max(current_generation, source_generation) + 1
        renewed = copy.deepcopy(projection)
        renewed.update({
            "ready": True,
            "status": "ready",
            "generation": generation,
            "verifiedAt": renewed_at.isoformat(),
            "expiresAt": (
                renewed_at + timedelta(seconds=READINESS_LEASE_SECONDS)
            ).isoformat(),
            "renewalMode": "runtime_state",
            "renewedFromGeneration": source_generation,
        })
        _projection_warm_state.clear()
        _projection_warm_state.update(renewed)
        return copy.deepcopy(renewed)


def _invalidate_projection_capability(capability: str, error: Exception | str) -> None:
    """Fail the capability lease closed after a dependency-level route failure."""
    with _projection_warm_state_lock:
        capabilities = copy.deepcopy(_projection_warm_state.get("capabilities") or {})
        capabilities[capability] = {
            "ready": False,
            "status": "unavailable",
            "errorCode": type(error).__name__ if isinstance(error, Exception) else str(error),
        }
        _projection_warm_state.update({
            "ready": False,
            "status": "invalidated",
            "expiresAt": datetime.now(timezone.utc).isoformat(),
            "capabilities": capabilities,
            "lastInvalidatedAt": datetime.now(timezone.utc).isoformat(),
            "lastInvalidationReason": capability,
        })


def _get_pocketverse_boot_state() -> Dict[str, Any]:
    with _POCKETVERSE_BOOT_LOCK:
        return dict(_POCKETVERSE_BOOT_STATE)


def _mark_pocketverse_boot_state(**updates):
    with _POCKETVERSE_BOOT_LOCK:
        _POCKETVERSE_BOOT_STATE.update(updates)


def _run_pocketverse_boot(mode: str):
    def worker():
        _mark_pocketverse_boot_state(
            mode=mode,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=None,
            error=None,
        )
        try:
            boot_status = boot_sequence()
            layers = boot_status.get("layers", {})
            logger.info("Pocketverse boot status: %s", json.dumps(layers, indent=2, default=str))
            _mark_pocketverse_boot_state(
                mode=mode,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=None,
                layers_active=sum(
                    1
                    for layer in layers.values()
                    if layer.get("status") in ["initialized", "scaffolded", "ready", "partial"] or layer.get("success")
                ),
            )
        except Exception as e:
            logger.warning("Pocketverse boot completed with issues (server will still start): %s", e)
            _mark_pocketverse_boot_state(
                mode=mode,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )

    if mode == "async":
        threading.Thread(target=worker, name="vvault-pocketverse-boot", daemon=True).start()
    else:
        worker()

# OAuthlib's HTTP escape hatch is local-development only.
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if str(os.environ.get("VVAULT_ENV") or "development").lower() not in {"production", "prod"}:
    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
else:
    os.environ.pop('OAUTHLIB_INSECURE_TRANSPORT', None)

DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'dist')
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'assets')
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'public')
DOOR_CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config',
    'chatty-vvault-doors.json',
)


def _normalize_origin(value: str) -> Optional[str]:
    candidate = (value or "").strip()
    if not candidate:
        return None
    if any(token in candidate for token in ("*", "\\", "[", "]", "?", "$", "^", "(", ")")):
        return None
    try:
        parsed = urlparse(candidate)
        parsed_port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    port_suffix = f":{parsed_port}" if parsed_port is not None else ""
    return f"{parsed.scheme}://{parsed.hostname}{port_suffix}"


def _is_local_origin(value: Optional[str]) -> bool:
    origin = _normalize_origin(value or "")
    if not origin:
        return False
    parsed = urlparse(origin)
    return (parsed.hostname or "").strip().lower() in {"localhost", "127.0.0.1", "::1"}


_door_contract_cache = None


def _load_chatty_vvault_door_contract() -> Dict[str, Any]:
    global _door_contract_cache
    if _door_contract_cache is not None:
        return _door_contract_cache
    override_path = (os.environ.get("VVAULT_DOOR_CONTRACT_PATH") or "").strip()
    with open(override_path or DOOR_CONTRACT_PATH, 'r', encoding='utf-8') as handle:
        _door_contract_cache = json.load(handle)
    return _door_contract_cache


def _runtime_is_production() -> bool:
    node_env = (os.environ.get("NODE_ENV") or "").strip().lower()
    replit_deployment = (os.environ.get("REPL_DEPLOYMENT") or "").strip() == "1"
    production_port = (os.environ.get("PORT") or "").strip() == "5000"
    if node_env == "production" or replit_deployment or production_port:
        return True
    explicit_origins = [
        os.environ.get("VVAULT_FRONTEND_URL"),
        os.environ.get("VVAULT_BACKEND_URL"),
        os.environ.get("OAUTH_BASE_URL"),
    ]
    return any(origin and not _is_local_origin(origin) for origin in explicit_origins)


def _resolve_chatty_vvault_door_name() -> str:
    explicit = (os.environ.get("CHATTY_VVAULT_DOOR") or os.environ.get("VVAULT_RUNTIME_DOOR") or "").strip()
    if explicit in {"private", "public"}:
        return explicit
    return "public" if _runtime_is_production() else "private"


def _resolve_chatty_vvault_door() -> Dict[str, Any]:
    contract = _load_chatty_vvault_door_contract()
    selected_door = _resolve_chatty_vvault_door_name()
    raw_door = (contract.get("doors") or {}).get(selected_door) or {}
    allowed_browser_origins = [
        origin
        for origin in (_normalize_origin(value) for value in raw_door.get("allowedBrowserOrigins", []))
        if origin
    ]
    door = {
        "version": contract.get("version"),
        "name": raw_door.get("name"),
        "selected_door": selected_door,
        "chatty_origin": _normalize_origin(raw_door.get("chattyPublicOrigin") or ""),
        "chatty_api_origin": _normalize_origin(raw_door.get("chattyApiOrigin") or ""),
        "code_origin": _normalize_origin(raw_door.get("codePublicOrigin") or ""),
        "code_api_origin": _normalize_origin(raw_door.get("codeApiOrigin") or ""),
        "vvault_origin": _normalize_origin(raw_door.get("vvaultOrigin") or ""),
        "auth_mode": str(raw_door.get("authMode") or "").strip(),
        "database_authority": raw_door.get("databaseAuthority"),
        "runtime_memory_authority": raw_door.get("runtimeMemoryAuthority"),
        "canonical_schema": raw_door.get("canonicalSchema"),
        "storage_owner": raw_door.get("storageOwner"),
        "transcript_owner": raw_door.get("transcriptOwner"),
        "transcript_compatibility_owner": raw_door.get("transcriptCompatibilityOwner"),
        "allowed_browser_origins": allowed_browser_origins,
        "allow_legacy_exchange": raw_door.get("allowLegacyExchange") is True,
        "problems": [],
    }

    required_origins = {
        "chatty_origin": "chatty_origin_missing",
        "chatty_api_origin": "chatty_api_origin_missing",
        "code_origin": "code_origin_missing",
        "code_api_origin": "code_api_origin_missing",
        "vvault_origin": "vvault_origin_missing",
    }
    for field, problem in required_origins.items():
        if not door[field]:
            door["problems"].append(problem)
    if not door["allowed_browser_origins"]:
        door["problems"].append("allowed_browser_origins_missing")

    expected_authority = {
        "database_authority": "vvault_body",
        "runtime_memory_authority": "vvault_body",
        "canonical_schema": "ovvaults",
        "storage_owner": "ovvaults.vault_files",
        "transcript_owner": "ovvaults.transcripts",
        "transcript_compatibility_owner": "ovvaults.vault_files",
    }
    for field, expected in expected_authority.items():
        if door[field] != expected:
            door["problems"].append(f"{field}_invalid")
    if door["version"] != 2:
        door["problems"].append("contract_version_invalid")
    if door["name"] != selected_door:
        door["problems"].append("door_name_invalid")
    if door["auth_mode"] != "vvault_native":
        door["problems"].append("auth_mode_must_be_vvault_native")
    if door["allow_legacy_exchange"]:
        door["problems"].append("legacy_exchange_not_allowed")

    all_origins = [
        door["chatty_origin"],
        door["chatty_api_origin"],
        door["code_origin"],
        door["code_api_origin"],
        door["vvault_origin"],
        *door["allowed_browser_origins"],
    ]
    if selected_door == "public" and any(_is_local_origin(origin) for origin in all_origins if origin):
        door["problems"].append("door_public_with_localhost_target")
    if selected_door == "public" and any(not origin.startswith("https://") for origin in all_origins if origin):
        door["problems"].append("door_public_requires_https")
    if selected_door == "private" and any(not _is_local_origin(origin) for origin in all_origins if origin):
        door["problems"].append("door_private_with_production_target")

    door["problems"] = list(dict.fromkeys(door["problems"]))
    door["ok"] = len(door["problems"]) == 0
    return door


def _resolve_frontend_origin() -> Optional[str]:
    explicit = _normalize_origin(os.environ.get("VVAULT_FRONTEND_URL") or "")
    selected_door = _resolve_chatty_vvault_door_name()
    if explicit:
        if selected_door == "public" and not _is_local_origin(explicit):
            return explicit
        if selected_door == "private" and _is_local_origin(explicit):
            return explicit
    if selected_door == "public":
        return _resolve_chatty_vvault_door().get("vvault_origin")
    return "http://localhost:7784"


def _resolve_backend_origin() -> Optional[str]:
    return _resolve_chatty_vvault_door().get("vvault_origin")


def _build_cors_origins() -> List[str]:
    door = _resolve_chatty_vvault_door()
    if door.get("ok") is not True:
        problems = ", ".join(door.get("problems") or ["unknown_contract_error"])
        raise RuntimeError(f"Invalid Chatty-VVAULT door contract: {problems}")
    origins = []
    frontend_origin = _resolve_frontend_origin()
    if frontend_origin:
        origins.append(frontend_origin)
    origins.extend(door.get("allowed_browser_origins") or [])

    deduped = []
    seen = set()
    for origin in origins:
        normalized = _normalize_origin(origin or "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped

app = Flask(__name__, static_folder=DIST_DIR, static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'vvault-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
_cors_origins = _build_cors_origins()
CORS(app, origins=_cors_origins)

# Security headers (resilience hardening)
@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if request.path.startswith("/api/auth/") or request.path == "/api/vault/session-bridge":
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        # Form-navigation POSTs inherit the document policy. no-referrer
        # produces Origin:null, defeating the pinned callback origin check.
        # Expose only the origin on this one signed completion document.
        completion_form = (request.path in {"/api/auth/enrollment/continue", "/api/auth/chatty/authorize"}
                           and response.status_code == 200 and response.mimetype == "text/html")
        response.headers["Referrer-Policy"] = "strict-origin" if completion_form else "no-referrer"
    return response


def _is_canonical_mutating_request() -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if request.method == "OPTIONS":
        return False
    path = request.path or ""
    if path == "/api/auth/logout":
        return False
    if path == "/api/vault/session-bridge":
        return False
    if path == "/api/vault/files/preview" and request.method == "POST":
        return False
    if path.startswith("/api/chatty/marketplace/listings/") and path.endswith("/install-preflight") and request.method == "POST":
        return False
    if path == "/api/vault/knowledge-files/upload" and request.method == "POST":
        return False
    if path.startswith("/api/vault/knowledge-files/") and request.method == "DELETE":
        return False
    if path == "/api/vault/simdrive/write" and request.method == "POST":
        return False
    if path == "/api/vault/simdrive/inject" and request.method == "POST":
        return False
    if path == "/api/vault/system-files" and request.method == "POST":
        return False
    if path == "/api/vault/system-files/outbox/replay" and request.method == "POST":
        return False
    if path in {"/api/vault/memup/sync", "/api/vault/memup/materialize"} and request.method == "POST":
        return False
    if path.startswith("/api/vault/configs/") and request.method == "POST":
        return False
    if path.startswith("/api/vault/constructs/") and path.endswith("/editor") and request.method == "PUT":
        return False
    if path.startswith("/api/vault/constructs/") and path.endswith("/identity-projection/project") and request.method == "POST":
        return False
    if path.startswith("/api/chatty/construct/") and path.endswith("/ledger/generate") and request.method == "POST":
        return False
    if path.startswith("/api/chatty/transcript/") and request.method == "POST":
        return False
    if path == "/api/chatty/message" and request.method == "POST":
        return False
    return (
        path.startswith("/api/vault/")
        or path.startswith("/api/chatty/")
    )


@app.before_request
def _gate_vvault_canonical_writes():
    if not _is_canonical_mutating_request():
        return None
    body_status = _body_database_dependency_status()
    if body_status.get("ready"):
        return None
    return _vvault_write_block_response(request.path, dependency_status=body_status)

# Rate limiting for auth and admin (in-memory, per IP)
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_AUTH: Dict[str, deque] = {}
_RATE_LIMIT_ADMIN: Dict[str, deque] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_AUTH_MAX = 30
_RATE_LIMIT_ADMIN_MAX = 20


def _is_runtime_lock_active() -> bool:
    value = (os.environ.get('VVAULT_RUNTIME_LOCK') or '').strip().lower()
    return value in {'1', 'true', 'yes', 'on', 'locked'}


def _runtime_lock_deferred_response(construct_id: str, action: str):
    return jsonify({
        "success": True,
        "deferred": True,
        "construct_id": construct_id,
        "action": action,
        "message": "Runtime lock active; write deferred.",
    }), 202


def _rate_limit_key(route_type: str) -> Optional[str]:
    """Return None if allowed, else error message. route_type is 'auth' or 'admin'."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if "," in ip:
        ip = ip.split(",")[0].strip()
    now = time.time()
    with _RATE_LIMIT_LOCK:
        if route_type == "auth":
            store = _RATE_LIMIT_AUTH
            max_n = _RATE_LIMIT_AUTH_MAX
        else:
            store = _RATE_LIMIT_ADMIN
            max_n = _RATE_LIMIT_ADMIN_MAX
        if ip not in store:
            store[ip] = deque()
        q = store[ip]
        while q and q[0] < now - _RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= max_n:
            return "rate_limit_exceeded"
        q.append(now)
    return None


# Allowed redirect targets for OAuth (no open redirect)
def _allowed_redirect_base(url: str) -> bool:
    """True if url's scheme+host is in the CORS/allowed origins list."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return any(
        base == o or base.rstrip("/") == o.rstrip("/")
        for o in _cors_origins
    )


# Privileged-action audit logging (resilience / sabotage visibility)
_audit_db_path = os.environ.get("VVAULT_AUDIT_DB_PATH") or str(_repo_root / "vvault" / "data" / "audit.db")
try:
    os.makedirs(os.path.dirname(_audit_db_path), exist_ok=True)
    _audit_logger = AuditLogger(_audit_db_path)
except Exception as _audit_init_err:
    logger.warning(f"Audit logger init failed: {_audit_init_err}; privileged events will not be persisted to audit DB")
    _audit_logger = None


def _log_privileged_event(
    event_type: str,
    resource: str,
    action: str,
    result: str,
    description: str,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """Log a privileged action to the audit layer (config/layer/secret/deploy/role/mass_delete)."""
    if _audit_logger is None:
        return
    try:
        level, _ = get_privileged_event_severity(event_type)
        uid = user_id
        sid = session_id
        if uid is None or sid is None:
            cu = getattr(request, "current_user", None) if request else None
            uid = (cu.get("id") if cu else None) or "service"
            if request:
                request_id = str(request.headers.get("X-Request-ID") or "").strip()[:128]
                if request_id:
                    sid = f"request:{request_id}"
                else:
                    credential = str(getattr(request, "current_token", None) or "")
                    if not credential:
                        credential = str(request.headers.get("Authorization") or "")
                    pepper = _configured_service_token() or str(app.secret_key or "")
                    sid = (
                        "auth:" + hmac.new(
                            pepper.encode("utf-8"),
                            credential.encode("utf-8"),
                            hashlib.sha256,
                        ).hexdigest()[:16]
                        if credential and pepper
                        else "service"
                    )
        meta = dict(metadata or {})
        _audit_logger.log_event(
            user_id=uid or "unknown",
            session_id=sid or "",
            event_type=event_type,
            event_category="privileged",
            audit_level=level,
            description=description,
            resource=resource or "",
            action=action or "",
            result=result or "",
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr) if request else "",
            user_agent=request.headers.get("User-Agent", "") if request else "",
            metadata=meta,
        )
    except Exception as e:
        logger.warning(f"Failed to log privileged event: {e}")


# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
VVAULT_FRONTEND_URL = _resolve_frontend_origin() or "http://localhost:7784"
VVAULT_BACKEND_URL = _resolve_backend_origin() or "http://localhost:8000"
VVAULT_ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("VVAULT_ADMIN_EMAILS", "admin@vvault.com").split(",")
    if email.strip()
}

_OAUTH_PLACEHOLDER_VALUES = {
    "",
    "YOUR_CLIENT_SECRET_HERE",
    "YOUR_CLIENT_ID_HERE",
    "your-google-client-id",
    "your-google-client-secret",
}


def _google_oauth_ready() -> bool:
    return (
        bool(GOOGLE_CLIENT_ID)
        and bool(GOOGLE_CLIENT_SECRET)
        and GOOGLE_CLIENT_ID not in _OAUTH_PLACEHOLDER_VALUES
        and GOOGLE_CLIENT_SECRET not in _OAUTH_PLACEHOLDER_VALUES
    )


def _google_oauth_config_error() -> str:
    if not GOOGLE_CLIENT_ID or GOOGLE_CLIENT_ID in _OAUTH_PLACEHOLDER_VALUES:
        return "Google OAuth client ID is not configured"
    if not GOOGLE_CLIENT_SECRET or GOOGLE_CLIENT_SECRET in _OAUTH_PLACEHOLDER_VALUES:
        return "Google OAuth client secret is not configured"
    return "Google OAuth is not configured"


def _get_frontend_url(default: str = None) -> str:
    frontend_url = _resolve_frontend_origin() or default or "http://localhost:7784"
    return frontend_url.rstrip("/")


def _get_backend_url(default: str = None) -> str:
    backend_url = _resolve_backend_origin() or default or "http://localhost:8000"
    return backend_url.rstrip("/")


def _dependency_error_code(exc: Exception) -> str:
    """Return a sanitized dependency error code without leaking config values."""
    return type(exc).__name__


def _body_database_dependency_status() -> Dict[str, Any]:
    """Check VVAULT-native body database readiness without remote runtime authority."""
    status: Dict[str, Any] = {
        "required": True,
        "ready": False,
        "status": "unhealthy",
        "configured": False,
        "schema": getattr(chatty_body_service, "BODY_SCHEMA", "ovvaults"),
        "source_database": None,
        "checks": {
            "vault_files_readable": False,
            "transcripts_readable": False,
        },
    }
    try:
        url = chatty_body_service.database_url()
        status["configured"] = bool(url)
        status["source_database"] = chatty_body_service.source_database_name(url)
        if not url:
            raise RuntimeError("VVAULT body database URL is unavailable")

        with chatty_body_service._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM vault_files LIMIT 1")
                status["checks"]["vault_files_readable"] = True
                cur.execute("SELECT 1 FROM transcripts LIMIT 1")
                status["checks"]["transcripts_readable"] = True

        status["ready"] = True
        status["status"] = "healthy"
    except Exception as exc:
        status["ready"] = False
        status["status"] = "unhealthy"
        status["error_code"] = _dependency_error_code(exc)
    return status


READINESS_REFRESH_SECONDS = max(
    1.0,
    float(os.environ.get("VVAULT_READINESS_REFRESH_SECONDS", "10")),
)
READINESS_PROJECTION_PROBE_BUDGET_SECONDS = max(
    READINESS_REFRESH_SECONDS,
    float(os.environ.get("VVAULT_READINESS_PROJECTION_PROBE_BUDGET_SECONDS", "20")),
)
READINESS_RENEWAL_MARGIN_SECONDS = max(
    READINESS_REFRESH_SECONDS * 2,
    READINESS_REFRESH_SECONDS + READINESS_PROJECTION_PROBE_BUDGET_SECONDS,
    float(os.environ.get("VVAULT_READINESS_RENEWAL_MARGIN_SECONDS", "20")),
)
READINESS_LEASE_SECONDS = max(
    READINESS_RENEWAL_MARGIN_SECONDS + (READINESS_REFRESH_SECONDS * 2),
    float(os.environ.get("VVAULT_READINESS_LEASE_SECONDS", "60")),
)
READINESS_STALE_GRACE_SECONDS = max(
    READINESS_LEASE_SECONDS,
    float(os.environ.get("VVAULT_READINESS_STALE_GRACE_SECONDS", "120")),
)
_readiness_snapshot_lock = threading.Lock()
_readiness_snapshot_generation = 0
_readiness_snapshot_expires_monotonic = 0.0
_readiness_last_refresh_succeeded = False
_readiness_last_good_monotonic = 0.0
_readiness_snapshot = {
    "required": True,
    "ready": False,
    "status": "initializing",
    "configured": bool(chatty_body_service.database_url()),
    "schema": getattr(chatty_body_service, "BODY_SCHEMA", "ovvaults"),
    "source_database": chatty_body_service.source_database_name(),
    "connection_state": "initializing",
    "authority": "vvault_body",
    "storage_owner": "ovvaults.vault_files",
    "transcript_owner": "ovvaults.transcripts",
    "checks": {
        "vault_files_readable": False,
        "transcripts_readable": False,
        "users_readable": False,
        "sessions_readable": False,
    },
    "generation": 0,
    "verified_at": None,
    "expires_at": None,
}


def _probe_ovvaults_readiness() -> Dict[str, Any]:
    """Validate canonical ownership with one checkout and one database round trip."""
    checks = {
        "vault_files_readable": False,
        "transcripts_readable": False,
        "users_readable": False,
        "sessions_readable": False,
    }
    source_database = chatty_body_service.source_database_name()
    with chatty_body_service._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_database() AS source_database,
                    to_regclass('vault_files') IS NOT NULL
                        AND has_table_privilege('vault_files', 'SELECT')
                        AS vault_files_readable,
                    to_regclass('transcripts') IS NOT NULL
                        AND has_table_privilege('transcripts', 'SELECT')
                        AS transcripts_readable,
                    to_regclass('users') IS NOT NULL
                        AND has_table_privilege('users', 'SELECT')
                        AS users_readable,
                    to_regclass('sessions') IS NOT NULL
                        AND has_table_privilege('sessions', 'SELECT')
                        AS sessions_readable
                """
            )
            readiness_row = cur.fetchone()
            if isinstance(readiness_row, dict):
                source_database = readiness_row.get("source_database") or source_database
                values = [
                    value
                    for key, value in readiness_row.items()
                    if key != "source_database"
                ]
            else:
                row_values = list(readiness_row or ())
                if row_values:
                    source_database = row_values.pop(0) or source_database
                values = row_values
            for check, value in zip(checks, values):
                checks[check] = bool(value)
            if not all(checks.values()):
                raise RuntimeError("canonical OVVAULTS tables are not readable")
    return {
        "source_database": source_database,
        "checks": checks,
    }


def _refresh_readiness_snapshot() -> Dict[str, Any]:
    """Refresh the readiness lease; a failed refresh never invents availability."""
    global _readiness_snapshot
    global _readiness_snapshot_expires_monotonic
    global _readiness_snapshot_generation
    global _readiness_last_refresh_succeeded
    global _readiness_last_good_monotonic

    now = datetime.now(timezone.utc)
    try:
        # A background readiness probe must never rotate the shared authority
        # pool merely because active inference work is using its connections.
        # PoolTimeout is a failed lease refresh; the existing snapshot remains
        # fail-closed and the worker retries on its next bounded cycle.
        probe = _probe_ovvaults_readiness()
        with _readiness_snapshot_lock:
            if not _readiness_last_refresh_succeeded:
                _readiness_snapshot_generation += 1
            _readiness_last_refresh_succeeded = True
            _readiness_last_good_monotonic = time.monotonic()
            expires_at = now + timedelta(seconds=READINESS_LEASE_SECONDS)
            _readiness_snapshot_expires_monotonic = time.monotonic() + READINESS_LEASE_SECONDS
            _readiness_snapshot = {
                "required": True,
                "ready": True,
                "status": "healthy",
                "configured": True,
                "schema": getattr(chatty_body_service, "BODY_SCHEMA", "ovvaults"),
                "source_database": probe["source_database"],
                "connection_state": "connected",
                "authority": "vvault_body",
                "storage_owner": "ovvaults.vault_files",
                "transcript_owner": "ovvaults.transcripts",
                "checks": probe["checks"],
                "generation": _readiness_snapshot_generation,
                "verified_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
            return copy.deepcopy(_readiness_snapshot)
    except Exception as exc:
        with _readiness_snapshot_lock:
            _readiness_last_refresh_succeeded = False
            snapshot = copy.deepcopy(_readiness_snapshot)
            snapshot["last_refresh_error_code"] = _dependency_error_code(exc)
            _readiness_snapshot = snapshot
        return _current_readiness_snapshot()


def _current_readiness_snapshot() -> Dict[str, Any]:
    with _readiness_snapshot_lock:
        snapshot = copy.deepcopy(_readiness_snapshot)
        expired = time.monotonic() >= _readiness_snapshot_expires_monotonic
        last_good_age = (
            time.monotonic() - _readiness_last_good_monotonic
            if _readiness_last_good_monotonic else None
        )
    snapshot["fresh"] = not expired and bool(snapshot.get("ready"))
    snapshot["refreshing"] = bool(expired and last_good_age is not None)
    if expired:
        within_grace = last_good_age is not None and last_good_age <= READINESS_STALE_GRACE_SECONDS
        snapshot["ready"] = within_grace
        snapshot["status"] = "stale" if within_grace else "unavailable"
        snapshot["connection_state"] = "stale" if within_grace else "degraded"
        snapshot["error_code"] = (
            "READINESS_REFRESHING"
            if within_grace else "READINESS_SNAPSHOT_EXPIRED"
        )
    snapshot["available"] = bool(snapshot.get("ready"))
    snapshot["last_good_age_ms"] = (
        int(last_good_age * 1000) if last_good_age is not None else None
    )
    return snapshot


def _readiness_refresh_worker() -> None:
    while True:
        time.sleep(READINESS_REFRESH_SECONDS)
        runtime = _refresh_readiness_snapshot()
        projection = _current_projection_warm_state()
        if runtime.get("ready") and _projection_lease_needs_refresh(projection):
            if _renew_projection_capability_lease(projection) is None:
                _prime_mandatory_projection_caches()


def _start_readiness_refresh_worker() -> threading.Thread:
    worker = threading.Thread(
        target=_readiness_refresh_worker,
        name="vvault-ovvaults-readiness",
        daemon=True,
    )
    worker.start()
    return worker


def _provider_transcript_search_backfill_worker() -> None:
    """Finish migration 0015's historical index work outside request paths."""
    while True:
        try:
            result = chatty_body_service.backfill_provider_transcript_search_chunks(
                batch_size=4,
            )
            if result.get("files_considered", 0) == 0:
                # Historical backfill is finite. New and updated provider files
                # are indexed by the database trigger, so repeatedly rescanning
                # the full canonical file table after completion only competes
                # with live memory reads for the bounded OVVAULTS pool.
                return
            else:
                # Keep this derived-index maintenance subordinate to live memory
                # reads. Large provider transcripts can hold a database worker for
                # several seconds, so a short pause between bounded batches is not
                # enough to prevent request starvation.
                time.sleep(2.0)
        except Exception as exc:
            logger.warning(
                "Provider transcript search backfill paused: %s",
                _dependency_error_code(exc),
            )
            time.sleep(10.0)


def _start_provider_transcript_search_backfill_worker() -> threading.Thread:
    worker = threading.Thread(
        target=_provider_transcript_search_backfill_worker,
        name="vvault-provider-transcript-search-backfill",
        daemon=True,
    )
    worker.start()
    return worker


def _storage_dependency_metadata() -> Dict[str, Any]:
    """Report VVAULT-native object storage config metadata without probing it yet."""
    s3_keys = ["S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    s3_configured = all(bool(os.environ.get(key)) for key in s3_keys)
    rest_url = (
        os.environ.get("VVAULT_OBJECT_STORAGE_URL")
        or os.environ.get("VVAULT_BODY_DB_URL")
    )
    rest_key = (
        os.environ.get("VVAULT_OBJECT_STORAGE_SERVICE_KEY")
        or os.environ.get("VVAULT_BODY_DB_SERVICE_ROLE_KEY")
        or os.environ.get("VVAULT_BODY_DB_ANON_KEY")
    )
    rest_configured = bool(rest_url and rest_key)
    configured = s3_configured or rest_configured
    return {
        "required_for_readiness": False,
        "configured": configured,
        "status": "configured" if configured else "unconfigured",
        "bucket_configured": bool(
            os.environ.get("S3_BUCKET")
            or os.environ.get("VVAULT_STORAGE_BUCKET")
        ),
        "provider": "s3_compatible" if s3_configured else "object_storage_rest" if rest_configured else "unconfigured",
    }


def _auth_dependency_metadata(readiness_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Report auth-adjacent runtime config metadata without making it readiness-blocking."""
    service_api_configured = bool(globals().get("VVAULT_SERVICE_TOKEN"))
    google_oauth_configured = _google_oauth_ready()
    if readiness_snapshot is None:
        auth_status = _auth_repository_status()
    else:
        checks = readiness_snapshot.get("checks") or {}
        auth_ready = bool(
            readiness_snapshot.get("ready")
            and checks.get("users_readable")
            and checks.get("sessions_readable")
        )
        auth_status = {
            "ready": auth_ready,
            "status": "healthy" if auth_ready else "unhealthy",
            "auth_owner": AUTH_OWNER,
            "session_owner": SESSION_OWNER,
            "source_database": readiness_snapshot.get("source_database"),
            "error_code": readiness_snapshot.get("error_code"),
        }
    return {
        "required_for_readiness": False,
        "status": auth_status.get("status") or "unknown",
        "ready": bool(auth_status.get("ready")),
        "auth_owner": auth_status.get("auth_owner") or AUTH_OWNER,
        "session_owner": auth_status.get("session_owner") or SESSION_OWNER,
        "source_database": auth_status.get("source_database"),
        "error_code": auth_status.get("error_code"),
        "service_api": {
            "configured": service_api_configured,
        },
        "google_oauth": {
            "configured": google_oauth_configured,
            "callback_route": "/api/auth/google/callback",
        },
    }


def _runtime_metadata() -> Dict[str, Any]:
    return {
        "server_pid": os.getpid(),
        "repo_root": str(_repo_root),
        "started_at": SERVER_STARTED_AT,
        "log_configured": bool(os.environ.get("VVAULT_LOG") or os.environ.get("VVAULT_DEVFULL_LOG")),
        "startup": dict(_startup_timings),
        "executionHostKeyRegistry": construct_execution_service_module.host_key_registry_readiness(),
        "executionAuthorizationKeyRegistry": construct_execution_service_module.execution_authorization_key_readiness(),
        "sourceProvenance": {
            "vvaultWebServer": dict(_SERVER_SOURCE_PROVENANCE),
            "autoRuntimeService": dict(_AUTO_RUNTIME_SOURCE_PROVENANCE),
            "autoAccessAssertion": dict(_AUTO_ACCESS_ASSERTION_SOURCE_PROVENANCE),
            "autoProjectionSigning": dict(_AUTO_PROJECTION_SIGNING_SOURCE_PROVENANCE),
            "chattyBodyService": dict(_CHATTY_BODY_SOURCE_PROVENANCE),
            "accountContextService": dict(_ACCOUNT_CONTEXT_SOURCE_PROVENANCE),
            "conversationThreadService": dict(_CONVERSATION_THREAD_SOURCE_PROVENANCE),
            "singletonConstructAuthorization": dict(
                _SINGLETON_CONSTRUCT_AUTHORIZATION_SOURCE_PROVENANCE
            ),
            "knowledgeContract": dict(_KNOWLEDGE_CONTRACT_SOURCE_PROVENANCE),
            "knowledgeActivationService": dict(_KNOWLEDGE_ACTIVATION_SOURCE_PROVENANCE),
            "knowledgePublicationService": dict(_KNOWLEDGE_PUBLICATION_SOURCE_PROVENANCE),
            "canonicalContextService": dict(_CANONICAL_CONTEXT_SOURCE_PROVENANCE),
            "canonicalContextUnitSchema": dict(_CANONICAL_CONTEXT_UNIT_SCHEMA_PROVENANCE),
            "canonicalContextManifestSchema": dict(_CANONICAL_CONTEXT_MANIFEST_SCHEMA_PROVENANCE),
            "constructWorkLoopService": dict(_CONSTRUCT_WORK_LOOP_SOURCE_PROVENANCE),
            "constructExecutionService": dict(_CONSTRUCT_EXECUTION_SOURCE_PROVENANCE),
            "constructExecutionProgramSchema": dict(_CONSTRUCT_EXECUTION_PROGRAM_SCHEMA_PROVENANCE),
            "constructExecutionEventSchema": dict(_CONSTRUCT_EXECUTION_EVENT_SCHEMA_PROVENANCE),
            "constructWorkProgramSchema": dict(_CONSTRUCT_WORK_PROGRAM_SCHEMA_PROVENANCE),
            "constructWorkEventSchema": dict(_CONSTRUCT_WORK_EVENT_SCHEMA_PROVENANCE),
            "constructWorkEventBatchSchema": dict(
                _CONSTRUCT_WORK_EVENT_BATCH_SCHEMA_PROVENANCE
            ),
            "constructWorkProgramCreateAuthorizationSchema": dict(
                _CONSTRUCT_WORK_CREATE_AUTH_SCHEMA_PROVENANCE
            ),
            "constructWorkScopeSchema": dict(_CONSTRUCT_WORK_SCOPE_SCHEMA_PROVENANCE),
            "constructWorkActiveScopeSchema": dict(
                _CONSTRUCT_WORK_ACTIVE_SCOPE_SCHEMA_PROVENANCE
            ),
            "constructWorkEvidenceSchema": dict(_CONSTRUCT_WORK_EVIDENCE_SCHEMA_PROVENANCE),
            "constructWorkHandoffAuthorizationSchema": dict(
                _CONSTRUCT_WORK_HANDOFF_AUTH_SCHEMA_PROVENANCE
            ),
            "constructWorkPreflightSchema": dict(_CONSTRUCT_WORK_PREFLIGHT_SCHEMA_PROVENANCE),
        },
    }


def _get_vvault_runtime_status(*, deep: bool = False) -> Dict[str, Any]:
    body_database = _body_database_dependency_status() if deep else _current_readiness_snapshot()
    ready = bool(body_database.get("ready"))
    return {
        "ready": ready,
        "available": ready,
        "fresh": bool(body_database.get("fresh")),
        "refreshing": bool(body_database.get("refreshing")),
        "status": "ready" if ready else "not_ready",
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": ready,
        "connection_state": body_database.get("connection_state") or ("connected" if ready else "degraded"),
        "runtime": _runtime_metadata(),
        "body_database": body_database,
        "storage": _storage_dependency_metadata(),
        "auth": _auth_dependency_metadata(None if deep else body_database),
    }


AUTH_REPOSITORY = vvault_auth_repository.VVaultAuthRepository()
AUTH_OWNER = vvault_auth_repository.AUTH_OWNER
SESSION_OWNER = vvault_auth_repository.SESSION_OWNER
VAULT_FILE_REPOSITORY = vvault_file_repository.VVaultFileRepository(auth_repository=AUTH_REPOSITORY)
VAULT_FILE_OWNER = vvault_file_repository.FILE_OWNER
VAULT_STORAGE_OWNER = vvault_file_repository.STORAGE_OWNER
UNSUPPORTED_OUTBOX_ITEM = "UNSUPPORTED_OUTBOX_ITEM"
VAULT_FILE_UPSERT = "vault_file_upsert"

SYSTEM_FILE_OUTBOX_MUTABLE_FIELDS = ["content", "file_type", "filename", "metadata", "sha256", "updated_at"]
SYSTEM_FILE_OUTBOX_IDENTITY_FIELDS = ["storage_path", "is_system", "user_id"]


def _load_pocketverse_metadata_from_body(construct_id: str) -> Optional[Dict[str, Any]]:
    callsign = (construct_id or "").strip().lower()
    if not callsign:
        return None
    bare_name = _bare_name_from_callsign(callsign)
    rows = VAULT_FILE_REPOSITORY.list_construct_identity_rows(
        callsign=callsign,
        bare_name=bare_name,
        user_id=None,
    )
    for row in rows:
        path = str(row.get("storage_path") or row.get("filename") or "").lower()
        if not path.endswith("/metadata.json"):
            continue
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            parsed = _safe_json_loads(content)
            if isinstance(parsed, dict):
                return parsed
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata:
            return metadata
    return None


def _is_admin_email(email: Optional[str]) -> bool:
    return bool(email and email.strip().lower() in VVAULT_ADMIN_EMAILS)


def _resolve_user_role(email: Optional[str], local_user: Optional[Dict] = None, fallback_user: Optional[Dict] = None) -> str:
    for candidate in (local_user, fallback_user):
        if candidate and candidate.get('role'):
            return candidate['role']
    if _is_admin_email(email):
        return 'admin'
    return 'user'


def _session_token_hash(token: str) -> str:
    return vvault_auth_repository.hash_session_token(token, app.config.get("SECRET_KEY", ""))


def _auth_repository_status() -> Dict[str, Any]:
    return AUTH_REPOSITORY.healthcheck()


def _auth_repository_ready() -> bool:
    return bool(_auth_repository_status().get("ready"))


def _auth_repository_unavailable_response(route: str):
    status = _auth_repository_status()
    logger.warning(
        "VVAULT_AUTH_UNAVAILABLE route=%s auth_owner=%s session_owner=%s error_code=%s",
        route,
        status.get("auth_owner") or AUTH_OWNER,
        status.get("session_owner") or SESSION_OWNER,
        status.get("error_code"),
    )
    return jsonify({
        "success": False,
        "error": "VVAULT auth database is unavailable",
        "error_code": "VVAULT_AUTH_UNAVAILABLE",
        "auth_owner": status.get("auth_owner") or AUTH_OWNER,
        "session_owner": status.get("session_owner") or SESSION_OWNER,
    }), 503


def _oauth_auth_unavailable_redirect(frontend_url: str):
    from flask import redirect
    from urllib.parse import quote

    error_message = quote(
        "Google sign-in cannot complete because VVAULT auth storage is unavailable. Please retry after recovery.",
        safe="",
    )
    logger.warning(
        "GOOGLE_OAUTH_BLOCKED dependency=vvault_auth contract=identity_fail_closed auth_owner=%s session_owner=%s ts=%s",
        AUTH_OWNER,
        SESSION_OWNER,
        datetime.now(timezone.utc).isoformat(),
    )
    return redirect(f"{frontend_url}/?oauth_error={error_message}")


def _fetch_all_rows(query_factory, page_size: int = 1000) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0

    while True:
        result = query_factory().range(offset, offset + page_size - 1).execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return rows


def _fetch_scoped_vault_rows(
    requested_path: str,
    *,
    user_id: Optional[str],
    is_admin: bool,
    page_size: int = 1000,
) -> List[Dict[str, Any]]:
    return VAULT_FILE_REPOSITORY.list_for_browser(
        user_id=user_id,
        is_admin=False,
        requested_path=requested_path,
    )


def _parse_vault_timestamp(value: Optional[str]) -> float:
    if not value:
        return 0.0
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _vault_file_key(row: Dict[str, Any]) -> str:
    path = (row.get('storage_path') or row.get('filename') or '').strip()
    if path:
        return path

    filename = (row.get('filename') or '').strip()
    construct_id = (row.get('construct_id') or '').strip()
    metadata = row.get('metadata') or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return map_to_vsi_folder(filename, construct_id, metadata)


def _choose_preferred_vault_row(current: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    # The repository derives this from the immutable ledger: a replacement is
    # current only when no later receipt names it as prior_row_id.
    current_integrity_rank = 1 if current.get("integrity_repair_leaf") is True else 0
    candidate_integrity_rank = 1 if candidate.get("integrity_repair_leaf") is True else 0
    if candidate_integrity_rank != current_integrity_rank:
        return candidate if candidate_integrity_rank > current_integrity_rank else current
    current_ts = max(
        _parse_vault_timestamp(current.get('updated_at')),
        _parse_vault_timestamp(current.get('created_at')),
    )
    candidate_ts = max(
        _parse_vault_timestamp(candidate.get('updated_at')),
        _parse_vault_timestamp(candidate.get('created_at')),
    )
    if candidate_ts > current_ts:
        return candidate
    if candidate_ts < current_ts:
        return current

    current_len = len(current.get('content') or '')
    candidate_len = len(candidate.get('content') or '')
    if candidate_len > current_len:
        return candidate
    return current


def _dedupe_vault_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        key = _vault_file_key(row)
        if not key:
            continue
        if key in deduped:
            deduped[key] = _choose_preferred_vault_row(deduped[key], row)
        else:
            deduped[key] = row

    ordered = list(deduped.values())
    ordered.sort(
        key=lambda row: max(
            _parse_vault_timestamp(row.get('updated_at')),
            _parse_vault_timestamp(row.get('created_at')),
        ),
        reverse=True,
    )
    return ordered


KNOWLEDGE_WRITE_STATEMENT_TIMEOUT_MS = 180_000


def _upsert_vault_file_record(record: Dict[str, Any], *, context: str) -> Dict[str, Any]:
    logical_path = (record.get('storage_path') or record.get('filename') or '').strip()
    if not logical_path:
        raise ValueError("Vault file record is missing filename/storage_path")

    record = dict(record)
    record['filename'] = logical_path
    record['storage_path'] = logical_path
    statement_timeout_ms = (
        KNOWLEDGE_WRITE_STATEMENT_TIMEOUT_MS
        if context == 'knowledge_upload'
        else None
    )
    result = VAULT_FILE_REPOSITORY.upsert(
        record,
        statement_timeout_ms=statement_timeout_ms,
    )
    logger.info(
        "VFILE_LOCAL_UPSERT: context=%s action=%s path=%s id=%s",
        context,
        result.get('action'),
        logical_path,
        result.get('id'),
    )
    return result


def _vvault_unavailable_response(message: str, *, include_constructs: bool = False):
    payload = {
        "success": True,
        "vvault_available": False,
        "degraded": True,
        "canonical": False,
        "storage_mode": "vvault_body",
        "storage_owner": VAULT_FILE_OWNER,
        "error_code": "VVAULT_BODY_UNAVAILABLE",
        "message": message,
    }
    if include_constructs:
        payload.update({"constructs": [], "count": 0})
    else:
        payload.update({"files": [], "count": 0, "user_root": "Vault"})
    return jsonify(payload)


def _is_dependency_timeout(error: Exception) -> bool:
    def _iter_chain(root: Exception, limit: int = 8):
        seen = set()
        current = root
        depth = 0
        while current is not None and depth < limit and id(current) not in seen:
            seen.add(id(current))
            yield current
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
            depth += 1

    timeout_type_names = {
        "Timeout",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
        "ReadTimeoutError",
        "PoolTimeout",
    }
    timeout_type_modules = (
        "requests",
        "httpx",
        "urllib3",
    )

    for err in _iter_chain(error):
        err_type = type(err)
        type_name = err_type.__name__
        module_name = getattr(err_type, "__module__", "")
        if type_name in timeout_type_names:
            return True
        if any(mod in module_name for mod in timeout_type_modules) and "timeout" in type_name.lower():
            return True

        lowered = str(err or "").lower()
        signals = (
            "'code': 522",
            '"code": 522',
            "error code 522",
            "cloudflare",
            "request timeout",
            "timed out",
            "timeout",
            "json could not be generated",
            "connection timeout",
        )
        if any(signal in lowered for signal in signals):
            return True

    return False


def _dependency_timeout_message() -> str:
    return (
        "VVAULT local persistence is temporarily unavailable. "
        "Canonical writes remain blocked until local readiness is restored."
    )


def _log_vvault_dependency_outage(route: str, contract: str, status_code: int, error_code: str) -> None:
    logger.warning(
        "VVAULT_DEPENDENCY_OUTAGE route=%s operation=dependency dependency=body_database "
        "contract=%s status=%s error_code=%s storage_mode=vvault_body canonical=false ts=%s",
        route,
        contract,
        status_code,
        error_code,
        datetime.now(timezone.utc).isoformat(),
    )


def _dependency_timeout_response(
    route: str,
    *,
    status_code: int,
    contract: str,
    include_success: bool = False,
    include_constructs: bool = False,
    include_files: bool = False,
    extra: Optional[Dict[str, Any]] = None,
):
    error_code = "VVAULT_DEPENDENCY_TIMEOUT"
    payload = {
        "vvault_available": False,
        "degraded": True,
        "canonical": False,
        "storage_mode": "vvault_body",
        "storage_owner": VAULT_FILE_OWNER,
        "error_code": error_code,
        "message": _dependency_timeout_message(),
    }
    if include_success:
        payload["success"] = status_code < 400
    if include_constructs:
        payload.update({"constructs": [], "count": 0})
    if include_files:
        payload.update({"files": [], "count": 0, "user_root": "Vault"})
    if extra:
        payload.update(extra)

    _log_vvault_dependency_outage(route, contract, status_code, error_code)
    return jsonify(payload), status_code


def _dependency_timeout_read_response(
    route: str,
    *,
    include_constructs: bool = False,
    include_files: bool = False,
    extra: Optional[Dict[str, Any]] = None,
):
    return _dependency_timeout_response(
        route,
        status_code=200,
        contract="soft_degrade",
        include_success=True,
        include_constructs=include_constructs,
        include_files=include_files,
        extra=extra,
    )


def _dependency_timeout_write_response(route: str, *, extra: Optional[Dict[str, Any]] = None):
    return _dependency_timeout_response(
        route,
        status_code=503,
        contract="strict_503",
        extra=extra,
    )


def _vvault_write_block_response(route: str, *, dependency_status: Optional[Dict[str, Any]] = None):
    status = dependency_status or _body_database_dependency_status()
    error_code = status.get("error_code") or "VVAULT_NOT_READY"
    logger.warning(
        "VVAULT_WRITE_BLOCKED route=%s operation=write dependency=body_database "
        "error_code=%s status=503 storage_mode=vvault_body canonical=false ts=%s",
        route,
        error_code,
        datetime.now(timezone.utc).isoformat(),
    )
    return jsonify({
        "success": False,
        "vvault_available": False,
        "degraded": True,
        "canonical": False,
        "storage_mode": "vvault_body",
        "storage_owner": VAULT_FILE_OWNER,
        "error_code": error_code,
        "message": "VVAULT local persistence is unavailable; canonical writes remain blocked.",
    }), 503


def _vvault_read_block_response(route: str, *, dependency_status: Optional[Dict[str, Any]] = None):
    status = dependency_status or _body_database_dependency_status()
    error_code = status.get("error_code") or "VVAULT_NOT_READY"
    payload = {
        "success": True,
        "vvault_available": False,
        "degraded": True,
        "canonical": False,
        "storage_mode": "vvault_body",
        "storage_owner": VAULT_FILE_OWNER,
        "error_code": error_code,
        "message": "VVAULT local persistence is unavailable; canonical reads are unavailable.",
    }
    status_code = 503
    if route == "/api/vault/files" or route.startswith("/api/vault/files?"):
        payload.update({"files": [], "count": 0, "user_root": "Vault"})
        status_code = 200
    elif route == "/api/chatty/constructs":
        payload.update({"constructs": [], "count": 0})
        status_code = 200
    elif route == "/api/vault/user-info":
        current_user = getattr(request, "current_user", None) or {}
        user_email = current_user.get("email", "")
        user_role = current_user.get("role", "user")
        display_name = user_email.split("@")[0].replace(".", " ").title() if user_email else "Vault User"
        payload.update({
            "display_name": display_name,
            "user_id": None,
            "is_admin": user_role == "admin",
            "root_label": display_name if user_role != "admin" else "Vault (Admin)",
        })
        status_code = 200
    logger.warning(
        "VVAULT_READ_BLOCKED route=%s operation=read dependency=body_database "
        "error_code=%s status=%s storage_mode=vvault_body canonical=false ts=%s",
        route,
        error_code,
        status_code,
        datetime.now(timezone.utc).isoformat(),
    )
    return jsonify(payload), status_code


def _metadata_to_dict(metadata: Any) -> Dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


_VAULT_FILES_HAS_UPDATED_AT: Optional[bool] = None


def _is_missing_updated_at_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        'updated_at' in message
        and ('does not exist' in message or '42703' in message or 'pgrst204' in message)
    )


def _select_with_optional_updated_at(columns: str, include_updated_at: bool) -> str:
    parts = [part.strip() for part in columns.split(',') if part.strip()]
    if not include_updated_at:
        parts = [part for part in parts if part != 'updated_at']
    return ', '.join(parts)


def _identity_projection_specs() -> Dict[str, Dict[str, Any]]:
    return {
        "conditioning": {
            "canonical_filename": "conditioning.txt",
            "legacy_basenames": ["conditioning.json"],
            "format": "text",
            "comparison_mode": "text",
        },
        "definition": {
            "canonical_filename": "definition.json",
            "legacy_basenames": ["definition.txt", "definitions.json"],
            "format": "json",
            "comparison_mode": "json",
            "attempt_json_parse": True,
        },
        "physicalFeatures": {
            "canonical_filename": "physical_features.json",
            "legacy_basenames": ["physical_features.txt"],
            "format": "text",
            "comparison_mode": "text",
            "attempt_json_parse": True,
        },
        "voice": {
            "canonical_filename": "voice.json",
            "legacy_basenames": [],
            "format": "json",
            "comparison_mode": "json",
            "attempt_json_parse": True,
        },
    }


def _identity_projection_canonical_path(callsign: str, field: str) -> str:
    spec = _identity_projection_specs()[field]
    return f"instances/{callsign}/identity/{spec['canonical_filename']}"


def _identity_projection_select_columns(include_content: bool = False) -> str:
    columns = ['id', 'filename', 'storage_path', 'sha256', 'metadata', 'created_at', 'construct_id', 'user_id']
    if include_content:
        columns.append('content')
    if _VAULT_FILES_HAS_UPDATED_AT is not False:
        columns.append('updated_at')
    return ', '.join(columns)


def _query_construct_identity_projection_pool(
    callsign: str, bare_name: str, owner_user_id: str,
) -> List[Dict[str, Any]]:
    return VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=bare_name,
        user_id=owner_user_id,
        include_content=False,
    )


def _load_identity_projection_content(file_id: str, owner_user_id: str) -> Any:
    row = VAULT_FILE_REPOSITORY.get_user_file(file_id=file_id, user_id=owner_user_id)
    return row.get('content') if row else None


def _normalize_identity_projection_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    normalized = value.replace('\r\n', '\n').replace('\r', '\n')
    return normalized.rstrip('\n')


def _try_parse_projection_json(value: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "attempted": True,
        "valid": False,
    }
    try:
        parsed = value if not isinstance(value, str) else json.loads(value)
        normalized = json.dumps(parsed, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        result["valid"] = True
        result["normalized_json_sha256"] = _sha256_text(normalized)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _identity_projection_candidate_sort_key(candidate: Dict[str, Any]) -> Tuple[float, float, str]:
    updated = _parse_vault_timestamp(candidate.get('updated_at'))
    created = _parse_vault_timestamp(candidate.get('created_at'))
    return (updated, created, str(candidate.get('id') or ''))


def _select_current_identity_projection(field: str, candidates: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    del field  # reserved for future field-specific ranking
    if not candidates:
        return None, []

    canonical_matches = [c for c in candidates if c.get('match_type') == 'canonical']
    pool = canonical_matches if canonical_matches else candidates
    ordered = sorted(pool, key=_identity_projection_candidate_sort_key, reverse=True)
    current = ordered[0]
    duplicates = [candidate for candidate in candidates if candidate.get('id') != current.get('id')]
    return current, duplicates


def _build_identity_projection_comparison(field: str, current_content: Any) -> Tuple[Dict[str, Any], bool]:
    spec = _identity_projection_specs()[field]

    if spec["comparison_mode"] == "json":
        json_parse = _try_parse_projection_json(current_content)
        comparison: Dict[str, Any] = {
            "mode": "json",
            "json_parse": json_parse,
        }
        if json_parse.get("valid"):
            comparison["normalized_sha256"] = json_parse["normalized_json_sha256"]
            return comparison, False
        return comparison, True

    normalized_text = _normalize_identity_projection_text(current_content)
    comparison = {
        "mode": "text",
        "normalized_sha256": _sha256_text(normalized_text),
        "text_length": len(normalized_text),
    }
    if spec.get("attempt_json_parse"):
        comparison["json_parse"] = _try_parse_projection_json(current_content)
    return comparison, False


def _build_identity_projection_field_state(
    field: str, callsign: str, candidates: List[Dict[str, Any]], owner_user_id: str,
) -> Dict[str, Any]:
    spec = _identity_projection_specs()[field]
    canonical_path = _identity_projection_canonical_path(callsign, field)
    current, duplicates = _select_current_identity_projection(field, candidates)

    field_state: Dict[str, Any] = {
        "exists": current is not None,
        "status": "missing",
        "canonical_path": canonical_path,
        "format": spec["format"],
        "current": None,
        "comparison": None,
        "duplicates": [],
    }

    if not current:
        return field_state

    field_state["current"] = {
        "id": current.get("id"),
        "storage_path": current.get("storage_path"),
        "filename": current.get("filename"),
        "created_at": current.get("created_at"),
        "updated_at": current.get("updated_at"),
        "sha256": current.get("sha256"),
    }

    current_content = current.get("content")
    if current_content is None and current.get("id"):
        current_content = _load_identity_projection_content(current["id"], owner_user_id)
    comparison, is_invalid = _build_identity_projection_comparison(field, current_content)
    field_state["comparison"] = comparison

    if is_invalid:
        field_state["status"] = "invalid"
    elif duplicates:
        field_state["status"] = "conflict"
    else:
        field_state["status"] = "present"

    field_state["duplicates"] = [
        {
            "id": candidate.get("id"),
            "storage_path": candidate.get("storage_path"),
            "filename": candidate.get("filename"),
            "created_at": candidate.get("created_at"),
            "updated_at": candidate.get("updated_at"),
        }
        for candidate in sorted(duplicates, key=_identity_projection_candidate_sort_key, reverse=True)
    ]
    return field_state


def _load_identity_projection_candidates(
    construct_id: str, owner_user_id: str,
) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    callsign = _normalize_callsign(construct_id)
    bare_name = _bare_name_from_callsign(callsign)
    specs = _identity_projection_specs()
    pool = _query_construct_identity_projection_pool(callsign, bare_name, owner_user_id)

    grouped: Dict[str, List[Dict[str, Any]]] = {field: [] for field in specs}
    candidates_by_field: Dict[str, Dict[str, Dict[str, Any]]] = {field: {} for field in specs}

    for row in pool:
        row_copy = dict(row)
        row_copy["metadata"] = _metadata_to_dict(row.get("metadata"))
        row_filename = row_copy.get("filename") or ""
        row_storage_path = row_copy.get("storage_path") or ""
        row_basename = os.path.basename(row_storage_path or row_filename)
        in_identity_folder = (
            "/identity/" in row_storage_path
            or "/identity/" in row_filename
            or row_copy["metadata"].get("folder") == "identity"
        )

        for field, spec in specs.items():
            canonical_path = _identity_projection_canonical_path(callsign, field)
            accepted_basenames = {spec["canonical_filename"], *spec["legacy_basenames"]}

            match_type: Optional[str] = None
            if row_storage_path == canonical_path or row_filename == canonical_path:
                match_type = "canonical"
            elif row_basename in accepted_basenames and (in_identity_folder or row_filename == row_basename):
                match_type = "legacy"

            if not match_type:
                continue

            row_with_match = dict(row_copy)
            row_with_match["match_type"] = match_type
            candidates_by_field[field][str(row_with_match.get("id"))] = row_with_match

    for field in specs:
        grouped[field] = list(candidates_by_field[field].values())

    return callsign, grouped


def _read_identity_projection_snapshot(construct_id: str, owner_user_id: str) -> Dict[str, Any]:
    callsign, grouped = _load_identity_projection_candidates(construct_id, owner_user_id)

    fields: Dict[str, Any] = {}
    fields_present: List[str] = []
    fields_missing: List[str] = []
    conflict_fields: List[str] = []
    invalid_fields: List[str] = []

    for field in _identity_projection_specs():
        state = _build_identity_projection_field_state(
            field, callsign, grouped.get(field, []), owner_user_id,
        )
        fields[field] = state
        if state["status"] == "present":
            fields_present.append(field)
        elif state["status"] == "missing":
            fields_missing.append(field)
        elif state["status"] == "conflict":
            conflict_fields.append(field)
        elif state["status"] == "invalid":
            invalid_fields.append(field)

    return {
        "success": True,
        "construct_id": callsign,
        "fields_present": fields_present,
        "fields_missing": fields_missing,
        "conflict_fields": conflict_fields,
        "invalid_fields": invalid_fields,
        "fields": fields,
    }


def _serialize_projected_field(field: str, value: Any) -> Tuple[str, str]:
    if field == 'conditioning':
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value, "text"

    if field == 'definition':
        if not isinstance(value, dict):
            raise ValueError("definition must be a canonical JSON object")
        return json.dumps(value, indent=2, ensure_ascii=False), "json"

    if field == 'physicalFeatures':
        if isinstance(value, dict):
            return json.dumps(value, indent=2, ensure_ascii=False), "json"
        raise ValueError("physicalFeatures must be a canonical JSON object")

    if field == 'voice':
        if isinstance(value, str):
            raise ValueError("voice must be a JSON value, not a plain string")
        try:
            return json.dumps(value, indent=2, ensure_ascii=False), "json"
        except TypeError as exc:
            raise ValueError(f"voice must be JSON-serializable: {exc}") from exc

    raise ValueError(f"Unsupported identity projection field: {field}")


def _find_canonical_identity_projection_rows(
    callsign: str, canonical_path: str, *, owner_user_id: str
) -> List[Dict[str, Any]]:
    row = VAULT_FILE_REPOSITORY.find_exact(
        filename=canonical_path,
        storage_path=canonical_path,
        construct_id=callsign,
        user_id=owner_user_id,
        is_admin=False,
    )
    return [row] if row else []


def _upsert_identity_projection_record(record: Dict[str, Any], canonical_path: str) -> Tuple[str, Optional[str], Optional[str]]:
    callsign = record['construct_id']
    existing_rows = _find_canonical_identity_projection_rows(
        callsign, canonical_path, owner_user_id=record['user_id']
    )
    previous_sha = None

    if existing_rows:
        existing_rows.sort(key=_identity_projection_candidate_sort_key, reverse=True)
        current = existing_rows[0]
        previous_sha = current.get('sha256')
        update_record = dict(record)
        update_record['created_at'] = current.get('created_at') or record['created_at']
        result = _upsert_vault_file_record(update_record, context='identity_projection')
        return result.get('action') or 'updated', result.get('id') or current['id'], previous_sha

    result = _upsert_vault_file_record(record, context='identity_projection')
    return result.get('action') or 'created', result.get('id'), None


def _project_identity_fields(
    construct_id: str, fields: Dict[str, Any], *, owner_user_id: str, dry_run: bool = False
) -> Dict[str, Any]:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")

    callsign = _normalize_callsign(construct_id)
    specs = _identity_projection_specs()
    now = datetime.now(timezone.utc).isoformat()
    if not _is_uuid(owner_user_id):
        raise ValueError("authenticated canonical owner is required")
    results: Dict[str, Any] = {}

    for field, value in fields.items():
        if field not in specs:
            raise ValueError(f"Unsupported identity projection field: {field}")
        if value is None:
            raise ValueError(f"{field} cannot be null")

        content, storage_format = _serialize_projected_field(field, value)
        canonical_path = _identity_projection_canonical_path(callsign, field)
        current_rows = _find_canonical_identity_projection_rows(
            callsign, canonical_path, owner_user_id=owner_user_id
        )
        current_rows.sort(key=_identity_projection_candidate_sort_key, reverse=True)
        current = current_rows[0] if current_rows else None
        previous_sha = current.get('sha256') if current else None
        new_sha = _sha256_text(content)
        action = 'updated' if current else 'created'

        results[field] = {
            "action": action,
            "canonical_path": canonical_path,
            "storage_format": storage_format,
            "previous_sha256": previous_sha,
            "new_sha256": new_sha,
        }

        if dry_run:
            continue

        existing_metadata = _metadata_to_dict(current.get('metadata')) if current else {}
        existing_metadata['folder'] = 'identity'
        existing_metadata['identity_projection'] = {
            "field": field,
            "schema_version": 1,
            "storage_format": storage_format,
            "source": "chatty_projection",
            "projected_at": now,
        }

        record = {
            "filename": canonical_path,
            "storage_path": canonical_path,
            "file_type": "text",
            "content": content,
            "construct_id": callsign,
            "user_id": current.get('user_id') if current else owner_user_id,
            "is_system": False,
            "sha256": new_sha,
            "metadata": json.dumps(existing_metadata),
            "created_at": current.get('created_at') if current else now,
            "updated_at": now,
        }
        action, file_id, previous_sha = _upsert_identity_projection_record(record, canonical_path)
        results[field].update({
            "action": action,
            "file_id": file_id,
            "previous_sha256": previous_sha,
        })

    return {
        "success": True,
        "construct_id": callsign,
        "dry_run": dry_run,
        "results": results,
    }


def _get_current_user_email() -> Optional[str]:
    current_user = getattr(request, 'current_user', None)
    if not current_user:
        return None
    return current_user.get('email')


def _get_authenticated_user_id() -> Optional[str]:
    current_user = getattr(request, 'current_user', None) or {}
    current_user_id = current_user.get('id') or current_user.get('user_id')
    if current_user.get('auth_mode') in {'signed_assertion', 'session', 'legacy_chatty_service'}:
        # Native sessions and assertions carry their owner UUID directly.
        if _is_uuid(current_user_id):
            return current_user_id.strip()
        if current_user.get('auth_mode') == 'legacy_chatty_service':
            legacy_user = db_get_user(current_user.get('email') or '')
            legacy_user_id = str((legacy_user or {}).get('id') or '').strip()
            return legacy_user_id if _is_uuid(legacy_user_id) else None
    return None


def _auth_identity_failure_response(receipt: Dict[str, Any]):
    error_code = receipt.get("error_code") or "IDENTITY_RESOLUTION_FAILED"
    status = 409 if error_code == "IDENTITY_CONFLICT" else 503
    return jsonify(
        {
            "success": False,
            "error": receipt.get("message") or "Identity resolution failed",
            "error_code": error_code,
            "identity_receipt": receipt,
        }
    ), status


def _oauth_identity_authority_available() -> Tuple[bool, Dict[str, Any]]:
    state = _auth_repository_status()
    return bool(state.get("ready")), state


def _oauth_identity_authority_redirect(frontend_url: str, state: Dict[str, Any]):
    from flask import redirect
    from urllib.parse import quote

    error_message = quote(
        "Google sign-in cannot complete because VVAULT auth storage is unavailable. Please retry after recovery.",
        safe="",
    )
    logger.warning(
        "GOOGLE_OAUTH_BLOCKED dependency=vvault_auth contract=identity_fail_closed "
        "auth_owner=%s session_owner=%s error_code=%s ts=%s",
        state.get("auth_owner") or AUTH_OWNER,
        state.get("session_owner") or SESSION_OWNER,
        state.get("error_code"),
        datetime.now(timezone.utc).isoformat(),
    )
    return redirect(f"{frontend_url}/?oauth_error={error_message}")


def _load_vault_file_text(row: Optional[Dict[str, Any]]) -> str:
    return VAULT_FILE_REPOSITORY.load_text(row)


def _looks_readable_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    if "\x00" in value:
        return False
    printable = sum(1 for ch in value if ch.isprintable() or ch in "\n\r\t")
    return printable / max(len(value), 1) >= 0.95


def _is_structured_preview_type(ext: str, file_type: str) -> bool:
    return ext in {'.capsule', '.json'} or file_type == 'application/json'


def _is_text_preview_type(ext: str, file_type: str) -> bool:
    if _is_structured_preview_type(ext, file_type):
        return True
    return file_type.startswith('text/') or file_type in {
        'text',
        'conversation',
        'transcript',
        'prompt',
        'config',
        'identity',
        'capsule',
    }


def _preview_deadline(preview_budget_ms: Optional[int]) -> Optional[float]:
    if preview_budget_ms is None:
        return None
    return time.perf_counter() + max(preview_budget_ms, 0) / 1000.0


def _preview_deadline_expired(deadline: Optional[float]) -> bool:
    return deadline is not None and time.perf_counter() >= deadline


def _preview_elapsed_ms(started_at: float) -> int:
    return int(round((time.perf_counter() - started_at) * 1000))


def _query_transcript_rows_for_preview(callsign: str, bare_name: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen_ids = set()
    transcript_keywords = ['transcript', 'character_ai', 'chatgpt', 'chat_with_', 'conversation', 'chat']
    rows = VAULT_FILE_REPOSITORY.query_transcript_rows_for_preview(
        callsign=callsign,
        bare_name=bare_name,
        limit=VAULT_PREVIEW_MAX_TRANSCRIPTS,
    )
    for row in rows:
        row_id = row.get('id')
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)

        path = (row.get('filename') or row.get('storage_path') or '').lower()
        ftype = (row.get('file_type') or '').lower()
        if not path:
            continue
        if any(ext in path for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.capsule']):
            continue
        if not (
            any(keyword in path for keyword in transcript_keywords)
            or 'transcript' in ftype
            or 'markdown' in ftype
            or 'text' in ftype
        ):
            continue
        candidates.append(row)
        if len(candidates) >= VAULT_PREVIEW_MAX_TRANSCRIPTS:
            return candidates

    return candidates


def _build_capsule_preview_from_transcripts(construct_id: str, deadline: Optional[float] = None) -> str:
    started_at = time.perf_counter()
    callsign = _normalize_callsign(construct_id)
    bare_name = _bare_name_from_callsign(callsign)
    if _preview_deadline_expired(deadline):
        logger.info("VAULT_PREVIEW_TIMING: capsule transcript preview skipped for %s because budget was already exhausted", callsign)
        return ""
    transcript_rows = _query_transcript_rows_for_preview(callsign, bare_name)
    if not transcript_rows:
        return ""

    transcript_files = []
    for row in transcript_rows:
        if _preview_deadline_expired(deadline):
            logger.info(
                "VAULT_PREVIEW_TIMING: capsule transcript preview hit deadline for %s after %sms while collecting transcript details",
                callsign,
                _preview_elapsed_ms(started_at),
            )
            break
        row_id = row.get('id')
        if not row_id:
            continue
        detail_row = row
        content = detail_row.get('content') if isinstance(detail_row.get('content'), str) else ""

        if content and len(content) > 100:
            transcript_files.append({
                'id': row_id,
                'filename': detail_row.get('filename') or detail_row.get('storage_path') or '',
                'content': content,
                'created_at': detail_row.get('created_at') or row.get('created_at', ''),
            })
            if len(transcript_files) >= VAULT_PREVIEW_MAX_TRANSCRIPTS:
                break

    if not transcript_files:
        return ""

    parser = ContinuityParser(callsign)
    entries = parser.process_all_transcripts(transcript_files)
    now = datetime.now(timezone.utc).isoformat()

    if entries:
        ledger_entries = parser.generate_ledger_json(entries, include_exchanges=False)
        payload = {
            'construct_id': callsign,
            'capsule_version': '2.0.0-preview',
            'generator': 'vault_transcript_preview',
            'preview_only': True,
            'last_synced_at': now,
            'summary': {
                'total_sessions': len(ledger_entries),
                'total_source_transcripts': len(transcript_files),
                'sampled_source_transcripts': len(transcript_files),
                'total_exchanges': sum(entry.get('exchange_count', 0) for entry in ledger_entries),
                'date_range': {
                    'earliest': min((entry.get('estimated_date', '') for entry in ledger_entries), default=''),
                    'latest': max((entry.get('estimated_date', '') for entry in ledger_entries), default=''),
                },
                'sources': sorted({entry.get('source', 'Conversation') for entry in ledger_entries}),
            },
            'sessions': ledger_entries,
        }
        logger.info(
            "VAULT_PREVIEW_TIMING: capsule transcript preview built structured preview for %s from %s transcripts in %sms",
            callsign,
            len(transcript_files),
            _preview_elapsed_ms(started_at),
        )
        return json.dumps(payload, indent=2, default=str)

    transcript_previews = []
    for transcript in transcript_files[:50]:
        filename = transcript.get('filename', '')
        transcript_previews.append({
            'filename': filename,
            'created_at': transcript.get('created_at', ''),
            'content_length': len(transcript.get('content') or ''),
            'excerpt': (transcript.get('content') or '').strip()[:1000],
            'source': parser.detect_source(filename),
        })

    payload = {
        'construct_id': callsign,
        'capsule_version': '2.0.0-preview',
        'generator': 'vault_transcript_preview',
        'preview_only': True,
        'preview_degraded': True,
        'last_synced_at': now,
        'summary': {
            'total_source_transcripts': len(transcript_files),
            'previewed_transcripts': len(transcript_previews),
            'sampled_source_transcripts': len(transcript_files),
            'reason': 'continuity_parser_returned_no_entries',
        },
        'transcript_previews': transcript_previews,
    }
    logger.info(
        "VAULT_PREVIEW_TIMING: capsule transcript preview built degraded preview for %s from %s transcripts in %sms",
        callsign,
        len(transcript_files),
        _preview_elapsed_ms(started_at),
    )
    return json.dumps(payload, indent=2, default=str)


def _build_capsule_preview_from_candidate_ids(
    construct_id: str,
    transcript_ids: List[str],
    *,
    user_id: Optional[str] = None,
    deadline: Optional[float] = None,
) -> str:
    started_at = time.perf_counter()
    callsign = _normalize_callsign(construct_id)
    ids = [str(value).strip() for value in (transcript_ids or []) if str(value).strip()]
    if not ids:
        return ""

    ids = ids[:VAULT_PREVIEW_MAX_TRANSCRIPTS]
    rows = VAULT_FILE_REPOSITORY.get_by_ids(ids)

    transcript_files = []
    for row in _sort_vault_rows(rows):
        if _preview_deadline_expired(deadline):
            logger.info(
                "VAULT_PREVIEW_TIMING: candidate transcript preview hit deadline for %s after %sms",
                callsign,
                _preview_elapsed_ms(started_at),
            )
            break
        if user_id and row.get('user_id') not in (None, user_id):
            continue
        content = row.get('content') if isinstance(row.get('content'), str) else ""
        if content and len(content) > 100:
            transcript_files.append({
                'id': row.get('id'),
                'filename': row.get('filename') or row.get('storage_path') or '',
                'content': content,
                'created_at': row.get('created_at', ''),
            })

    if not transcript_files:
        return ""

    parser = ContinuityParser(callsign)
    entries = parser.process_all_transcripts(transcript_files)
    now = datetime.now(timezone.utc).isoformat()

    if entries:
        ledger_entries = parser.generate_ledger_json(entries, include_exchanges=False)
        payload = {
            'construct_id': callsign,
            'capsule_version': '2.0.0-preview',
            'generator': 'vault_transcript_preview_candidates',
            'preview_only': True,
            'last_synced_at': now,
            'summary': {
                'total_sessions': len(ledger_entries),
                'total_source_transcripts': len(transcript_files),
                'sampled_source_transcripts': len(transcript_files),
                'total_exchanges': sum(entry.get('exchange_count', 0) for entry in ledger_entries),
                'date_range': {
                    'earliest': min((entry.get('estimated_date', '') for entry in ledger_entries), default=''),
                    'latest': max((entry.get('estimated_date', '') for entry in ledger_entries), default=''),
                },
                'sources': sorted({entry.get('source', 'Conversation') for entry in ledger_entries}),
            },
            'sessions': ledger_entries,
        }
        logger.info(
            "VAULT_PREVIEW_TIMING: candidate transcript preview built structured preview for %s from %s transcripts in %sms",
            callsign,
            len(transcript_files),
            _preview_elapsed_ms(started_at),
        )
        return json.dumps(payload, indent=2, default=str)

    transcript_previews = []
    for transcript in transcript_files[:50]:
        filename = transcript.get('filename', '')
        transcript_previews.append({
            'filename': filename,
            'created_at': transcript.get('created_at', ''),
            'content_length': len(transcript.get('content') or ''),
            'excerpt': (transcript.get('content') or '').strip()[:1000],
            'source': parser.detect_source(filename),
        })

    payload = {
        'construct_id': callsign,
        'capsule_version': '2.0.0-preview',
        'generator': 'vault_transcript_preview_candidates',
        'preview_only': True,
        'preview_degraded': True,
        'last_synced_at': now,
        'summary': {
            'total_source_transcripts': len(transcript_files),
            'previewed_transcripts': len(transcript_previews),
            'sampled_source_transcripts': len(transcript_files),
            'reason': 'continuity_parser_returned_no_entries',
        },
        'transcript_previews': transcript_previews,
    }
    logger.info(
        "VAULT_PREVIEW_TIMING: candidate transcript preview built degraded preview for %s from %s transcripts in %sms",
        callsign,
        len(transcript_files),
        _preview_elapsed_ms(started_at),
    )
    return json.dumps(payload, indent=2, default=str)


def _reconstruct_capsule_preview_text(row: Optional[Dict[str, Any]], deadline: Optional[float] = None) -> str:
    started_at = time.perf_counter()
    if not row:
        return ""

    construct_id = str(row.get('construct_id') or '').strip()
    user_id = row.get('user_id')
    if not construct_id:
        return ""

    if _preview_deadline_expired(deadline):
        logger.info(
            "VAULT_PREVIEW_TIMING: capsule preview reconstruction skipped for %s because budget was already exhausted",
            construct_id,
        )
        return ""

    try:
        preview = _build_capsule_preview_from_transcripts(construct_id, deadline=deadline)
        if preview:
            logger.info(
                "VAULT_PREVIEW_TIMING: capsule preview reconstructed via transcript path for %s in %sms",
                construct_id,
                _preview_elapsed_ms(started_at),
            )
            return preview
    except Exception as exc:
        logger.warning("capsule transcript preview reconstruction failed for %s: %s", construct_id, exc)

    if _preview_deadline_expired(deadline):
        logger.info(
            "VAULT_PREVIEW_TIMING: capsule preview reconstruction skipped memup fallback for %s after %sms",
            construct_id,
            _preview_elapsed_ms(started_at),
        )
        return ""

    return ""


def _derive_vault_preview_payload(row: Optional[Dict[str, Any]], preview_budget_ms: Optional[int] = VAULT_PREVIEW_ROUTE_BUDGET_MS) -> Dict[str, Any]:
    started_at = time.perf_counter()
    deadline = _preview_deadline(preview_budget_ms)
    file_row = dict(row or {})
    filename = file_row.get('filename') or file_row.get('storage_path') or ''
    ext = os.path.splitext(filename)[1].lower()
    file_type = str(file_row.get('file_type') or '').lower()
    content = file_row.get('content')

    preview_kind = 'binary'
    preview_status = 'true_binary'
    preview_source = 'none'
    preview_timed_out = False
    storage_elapsed_ms = 0
    reconstruct_elapsed_ms = 0
    recovered_text = content if isinstance(content, str) and content else ''
    is_text_preview = _is_text_preview_type(ext, file_type)
    is_structured_preview = _is_structured_preview_type(ext, file_type)

    if recovered_text:
        preview_source = 'inline'
    elif is_text_preview:
        if ext != '.capsule':
            storage_started_at = time.perf_counter()
            recovered_text = _load_vault_file_text(file_row)
            storage_elapsed_ms = _preview_elapsed_ms(storage_started_at)
            if recovered_text:
                file_row['content'] = recovered_text
                preview_source = 'storage'
            else:
                preview_timed_out = _preview_deadline_expired(deadline)
        if not recovered_text and ext == '.capsule' and not _preview_deadline_expired(deadline):
            reconstruct_started_at = time.perf_counter()
            recovered_text = _reconstruct_capsule_preview_text(file_row, deadline=deadline)
            reconstruct_elapsed_ms = _preview_elapsed_ms(reconstruct_started_at)
            if recovered_text:
                file_row['content'] = recovered_text
                preview_source = 'memup'
            else:
                preview_timed_out = _preview_deadline_expired(deadline)
        elif not recovered_text and ext == '.capsule':
            preview_timed_out = True

    if isinstance(recovered_text, str) and recovered_text:
        if is_structured_preview:
            parsed = _safe_json_loads(recovered_text)
            if parsed is not None:
                preview_kind = 'json'
                preview_status = 'inline' if preview_source == 'inline' else 'recovered'
            elif _looks_readable_text(recovered_text):
                preview_kind = 'text'
                preview_status = 'malformed_text'
            else:
                preview_kind = 'binary'
                preview_status = 'true_binary'
        elif is_text_preview:
            if _looks_readable_text(recovered_text):
                preview_kind = 'text'
                preview_status = 'inline' if preview_source == 'inline' else 'recovered'
            else:
                preview_kind = 'binary'
                preview_status = 'true_binary'
        elif _looks_readable_text(recovered_text):
            preview_kind = 'text'
            preview_status = 'inline' if preview_source == 'inline' else 'recovered'
    elif is_text_preview:
        if ext == '.capsule':
            file_row['content'] = _build_unavailable_capsule_preview(file_row, filename, file_type)
            preview_kind = 'json'
            preview_status = 'unavailable'
            preview_source = 'diagnostic'
        else:
            preview_kind = 'binary'
            preview_status = 'unavailable'

    preview_elapsed_ms = _preview_elapsed_ms(started_at)
    preview_timed_out = preview_timed_out or (
        preview_budget_ms is not None and preview_budget_ms > 0 and preview_elapsed_ms >= preview_budget_ms
    )
    file_row['preview_kind'] = preview_kind
    file_row['preview_status'] = preview_status
    file_row['preview_source'] = preview_source
    file_row['preview_timed_out'] = preview_timed_out
    file_row['preview_elapsed_ms'] = preview_elapsed_ms
    file_row['preview_budget_ms'] = preview_budget_ms
    file_row['preview_storage_elapsed_ms'] = storage_elapsed_ms
    file_row['preview_reconstruct_elapsed_ms'] = reconstruct_elapsed_ms
    if ext == '.capsule':
        content_value = file_row.get('content')
        content_length = len(content_value) if isinstance(content_value, str) else 0
        logger.info(
            "VAULT_PREVIEW: capsule path=%s kind=%s status=%s source=%s file_type=%s has_content=%s content_length=%s construct_id=%s elapsed_ms=%s budget_ms=%s timed_out=%s storage_ms=%s reconstruct_ms=%s",
            filename,
            preview_kind,
            preview_status,
            preview_source,
            file_type,
            bool(content_length),
            content_length,
            file_row.get('construct_id'),
            file_row['preview_elapsed_ms'],
            preview_budget_ms,
            preview_timed_out,
            storage_elapsed_ms,
            reconstruct_elapsed_ms,
        )
    return file_row


def _sort_vault_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows or [],
        key=lambda row: max(
            _parse_vault_timestamp(row.get('updated_at')),
            _parse_vault_timestamp(row.get('created_at')),
        ),
        reverse=True,
    )


def _pick_latest_vault_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ordered = _sort_vault_rows(rows)
    return ordered[0] if ordered else None


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _build_unavailable_capsule_preview(row: Dict[str, Any], filename: str, file_type: str) -> str:
    return json.dumps(
        {
            "preview_only": True,
            "preview_unavailable": True,
            "reason": "Capsule content could not be recovered from storage or transcript reconstruction.",
            "construct_id": row.get("construct_id"),
            "capsule_path": filename,
            "storage_path": row.get("storage_path"),
            "stored_file_type": file_type or row.get("file_type") or "binary",
            "metadata": _safe_json_loads(row.get("metadata")) or row.get("metadata"),
            "preview_contract_version": "capsule-diagnostic-v1",
        },
        indent=2,
        default=str,
    )


def _original_capsule_path(construct_id: str) -> str:
    return f'instances/{construct_id}/memup/{construct_id}.capsule'


def _materialized_capsule_path(construct_id: str) -> str:
    return f'instances/{construct_id}/memup/{construct_id}.materialized.capsule'


def _is_materialized_capsule_path(path: str) -> bool:
    return isinstance(path, str) and path.endswith('.materialized.capsule')


def _is_original_capsule_path(path: str) -> bool:
    return isinstance(path, str) and path.endswith('.capsule') and not _is_materialized_capsule_path(path)


def _lookup_materialized_capsule_backing_row(
    requested_row: Dict[str, Any],
    *,
    user_id: Optional[str],
    is_admin: bool,
) -> Optional[Dict[str, Any]]:
    filename = requested_row.get('filename') or requested_row.get('storage_path') or ''
    construct_id = str(requested_row.get('construct_id') or '').strip()
    if not construct_id or not _is_original_capsule_path(filename):
        return None

    materialized_path = _materialized_capsule_path(construct_id)
    if materialized_path == filename:
        return None

    return _lookup_exact_vault_preview_row(
        filename=materialized_path,
        storage_path=materialized_path,
        construct_id=construct_id,
        user_id=user_id,
        is_admin=is_admin,
    )


def _build_preview_payload_from_materialized_sibling(
    requested_row: Dict[str, Any],
    backing_row: Dict[str, Any],
    *,
    preview_budget_ms: int,
) -> Dict[str, Any]:
    preview_row = dict(requested_row or {})
    preview_row['content'] = backing_row.get('content')
    preview_row['file_type'] = backing_row.get('file_type') or preview_row.get('file_type')
    if backing_row.get('metadata') is not None:
        preview_row['metadata'] = backing_row.get('metadata')

    file_payload = _derive_vault_preview_payload(preview_row, preview_budget_ms=preview_budget_ms)
    file_payload['id'] = requested_row.get('id')
    file_payload['filename'] = requested_row.get('filename') or requested_row.get('storage_path') or backing_row.get('filename')
    file_payload['storage_path'] = requested_row.get('storage_path') or requested_row.get('filename') or backing_row.get('storage_path')
    file_payload['construct_id'] = requested_row.get('construct_id') or backing_row.get('construct_id')
    file_payload['user_id'] = requested_row.get('user_id') if requested_row.get('user_id') is not None else backing_row.get('user_id')
    file_payload['is_system'] = requested_row.get('is_system', backing_row.get('is_system', False))
    file_payload['preview_source'] = 'materialized_sibling'
    file_payload['preview_backing_file_id'] = backing_row.get('id')
    file_payload['preview_backing_path'] = backing_row.get('filename') or backing_row.get('storage_path')
    return file_payload


def _lookup_exact_vault_preview_row(
    *,
    filename: str,
    storage_path: str,
    construct_id: str,
    user_id: Optional[str],
    is_admin: bool,
) -> Optional[Dict[str, Any]]:
    return VAULT_FILE_REPOSITORY.find_exact(
        filename=filename,
        storage_path=storage_path,
        construct_id=construct_id,
        user_id=user_id,
        is_admin=is_admin,
    )


def _candidate_transcript_ids_for_construct(construct_id: str) -> List[str]:
    callsign = _normalize_callsign(construct_id)
    bare_name = _bare_name_from_callsign(callsign)
    rows = _query_transcript_rows_for_preview(callsign, bare_name)
    return [str(row.get('id')).strip() for row in rows if row.get('id')][:VAULT_PREVIEW_MAX_TRANSCRIPTS]


def _persist_capsule_from_candidate_transcripts(
    construct_id: str,
    transcript_ids: List[str],
    user_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    ids = [str(value).strip() for value in (transcript_ids or []) if str(value).strip()]
    if not ids or not user_id:
        return None

    try:
        try:
            from memup_sync import persist_construct_memup_from_candidate_transcripts
        except ImportError:
            try:
                from vvault.server.memup_sync import persist_construct_memup_from_candidate_transcripts
            except ImportError:
                # Older Memup runtimes expose the complete construct sync but
                # not the candidate-id adapter. The canonical repository query
                # inside sync_construct_memup is already construct- and
                # user-scoped, so use it and preserve the materialize response
                # contract without copying transcript contents.
                try:
                    from memup_sync import sync_construct_memup
                except ImportError:
                    from vvault.server.memup_sync import sync_construct_memup
                synced = sync_construct_memup(VAULT_FILE_REPOSITORY, construct_id, user_id)
                if not synced.get('success'):
                    return None
                return {
                    'capsule_data': {
                        'capsule_version': '1.0',
                        'summary': {
                            'total_sessions': synced.get('total_sessions'),
                            'total_exchanges': synced.get('total_exchanges'),
                            'date_range': synced.get('date_range'),
                            'topics': synced.get('topics', []),
                        },
                    },
                    'write_result': synced.get('capsule_file') or {},
                    'original_capsule': {},
                    'sync_result': synced,
                }

        return persist_construct_memup_from_candidate_transcripts(
            VAULT_FILE_REPOSITORY,
            construct_id,
            ids,
            user_id,
        )
    except Exception as exc:
        logger.warning(
            "MEMUP_MATERIALIZE: canonical capsule writeback failed for %s via candidate transcripts: %s",
            construct_id,
            exc,
        )
        return None


def _first_non_empty_string(values: List[Any], default: str = "") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _first_non_empty_list(values: List[Any]) -> List[str]:
    for value in values:
        if isinstance(value, list) and value:
            return [str(item).strip() for item in value if str(item).strip()]
    return []


def _default_construct_model_config() -> Dict[str, str]:
    return {
        "primary": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "conversation": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "creative": "openrouter:mistralai/mistral-7b-instruct",
        "coding": "openrouter:deepseek/deepseek-coder-33b-instruct",
    }


def _default_construct_capabilities() -> Dict[str, Any]:
    return {
        "agent": True,
        "webSearch": False,
        "canvas": False,
        "imageGeneration": False,
        "codeInterpreter": True,
    }


def _default_construct_memory_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
    }


def _normalize_construct_models(value: Any) -> Any:
    if isinstance(value, dict) and value:
        return value
    if isinstance(value, list) and value:
        return value
    return _default_construct_model_config()


def _normalize_construct_capabilities(value: Any) -> Dict[str, Any]:
    normalized = dict(_default_construct_capabilities())
    if isinstance(value, dict):
        for key, entry in value.items():
            key_str = str(key).strip()
            if key_str:
                normalized[key_str] = entry
        return normalized
    if isinstance(value, list):
        for item in value:
            key_str = str(item).strip()
            if key_str:
                normalized[key_str] = True
        return normalized
    return normalized


def _normalize_construct_memory_settings(value: Any) -> Dict[str, Any]:
    normalized = dict(_default_construct_memory_settings())
    if isinstance(value, dict):
        normalized.update(value)
        return normalized
    if isinstance(value, bool):
        normalized["enabled"] = value
    return normalized


def _normalize_construct_refs(value: Any) -> List[Any]:
    if not isinstance(value, list):
        return []
    normalized: List[Any] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
        elif isinstance(item, dict):
            normalized.append(item)
        elif item is not None:
            normalized.append(str(item))
    return normalized


def _normalize_construct_voice_payload(value: Any) -> Any:
    if value is None:
        return {"text": ""}
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, (dict, list)):
        return value
    return {"text": str(value)}


def _canonical_prompt_capabilities(value: Dict[str, Any]) -> Dict[str, bool]:
    aliases = {
        "web_search": ("web_search", "webSearch"),
        "canvas": ("canvas",),
        "image_generation": ("image_generation", "imageGeneration"),
        "code_interpreter": ("code_interpreter", "codeInterpreter"),
        "agent": ("agent",),
        "proactive_initiation": ("proactive_initiation", "proactiveInitiation"),
    }
    return {
        canonical: bool(next((value[key] for key in keys if key in value), False))
        for canonical, keys in aliases.items()
    }


def _template_model(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        return {
            "provider": str(value.get("provider") or ""),
            "model": str(value.get("model") or value.get("id") or ""),
        }
    text = str(value or "")
    provider, separator, model = text.partition(":")
    return {
        "provider": provider if separator else "",
        "model": model if separator else text,
    }


def _build_construct_prompt_manifest(
    callsign: str,
    display_name: str,
    full_name: str,
    description: str,
    instructions: str,
    conversation_starters: List[str],
    capabilities: Dict[str, Any],
    memory_settings: Dict[str, Any],
    canon_refs: List[Any],
    knowledge_refs: List[Any],
    *,
    role: str = "assistant",
    system_prompt: str = "",
    source: str,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    summary_capabilities: Optional[List[str]] = None,
    models: Optional[Dict[str, Any]] = None,
    orchestration_mode: str = "standard",
    memory_profile: str = "continuitygpt",
    roleplay_enabled: bool = True,
    provider: str = "",
    tags: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    config_json: Any = None,
) -> Dict[str, Any]:
    updated = updated_at or datetime.now(timezone.utc).isoformat()
    created = created_at or updated
    normalized_capabilities = {
        "webSearch": bool(capabilities.get("webSearch", capabilities.get("web_search", False))),
        "canvas": bool(capabilities.get("canvas", False)),
        "imageGeneration": bool(capabilities.get("imageGeneration", capabilities.get("image_generation", False))),
        "codeInterpreter": bool(capabilities.get("codeInterpreter", capabilities.get("code_interpreter", False))),
        "agent": bool(capabilities.get("agent", False)),
        "proactiveInitiation": bool(capabilities.get("proactiveInitiation", capabilities.get("proactive_initiation", False))),
    }
    conditioning_text = instructions or system_prompt or ""
    canonical_config_json = {
        "bodyVersion": 1,
        "displayName": display_name,
        "fullName": full_name,
        "aliases": aliases or [],
        "conditioning": (
            ">>CALLSIGN_CONDITIONING_START\n\n"
            f"{conditioning_text}\n\n"
            ">>CALLSIGN_CONDITIONING_END\n"
        ),
        "canonRefs": canon_refs,
        "knowledgeRefs": knowledge_refs,
        "provider": provider or "",
        "tags": tags or [],
        "categories": categories or [],
        "summaryCapabilities": summary_capabilities or [],
        "capabilities": normalized_capabilities,
        "hasPersistentMemory": bool(memory_settings.get("enabled", True)),
    }
    if isinstance(config_json, dict):
        canonical_config_json.update(config_json)
    return {
        "constructCallsign": callsign,
        "name": display_name,
        "displayName": display_name,
        "fullName": full_name,
        "aliases": aliases or [],
        "description": description or "",
        "instructions": instructions or system_prompt or "",
        "conversationStarters": conversation_starters,
        "capabilities": normalized_capabilities,
        "canonRefs": canon_refs,
        "knowledgeRefs": knowledge_refs,
        "summaryCapabilities": summary_capabilities or [],
        "modelId": str((models or {}).get("primary") or (models or {}).get("conversation") or ""),
        "conversationModel": str((models or {}).get("conversation") or ""),
        "creativeModel": str((models or {}).get("creative") or ""),
        "codingModel": str((models or {}).get("coding") or ""),
        "memoryEnabled": bool(memory_settings.get("enabled", True)),
        "memoryProfile": memory_profile or ("continuitygpt" if memory_settings.get("enabled", True) else "off"),
        "roleplayEnabled": bool(roleplay_enabled),
        "orchestrationMode": orchestration_mode,
        "provider": provider or "",
        "tags": tags or [],
        "categories": categories or [],
        "configJson": canonical_config_json,
        "createdAt": created,
        "source": source,
    }


def _normalize_construct_privacy(value: Any) -> str:
    """Validate the canonical construct visibility enum used by create/update."""
    if value is None:
        return "private"
    if not isinstance(value, str):
        raise ValueError("privacy must be one of: private, link, store")
    privacy = value.strip().lower()
    if privacy not in {"private", "link", "store"}:
        raise ValueError("privacy must be one of: private, link, store")
    return privacy


class LifecycleMutationForbidden(ValueError):
    """Ordinary construct editors cannot mutate Forge-owned lifecycle state."""


def _build_construct_metadata_payload(
    callsign: str,
    display_name: str,
    full_name: str,
    description: str,
    models: Any,
    orchestration_mode: str,
    capabilities: Dict[str, Any],
    memory_settings: Dict[str, Any],
    canon_refs: List[Any],
    knowledge_refs: List[Any],
    *,
    role: str = "assistant",
    status: str = "active",
    source: str,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    avatar_enabled: bool = False,
    privacy: str = "private",
    lifecycle_stage: str = "gpt",
) -> Dict[str, Any]:
    updated = updated_at or datetime.now(timezone.utc).isoformat()
    model_values = models if isinstance(models, dict) else {}
    normalized_capabilities = _canonical_prompt_capabilities(capabilities)
    return {
        "construct_id": callsign,
        "display_name": display_name,
        "status": status,
        "privacy": privacy,
        "lifecycle_stage": lifecycle_stage,
        "schema_version": "1.0.0",
        "orchestration": {
            "mode": orchestration_mode,
            "construct_runtime": "chatty",
        },
        "models": {
            "conversation": _template_model(model_values.get("conversation") or model_values.get("primary")),
            "creative": _template_model(model_values.get("creative")),
            "coding": _template_model(model_values.get("coding")),
        },
        "capabilities": normalized_capabilities,
        "actions": {
            "enabled": bool(actions),
            "items": actions or [],
        },
        "runtime": {
            "default_temperature": None,
            "max_context_messages": None,
            "retrieval_enabled": bool(memory_settings.get("enabled", True)),
            "preview_enabled": False,
        },
        "ui": {
            "avatar_enabled": bool(avatar_enabled),
            "show_in_sidebar": True,
            "accent_color": "",
        },
    }


def _require_canonical_json_schema(
    document: Dict[str, Any],
    schema_id: str,
) -> None:
    schema = canonical_data_contract.load_schemas().get(schema_id)
    if not schema:
        raise RuntimeError(f"Canonical schema is missing: {schema_id}")
    errors = canonical_data_contract.validate_json_document(document, schema)
    if errors:
        raise ValueError(
            f"{schema_id} validation failed: {'; '.join(errors)}"
        )


def _build_starter_capsule(
    callsign: str,
    definition: str,
    generated_at: str,
    *,
    capsule_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    authored_uuid = capsule_uuid or str(uuid4())
    fingerprint = hashlib.sha256(
        f"{callsign}:{authored_uuid}:{generated_at}".encode("utf-8")
    ).hexdigest()
    return {
        "metadata": {
            "construct_id": callsign,
            "capsule_uuid": authored_uuid,
            "lineage_uuid": authored_uuid,
            "capsule_version": "2.1.0",
            "profile_kind": "custom",
            "generated_at": generated_at,
            "generator": "vvault_construct_create",
            "fingerprint_hash": fingerprint,
            "tether_signature": None,
        },
        "quality_contract": {
            "accurate": True,
            "relevant": True,
            "non_redundant": True,
            "portable": True,
            "source_backed": True,
            "storage_topology_free": True,
        },
        "identity": {
            "construct_id": callsign,
            "role": "assistant",
            "core_definition": definition,
            "do_not_flatten_into": [],
        },
        "memory": {
            "core_memories": [],
            "continuity_hooks": [],
            "memory_index_refs": [],
        },
        # The authoring template contains a null placeholder row. A new
        # construct has no source to attest yet, so its instantiated canonical
        # document uses the same field with an honest empty collection.
        "source_manifest": {"sources": []},
        "retrieval_policy": {
            "primary": "memory_index_refs",
            "fallback": ["source_manifest"],
            "requires_source_hash": True,
        },
        "signatures": {
            "linguistic_sigil": {
                "signature_phrase": None,
                "common_phrases": [],
            },
            "visual_sigil": {
                "artifact_id": None,
                "glyph_hash": None,
                "number_band_hash": None,
                "render_profile": None,
                "generated_at": None,
            },
        },
        "body": None,
    }


def _physical_features_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        authored = {
            key: entry
            for key, entry in value.items()
            if key not in {"schema_id", "schema_version", "instance_id", "updated_at"}
            and entry not in (None, "")
        }
        if set(authored) == {"overall"}:
            return str(authored["overall"]).strip()
        return "\n".join(f"{key}: {entry}" for key, entry in authored.items())
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return ""


IMAGE_PREVIEW_MIME_BY_EXT = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.avif': 'image/avif',
    '.bmp': 'image/bmp',
}
IMAGE_PREVIEW_MAX_BYTES = int(os.environ.get('VVAULT_IMAGE_PREVIEW_MAX_BYTES', str(10 * 1024 * 1024)))
MEDIA_PREVIEW_MIME_BY_EXT = {
    **IMAGE_PREVIEW_MIME_BY_EXT,
    '.pdf': 'application/pdf',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.opus': 'audio/opus',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.flac': 'audio/flac',
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.webm': 'video/webm',
    '.ogv': 'video/ogg',
    '.mov': 'video/quicktime',
}
MEDIA_PREVIEW_MAX_BYTES = int(os.environ.get('VVAULT_MEDIA_PREVIEW_MAX_BYTES', str(100 * 1024 * 1024)))
ARCHIVE_PREVIEW_MAX_ENTRIES = max(1, int(os.environ.get('VVAULT_ARCHIVE_PREVIEW_MAX_ENTRIES', '250')))


def _media_preview_bytes(file_row: Optional[Dict[str, Any]]) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Resolve browser-previewable bytes without exposing arbitrary file types."""
    if not file_row:
        return None, None, 'missing_content'

    filename = file_row.get('filename') or file_row.get('storage_path') or ''
    ext = os.path.splitext(str(filename).split('?', 1)[0])[1].lower()
    metadata = _metadata_to_dict(file_row.get('metadata'))
    declared_candidates = (
        file_row.get('content_type'),
        file_row.get('file_type'),
        metadata.get('mimeType'),
        metadata.get('contentType'),
    )
    generic_declared_types = {'', 'binary', 'application/octet-stream'}
    declared_mime = next(
        (
            str(candidate).split(';', 1)[0].strip().lower()
            for candidate in declared_candidates
            if candidate and str(candidate).split(';', 1)[0].strip().lower() not in generic_declared_types
        ),
        '',
    )
    mime = MEDIA_PREVIEW_MIME_BY_EXT.get(ext)
    if not mime and (
        declared_mime.startswith('image/')
        or declared_mime.startswith('audio/')
        or declared_mime.startswith('video/')
        or declared_mime == 'application/pdf'
    ):
        mime = declared_mime
    if not mime:
        return None, None, 'unsupported_media_type'

    content = file_row.get('content')
    inline_content_invalid = False
    if isinstance(content, bytes):
        body = content
    elif isinstance(content, str) and content.strip():
        text = content.strip()
        if re.search(r'\[(?:REDACTED|TRUNCATED|BINARY)[^\]]*\]', text, re.I):
            inline_content_invalid = True
            body = b''
        else:
            data_url_match = re.match(r'^data:([^;,]+);base64,(.+)$', text, re.I | re.S)
            try:
                if data_url_match:
                    data_mime = data_url_match.group(1).lower()
                    if data_mime.startswith(('image/', 'audio/', 'video/')) or data_mime == 'application/pdf':
                        mime = data_mime
                    body = base64.b64decode(re.sub(r'\s+', '', data_url_match.group(2)), validate=True)
                elif mime == 'image/svg+xml' and text.lstrip().startswith('<svg'):
                    body = text.encode('utf-8')
                else:
                    body = base64.b64decode(re.sub(r'\s+', '', text), validate=True)
            except (ValueError, TypeError):
                inline_content_invalid = True
                body = b''
    else:
        body = b''

    if not body:
        stored = VAULT_FILE_REPOSITORY.load_bytes(file_row)
        if not stored:
            return None, mime, 'corrupt_inline_content' if inline_content_invalid else 'storage_unavailable'
        body, stored_content_type = stored
        stored_mime = str(stored_content_type or '').split(';', 1)[0].strip().lower()
        if stored_mime.startswith(('image/', 'audio/', 'video/')) or stored_mime == 'application/pdf':
            mime = stored_mime
    if not body:
        return None, mime, 'empty_content'
    if len(body) > MEDIA_PREVIEW_MAX_BYTES:
        return None, mime, 'media_too_large'
    return body, mime, None


def _avatar_cache_size_locked() -> int:
    return sum(
        len(entry[1].get("body") or b"")
        for entry in _avatar_cache.values()
    )


def _store_avatar_cache_locked(
    key: tuple[str, str], result: dict[str, Any]
) -> None:
    _avatar_cache[key] = (time.monotonic(), result)
    while (
        len(_avatar_cache) > AVATAR_CACHE_MAX_ENTRIES
        or _avatar_cache_size_locked() > AVATAR_CACHE_MAX_BYTES
    ):
        oldest_key = min(_avatar_cache, key=lambda item: _avatar_cache[item][0])
        if oldest_key == key and len(_avatar_cache) == 1:
            _avatar_cache.pop(oldest_key, None)
            break
        _avatar_cache.pop(oldest_key, None)


def _avatar_result_with_cache_state(
    result: dict[str, Any], *, cache_state: str, refreshing: bool
) -> dict[str, Any]:
    return {
        **result,
        "cacheState": cache_state,
        "refreshing": refreshing,
    }


def _canonical_owner_avatar_descriptor(
    owner_user_id: str, construct_id: str
) -> dict[str, Any]:
    """Resolve canonical avatar metadata without hydrating image bytes."""
    owner_id = str(owner_user_id)
    callsign = _normalize_callsign(construct_id)
    key = (owner_id, callsign)
    now = time.monotonic()
    with _avatar_cache_lock:
        cached = _avatar_descriptor_cache.get(key)
        if cached and now - cached[0] <= AVATAR_CACHE_TTL_SECONDS:
            return _avatar_result_with_cache_state(
                cached[1], cache_state="fresh", refreshing=False
            )
    try:
        row = VAULT_FILE_REPOSITORY.get_canonical_owner_avatar_descriptor(
            user_id=owner_id, callsign=callsign
        )
        if not row:
            result = {
                "state": "missing", "errorCode": None, "body": None,
                "rowId": None, "sha256": None, "contentType": None,
                "sizeBytes": 0, "filename": None,
            }
        else:
            expected_sha = str(row.get("sha256") or "").strip().lower()
            body_available = bool(row.get("body_available"))
            content_type = str(row.get("content_type") or "image/png")
            error_code = None
            if not body_available:
                error_code = "AVATAR_BODY_UNAVAILABLE"
            elif not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                error_code = "AVATAR_SHA256_UNAVAILABLE"
            result = {
                "state": "hydration_error" if error_code else "available",
                "errorCode": error_code,
                "body": None,
                "rowId": str(row.get("id") or ""),
                "sha256": expected_sha or None,
                "contentType": content_type,
                "sizeBytes": int(row.get("size_bytes") or 0),
                "filename": row.get("filename"),
            }
    except Exception as exc:
        with _avatar_cache_lock:
            stale = _avatar_descriptor_cache.get(key)
        if stale and time.monotonic() - stale[0] <= AVATAR_CACHE_LKG_SECONDS:
            return _avatar_result_with_cache_state(
                stale[1], cache_state="stale", refreshing=True
            )
        result = {
            "state": "hydration_error",
            "errorCode": f"AVATAR_SOURCE_{type(exc).__name__.upper()}",
            "body": None, "rowId": None, "sha256": None,
            "contentType": None, "sizeBytes": 0, "filename": None,
        }
    with _avatar_cache_lock:
        _avatar_descriptor_cache[key] = (time.monotonic(), result)
    return _avatar_result_with_cache_state(
        result, cache_state="fresh", refreshing=False
    )


def _canonical_owner_avatar(
    owner_user_id: str, construct_id: str
) -> dict[str, Any]:
    """Single-flight, owner-scoped avatar hydration with bounded stale LKG."""
    owner_id = str(owner_user_id)
    callsign = _normalize_callsign(construct_id)
    key = (owner_id, callsign)
    now = time.monotonic()
    with _avatar_cache_lock:
        cached = _avatar_cache.get(key)
        if cached and now - cached[0] <= AVATAR_CACHE_TTL_SECONDS:
            return _avatar_result_with_cache_state(
                cached[1], cache_state="fresh", refreshing=False
            )
        inflight = _avatar_cache_inflight.get(key)
        if inflight is None:
            inflight = threading.Event()
            _avatar_cache_inflight[key] = inflight
            leader = True
        else:
            leader = False

    if not leader:
        # One request owns the database/object hydration. Other thumbnails wait
        # without borrowing another body-pool connection.
        inflight.wait(timeout=15.0)
        with _avatar_cache_lock:
            resolved = _avatar_cache.get(key)
        if resolved:
            age = time.monotonic() - resolved[0]
            return _avatar_result_with_cache_state(
                resolved[1],
                cache_state=(
                    "fresh" if age <= AVATAR_CACHE_TTL_SECONDS else "stale"
                ),
                refreshing=age > AVATAR_CACHE_TTL_SECONDS,
            )
        return {
            "state": "hydration_error",
            "errorCode": "AVATAR_REFRESH_TIMEOUT",
            "body": None,
            "cacheState": "unavailable",
            "refreshing": True,
        }

    result: dict[str, Any]
    try:
        if not _avatar_hydration_slots.acquire(timeout=30.0):
            raise TimeoutError("avatar hydration concurrency gate timed out")
        try:
            row = VAULT_FILE_REPOSITORY.get_canonical_owner_avatar(
                user_id=owner_id, callsign=callsign
            )
        finally:
            _avatar_hydration_slots.release()
        if not row:
            result = {
                "state": "missing",
                "errorCode": None,
                "body": None,
                "rowId": None,
                "sha256": None,
                "contentType": None,
                "sizeBytes": 0,
                "filename": None,
            }
        else:
            body, content_type, unavailable_reason = _media_preview_bytes(row)
            expected_sha = str(row.get("sha256") or "").strip().lower()
            actual_sha = hashlib.sha256(body).hexdigest() if body else None
            if unavailable_reason or body is None:
                result = {
                    "state": "hydration_error",
                    "errorCode": (
                        f"AVATAR_{str(unavailable_reason).upper()}"
                        if unavailable_reason else "AVATAR_BODY_UNAVAILABLE"
                    ),
                    "body": None,
                    "rowId": str(row.get("id") or ""),
                    "sha256": expected_sha or None,
                    "contentType": content_type or "image/png",
                    "sizeBytes": int(row.get("size_bytes") or 0),
                    "filename": row.get("filename"),
                }
            elif not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                result = {
                    "state": "hydration_error",
                    "errorCode": "AVATAR_SHA256_UNAVAILABLE",
                    "body": None,
                    "rowId": str(row.get("id") or ""),
                    "sha256": expected_sha or None,
                    "contentType": content_type or "image/png",
                    "sizeBytes": len(body),
                    "filename": row.get("filename"),
                }
            elif not hmac.compare_digest(actual_sha or "", expected_sha):
                result = {
                    "state": "hydration_error",
                    "errorCode": "AVATAR_SHA256_MISMATCH",
                    "body": None,
                    "rowId": str(row.get("id") or ""),
                    "sha256": expected_sha,
                    "contentType": content_type or "image/png",
                    "sizeBytes": len(body),
                    "filename": row.get("filename"),
                }
            elif not body.startswith(b"\x89PNG\r\n\x1a\n"):
                result = {
                    "state": "hydration_error",
                    "errorCode": "AVATAR_PNG_MAGIC_INVALID",
                    "body": None,
                    "rowId": str(row.get("id") or ""),
                    "sha256": expected_sha,
                    "contentType": content_type or "image/png",
                    "sizeBytes": len(body),
                    "filename": row.get("filename"),
                }
            else:
                result = {
                    "state": "available",
                    "errorCode": None,
                    "body": body,
                    "rowId": str(row.get("id") or ""),
                    "sha256": expected_sha,
                    "contentType": "image/png",
                    "sizeBytes": len(body),
                    "filename": row.get("filename"),
                }
    except Exception as exc:
        with _avatar_cache_lock:
            stale = _avatar_cache.get(key)
        if (
            stale
            and time.monotonic() - stale[0] <= AVATAR_CACHE_LKG_SECONDS
        ):
            result = stale[1]
            return_result = _avatar_result_with_cache_state(
                result, cache_state="stale", refreshing=True
            )
            with _avatar_cache_lock:
                event = _avatar_cache_inflight.pop(key, None)
                if event:
                    event.set()
            return return_result
        result = {
            "state": "hydration_error",
            "errorCode": f"AVATAR_SOURCE_{type(exc).__name__.upper()}",
            "body": None,
            "rowId": None,
            "sha256": None,
            "contentType": None,
            "sizeBytes": 0,
            "filename": None,
        }

    with _avatar_cache_lock:
        _store_avatar_cache_locked(key, result)
        event = _avatar_cache_inflight.pop(key, None)
        if event:
            event.set()
    return _avatar_result_with_cache_state(
        result, cache_state="fresh", refreshing=False
    )


def _archive_preview(file_row: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return a bounded ZIP directory listing without extracting archive contents."""
    if not file_row:
        return None, 'missing_content'
    filename = str(file_row.get('filename') or file_row.get('storage_path') or '')
    if os.path.splitext(filename.split('?', 1)[0])[1].lower() != '.zip':
        return None, 'unsupported_archive_type'

    content = file_row.get('content')
    body = b''
    if isinstance(content, bytes):
        body = content
    elif isinstance(content, str) and content.strip():
        text = content.strip()
        data_url_match = re.match(r'^data:[^;,]+;base64,(.+)$', text, re.I | re.S)
        encoded = data_url_match.group(1) if data_url_match else text
        try:
            body = base64.b64decode(re.sub(r'\s+', '', encoded), validate=True)
        except (ValueError, TypeError):
            body = b''
    if not body:
        stored = VAULT_FILE_REPOSITORY.load_bytes(file_row)
        if not stored:
            return None, 'storage_unavailable'
        body = stored[0]
    if len(body) > MEDIA_PREVIEW_MAX_BYTES:
        return None, 'archive_too_large'

    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            entries = [
                {
                    'name': member.filename,
                    'size': member.file_size,
                    'compressed_size': member.compress_size,
                    'is_directory': member.is_dir(),
                }
                for member in members[:ARCHIVE_PREVIEW_MAX_ENTRIES]
            ]
            return {
                'entries': entries,
                'entry_count': len(members),
                'truncated': len(members) > ARCHIVE_PREVIEW_MAX_ENTRIES,
            }, None
    except (zipfile.BadZipFile, ValueError):
        return None, 'corrupt_archive'


def _image_preview_data_url(file_row: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if not file_row:
        return None, 'missing_content'

    filename = file_row.get('filename') or ''
    ext = os.path.splitext(filename)[1].lower()
    mime = IMAGE_PREVIEW_MIME_BY_EXT.get(ext)
    file_type = (file_row.get('file_type') or '').strip().lower()
    if not mime and file_type in IMAGE_PREVIEW_MIME_BY_EXT.values():
        mime = file_type
    if not mime:
        return None, 'unsupported_image_type'

    content = file_row.get('content')
    if content is None:
        content = ''
    if content:
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if content.startswith('data:image/'):
            return content, None
        if mime == 'image/svg+xml' and content.lstrip().startswith('<svg'):
            encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
            return f'data:{mime};base64,{encoded}', None
        compact = re.sub(r'\s+', '', content)
        if re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', compact or ''):
            return f'data:{mime};base64,{compact}', None

    storage_path = (file_row.get('storage_path') or '').strip()
    if not storage_path:
        return None, 'missing_content'

    stored = VAULT_FILE_REPOSITORY.load_bytes(file_row)
    if not stored:
        return None, 'storage_unavailable'
    image_bytes, _stored_content_type = stored
    if not image_bytes:
        return None, 'empty_content'
    if len(image_bytes) > IMAGE_PREVIEW_MAX_BYTES:
        return None, 'image_too_large'

    encoded = base64.b64encode(image_bytes).decode('ascii')
    return f'data:{mime};base64,{encoded}', None


def _binary_data_url_from_row(row: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    data_url, unavailable_reason = _image_preview_data_url(row)
    if unavailable_reason or not data_url:
        return None, None
    mime = data_url[5:].split(';', 1)[0] if data_url.startswith('data:') else None
    return data_url, mime


def _query_construct_identity_rows(callsign: str, user_id: Optional[str]) -> List[Dict[str, Any]]:
    bare_name = _bare_name_from_callsign(callsign)
    return _dedupe_vault_rows(
        VAULT_FILE_REPOSITORY.list_construct_identity_rows(
            callsign=callsign,
            bare_name=bare_name,
            user_id=user_id,
        )
    )


def _query_construct_file_rows(callsign: str, user_id: Optional[str], include_content: bool = False) -> List[Dict[str, Any]]:
    bare_name = _bare_name_from_callsign(callsign)
    return _dedupe_vault_rows(
        VAULT_FILE_REPOSITORY.list_construct_file_rows(
            callsign=callsign,
            bare_name=bare_name,
            user_id=user_id,
            include_content=include_content,
        )
    )


def _bounded_identity_label(value: Any) -> Optional[str]:
    """Return a user-facing identity label, rejecting prompt-shaped content."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 160
        or "\n" in candidate
        or "\r" in candidate
        or "\\n" in candidate
        or "\\r" in candidate
    ):
        return None
    return candidate


def _first_bounded_identity_label(values: List[Any], default: str) -> str:
    for value in values:
        candidate = _bounded_identity_label(value)
        if candidate:
            return candidate
    return default


def _legacy_prompt_instructions(prompt_text: str) -> str:
    """Project instructions from legacy prompt.txt without exposing its JSON wrapper."""
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        return ""
    document = _safe_json_loads(prompt_text)
    if isinstance(document, dict):
        return _first_non_empty_string([
            document.get("integration_prompt"),
            document.get("instructions"),
            document.get("prompt"),
            document.get("system_prompt"),
        ])
    return prompt_text.strip()


def _canonical_editor_file_path(row: Dict[str, Any], callsign: str) -> str:
    filename = str(row.get("filename") or "")
    marker = f"instances/{callsign}/"
    index = filename.find(marker)
    logical = filename[index:] if index >= 0 else filename
    return logical.split("#source:", 1)[0].strip("/")


_EDITOR_BASELINE_FIELDS = (
    "displayName", "fullName", "description", "instructions",
    "systemPromptOverride", "aliases", "summaryCapabilities",
    "conversationStarters", "conditioning", "definition",
    "physicalFeatures", "voice", "gender", "models", "capabilities",
    "memory", "canonRefs", "knowledgeRefs", "actions", "config", "privacy",
)


def _construct_editor_editable_baseline(payload: Dict[str, Any]) -> Dict[str, Any]:
    baseline = {
        field: copy.deepcopy(payload.get(field))
        for field in _EDITOR_BASELINE_FIELDS
    }
    avatar = payload.get("avatar") if isinstance(payload.get("avatar"), dict) else {}
    baseline["avatar"] = {
        "exists": bool(avatar.get("exists")),
        "sha256": avatar.get("sha256"),
        "contentType": avatar.get("contentType"),
    }
    knowledge_files = []
    for item in payload.get("knowledgeFiles") or []:
        if not isinstance(item, dict):
            continue
        knowledge_files.append({
            "id": item.get("id"),
            "path": item.get("storage_path") or item.get("path"),
            "sha256": item.get("sha256"),
            "size": item.get("content_length") or item.get("size") or 0,
            "contentType": item.get("content_type") or item.get("mimeType"),
        })
    baseline["knowledgeFiles"] = sorted(
        knowledge_files,
        key=lambda item: (str(item.get("path") or ""), str(item.get("id") or "")),
    )
    return baseline


def _attach_construct_editor_baseline(
    payload: Dict[str, Any], user_id: str, callsign: str
) -> Dict[str, Any]:
    baseline = _construct_editor_editable_baseline(payload)
    canonical = json.dumps(
        baseline, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    payload["editableBaseline"] = baseline
    payload["editableBaselineSha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    payload["hydrationRevision"] = payload["editableBaselineSha256"]
    payload["editableBaselineSchemaVersion"] = "1.0.0"
    try:
        versions = chatty_body_service.list_construct_editor_versions(
            user_id, callsign, limit=1
        )
    except Exception:
        versions = []
    payload["latestVersion"] = copy.deepcopy(versions[0]) if versions else None
    return payload


def _build_construct_editor_payload(callsign: str, user_id: Optional[str]) -> Dict[str, Any]:
    # The editor needs content only for the small identity bundle. File listing,
    # transcript hydration, and knowledge-file content have dedicated routes;
    # loading all of them here made edit latency scale with a construct's entire
    # history.
    identity_rows = _query_construct_identity_rows(callsign, user_id)
    files_rows = _dedupe_vault_rows(identity_rows)
    bare_name = _bare_name_from_callsign(callsign)
    file_summary = VAULT_FILE_REPOSITORY.construct_file_summary(
        callsign=callsign,
        bare_name=bare_name,
        user_id=user_id,
    )
    capsule_row = VAULT_FILE_REPOSITORY.latest_construct_capsule_row(
        callsign=callsign,
        bare_name=bare_name,
        user_id=user_id,
    )

    rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for row in files_rows:
        rows_by_name.setdefault(os.path.basename(row.get('filename') or ''), []).append(row)

    source_rows = {
        name: _pick_latest_vault_row(rows)
        for name, rows in rows_by_name.items()
    }

    prompt_json = _safe_json_loads(_load_vault_file_text(source_rows.get('prompt.json'))) or {}
    if not isinstance(prompt_json, dict):
        prompt_json = {}
    metadata_json = _safe_json_loads(_load_vault_file_text(source_rows.get('metadata.json'))) or {}
    if not isinstance(metadata_json, dict):
        metadata_json = {}
    definition_json = _safe_json_loads(_load_vault_file_text(source_rows.get('definition.json'))) or {}
    if not isinstance(definition_json, dict):
        definition_json = {}
    physical_features_json = _safe_json_loads(_load_vault_file_text(source_rows.get('physical_features.json')))
    voice_json = _safe_json_loads(_load_vault_file_text(source_rows.get('voice.json'))) or {}
    if not isinstance(voice_json, dict):
        voice_json = {}
    gender_json = _safe_json_loads(_load_vault_file_text(source_rows.get('gender.json'))) or {}
    if not isinstance(gender_json, dict):
        gender_json = {}

    definition_text = _load_vault_file_text(source_rows.get('definition.txt'))
    conditioning_text = _load_vault_file_text(source_rows.get('conditioning.txt'))
    prompt_text = _load_vault_file_text(source_rows.get('prompt.txt'))
    physical_features_text = _load_vault_file_text(source_rows.get('physical_features.txt'))
    voice_md_text = _load_vault_file_text(source_rows.get('voice.md'))

    avatar_row = next(
        (
            source_rows.get(filename)
            for filename in (
                'avatar.png',
                'avatar.webp',
                'avatar.jpg',
                'avatar.jpeg',
                'avatar.avif',
                'avatar.gif',
            )
            if source_rows.get(filename)
        ),
        None,
    )
    # The editor projection must remain metadata-only. Avatar hydration has a
    # dedicated owner-scoped, single-flight bytes boundary.
    avatar_descriptor_url = (
        f"/api/chatty/construct/{callsign}/avatar" if avatar_row else None
    )
    avatar_bytes_url = (
        f"/api/chatty/construct/{callsign}/avatar/bytes" if avatar_row else None
    )
    avatar_content_type = (
        str(avatar_row.get("content_type") or "image/png")
        if avatar_row else None
    )
    total_bytes = int(file_summary.get("total_bytes") or 0)
    sample_filenames: List[str] = []
    files: List[Dict[str, Any]] = []
    for row in files_rows[:20]:
        sample_filenames.append(row.get('filename'))
    editor_rows_by_path: Dict[str, Dict[str, Any]] = {}
    for row in files_rows:
        canonical_path = _canonical_editor_file_path(row, callsign)
        if not canonical_path:
            continue
        current = editor_rows_by_path.get(canonical_path)
        row_filename = str(row.get("filename") or "").strip("/")
        row_is_exact = row_filename == canonical_path
        current_is_exact = (
            str(current.get("filename") or "").strip("/") == canonical_path
            if current else False
        )
        if current is None or (row_is_exact and not current_is_exact):
            editor_rows_by_path[canonical_path] = row
        elif row_is_exact == current_is_exact:
            editor_rows_by_path[canonical_path] = _choose_preferred_vault_row(current, row)
    for canonical_path, row in editor_rows_by_path.items():
        metadata = _metadata_to_dict(row.get('metadata'))
        size = metadata.get('size') or metadata.get('bytes') or row.get('size_bytes') or 0
        content = row.get('content')
        if not size and isinstance(content, (str, bytes)):
            size = len(content)
        backing_path = str(row.get('storage_path') or row.get('object_key') or row.get('filename') or '')
        filename = os.path.basename(canonical_path)
        lowered_path = canonical_path.lower()
        files.append({
            "id": str(row.get('id') or ''),
            "filename": filename,
            "originalName": filename,
            "path": canonical_path,
            "storagePath": canonical_path,
            "backingStoragePath": backing_path,
            "mimeType": row.get('content_type') or row.get('file_type') or 'application/octet-stream',
            "size": size if isinstance(size, int) else 0,
            "uploadedAt": (
                row.get('created_at').isoformat()
                if hasattr(row.get('created_at'), 'isoformat')
                else row.get('created_at')
            ),
            "isActive": True,
            "sha256": row.get('sha256'),
            "category": (
                "transcript" if "chat_with_" in lowered_path or "/chatty/" in lowered_path
                else "identity" if "/identity/" in lowered_path or lowered_path.endswith('.capsule')
                else "knowledge"
            ),
        })

    updated_at = None
    summary_updated_at = file_summary.get("updated_at")
    if hasattr(summary_updated_at, "isoformat"):
        updated_at = summary_updated_at.isoformat()
    elif summary_updated_at:
        updated_at = str(summary_updated_at)

    prompt_capabilities = prompt_json.get('capabilities')
    metadata_capabilities = metadata_json.get('capabilities')
    capabilities = _normalize_construct_capabilities(
        prompt_capabilities if isinstance(prompt_capabilities, (dict, list)) else metadata_capabilities
    )

    prompt_memory = prompt_json.get('memory')
    metadata_memory = metadata_json.get('memory')
    memory_settings = _normalize_construct_memory_settings(
        prompt_memory if isinstance(prompt_memory, (dict, bool)) else metadata_memory
    )

    prompt_references = prompt_json.get("references") if isinstance(prompt_json.get("references"), dict) else {}
    canon_refs = _normalize_construct_refs(
        prompt_references.get("canon")
        or prompt_json.get('canonRefs')
        or metadata_json.get('canon_refs')
    )
    knowledge_refs = _normalize_construct_refs(
        prompt_references.get("knowledge")
        or prompt_json.get('knowledgeRefs')
        or metadata_json.get('knowledge_refs')
    )

    models = _normalize_construct_models(metadata_json.get('models'))
    actions_value = metadata_json.get('actions')
    if isinstance(actions_value, dict):
        actions = actions_value.get("items")
    else:
        actions = actions_value
    if not isinstance(actions, list):
        actions = []
    display_name = _first_bounded_identity_label([
        metadata_json.get('display_name'),
        metadata_json.get('instance_name'),
        prompt_json.get('displayName'),
        prompt_json.get('display_name'),
        prompt_json.get('name'),
    ], default=chatty_body_service.display_name(callsign))
    full_name = _first_bounded_identity_label([
        metadata_json.get('full_name'),
        definition_json.get('full_name'),
        prompt_json.get('fullName'),
    ], default=display_name)
    legacy_instructions = _legacy_prompt_instructions(prompt_text)
    instructions = _first_non_empty_string([
        prompt_json.get('instructions'),
        prompt_json.get('prompt'),
        legacy_instructions,
    ])
    created_at = _first_non_empty_string([
        prompt_json.get('createdAt'),
        prompt_json.get('created_at'),
        metadata_json.get('created_at'),
    ])

    privacy = _first_non_empty_string([
        metadata_json.get('privacy'),
    ], default='private')
    share_path = (
        f"/share/{callsign}" if privacy in {"link", "store"} else None
    )
    payload = {
        "ok": True,
        "constructId": callsign,
        "callsign": callsign,
        "displayName": display_name,
        "fullName": full_name,
        "description": _first_non_empty_string([prompt_json.get('description'), metadata_json.get('description')]),
        "instructions": instructions,
        "systemPromptOverride": _first_non_empty_string([
            prompt_json.get('instructions'),
            prompt_json.get('systemPromptOverride'),
            prompt_json.get('prompt'),
            legacy_instructions,
        ]),
        "aliases": _first_non_empty_list([prompt_json.get("aliases")]),
        "summaryCapabilities": _first_non_empty_list([prompt_json.get("summaryCapabilities")]),
        "conversationStarters": _first_non_empty_list([
            prompt_json.get('conversationStarters'),
            prompt_json.get('conversation_starters'),
        ]),
        "conditioning": _first_non_empty_string([conditioning_text]),
        "definition": _first_non_empty_string([
            definition_json.get('instructions'),
            definition_json.get('prompt'),
            definition_json.get('core_definition'),
            definition_text,
        ]),
        "physicalFeatures": _first_non_empty_string([
            _physical_features_to_text(physical_features_json),
            physical_features_text,
        ]),
        "voice": _first_non_empty_string([voice_md_text, voice_json.get('description'), voice_json.get('text')]),
        "gender": _first_non_empty_string([gender_json.get('gender')]),
        "avatar": {
            "exists": bool(avatar_row),
            "filename": avatar_row.get('filename') if avatar_row else None,
            "url": avatar_bytes_url,
            "descriptorUrl": avatar_descriptor_url,
            "bytesUrl": avatar_bytes_url,
            "sha256": avatar_row.get('sha256') if avatar_row else None,
            "contentType": avatar_content_type,
            "unavailableReason": None,
            "contentState": "descriptor" if avatar_row else "missing",
        },
        "filesSummary": {
            "totalCount": int(file_summary.get("total_count") or 0),
            "totalBytes": total_bytes,
            "sampleFilenames": sample_filenames,
            "updatedAt": updated_at,
        },
        "files": files,
        "capsule": {
            "exists": bool(capsule_row),
            "filename": capsule_row.get('filename') if capsule_row else None,
            "storagePath": capsule_row.get('storage_path') if capsule_row else None,
            "sha256": capsule_row.get('sha256') if capsule_row else None,
            "updatedAt": (
                capsule_row.get('updated_at').isoformat()
                if capsule_row and hasattr(capsule_row.get('updated_at'), 'isoformat')
                else capsule_row.get('updated_at') if capsule_row else None
            ),
        },
        "models": models,
        "capabilities": capabilities,
        "memory": memory_settings,
        "canonRefs": canon_refs,
        "knowledgeRefs": knowledge_refs,
        "actions": actions,
        "config": {
            "provider": _first_non_empty_string([prompt_json.get("provider")]),
            "tags": prompt_json.get("tags") if isinstance(prompt_json.get("tags"), list) else [],
            "categories": prompt_json.get("categories") if isinstance(prompt_json.get("categories"), list) else [],
            "orchestrationMode": _first_non_empty_string([
                prompt_json.get("orchestrationMode"),
                (metadata_json.get("orchestration") or {}).get("mode")
                if isinstance(metadata_json.get("orchestration"), dict) else None,
            ], default="standard"),
            "memoryEnabled": bool(prompt_json.get("memoryEnabled", memory_settings.get("enabled", True))),
            "memoryProfile": _first_non_empty_string([
                prompt_json.get("memoryProfile"),
            ], default="continuitygpt" if memory_settings.get("enabled", True) else "off"),
            "hasPersistentMemory": bool(
                (prompt_json.get("configJson") or {}).get("hasPersistentMemory", memory_settings.get("enabled", True))
                if isinstance(prompt_json.get("configJson"), dict)
                else memory_settings.get("enabled", True)
            ),
            "roleplayEnabled": bool(prompt_json.get("roleplayEnabled", True)),
            "configJson": prompt_json.get("configJson"),
        },
        "construct_category": _first_non_empty_string([
            metadata_json.get('construct_category'),
            metadata_json.get('constructCategory'),
            metadata_json.get('category'),
        ], default='user'),
        "privacy": privacy,
        "visibility": {
            "privacy": privacy,
            "shareEnabled": privacy in {"link", "store"},
            "sharePath": share_path,
        },
        "lifecycleStage": _first_non_empty_string([
            metadata_json.get('lifecycle_stage'),
            metadata_json.get('lifecycleStage'),
        ], default='gpt'),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "lastEdited": updated_at,
    }
    if user_id:
        files_result = chatty_body_service.construct_files(
            callsign, user_id=str(user_id)
        )
        file_payload = files_result.payload if files_result.status == "body_native" else {}
        payload["knowledgeFiles"] = [
            copy.deepcopy(item)
            for bucket in ("assets", "documents")
            for item in (file_payload.get(bucket) or [])
            if str(item.get("file_type") or "").lower() != "transcript"
            and "/chatty/" not in str(item.get("storage_path") or "").lower()
        ]
        _attach_construct_editor_baseline(payload, str(user_id), callsign)
    return payload


def _cached_construct_editor_payload(callsign: str, user_id: str) -> Dict[str, Any]:
    key = (str(user_id), callsign)
    now = time.monotonic()
    with _construct_editor_cache_lock:
        cached = _construct_editor_cache.get(key)
        if cached and now - cached[0] <= CONSTRUCT_EDITOR_CACHE_TTL_SECONDS:
            result = copy.deepcopy(cached[1])
            result.update({"cacheState": "fresh", "refreshing": False})
            return result
        inflight = _construct_editor_inflight.get(key)
        if inflight is None:
            inflight = threading.Event()
            _construct_editor_inflight[key] = inflight
            leader = True
        else:
            leader = False
    if not leader:
        inflight.wait(timeout=20.0)
        with _construct_editor_cache_lock:
            completed = _construct_editor_cache.get(key)
        if completed:
            result = copy.deepcopy(completed[1])
            age = time.monotonic() - completed[0]
            if age <= CONSTRUCT_EDITOR_CACHE_LKG_SECONDS:
                result.update({
                    "cacheState": "fresh" if age <= CONSTRUCT_EDITOR_CACHE_TTL_SECONDS else "stale",
                    "refreshing": False,
                })
                return result
    try:
        result = _build_construct_editor_payload(callsign, user_id)
    except Exception:
        with _construct_editor_cache_lock:
            _construct_editor_inflight.pop(key, None)
        inflight.set()
        if cached and time.monotonic() - cached[0] <= CONSTRUCT_EDITOR_CACHE_LKG_SECONDS:
            stale = copy.deepcopy(cached[1])
            stale.update({"cacheState": "stale", "refreshing": False})
            return stale
        raise
    with _construct_editor_cache_lock:
        _construct_editor_cache[key] = (time.monotonic(), copy.deepcopy(result))
        _construct_editor_inflight.pop(key, None)
    inflight.set()
    result = copy.deepcopy(result)
    result.update({"cacheState": "miss", "refreshing": False})
    return result


def _construct_editor_version_snapshot(
    editor: dict[str, Any], owner_user_id: str | None = None
) -> dict[str, Any]:
    """Bounded editable state used for immutable history and restore."""
    avatar = editor.get("avatar") if isinstance(editor.get("avatar"), dict) else {}
    config = editor.get("config") if isinstance(editor.get("config"), dict) else {}
    avatar_data_url = avatar.get("url")
    if (
        owner_user_id
        and avatar.get("exists")
        and not (
            isinstance(avatar_data_url, str)
            and avatar_data_url.startswith("data:image/")
        )
    ):
        hydrated = _canonical_owner_avatar(
            owner_user_id,
            str(editor.get("constructId") or editor.get("callsign") or ""),
        )
        if hydrated.get("state") == "available" and hydrated.get("body"):
            avatar_data_url = (
                "data:image/png;base64,"
                + base64.b64encode(hydrated["body"]).decode("ascii")
            )
        elif hydrated.get("state") == "missing":
            avatar_data_url = None
            avatar["exists"] = False
        else:
            avatar_data_url = None
            avatar["unavailableReason"] = hydrated.get("errorCode")
    if isinstance(avatar_data_url, str) and avatar_data_url.startswith("data:image/"):
        avatar_state = "available"
        avatar_error = None
    elif avatar.get("exists"):
        avatar_state = "hydration_error"
        avatar_error = (
            str(avatar.get("unavailableReason") or "").strip()
            or "AVATAR_BODY_UNAVAILABLE"
        )
        avatar_data_url = None
    else:
        avatar_state = "missing"
        avatar_error = None
        avatar_data_url = None
    # Keep history bounded. An oversized current avatar remains an explicit
    # historical hydration error instead of becoming a live-file dependency.
    if isinstance(avatar_data_url, str) and len(avatar_data_url) > 8 * 1024 * 1024:
        avatar_state = "hydration_error"
        avatar_error = "AVATAR_SNAPSHOT_EXCEEDS_8_MIB"
        avatar_data_url = None
    avatar_sha256 = avatar.get("sha256")
    avatar_content_type = avatar.get("contentType")
    if avatar_state == "available" and avatar_data_url:
        try:
            header, encoded = avatar_data_url.split(",", 1)
            avatar_bytes = base64.b64decode(encoded, validate=True)
            avatar_sha256 = hashlib.sha256(avatar_bytes).hexdigest()
            avatar_content_type = header[5:].split(";", 1)[0]
        except Exception:
            avatar_state = "hydration_error"
            avatar_error = "AVATAR_SNAPSHOT_DATA_URL_INVALID"
            avatar_data_url = None
    return {
        "schemaVersion": "2.0.0",
        "constructId": editor.get("constructId") or editor.get("callsign"),
        "displayName": editor.get("displayName") or "",
        "fullName": editor.get("fullName") or "",
        "description": editor.get("description") or "",
        "instructions": editor.get("instructions") or "",
        "systemPromptOverride": editor.get("systemPromptOverride") or "",
        "conversationStarters": editor.get("conversationStarters") or [],
        "conditioning": editor.get("conditioning") or "",
        "definition": editor.get("definition") or "",
        "physicalFeatures": editor.get("physicalFeatures") or "",
        "voice": editor.get("voice") or "",
        "gender": editor.get("gender") or "",
        "avatarSnapshot": {
            "state": avatar_state,
            "dataUrl": avatar_data_url,
            "sha256": avatar_sha256,
            "contentType": avatar_content_type,
            "errorCode": avatar_error,
        },
        "models": editor.get("models") or {},
        "capabilities": editor.get("capabilities") or {},
        "memory": editor.get("memory") or {},
        "canonRefs": editor.get("canonRefs") or [],
        "actions": editor.get("actions") or [],
        "privacy": editor.get("privacy") or "private",
        "config": config,
        "continuityConfiguration": {
            "path": "/app/vvault",
        },
    }


def _upsert_construct_prompt_file(
    callsign: str,
    user_id: Optional[str],
    payload: Dict[str, Any],
    *,
    source: str = "vvault_construct_editor",
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    path = f"instances/{callsign}/identity/prompt.json"
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    sha256 = _sha256_text(content)
    record = {
        "filename": path,
        "storage_path": path,
        "file_type": "text",
        "content": content,
        "construct_id": callsign,
        "user_id": user_id,
        "is_system": False,
        "sha256": sha256,
        "metadata": json.dumps({
            "folder": "identity",
            "source": source,
            "updatedAt": now,
        }),
        "created_at": now,
        "updated_at": now,
    }
    return _upsert_vault_file_record(record, context='construct_prompt')


def _upsert_construct_metadata_file(
    callsign: str,
    user_id: Optional[str],
    payload: Dict[str, Any],
    *,
    source: str = "vvault_construct_editor",
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    path = f"instances/{callsign}/config/metadata.json"
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    record = {
        "filename": path,
        "storage_path": path,
        "file_type": "text",
        "content": content,
        "construct_id": callsign,
        "user_id": user_id,
        "is_system": False,
        "sha256": _sha256_text(content),
        "metadata": json.dumps({
            "folder": "config",
            "source": source,
            "updatedAt": now,
        }),
        "created_at": now,
        "updated_at": now,
    }
    return _upsert_vault_file_record(record, context='construct_metadata')


def _upsert_text_construct_file(callsign: str, user_id: Optional[str], filename: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    path = f"instances/{callsign}/identity/{filename}"
    record = {
        "filename": path,
        "storage_path": path,
        "file_type": "text",
        "content": content,
        "construct_id": callsign,
        "user_id": user_id,
        "is_system": False,
        "sha256": _sha256_text(content),
        "metadata": json.dumps({
            "folder": "identity",
            "source": "vvault_construct_editor",
            **(metadata or {}),
        }),
        "created_at": now,
        "updated_at": now,
    }
    return _upsert_vault_file_record(record, context=f'construct_editor_{filename}')


def _upsert_binary_construct_file(callsign: str, user_id: Optional[str], filename: str, base64_content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    path = f"instances/{callsign}/identity/{filename}"
    record = {
        "filename": path,
        "storage_path": path,
        "file_type": "binary",
        "content": base64_content,
        "construct_id": callsign,
        "user_id": user_id,
        "is_system": False,
        "sha256": hashlib.sha256(base64.b64decode(base64_content)).hexdigest(),
        "metadata": json.dumps({
            "folder": "identity",
            "source": "vvault_construct_editor",
            **(metadata or {}),
        }),
        "created_at": now,
        "updated_at": now,
    }
    return _upsert_vault_file_record(record, context=f'construct_editor_{filename}')

# Initialize Google OAuth client
google_client = None
if GOOGLE_CLIENT_ID:
    google_client = WebApplicationClient(GOOGLE_CLIENT_ID)

OAUTH_BASE_URL = _resolve_backend_origin() or ""

# Service API Configuration (for FXShinobi/Chatty backend-to-backend calls)
VVAULT_SERVICE_TOKEN = os.environ.get("VVAULT_SERVICE_TOKEN")
_IMPORTED_VVAULT_SERVICE_TOKEN = VVAULT_SERVICE_TOKEN
VVAULT_ENCRYPTION_KEY = os.environ.get("VVAULT_ENCRYPTION_KEY", os.environ.get("SECRET_KEY", "default-encryption-key"))
CONGRUENCY_PURGE_CONSTRUCTS = (
    "arbiter-001", "aurora-001", "clean-001", "click-001", "codegpt-001",
    "continuitygpt-001", "dayday-001", "db-override-test-001",
    "engineergpt-001", "insight-001", "katana-002", "linda-001", "luna-001",
    "monday-001", "projectionproof-001", "qc-001", "researchgpt-001",
    "scout-001", "tom-001", "val-001",
)

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'backups', 'vault_files')
BACKUP_MAX_AGE_DAYS = 30

def _backup_before_write(file_id: str, filename: str, content: str) -> bool:
    """Save a local JSON backup of vault_files content before modification.
    
    Creates backups/vault_files/ directory if needed.
    Saves as {file_id}_{timestamp}.json with old content, file_id, filename, and timestamp.
    Cleans up backups older than 30 days periodically.
    Never blocks the main operation - logs errors but returns gracefully.
    """
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        safe_file_id = str(file_id).replace('/', '_').replace('\\', '_')
        backup_filename = f"{safe_file_id}_{timestamp}.json"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)

        backup_data = {
            "file_id": str(file_id),
            "filename": filename,
            "content": content,
            "backed_up_at": datetime.now().isoformat()
        }

        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        logger.info(f"BACKUP: Saved backup for file_id={file_id} filename={filename} content_length={len(content or '')} to {backup_filename}")

        _cleanup_old_backups()

        return True
    except Exception as e:
        logger.error(f"BACKUP ERROR: Failed to backup file_id={file_id} filename={filename}: {e}")
        return False

def _cleanup_old_backups():
    """Remove backups older than BACKUP_MAX_AGE_DAYS. Runs silently."""
    try:
        if not os.path.exists(BACKUP_DIR):
            return
        
        cutoff = datetime.now().timestamp() - (BACKUP_MAX_AGE_DAYS * 86400)
        removed = 0
        
        for fname in os.listdir(BACKUP_DIR):
            fpath = os.path.join(BACKUP_DIR, fname)
            if os.path.isfile(fpath) and fname.endswith('.json'):
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    removed += 1
        
        if removed > 0:
            logger.info(f"BACKUP CLEANUP: Removed {removed} backups older than {BACKUP_MAX_AGE_DAYS} days")
    except Exception as e:
        logger.error(f"BACKUP CLEANUP ERROR: {e}")

def _protected_vault_update(file_id: str, new_content: str, force: bool = False, context: str = "unknown") -> dict:
    """Wrap vault_files update operations with delete protection.
    
    Before performing a full content replacement:
    1. Reads existing content from VVAULT-native vault_files
    2. If existing content is longer than new content by more than 50%, rejects the update
    3. Accepts force parameter to bypass the check
    4. Logs all content updates with before/after lengths
    
    Returns: {"allowed": True/False, "error": str or None, "existing_content": str, "existing_length": int}
    """
    result = {"allowed": True, "error": None, "existing_content": "", "existing_length": 0}
    
    try:
        existing = VAULT_FILE_REPOSITORY.get_by_id(file_id)

        if not existing:
            logger.warning(f"PROTECTED_UPDATE [{context}]: file_id={file_id} not found in VVAULT body")
            result["allowed"] = True
            return result
        
        existing_content = existing.get('content', '') or ''
        existing_filename = existing.get('filename', '')
        existing_length = len(existing_content)
        new_length = len(new_content)
        
        result["existing_content"] = existing_content
        result["existing_length"] = existing_length
        
        logger.info(f"PROTECTED_UPDATE [{context}]: file_id={file_id} existing_length={existing_length} new_length={new_length} force={force}")
        
        if existing_length > 0 and new_length < existing_length * 0.5:
            if not force:
                reduction_pct = round((1 - new_length / existing_length) * 100, 1)
                logger.warning(
                    f"PROTECTED_UPDATE REJECTED [{context}]: file_id={file_id} "
                    f"existing_length={existing_length} new_length={new_length} "
                    f"reduction={reduction_pct}% - looks like data loss"
                )
                result["allowed"] = False
                result["error"] = (
                    "Content replacement rejected: new content is significantly smaller "
                    "than existing content. This looks like data loss. Use force=true to override."
                )
                return result
            else:
                logger.warning(
                    f"PROTECTED_UPDATE FORCED [{context}]: file_id={file_id} "
                    f"existing_length={existing_length} new_length={new_length} - force=true bypassed protection"
                )
        
        _backup_before_write(file_id, existing_filename, existing_content)
        
        result["allowed"] = True
        return result
        
    except Exception as e:
        logger.error(f"PROTECTED_UPDATE ERROR [{context}]: file_id={file_id} error={e}")
        result["allowed"] = True
        return result

# Encryption helpers for service credentials
from cryptography.fernet import Fernet
import base64

def _get_fernet_key():
    """Generate a valid Fernet key from VVAULT_ENCRYPTION_KEY"""
    key_bytes = VVAULT_ENCRYPTION_KEY.encode()[:32].ljust(32, b'0')
    return base64.urlsafe_b64encode(key_bytes)

def encrypt_credential(value: str) -> str:
    """Encrypt a credential value"""
    f = Fernet(_get_fernet_key())
    return f.encrypt(value.encode()).decode()

def decrypt_credential(encrypted_value: str) -> str:
    """Decrypt a credential value"""
    f = Fernet(_get_fernet_key())
    return f.decrypt(encrypted_value.encode()).decode()

# Service token auth decorator
from functools import wraps

def require_service_token(f):
    """Decorator to require VVAULT_SERVICE_TOKEN for backend-to-backend calls"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _configured_service_token():
            logger.warning("SERVICE_API: VVAULT_SERVICE_TOKEN not configured")
            return jsonify({
                "success": False,
                "error": "Service API not configured"
            }), 503
        
        if not _service_token_matches():
            logger.warning(f"SERVICE_API: Invalid service token attempt")
            return jsonify({
                "success": False,
                "error": "Invalid service token"
            }), 401
        
        return f(*args, **kwargs)
    return decorated_function

def _check_session_table_available() -> bool:
    """Check if VVAULT-native session storage is available."""
    return _auth_repository_ready()

def db_create_session(email: str, role: str, token: str, expires_at: datetime, remember_me: bool = False, user_id: str | None = None) -> bool:
    """Create a VVAULT-native session; persist only a token hash."""
    user = {"id": user_id} if user_id else AUTH_REPOSITORY.get_user_by_email(email)
    if not user:
        raise RuntimeError("VVAULT auth user does not exist")
    token_hash = _session_token_hash(token)
    AUTH_REPOSITORY.create_session(
        user_id=str(user["id"]),
        token_hash=token_hash,
        expires_at=expires_at,
    )
    logger.info(f"Session persisted to VVAULT auth DB for {email} (remember_me={remember_me})")
    return True


def db_delete_session(token: str) -> bool:
    """Revoke session from VVAULT-native session storage."""
    AUTH_REPOSITORY.revoke_session_by_hash(_session_token_hash(token))
    return True

def db_get_session(token: str) -> Optional[Dict]:
    """Get session from VVAULT-native session storage."""
    try:
        session_data = AUTH_REPOSITORY.get_session_by_hash(_session_token_hash(token))
        if not session_data:
            return None
        return {
            'id': str(session_data.get('user_id')),
            'session_id': str(session_data.get('session_id')),
            'email': session_data['email'],
            'name': session_data.get('name') or session_data['email'].split('@')[0],
            'role': session_data.get('role') or 'user',
            'auth_provider': session_data.get('auth_provider'),
            'expires_at': session_data.get('expires_at'),
            'created_at': session_data.get('session_created_at'),
            'source': 'vvault_auth',
            'auth_mode': 'session',
            'account_state': session_data.get('account_state'),
            'enrollment_session_kind': session_data.get('enrollment_session_kind'),
            'enrollment_device_id': str(session_data.get('enrollment_device_id') or ''),
            'enrollment_device_status': session_data.get('enrollment_device_status'),
        }
    except Exception as e:
        logger.debug(f"VVAULT auth session lookup failed: {type(e).__name__}")
        return None

def db_get_user(email: str) -> Optional[Dict]:
    """Get user from VVAULT-native auth storage."""
    if has_request_context():
        request.environ.pop("vvault.auth_lookup_error", None)
    try:
        user = AUTH_REPOSITORY.get_user_by_email(email)
        if not user:
            return None
        user['source'] = 'vvault_auth'
        user['role'] = _resolve_user_role(email, local_user=user)
        return user
    except Exception as e:
        if has_request_context():
            request.environ["vvault.auth_lookup_error"] = type(e).__name__
        logger.error(f"Failed to get user from VVAULT auth database: {type(e).__name__}")
        return None

def db_cleanup_expired_sessions():
    """Clean up expired sessions from VVAULT-native session storage."""
    try:
        AUTH_REPOSITORY.cleanup_expired_sessions()
    except Exception as e:
        logger.error(f"Failed to cleanup expired VVAULT auth sessions: {type(e).__name__}")

# Audit log for zero trust compliance
AUTH_AUDIT_LOG = []

def log_auth_decision(action: str, user_id: str, resource: str, result: str, reason: str = None, ip: str = None):
    """Log authentication/authorization decisions for zero trust audit trail"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "user_id": user_id,
        "resource": resource,
        "result": result,
        "reason": reason,
        "ip_address": ip,
        "user_agent": request.headers.get('User-Agent', 'unknown') if request else None
    }
    AUTH_AUDIT_LOG.append(entry)
    if len(AUTH_AUDIT_LOG) > 10000:
        AUTH_AUDIT_LOG.pop(0)
    
    log_level = logging.INFO if result == "allowed" else logging.WARNING
    logger.log(log_level, f"AUTH: {action} | user={user_id} | resource={resource} | result={result} | reason={reason}")

def get_current_user():
    """Extract and validate current user from request token (database-backed)"""
    try:
        auth_header = request.headers.get('Authorization') or ''
        token = auth_header.split(' ', 1)[1] if auth_header.startswith('Bearer ') else str(request.cookies.get('vvault_session') or '')
        if not token:
            return None, None
        
        session = db_get_session(token)
        if not session:
            return None, None
        state = str(session.get('account_state') or 'LEGACY')
        kind = str(session.get('enrollment_session_kind') or 'LEGACY')
        device_status = str(session.get('enrollment_device_status') or '')
        if state == 'ACTIVE' and kind == 'NORMAL' and device_status == 'TRUSTED':
            return session, token
        # Existing sessions remain usable only during the explicitly staged
        # migration window. New pending/device sessions never reach data routes.
        if state == 'LEGACY' and kind == 'LEGACY' and str(os.environ.get('VVAULT_ENROLLMENT_ENFORCE') or '').lower() not in {'1', 'true', 'yes'}:
            return session, token
        return None, None
    except Exception as e:
        logger.error(f"Error in get_current_user: {e}")
        return None, None

def require_auth(f):
    """Zero Trust: Decorator to require authentication on every request"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session, token = get_current_user()
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        if not session:
            log_auth_decision(
                action="access_attempt",
                user_id="anonymous",
                resource=request.path,
                result="denied",
                reason="no_valid_session",
                ip=ip
            )
            return jsonify({"success": False, "error": "Authentication required"}), 401
        
        log_auth_decision(
            action="access_granted",
            user_id=session.get('email', 'unknown'),
            resource=request.path,
            result="allowed",
            reason="valid_session",
            ip=ip
        )
        
        request.current_user = session
        try:
            from .relying_party_scope import set_relying_party_id
        except ImportError:
            from relying_party_scope import set_relying_party_id
        set_relying_party_id("vvault")
        request.current_token = token
        return f(*args, **kwargs)
    return decorated_function

def require_chatty_auth(f):
    """Auth decorator for Chatty integration endpoints.

    User-scoped calls accept only an owner-bound signed assertion or a native
    ACTIVE/trusted-device VVAULT session. Shared secrets never select owners.
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        auth_header = request.headers.get("Authorization", "")
        bearer = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        if bearer.count(".") == 2:
            try:
                unverified_claims = vvault_access_assertion._decode_segment(bearer.split(".")[1], "claims")
            except vvault_access_assertion.AccessAssertionRejected:
                unverified_claims = {}
            if unverified_claims.get("version") == vvault_access_assertion.ASSERTION_VERSION:
                try:
                    verified = vvault_access_assertion.verify_access_assertion(bearer)
                except vvault_access_assertion.AccessAssertionUnavailable:
                    return jsonify({
                        "success": False,
                        "error": "VVAULT access assertion verification is unavailable",
                        "errorCode": "ACCESS_ASSERTION_UNAVAILABLE",
                    }), 503
                except vvault_access_assertion.AccessAssertionRejected:
                    return jsonify({
                        "success": False,
                        "error": "VVAULT access assertion was rejected",
                        "errorCode": "ACCESS_ASSERTION_REJECTED",
                    }), 401
                required = vvault_access_assertion.required_scopes(request.method, request.path)
                if not required or not required.issubset(verified["scopes"]):
                    return jsonify({
                        "success": False,
                        "error": "VVAULT access assertion lacks the required scope",
                        "errorCode": "ACCESS_ASSERTION_REJECTED",
                    }), 403
                request.current_user = {
                    "id": verified["ownerUserId"],
                    "user_id": verified["ownerUserId"],
                    "role": "user",
                    "auth_mode": "signed_assertion",
                    "access_scopes": sorted(verified["scopes"]),
                    "subject": verified["subject"],
                }
                try:
                    from .relying_party_scope import set_relying_party_id
                except ImportError:
                    from relying_party_scope import set_relying_party_id
                set_relying_party_id(verified["relyingPartyId"])
                request.current_token = None
                request.vvault_access_assertion = {
                    "key_id": verified["keyId"],
                    "owner_fingerprint": verified["ownerFingerprint"],
                    "expires_at": verified["expiresAt"],
                }
                logger.info(
                    "VVAULT_ACCESS_ASSERTION %s",
                    json.dumps({
                        "requestId": str(request.headers.get("X-Request-ID") or "")[:128] or None,
                        "phase": "authorization",
                        "target": request.path,
                        "keyId": verified["keyId"],
                        "ownerFingerprint": verified["ownerFingerprint"],
                        "status": "accepted",
                        "retryResult": (
                            "retry" if request.headers.get("X-Chatty-Assertion-Retry") == "1"
                            else "not_attempted"
                        ),
                    }, separators=(",", ":")),
                )
                return f(*args, **kwargs)

        session, token = get_current_user()
        if session:
            log_auth_decision(
                action="access_granted",
                user_id=session.get('email', 'unknown'),
                resource=request.path,
                result="allowed",
                reason="valid_session",
                ip=ip
            )
            request.current_user = {**session, "auth_mode": session.get("auth_mode") or "session"}
            try:
                from .relying_party_scope import set_relying_party_id
            except ImportError:
                from relying_party_scope import set_relying_party_id
            set_relying_party_id("vvault")
            request.current_token = token
            return f(*args, **kwargs)

        # A shared credential may authorize service-only operations, but it
        # deliberately carries no owner. User-scoped routes still fail closed
        # in _get_authenticated_user_id unless a native session or signed,
        # owner-bound assertion was supplied.
        if _service_token_matches():
            legacy_email = str(request.headers.get("X-Chatty-User") or "").strip().lower()
            if legacy_email:
                request.current_user = {
                    "email": legacy_email,
                    "role": "service",
                    "auth_mode": "legacy_chatty_service",
                }
                request.current_token = None
                return f(*args, **kwargs)
            request.current_user = {
                "role": "service",
                "auth_mode": "service_token",
            }
            request.current_token = None
            return f(*args, **kwargs)

        log_auth_decision(
            action="access_attempt",
            user_id="anonymous",
            resource=request.path,
            result="denied",
            reason="no_valid_auth",
            ip=ip
        )
        return jsonify({"success": False, "error": "Authentication required"}), 401
    return decorated_function


def _construct_grade_preflight_strict_auth_error():
    """Fail closed unless Chatty auth established a non-development principal."""
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("auth_mode") == "signed_assertion":
        assertion = getattr(request, "vvault_access_assertion", None)
        if (
            isinstance(assertion, dict)
            and assertion.get("key_id")
            and assertion.get("owner_fingerprint")
            and assertion.get("expires_at")
        ):
            return None

    current_token = str(getattr(request, "current_token", None) or "")
    if current_user and current_token and current_user.get("auth_mode") == "session":
        return None

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    log_auth_decision(
        action="access_attempt",
        user_id="anonymous",
        resource=request.path,
        result="denied",
        reason="construct_grade_preflight_strict_auth_required",
        ip=ip,
    )
    return jsonify({
        "success": False,
        "canonical": True,
        "error": "Strict authentication is required for construct attribution preflight",
        "error_code": "CONSTRUCT_GRADE_PREFLIGHT_AUTH_REQUIRED",
    }), 401


def _construct_creation_provenance_token(session_token: str, user_id: str) -> str:
    message = f"vvault-construct-create:{user_id}:{session_token}".encode("utf-8")
    secret = app.config.get("SECRET_KEY", "").encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _construct_creation_source(user_id: str) -> str | None:
    service_token = request.headers.get("X-Service-Token") or request.headers.get("X-Chatty-Key")
    if (
        request.path == "/api/simforge/construct/create"
        and VVAULT_SERVICE_TOKEN
        and service_token
        and hmac.compare_digest(service_token, VVAULT_SERVICE_TOKEN)
    ):
        return "simforge"

    session_token = getattr(request, "current_token", None)
    supplied = request.headers.get("X-VVAULT-Creation-Provenance", "")
    if session_token and supplied:
        expected = _construct_creation_provenance_token(session_token, user_id)
        if hmac.compare_digest(supplied, expected):
            return "vvault_ui"
    return None


def require_role(*roles):
    """Zero Trust: Decorator to require specific role(s) for access"""
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            session, token = get_current_user()
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)

            if not session:
                log_auth_decision(
                    action="role_check",
                    user_id="anonymous",
                    resource=request.path,
                    result="denied",
                    reason="no_valid_session",
                    ip=ip
                )
                return jsonify({"success": False, "error": "Authentication required"}), 401

            user_role = session.get('role', 'user')
            if user_role not in roles:
                log_auth_decision(
                    action="role_check",
                    user_id=session.get('email', 'unknown'),
                    resource=request.path,
                    result="denied",
                    reason=f"insufficient_role: has={user_role}, needs={roles}",
                    ip=ip
                )
                return jsonify({"success": False, "error": "Insufficient permissions"}), 403
            
            log_auth_decision(
                action="role_check",
                user_id=session.get('email', 'unknown'),
                resource=request.path,
                result="allowed",
                reason=f"role_match: {user_role}",
                ip=ip
            )
            
            request.current_user = session
            request.current_token = token
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# VVAULT Configuration
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CAPSULES_DIR = os.path.join(PROJECT_DIR, "capsules")
VAULT_DIR = os.path.join(PROJECT_DIR, "vvault")
HUMAN_CAPSULE_TYPE = "human_personalization_profile"


def _validate_human_capsule_payload(payload: dict) -> Optional[str]:
    """Minimal guard for human personalization profile payloads."""
    if not isinstance(payload, dict):
        return "Payload must be an object"

    required_blocks = ["identity", "personalization", "appearance", "language", "aiPreferences", "signals"]
    for block in required_blocks:
        if block not in payload:
            return f"Missing required section: {block}"

    identity = payload.get("identity", {})
    if not identity.get("userId"):
        return "identity.userId is required"
    if not identity.get("email"):
        return "identity.email is required"

    signals = payload.get("signals", {})
    if not isinstance(signals, dict):
        return "signals must be an object"

    return None


def _build_human_capsule(payload: dict) -> dict:
    """Normalize a human personalization capsule ready for VVAULT storage."""
    now = datetime.utcnow().isoformat() + "Z"
    user_id = payload.get("identity", {}).get("userId", "unknown-human")
    capsule_name = f"human-{user_id}-{int(time.time())}.capsule"

    return {
        "name": capsule_name,
        "title": f"Human personalization profile for {user_id}",
        "description": "Chatty + VVAULT + neat human capsule with transcripts and harvested signals",
        "capsule_type": HUMAN_CAPSULE_TYPE,
        "created": now,
        "updated": now,
        "version": "1.0.0",
        "source": "chatty",
        "human": payload.get("identity"),
        "personalization": payload.get("personalization"),
        "appearance": payload.get("appearance"),
        "language": payload.get("language"),
        "voice": payload.get("voice"),
        "ai_preferences": payload.get("aiPreferences"),
        "notifications": payload.get("notifications"),
        "data_controls": payload.get("dataControls"),
        "security": payload.get("security"),
        "parental_controls": payload.get("parentalControls"),
        "account": payload.get("account"),
        "backup": payload.get("backup"),
        "profile_picture": payload.get("profilePicture"),
        "advanced": payload.get("advanced"),
        "metadata": payload.get("metadata"),
        "signals": payload.get("signals", {}),
        "id": str(uuid4())
    }

class VVAULTWebAPI:
    """VVAULT Web API handler"""
    
    def __init__(self):
        self.project_dir = PROJECT_DIR
        self.capsules_dir = CAPSULES_DIR
        self.status = {
            "server_started": datetime.now().isoformat(),
            "backend_port": 8000,
            "frontend_port": 7784,
            "system_status": "running",
            "capsules_loaded": 0
        }
        self._load_initial_data()
    
    def _load_initial_data(self):
        """Load initial VVAULT data"""
        try:
            # Ensure directories exist
            os.makedirs(self.capsules_dir, exist_ok=True)
            os.makedirs(VAULT_DIR, exist_ok=True)
            
            # Count capsules
            capsules = self.get_capsules()
            self.status["capsules_loaded"] = len(capsules)
            
            logger.info(f"✅ VVAULT Web API initialized with {len(capsules)} capsules")
            
        except Exception as e:
            logger.error(f"❌ Error loading initial data: {e}")
            self.status["system_status"] = "error"
    
    def get_status(self):
        """Get system status"""
        return {
            **self.status,
            "current_time": datetime.now().isoformat(),
            "uptime_seconds": (datetime.now() - datetime.fromisoformat(self.status["server_started"])).total_seconds(),
            "pocketverse_boot": _get_pocketverse_boot_state(),
        }
    
    def get_capsules(self):
        """Get list of all capsules"""
        capsules = []
        
        if not os.path.exists(self.capsules_dir):
            return capsules
        
        try:
            for root, dirs, files in os.walk(self.capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        capsule_path = os.path.join(root, file)
                        relative_path = os.path.relpath(capsule_path, self.capsules_dir)
                        
                        # Get basic capsule info
                        try:
                            stat = os.stat(capsule_path)
                            capsule_info = {
                                "name": file,
                                "path": relative_path,
                                "size": stat.st_size,
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                                "type": "capsule"
                            }
                            
                            # Try to load capsule data for additional info
                            try:
                                with open(capsule_path, 'r', encoding='utf-8') as f:
                                    capsule_data = json.load(f)
                                    capsule_info.update({
                                        "title": capsule_data.get("title", file),
                                        "description": capsule_data.get("description", ""),
                                        "version": capsule_data.get("version", "1.0.0"),
                                        "tags": capsule_data.get("tags", [])
                                    })
                            except:
                                # If we can't load the JSON, just use basic info
                                pass
                            
                            capsules.append(capsule_info)
                            
                        except Exception as e:
                            logger.warning(f"Error processing capsule {file}: {e}")
                            continue
        
        except Exception as e:
            logger.error(f"Error loading capsules: {e}")
        
        return capsules
    
    def get_capsule_data(self, capsule_name: str):
        """Get data for a specific capsule"""
        capsule_path = os.path.join(self.capsules_dir, capsule_name)
        
        if not os.path.exists(capsule_path):
            return None
        
        try:
            with open(capsule_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading capsule {capsule_name}: {e}")
            return None
    
    def create_capsule(self, capsule_data: dict):
        """Create a new capsule"""
        try:
            capsule_name = capsule_data.get("name", f"capsule-{int(time.time())}")
            if not capsule_name.endswith('.capsule'):
                capsule_name += '.capsule'
            
            capsule_path = os.path.join(self.capsules_dir, capsule_name)
            
            # Add metadata
            capsule_data.update({
                "created": datetime.now().isoformat(),
                "version": capsule_data.get("version", "1.0.0"),
                "type": "vvault_capsule"
            })
            
            with open(capsule_path, 'w', encoding='utf-8') as f:
                json.dump(capsule_data, f, indent=2)
            
            logger.info(f"✅ Created capsule: {capsule_name}")
            self.status["capsules_loaded"] = len(self.get_capsules())
            
            return {"success": True, "capsule": capsule_name}
            
        except Exception as e:
            logger.error(f"❌ Error creating capsule: {e}")
            return {"success": False, "error": str(e)}

# Initialize API handler
api = VVAULTWebAPI()

# API Routes
@app.route('/api/status')
def get_status():
    """Get system status"""
    return jsonify(api.get_status())

@app.route('/api/capsules')
@require_auth
def get_capsules():
    """Get list of all capsules"""
    try:
        capsules = api.get_capsules()
        return jsonify({
            "success": True,
            "capsules": capsules,
            "count": len(capsules)
        })
    except Exception as e:
        logger.error(f"Error in get_capsules endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/capsules/<capsule_name>')
@require_auth
def get_capsule(capsule_name):
    """Get data for a specific capsule"""
    try:
        capsule_data = api.get_capsule_data(capsule_name)
        if capsule_data is None:
            return jsonify({"success": False, "error": "Capsule not found"}), 404
        
        return jsonify({
            "success": True,
            "capsule": capsule_data
        })
    except Exception as e:
        logger.error(f"Error in get_capsule endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/capsules/<capsule_name>/vxrunner-baseline')
def get_capsule_vxrunner_baseline(capsule_name):
    """Convert a capsule to VXRunner forensic baseline format.
    
    Access control: Requires VXRUNNER_API_KEY via X-VXRunner-Key header
    or ?key= query parameter. If VXRUNNER_API_KEY is not set in the
    environment, the endpoint is open (development mode).
    """
    try:
        expected_key = os.environ.get("VXRUNNER_API_KEY")
        if expected_key:
            provided_key = (
                request.headers.get("X-VXRunner-Key")
                or request.args.get("key")
            )
            if provided_key != expected_key:
                return jsonify({"success": False, "error": "Unauthorized"}), 401

        if not capsule_name.endswith('.capsule'):
            capsule_name_file = capsule_name + '.capsule'
        else:
            capsule_name_file = capsule_name

        capsule_data = api.get_capsule_data(capsule_name_file)
        if capsule_data is None:
            capsule_data = api.get_capsule_data(capsule_name)
        if capsule_data is None:
            return jsonify({"success": False, "error": f"Capsule '{capsule_name}' not found"}), 404

        include_raw = request.args.get("include_raw_text", "true").lower() == "true"
        baseline = convert_capsule_to_baseline(capsule_data, include_raw_text=include_raw)

        return jsonify({
            "success": True,
            "baseline": baseline
        })
    except Exception as e:
        logger.error(f"Error in VXRunner baseline endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vxrunner/capsules')
def vxrunner_discover_capsules():
    """List available capsules for VXRunner discovery.
    
    Returns capsule names and metadata so VXRunner can auto-discover
    which baselines are available. Uses the same VXRUNNER_API_KEY auth
    as the baseline endpoint.
    """
    try:
        expected_key = os.environ.get("VXRUNNER_API_KEY")
        if expected_key:
            provided_key = (
                request.headers.get("X-VXRunner-Key")
                or request.args.get("key")
            )
            if provided_key != expected_key:
                return jsonify({"success": False, "error": "Unauthorized"}), 401

        capsules = api.get_capsules()
        capsule_list = []
        for c in capsules:
            name = c.get("name", "").replace(".capsule", "")
            capsule_list.append({
                "name": name,
                "filename": c.get("name", ""),
                "baseline_url": f"/api/capsules/{name}/vxrunner-baseline",
                "version": c.get("version", "1.0.0"),
                "modified": c.get("modified", ""),
            })

        return jsonify({
            "success": True,
            "capsules": capsule_list,
            "count": len(capsule_list)
        })
    except Exception as e:
        logger.error(f"Error in VXRunner capsule discovery: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/capsules', methods=['POST'])
@require_auth
def create_capsule():
    """Create a new capsule"""
    try:
        capsule_data = request.get_json()
        if not capsule_data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        result = api.create_capsule(capsule_data)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in create_capsule endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/human-capsule', methods=['POST'])
@require_auth
def ingest_human_capsule():
    """Ingest Chatty/neat human personalization capsule and persist to VVAULT."""
    try:
        payload = request.get_json(silent=True) or {}
        error = _validate_human_capsule_payload(payload)
        if error:
            return jsonify({"success": False, "error": error}), 400

        capsule_data = _build_human_capsule(payload)
        result = api.create_capsule(capsule_data)

        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error", "Failed to create capsule")}), 500

        return jsonify({
            "success": True,
            "capsule": result.get("capsule"),
            "capsule_payload": capsule_data
        })

    except Exception as e:
        logger.error(f"Error in ingest_human_capsule endpoint: {e}")
        return jsonify({"success": False, "error": "Human capsule ingestion failed"}), 500

@app.route('/')
def root():
    """Serve React frontend if dist/index.html exists, otherwise API status"""
    index_path = os.path.join(DIST_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(DIST_DIR, 'index.html')
    return jsonify({
        "status": "ok",
        "service": "vvault-api"
    })

@app.route('/api/health')
def health_check():
    """Health check endpoint backed by VVAULT-native runtime dependencies."""
    runtime_status = _get_vvault_runtime_status()
    return jsonify({
        "status": "healthy" if runtime_status["ready"] else "degraded",
        "authority": runtime_status["authority"],
        "storage_mode": runtime_status["storage_mode"],
        "canonical": runtime_status["canonical"],
        "connection_state": runtime_status["connection_state"],
        "timestamp": datetime.now().isoformat(),
        "service": "vvault-backend",
        "version": "1.0.0",
        "runtime": runtime_status["runtime"],
        "body_database": runtime_status["body_database"],
        "storage": runtime_status["storage"],
        "auth": runtime_status["auth"],
    })


@app.route('/api/health/deep')
def deep_health_check():
    """Explicit live OVVAULTS diagnostics; never used by the startup handshake."""
    runtime_status = _get_vvault_runtime_status(deep=True)
    status_code = 200 if runtime_status["ready"] else 503
    return jsonify({
        "status": "healthy" if runtime_status["ready"] else "degraded",
        "deep": True,
        "authority": runtime_status["authority"],
        "storage_mode": runtime_status["storage_mode"],
        "canonical": runtime_status["canonical"],
        "connection_state": runtime_status["connection_state"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "vvault-backend",
        "runtime": runtime_status["runtime"],
        "body_database": runtime_status["body_database"],
        "storage": runtime_status["storage"],
        "auth": runtime_status["auth"],
    }), status_code


@app.route('/api/ready')
def readiness_check():
    """Readiness requires VVAULT-native body database health."""
    runtime_status = _get_vvault_runtime_status()
    door = _resolve_chatty_vvault_door()
    projection_warm = _current_projection_warm_state()
    projection_fresh = _projection_lease_is_fresh(projection_warm)
    try:
        assertion_key_count = len(vvault_access_assertion.resolve_public_key_ring())
        assertion_trust_ready = assertion_key_count > 0
    except vvault_access_assertion.AccessAssertionUnavailable:
        assertion_key_count = 0
        assertion_trust_ready = False
    service_credential_accepted = _service_token_matches()
    trust_ready = bool(assertion_trust_ready and service_credential_accepted)
    ready = bool(
        runtime_status["ready"]
        and door.get("ok")
        and projection_fresh
    )
    return jsonify({
        "ready": ready,
        "available": ready,
        "fresh": bool(runtime_status.get("fresh") and projection_fresh),
        "refreshing": bool(
            runtime_status.get("refreshing")
            or (projection_warm.get("ready") and not projection_fresh)
        ),
        "status": "ready" if ready else "not_ready",
        "authority": runtime_status["authority"],
        "storage_mode": runtime_status["storage_mode"],
        "canonical": runtime_status["canonical"],
        "connection_state": runtime_status["connection_state"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "vvault-backend",
        "runtime": runtime_status["runtime"],
        "body_database": runtime_status["body_database"],
        "storage": runtime_status["storage"],
        "auth": runtime_status["auth"],
        "storage_owner": door.get("storage_owner"),
        "transcript_owner": door.get("transcript_owner"),
        "transcript_compatibility_owner": door.get("transcript_compatibility_owner"),
        "door_contract": door,
        "projectionWarm": bool(projection_warm.get("ready")),
        "projection_warm": projection_warm,
        "chatty_capabilities": projection_warm,
        "authMode": door.get("auth_mode"),
        "serviceReadiness": {
            "ready": bool(runtime_status["ready"] and door.get("ok")),
            "authority": "ovvaults",
        },
        "trustReadiness": {
            "ready": trust_ready,
            "authMode": door.get("auth_mode"),
            "acceptedKeyCount": assertion_key_count,
            "serviceCredentialConfigured": bool(
                _configured_service_token()
            ),
            "serviceCredentialAccepted": service_credential_accepted,
        },
    }), 200 if ready else 503

def _current_vvault_user_id() -> tuple[str | None, tuple[Any, int] | None]:
    user_id = _get_authenticated_user_id()
    if not user_id:
        return None, (jsonify({"success": False, "error": "Authentication required"}), 401)
    return user_id, None


def _code_project_repository() -> CodeProjectRepository:
    return CodeProjectRepository()


@app.route('/api/code/handshake')
def code_vvault_handshake():
    """Return the Code-to-VVAULT OVVAULTS authority contract as JSON."""
    door = _resolve_chatty_vvault_door()
    body_database = _body_database_dependency_status()
    success = bool(body_database.get("ready")) and door.get("ok") is True
    payload = {
        "success": success,
        "service": "vvault",
        "client": "code",
        "authority": "vvault_body",
        "canonical": True,
        "storage_mode": "vvault_body",
        "code_origin": door.get("code_origin"),
        "code_api_origin": door.get("code_api_origin"),
        "vvault_origin": door.get("vvault_origin"),
        "storage_owner": "ovvaults.vault_files",
        "transcript_owner": "ovvaults.transcripts",
        "transcript_compatibility_owner": "ovvaults.vault_files",
        "runtime_memory_authority": "vvault_body",
        "database_authority": "vvault_body",
        "body_database": body_database,
        "door_contract": door,
        "timestamp": datetime.now().isoformat(),
    }
    if success:
        return jsonify(payload)
    payload["error"] = body_database.get("error") or "code_vvault_handshake_not_ready"
    if door.get("ok") is not True:
        payload["error"] = "door_contract_not_ready"
        payload["problems"] = door.get("problems") or []
    return jsonify(payload), 503


@app.route('/api/code/projects', methods=['GET'])
@require_auth
def list_code_projects():
    """Return signed-in user's VVAULT-owned Code project index."""
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    try:
        repo = _code_project_repository()
        return jsonify({
            "success": True,
            "canonical": True,
            "authority": "vvault_body",
            "storage_owner": "ovvaults.vault_files",
            "transcript_owner": "ovvaults.transcripts",
            "projects": repo.list_projects(user_id=user_id),
        })
    except Exception as exc:
        logger.error(f"Error listing Code projects: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/code/projects', methods=['POST'])
@require_auth
def upsert_code_project():
    """Create or update one VVAULT-owned Code project by projectInstanceId."""
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    try:
        payload = request.get_json(silent=True) or {}
        project = payload.get("project") if isinstance(payload.get("project"), dict) else payload
        saved = _code_project_repository().upsert_project(user_id=user_id, project=project)
        return jsonify({
            "success": True,
            "canonical": True,
            "storage_owner": "ovvaults.vault_files",
            "project": saved,
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error upserting Code project: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/code/projects/migrate', methods=['POST'])
@require_auth
def migrate_code_projects():
    """Idempotently import recovered Code cache records into VVAULT."""
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    try:
        payload = request.get_json(silent=True) or {}
        raw_projects = payload.get("projects")
        if not isinstance(raw_projects, list):
            return jsonify({"success": False, "error": "projects array is required"}), 400
        repo = _code_project_repository()
        migrated = []
        file_count = 0
        for raw_project in raw_projects:
            if not isinstance(raw_project, dict):
                continue
            saved = repo.upsert_project(user_id=user_id, project=raw_project)
            migrated.append(saved)
            files = raw_project.get("files")
            if not isinstance(files, list):
                continue
            project_instance_id = saved.get("projectInstanceId")
            for file_record in files:
                if not isinstance(file_record, dict):
                    continue
                relative_path = file_record.get("relativePath") or file_record.get("path")
                content = file_record.get("content")
                if not isinstance(relative_path, str) or not isinstance(content, str):
                    continue
                if is_internal_code_project_path(relative_path):
                    continue
                repo.upsert_file(
                    user_id=user_id,
                    project_instance_id=project_instance_id,
                    relative_path=relative_path,
                    content=content,
                    content_type=file_record.get("contentType") or "text/plain",
                )
                file_count += 1
        return jsonify({
            "success": True,
            "canonical": True,
            "storage_owner": "ovvaults.vault_files",
            "migrated": migrated,
            "project_count": len(migrated),
            "file_count": file_count,
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error migrating Code projects: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/code/projects/<project_instance_id>', methods=['GET'])
@require_auth
def get_code_project(project_instance_id):
    """Return one VVAULT-owned Code project plus file and transcript links."""
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    try:
        repo = _code_project_repository()
        project = repo.get_project(user_id=user_id, project_instance_id=project_instance_id)
        if not project:
            return jsonify({"success": False, "error": "Project not found"}), 404
        return jsonify({
            "success": True,
            "canonical": True,
            "storage_owner": "ovvaults.vault_files",
            "transcript_owner": "ovvaults.transcripts",
            "project": project,
            "files": repo.list_files(user_id=user_id, project_instance_id=project_instance_id),
            "transcripts": repo.list_transcript_links(
                user_id=user_id, project_instance_id=project_instance_id
            ),
        })
    except Exception as exc:
        logger.error(f"Error getting Code project: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/code/projects/<project_instance_id>/files', methods=['GET'])
@require_auth
def list_code_project_files(project_instance_id):
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    try:
        return jsonify({
            "success": True,
            "canonical": True,
            "storage_owner": "ovvaults.vault_files",
            "files": _code_project_repository().list_files(user_id=user_id, project_instance_id=project_instance_id),
        })
    except Exception as exc:
        logger.error(f"Error listing Code project files: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/code/projects/<project_instance_id>/file', methods=['GET', 'PUT', 'DELETE'])
@require_auth
def code_project_file(project_instance_id):
    user_id, error = _current_vvault_user_id()
    if error:
        return error
    repo = _code_project_repository()
    try:
        if request.method == 'GET':
            relative_path = request.args.get("path", "").strip()
            if not relative_path:
                return jsonify({"success": False, "error": "path is required"}), 400
            file_record = repo.read_file(user_id=user_id, project_instance_id=project_instance_id, relative_path=relative_path)
            if not file_record:
                return jsonify({"success": False, "error": "File not found"}), 404
            return jsonify({"success": True, "canonical": True, "file": file_record})

        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            relative_path = payload.get("path") or payload.get("relativePath")
            content = payload.get("content")
            if not isinstance(relative_path, str) or not isinstance(content, str):
                return jsonify({"success": False, "error": "path and content are required"}), 400
            file_record = repo.upsert_file(
                user_id=user_id,
                project_instance_id=project_instance_id,
                relative_path=relative_path,
                content=content,
                content_type=payload.get("contentType") or "text/plain",
            )
            return jsonify({"success": True, "canonical": True, "file": file_record})

        relative_path = request.args.get("path", "").strip()
        if not relative_path:
            return jsonify({"success": False, "error": "path is required"}), 400
        deleted = repo.delete_file(user_id=user_id, project_instance_id=project_instance_id, relative_path=relative_path)
        return jsonify({"success": True, "canonical": True, "deleted": deleted})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error handling Code project file: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500



USER_PATH_PATTERN = re.compile(r'^vvault/users/shard_\d+/[^/]+/')

def _get_user_base_path(user_id: int, user_email: str) -> str:
    """Get the canonical base path for a user's vault files.
    
    Returns: vvault/users/shard_0000/{user_slug}/
    
    The user_slug is derived from email: devon_woodson_{user_id} pattern
    For now, we use a simple pattern; future: store slug in users table.
    """
    email_prefix = user_email.split('@')[0].replace('.', '_').replace('-', '_')
    user_slug = f"{email_prefix}_{user_id}"
    return f"vvault/users/shard_0000/{user_slug}/"

def _create_default_user_folders(user_id: int, user_email: str) -> bool:
    """Create default folder structure for a new user.
    
    Creates:
      - account/profile.json
      - instances/ (empty marker)
      - library/documents/ (empty marker)
      - library/media/ (empty marker)
    
    Returns True if successful, False otherwise.
    """
    try:
        base_path = _get_user_base_path(user_id, user_email)
        user_name = user_email.split('@')[0].replace('.', ' ').title()
        local_user = AUTH_REPOSITORY.get_user_by_email(user_email)
        if local_user and local_user.get("name"):
            user_name = local_user["name"]

        # Default profile content
        profile_content = json.dumps({
            "name": user_name,
            "email": user_email,
            "created_at": datetime.now().isoformat(),
            "preferences": {
                "theme": "dark",
                "timezone": "EST"
            }
        }, indent=2)
        
        default_folders = [
            {
                'filename': f"{base_path}account/profile.json",
                'file_type': 'application/json',
                'content': profile_content,
                'user_id': user_id,
                'is_system': False,
                'metadata': json.dumps({'type': 'user_profile'})
            },
            {
                'filename': f"{base_path}instances/.keep",
                'file_type': 'text/plain',
                'content': '',
                'user_id': user_id,
                'is_system': False,
                'metadata': json.dumps({'type': 'folder_marker'})
            },
            {
                'filename': f"{base_path}library/documents/.keep",
                'file_type': 'text/plain',
                'content': '',
                'user_id': user_id,
                'is_system': False,
                'metadata': json.dumps({'type': 'folder_marker'})
            },
            {
                'filename': f"{base_path}library/media/.keep",
                'file_type': 'text/plain',
                'content': '',
                'user_id': user_id,
                'is_system': False,
                'metadata': json.dumps({'type': 'folder_marker'})
            }
        ]
        
        for folder in default_folders:
            try:
                _upsert_vault_file_record(folder, context='default_user_folders')
            except Exception as e:
                logger.warning(f"Error creating folder {folder['filename']}: {e}")
        
        logger.info(f"Created default folders for user {user_id} at {base_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating default folders for user {user_id}: {e}")
        return False

def _get_user_construct_path(user_id: int, user_email: str, construct_id: str, subfolder: str = '') -> str:
    """Get the path for a construct's files under a user's vault.
    
    Args:
        user_id: The user's database ID
        user_email: The user's email address
        construct_id: The construct ID (e.g., 'katana-001')
        subfolder: Optional subfolder within the construct (e.g., 'chatgpt', 'tests')
    
    Returns: Full path like vvault/users/shard_0000/devon_woodson_1/instances/katana-001/chatgpt/
    """
    base = _get_user_base_path(user_id, user_email)
    path = f"{base}instances/{construct_id}/"
    if subfolder:
        path += f"{subfolder}/"
    return path

def _slugify_hydro_project_name(project_name: str) -> str:
    slug = re.sub(r'[^A-Za-z0-9]+', '_', (project_name or '').strip())
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug or 'workspace'

def _infer_project_name_from_root_path(root_path: Optional[str]) -> Optional[str]:
    if not root_path:
        return None
    trimmed = str(root_path).strip().rstrip('/')
    if not trimmed:
        return None
    basename = os.path.basename(trimmed)
    if basename in ('', '.', '/'):
        return None
    return basename

def _resolve_hydro_project_name(project_name: Optional[str] = None, root_path: Optional[str] = None) -> Optional[str]:
    if project_name and str(project_name).strip():
        return str(project_name).strip()
    return _infer_project_name_from_root_path(root_path)

def _resolve_chatty_transcript_target(
    construct_id: str,
    *,
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    project_name: Optional[str] = None,
    root_path: Optional[str] = None,
) -> Dict[str, Any]:
    callsign = _normalize_callsign(construct_id)
    resolved_project_name = _resolve_hydro_project_name(project_name, root_path)
    is_hydro_project_thread = callsign == 'hydro-001' and bool(resolved_project_name)

    if is_hydro_project_thread:
        project_slug = _slugify_hydro_project_name(str(resolved_project_name))
        folder = 'code'
        filename = f'{project_slug}_hydro_chat.md'
        title = f'Hydro Ask - {resolved_project_name}'
        thread_id = f'{callsign}_{project_slug}_hydro_chat'
    else:
        project_slug = None
        folder = 'chatty'
        filename = f'chat_with_{callsign}.md'
        title = f'Chat with {callsign.split("-")[0].title()}'
        thread_id = f'{callsign}_chat_with_{callsign}'

    if user_id and user_email:
        storage_path = f'{_get_user_construct_path(user_id, user_email, callsign, folder)}{filename}'
    else:
        storage_path = f'instances/{callsign}/{folder}/{filename}'

    return {
        'construct_id': callsign,
        'filename': filename,
        'storage_path': storage_path,
        'folder': folder,
        'project_name': resolved_project_name,
        'project_slug': project_slug,
        'title': title,
        'thread_id': thread_id,
        'is_hydro_project_thread': is_hydro_project_thread,
    }

def _find_chatty_transcript_rows(
    *,
    target: Dict[str, Any],
    user_id: Optional[int],
    columns: str,
):
    del columns
    rows = VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=target['construct_id'],
        bare_name=_bare_name_from_callsign(target['construct_id']),
        user_id=str(user_id) if user_id else None,
        include_content=True,
    )
    exact = [
        row for row in rows
        if (row.get('filename') == target['storage_path'] or row.get('storage_path') == target['storage_path'])
    ]
    if exact:
        return exact
    fallback = [
        row for row in rows
        if str(row.get('filename') or row.get('storage_path') or '').endswith(target['filename'])
    ]
    if not fallback:
        return []
    if target.get('is_hydro_project_thread'):
        suffix = f"/{target['folder']}/{target['filename']}"
        return [
            row for row in fallback
            if str(row.get('filename') or row.get('storage_path') or '').endswith(suffix)
        ]
    return fallback

def _strip_user_prefix(path: str) -> str:
    """Strip any internal user path prefix (vvault/users/shard_XXXX/user_slug/) for display.
    
    This uses a regex pattern to match any user path prefix, regardless of the exact slug format.
    Examples:
      - vvault/users/shard_0000/devon_woodson_123/instances/... -> instances/...
      - vvault/users/shard_0000/abc-def-uuid/library/... -> library/...
      - instances/katana-001/chatgpt/... -> instances/katana-001/chatgpt/... (unchanged)
    """
    match = USER_PATH_PATTERN.match(path)
    if match:
        return path[match.end():]
    
    if path.startswith('vvault/'):
        parts = path.split('/')
        if len(parts) >= 4 and parts[1] == 'users':
            return '/'.join(parts[4:]) if len(parts) > 4 else ''
    
    return path

def map_to_vsi_folder(filename: str, construct_id: str = '', metadata: dict = None) -> str:
    """Map a file to its correct VSI folder path based on name, construct, and metadata.
    
    Returns the full relative path like instances/{construct}/identity/prompt.json
    """
    if not metadata:
        metadata = {}
    ext = os.path.splitext(filename)[1].lower()
    base = os.path.basename(filename)
    folder = metadata.get('folder', '')
    
    IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'}
    DOC_EXTS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt'}
    IDENTITY_FILES = {
        'prompt.txt', 'prompt.json', 'conditioning.txt', 'definition.txt',
        'physical_features.json', 'voice.json', 'avatar.png', 'avatar.jpeg', 'avatar.jpg'
    }
    CONFIG_FILES = {'glyph.png', 'metadata.json', 'tone_profile.json', 'voice.md'}
    LOG_NAMES = {'chat.log', 'capsule.log', 'server.log', 'identity_guard.log', 'independence.log',
                 'ltm.log', 'stm.log', 'cns.log', 'watchdog.log', 'self_improvement_agent.log'}
    
    if construct_id:
        if folder:
            return f'instances/{construct_id}/{folder}/{base}'
        if base.endswith('.capsule'):
            return f'instances/{construct_id}/memup/{base}'
        if 'character.ai' in base.lower() or 'character_ai' in base.lower():
            return f'instances/{construct_id}/character.ai/{base}'
        if base.endswith('-K1.md') or base.startswith('test_') or base == 'CONTINUITY_GPT_PROMPT.md':
            return f'instances/{construct_id}/chatgpt/{base}'
        if base.startswith('chat_with_'):
            return f'instances/{construct_id}/chatty/{base}'
        if base in IDENTITY_FILES:
            return f'instances/{construct_id}/identity/{base}'
        if base in CONFIG_FILES:
            return f'instances/{construct_id}/config/{base}'
        if base in LOG_NAMES or base.startswith('drift-log'):
            return f'instances/{construct_id}/logs/{base}'
        if base.endswith('-enforcement.json'):
            return f'instances/{construct_id}/config/{base}'
        if base == 'memory.json':
            return f'instances/{construct_id}/memup/{base}'
        SIMDRIVE_PATTERNS = {'blueprint', 'overlay', 'hook', 'injection', 'cognitive_model', 'behavior_template'}
        if any(pat in base.lower() for pat in SIMDRIVE_PATTERNS):
            return f'instances/{construct_id}/simDrive/{base}'
        if ext in IMAGE_EXTS:
            return f'instances/{construct_id}/assets/{base}'
        if ext in DOC_EXTS:
            return f'instances/{construct_id}/documents/{base}'
        return f'instances/{construct_id}/documents/{base}'
    
    if base == 'profile.json':
        return f'account/{base}'
    meta_type = metadata.get('type', '')
    if meta_type == 'user_glyph':
        return f'account/{base}'
    if ext in IMAGE_EXTS:
        return f'library/assets/{base}'
    if ext in DOC_EXTS:
        return f'library/documents/{base}'
    if ext in {'.md', '.txt'}:
        return f'library/documents/{base}'
    return f'library/{base}'


def _transform_files_for_display(files: list, is_admin: bool = False, user_id: str = None) -> list:
    """Transform vault_files records for the file browser UI.
    
    Uses filename as the canonical display path (files now store full VSI paths).
    Falls back to building paths from construct_id + metadata.folder if filename is bare.
    """
    import re
    VVAULT_PREFIX = re.compile(r'^vvault/users/shard_\d+/[^/]+/')
    
    transformed = []
    for f in _dedupe_vault_rows(files):
        if row_is_projection_excluded(f):
            continue
        if f.get('is_system') and not is_admin:
            continue
        
        file_copy = dict(f)
        filename = f.get('filename') or 'unknown'
        construct_id = f.get('construct_id') or ''
        storage_path = f.get('storage_path') or ''
        
        metadata = file_copy.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        file_copy['metadata'] = metadata
        
        display_path = filename
        display_path = VVAULT_PREFIX.sub('', display_path)
        
        if '/' not in display_path:
            display_path = map_to_vsi_folder(display_path, construct_id, metadata)
        
        file_copy['display_path'] = display_path
        file_copy['storage_path'] = storage_path or display_path
        file_copy['internal_path'] = storage_path or display_path

        # Promote useful metadata for UI
        file_copy['display_name'] = display_path.split('/')[-1]
        file_copy['display_construct'] = construct_id or metadata.get('construct_id') or '-'
        file_copy['display_size'] = metadata.get('size')

        # Date preference: updated_at > metadata.last_synced_at > metadata.migrated_at > created_at
        file_copy['display_date'] = (
            file_copy.get('updated_at')
            or metadata.get('last_synced_at')
            or metadata.get('migrated_at')
            or file_copy.get('created_at')
        )
        
        transformed.append(file_copy)
    return transformed


def _filter_transformed_vault_files_for_path(files: List[Dict[str, Any]], requested_path: str) -> List[Dict[str, Any]]:
    normalized_path = str(requested_path or "").strip().strip("/")
    if not normalized_path:
        return list(files or [])

    prefix = f"{normalized_path}/"
    filtered: List[Dict[str, Any]] = []
    for file_row in files or []:
        display_path = str(file_row.get('display_path') or file_row.get('storage_path') or file_row.get('filename') or '').strip().strip("/")
        if display_path == normalized_path or display_path.startswith(prefix):
            filtered.append(file_row)
    return filtered


@app.route("/api/vault/session-bridge", methods=["POST", "OPTIONS"])
def session_bridge_from_standalone_auth():
    """Permanently retired legacy bridge."""
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify({
        "success": False,
        "error": "VVAULT session bridge is disabled; use VVAULT-native enrollment",
        "errorCode": "VVAULT_NATIVE_SESSION_REQUIRED",
    }), 403


@app.route('/api/vault/user-info')
@require_auth
def get_vault_user_info():
    """Get current user's VVAULT-native vault info."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        if not user_email:
            return jsonify({"success": False, "error": "Invalid session"}), 401
        display_name = current_user.get('name') or user_email.split('@')[0].replace('.', ' ').title()
        user_id = str(current_user.get('id') or "")
        role = str(current_user.get('role') or 'user')
        is_admin = role == 'admin'
        
        return jsonify({
            "success": True,
            "vvault_available": True,
            "degraded": False,
            "canonical": True,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
            "auth_owner": AUTH_OWNER,
            "session_owner": SESSION_OWNER,
            "display_name": display_name,
            "user_id": user_id,
            "email": user_email,
            "role": role,
            "is_admin": is_admin,
            "root_label": display_name if not is_admin else "Vault (Admin)"
        })
    except Exception as e:
        logger.error(f"Error getting user info: {type(e).__name__}")
        if _is_dependency_timeout(e):
            current_user = getattr(request, 'current_user', None) or {}
            user_email = current_user.get('email', '')
            user_role = current_user.get('role', 'user')
            display_name = user_email.split('@')[0].replace('.', ' ').title() if user_email else "Vault User"
            return _dependency_timeout_read_response(
                "/api/vault/user-info",
                extra={
                    "display_name": display_name,
                    "user_id": "",
                    "is_admin": user_role == 'admin',
                    "root_label": display_name if user_role != 'admin' else "Vault (Admin)",
                },
            )
        return jsonify({
            "success": False,
            "error": "VVAULT auth database is unavailable",
            "error_code": type(e).__name__,
            "auth_owner": AUTH_OWNER,
            "session_owner": SESSION_OWNER,
        }), 503

@app.route('/api/v1/contracts/life.vvault.data-layout/1.0.0')
@require_chatty_auth
def get_canonical_data_contract_v1():
    """Return the normative machine-readable VVAULT data contract."""
    return jsonify(canonical_data_contract.load_registry())


@app.route('/api/v1/artifacts/<path:artifact_id>')
@require_chatty_auth
def get_canonical_artifact_v1(artifact_id: str):
    instance_id = (request.args.get("instance_id") or "").strip()
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    payload, status = canonical_data_contract.resolve_artifact(
        artifact_id=artifact_id,
        instance_id=instance_id,
        owner_user_id=owner_user_id,
    )
    return jsonify(payload), status


@app.route('/api/v1/instances/<instance_id>/manifest')
@require_chatty_auth
def get_canonical_instance_manifest_v1(instance_id: str):
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    payload, status = canonical_data_contract.resolve_manifest(
        instance_id=instance_id,
        owner_user_id=owner_user_id,
    )
    return jsonify(payload), status


@app.route('/api/v1/migrations/canonical-layout/dry-run', methods=['POST'])
@require_chatty_auth
def dry_run_canonical_layout_migration_v1():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    rows = canonical_data_contract.authenticated_rows(owner_user_id)
    report = canonical_data_contract.audit_rows(rows)
    plan = canonical_data_contract.plan_migration(report, rows)
    return jsonify({
        "success": True,
        "canonical": True,
        "authority": "vvault_body",
        "storage_owner": "ovvaults.vault_files",
        "report": report,
        "migration_plan": plan,
    })


@app.route('/api/v1/migrations/canonical-layout/apply', methods=['POST'])
@require_role('admin')
def apply_canonical_layout_migration_v1():
    payload = request.get_json(silent=True) or {}
    operation_ids = {
        str(value)
        for value in payload.get("operation_ids", [])
        if str(value).strip()
    }
    if payload.get("handler") != "Devon" or payload.get("confirm") is not True:
        return jsonify({
            "success": False,
            "error": "explicit Devon handler confirmation is required",
            "error_code": "VVAULT_HANDLER_CONFIRMATION_REQUIRED",
        }), 403
    rows = canonical_data_contract.administrative_rows()
    report = canonical_data_contract.audit_rows(rows)
    plan = canonical_data_contract.plan_migration(report, rows)
    if plan["collision_count"]:
        return jsonify({
            "success": False,
            "error": "migration apply is blocked by unresolved canonical collisions",
            "error_code": "VVAULT_CANONICAL_COLLISIONS_UNRESOLVED",
            "collision_count": plan["collision_count"],
            "quarantine": plan["quarantine"],
        }), 409
    selected = [
        operation
        for operation in plan["operations"]
        if operation["plan_sha256"] in operation_ids
    ]
    if len(selected) != len(operation_ids):
        return jsonify({
            "success": False,
            "error": "one or more operation IDs are absent from the current authenticated dry-run",
            "error_code": "VVAULT_MIGRATION_PLAN_STALE",
        }), 409
    service = canonical_data_contract.CanonicalMigrationService()
    receipts = [
        service.apply_operation(operation, actor="Devon")
        for operation in selected
    ]
    return jsonify({
        "success": True,
        "migration_id": plan["migration_id"],
        "receipt_count": len(receipts),
        "receipts": receipts,
    })


@app.route('/api/v1/migrations/<migration_id>/receipts')
@require_role('admin')
def get_canonical_layout_migration_receipts_v1(migration_id: str):
    receipts = chatty_body_service._rows(
        """
        SELECT operation_id, migration_id, contract_version, actor,
               source_record_id::text AS source_record_id,
               destination_record_id::text AS destination_record_id,
               owner_user_id::text AS owner_user_id,
               instance_id, artifact_id, before_path, after_path,
               before_sha256, after_sha256, before_schema_version,
               after_schema_version, result, applied_at, rolled_back_at,
               rolled_back_by, rollback_receipt, receipt
        FROM ovvaults.canonical_artifact_migration_receipts
        WHERE migration_id = %s
        ORDER BY applied_at, operation_id
        """,
        (migration_id,),
    )
    return jsonify({
        "success": True,
        "migration_id": migration_id,
        "receipt_count": len(receipts),
        "receipts": receipts,
    })


@app.route('/api/vault/files')
@require_auth
def get_vault_files():
    """Get vault files from local VVAULT body storage."""
    route_started_at = time.perf_counter()
    user_lookup_ms = 0
    row_fetch_ms = 0
    transform_ms = 0
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        if not user_email:
            return jsonify({"success": False, "error": "Invalid session"}), 401
        requested_path = (request.args.get('path') or '').strip().strip('/')

        user_lookup_started_at = time.perf_counter()
        user_id = _get_authenticated_user_id()
        user_lookup_ms = int(round((time.perf_counter() - user_lookup_started_at) * 1000))
        user_name = current_user.get('name') or user_email.split('@')[0]

        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        row_fetch_started_at = time.perf_counter()
        rows = VAULT_FILE_REPOSITORY.list_for_browser(
            user_id=user_id,
            is_admin=False,
            requested_path=requested_path,
        )
        row_fetch_ms = int(round((time.perf_counter() - row_fetch_started_at) * 1000))

        transform_started_at = time.perf_counter()
        files = _transform_files_for_display(rows, is_admin=False, user_id=user_id)
        if requested_path:
            files = _filter_transformed_vault_files_for_path(files, requested_path)
        transform_ms = int(round((time.perf_counter() - transform_started_at) * 1000))

        logger.info(
            "VAULT_FILES_LIST path=%s mode=%s admin=%s user_lookup_ms=%s row_fetch_ms=%s transform_ms=%s row_count=%s file_count=%s route_elapsed_ms=%s",
            requested_path or "ALL_FILES",
            "scoped" if requested_path else "all_files",
            False,
            user_lookup_ms,
            row_fetch_ms,
            transform_ms,
            len(rows),
            len(files),
            int(round((time.perf_counter() - route_started_at) * 1000)),
        )
        
        return jsonify({
            "success": True,
            "degraded": False,
            "canonical": True,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
            "files": files,
            "count": len(files),
            "user_root": user_name
        })
    except Exception as e:
        logger.error(f"Error fetching vault files: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to load vault files",
            "error_code": type(e).__name__,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        }), 503


def _drive_request_owner_and_construct(payload: Optional[Dict[str, Any]] = None):
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        raise PermissionError("User not found")
    source = payload or {}
    construct_id = _normalize_callsign(
        source.get("constructId")
        or source.get("construct_id")
        or request.args.get("constructId")
        or request.args.get("construct_id")
        or ""
    )
    if not construct_id:
        raise ValueError("constructId is required")
    if not VAULT_FILE_REPOSITORY.construct_is_projectable(
        user_id=owner_user_id, callsign=construct_id
    ):
        raise LookupError("construct not found")
    return owner_user_id, construct_id


def _drive_error_response(exc: Exception):
    if isinstance(exc, PermissionError):
        return jsonify({"success": False, "error": str(exc), "error_code": "VVAULT_DRIVE_FORBIDDEN"}), 403
    if isinstance(exc, LookupError):
        return jsonify({"success": False, "error": str(exc), "error_code": "VVAULT_DRIVE_NODE_NOT_FOUND"}), 404
    if isinstance(exc, ValueError):
        return jsonify({"success": False, "error": str(exc), "error_code": "VVAULT_DRIVE_INVALID_REQUEST"}), 400
    logger.exception("VVAULT_DRIVE request failed")
    return jsonify({"success": False, "error": "VVAULT Drive operation failed", "error_code": type(exc).__name__}), 503


@app.route('/api/vault/drive/children')
@require_auth
def get_vault_drive_children():
    try:
        owner_user_id, construct_id = _drive_request_owner_and_construct()
        parent_node_id = (request.args.get("parentNodeId") or "root").strip()
        result = VAULT_DRIVE_REPOSITORY.children(
            owner_user_id=owner_user_id,
            construct_id=construct_id,
            parent_node_id=parent_node_id,
        )
        return jsonify({
            "success": True,
            "canonical": True,
            "constructId": construct_id,
            **result,
        })
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/workspace-root')
@require_auth
def get_vault_drive_workspace_root():
    """Return the authenticated account's canonical VVAULT workspace root."""
    try:
        owner_user_id = _get_authenticated_user_id()
        if not owner_user_id:
            raise PermissionError("User not found")
        construct_result = chatty_body_service.list_constructs(owner_user_id)
        if construct_result.http_status != 200:
            return jsonify({
                "success": False,
                "error": "Canonical construct projection is unavailable",
                "error_code": "VVAULT_WORKSPACE_ROOT_UNAVAILABLE",
            }), construct_result.http_status
        constructs = list(construct_result.payload.get("constructs") or [])
        projection = VAULT_DRIVE_REPOSITORY.workspace_root(
            owner_user_id=owner_user_id,
            constructs=constructs,
        )
        return jsonify({
            "success": True,
            "canonical": True,
            "scope": "owner_workspace",
            "ownerIdentifiersProjected": False,
            **projection,
        })
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/folders', methods=['POST'])
@require_auth
def create_vault_drive_folder():
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.create_folder(
            owner_user_id=owner_user_id,
            construct_id=construct_id,
            parent_node_id=str(payload.get("parentNodeId") or "root"),
            name=payload.get("name"),
        )
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result}), 201
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/nodes/<node_id>', methods=['PATCH'])
@require_auth
def mutate_vault_drive_folder(node_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        if "name" not in payload and "parentNodeId" not in payload:
            raise ValueError("name or parentNodeId is required")
        result = VAULT_DRIVE_REPOSITORY.mutate_node(
            owner_user_id=owner_user_id,
            construct_id=construct_id,
            node_id=node_id,
            name=payload.get("name") if "name" in payload else None,
            parent_node_id=str(payload.get("parentNodeId")) if "parentNodeId" in payload else None,
        )
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/nodes/<node_id>', methods=['DELETE'])
@require_auth
def trash_vault_drive_folder(node_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.set_node_trashed(
            owner_user_id=owner_user_id, construct_id=construct_id,
            node_id=node_id, restore=False,
        )
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/nodes/<node_id>/restore', methods=['POST'])
@require_auth
def restore_vault_drive_folder(node_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.set_node_trashed(
            owner_user_id=owner_user_id, construct_id=construct_id,
            node_id=node_id, restore=True,
        )
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/trash')
@require_auth
def get_vault_drive_trash():
    try:
        owner_user_id = _get_authenticated_user_id()
        if not owner_user_id:
            raise PermissionError("User not found")
        construct_id = _normalize_callsign(request.args.get("constructId") or "") or None
        return jsonify({"success": True, "canonical": True, **VAULT_DRIVE_REPOSITORY.trash(owner_user_id=owner_user_id, construct_id=construct_id)})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/batch/move', methods=['POST'])
@require_auth
def move_vault_drive_nodes():
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.move_nodes_atomic(owner_user_id=owner_user_id, construct_id=construct_id, node_ids=payload.get("nodeIds") or [], parent_node_id=str(payload.get("parentNodeId") or "root"))
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/batch/trash', methods=['POST'])
@require_auth
def trash_vault_drive_nodes():
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.trash_nodes_atomic(owner_user_id=owner_user_id, construct_id=construct_id, node_ids=payload.get("nodeIds") or [])
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/batch/restore', methods=['POST'])
@require_auth
def restore_vault_drive_nodes():
    try:
        payload = request.get_json(silent=True) or {}
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.restore_nodes_atomic(owner_user_id=owner_user_id, construct_id=construct_id, node_ids=payload.get("nodeIds") or [])
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)


@app.route('/api/vault/drive/batch/permanent-delete', methods=['POST'])
@require_auth
def permanently_delete_vault_drive_nodes():
    try:
        payload = request.get_json(silent=True) or {}
        if payload.get("confirmation") != "PERMANENTLY DELETE":
            raise ValueError("explicit permanent-delete confirmation is required")
        owner_user_id, construct_id = _drive_request_owner_and_construct(payload)
        result = VAULT_DRIVE_REPOSITORY.permanently_delete_nodes(owner_user_id=owner_user_id, construct_id=construct_id, node_ids=payload.get("nodeIds") or [], empty_trash=bool(payload.get("emptyTrash")))
        chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
        return jsonify({"success": True, "canonical": True, "constructId": construct_id, **result})
    except Exception as exc:
        return _drive_error_response(exc)

@app.route('/api/vault/knowledge-files')
@require_chatty_auth
def get_knowledge_files():
    """Get knowledge files for a construct from VVAULT-native vault_files.
    Used by GPTCreator to list construct documents stored in VVAULT.
    Query params: construct_id (required)
    """
    try:
        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_id = _get_authenticated_user_id()

        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        knowledge_folders = ['documents', 'identity', 'config', 'chatty']
        knowledge_data = []
        for row in VAULT_FILE_REPOSITORY.list_knowledge_files(construct_id=construct_id, user_id=user_id):
            fname = row.get('filename', '')
            parts = fname.split('/')
            folder = parts[-2] if len(parts) >= 2 else ''
            if folder in knowledge_folders:
                knowledge_data.append(row)
        result_data = knowledge_data

        files = []
        for f in result_data:
            meta = f.get('metadata')
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except: meta = {}
            if not isinstance(meta, dict): meta = {}
            
            filename = f.get('filename', '')
            base = os.path.basename(filename)
            folder = meta.get('folder', '')
            if not folder and '/' in filename:
                parts = filename.split('/')
                if len(parts) >= 2:
                    folder = parts[-2]
            
            files.append({
                'id': f['id'],
                'filename': base,
                'path': filename,
                'folder': folder,
                'file_type': f.get('file_type', ''),
                'created_at': f.get('created_at', ''),
                'sha256': f.get('sha256', ''),
            })
        
        return jsonify({
            "success": True,
            "construct_id": construct_id,
            "files": files,
            "count": len(files)
        })
    except Exception as e:
        logger.error(f"Error fetching knowledge files for {request.args.get('construct_id')}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


KNOWLEDGE_SKIP_EXTS = {'.ds_store', '.thumbs.db', '.desktop.ini'}
# Knowledge assets and documents may include large PDFs and media references.
# Keep this limit distinct from transcript ingestion: raising one must never
# silently broaden the other upload contract.
KNOWLEDGE_MAX_SINGLE_FILE = 100 * 1024 * 1024
TRANSCRIPT_MAX_SINGLE_FILE = 50 * 1024 * 1024
KNOWLEDGE_ALLOWED_EXTS = {
    '.txt', '.md', '.pdf', '.doc', '.docx', '.json', '.csv',
    '.xlsx', '.xls', '.pptx', '.ppt', '.rtf', '.html', '.htm',
    '.xml', '.yaml', '.yml', '.log', '.capsule', '.py', '.js',
    '.ts', '.sh', '.cfg', '.ini', '.toml', '.png', '.jpg',
    '.jpeg', '.svg', '.gif', '.webp',
}

def _guess_file_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    type_map = {
        '.md': 'text/markdown', '.txt': 'text/plain', '.json': 'application/json',
        '.pdf': 'application/pdf', '.csv': 'text/csv', '.capsule': 'application/json',
        '.yaml': 'text/yaml', '.yml': 'text/yaml', '.log': 'text/plain',
    }
    return type_map.get(ext, 'application/octet-stream')

BINARY_EXTS = {'.pdf', '.doc', '.docx', '.xlsx', '.xls', '.pptx', '.ppt',
               '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.rtf', '.zip'}


def _max_single_upload_bytes(upload_kind: str) -> int:
    return (
        KNOWLEDGE_MAX_SINGLE_FILE
        if upload_kind == 'knowledge'
        else TRANSCRIPT_MAX_SINGLE_FILE
    )


def _upload_file_size_allowed(upload_kind: str, size_bytes: int) -> bool:
    return 0 <= size_bytes <= _max_single_upload_bytes(upload_kind)


def _read_file_content(raw_bytes, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in BINARY_EXTS:
        return base64.b64encode(raw_bytes).decode('ascii')
    try:
        return raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return base64.b64encode(raw_bytes).decode('ascii')


def _uploaded_vsi_path(
    *,
    callsign: str,
    basename: str,
    subfolder: str = '',
    upload_kind: str,
    knowledge_destination: str | None = None,
) -> str:
    safe_basename = os.path.basename(basename)
    if upload_kind == 'transcript' or subfolder:
        relative_path = f"{subfolder.strip('/')}/{safe_basename}" if subfolder else safe_basename
        return preserved_upload_path(
            callsign=callsign,
            relative_path=relative_path,
            upload_kind=upload_kind,
            knowledge_destination=knowledge_destination,
        )
    return preserved_upload_path(
        callsign=callsign,
        relative_path=safe_basename,
        upload_kind=upload_kind,
        knowledge_destination=knowledge_destination,
    )


def _safe_upload_relative_path(value: str) -> str:
    return safe_upload_relative_path(value)


@app.route('/api/vault/knowledge-files/upload', methods=['POST'])
@require_chatty_auth
def upload_knowledge_files():
    """Bulk upload knowledge files for a construct.

    Accepts multipart/form-data with:
      - construct_id (form field, required)
      - files (one or more file fields)
      - If a file is a .zip, it is extracted and each inner file is stored individually.

    Each file is routed to its VSI folder via map_to_vsi_folder() and inserted
    into local VVAULT vault_files. Existing files with the same path are updated
    (upsert by filename + construct_id + user_id).

    Returns summary with created/updated/skipped/failed counts.
    """
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        construct_id = (request.form.get('construct_id') or '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        callsign = _normalize_callsign(construct_id)
        destination_folder_id = (
            request.form.get('destinationFolderId')
            or request.form.get('destination_folder_id')
            or ''
        ).strip()
        upload_kind_contract = (request.form.get('upload_kind') or '').strip().lower()
        knowledge_destination = (
            request.form.get('knowledge_destination') or ''
        ).strip().lower()
        if upload_kind_contract in {'knowledge:assets', 'knowledge:documents'}:
            upload_kind, encoded_destination = upload_kind_contract.split(':', 1)
            if knowledge_destination and knowledge_destination != encoded_destination:
                return jsonify({
                    "success": False,
                    "error": "knowledge destination fields conflict",
                }), 400
            knowledge_destination = encoded_destination
        else:
            upload_kind = upload_kind_contract
        if not destination_folder_id and upload_kind not in {'knowledge', 'transcript'}:
            return jsonify({
                "success": False,
                "error": (
                    "upload_kind is required and must be transcript, "
                    "knowledge:assets, or knowledge:documents"
                ),
            }), 400
        if not destination_folder_id and upload_kind == 'knowledge' and knowledge_destination not in {'assets', 'documents'}:
            return jsonify({
                "success": False,
                "error": "knowledge_destination is required and must be assets or documents",
            }), 400
        if not destination_folder_id and upload_kind == 'transcript' and knowledge_destination:
            return jsonify({
                "success": False,
                "error": "knowledge_destination is not valid for transcript uploads",
            }), 400
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        drive_context = None
        if destination_folder_id:
            drive_context = VAULT_DRIVE_REPOSITORY.upload_context(
                owner_user_id=user_id,
                construct_id=callsign,
                destination_node_id=destination_folder_id,
            )
            derived_upload_kind = drive_context['uploadKind']
            derived_destination = drive_context.get('knowledgeDestination') or ''
            if upload_kind and upload_kind != derived_upload_kind:
                return jsonify({
                    "success": False,
                    "error": "upload_kind conflicts with the canonical destination folder",
                }), 400
            if knowledge_destination and knowledge_destination != derived_destination:
                return jsonify({
                    "success": False,
                    "error": "knowledge_destination conflicts with the canonical destination folder",
                }), 400
            upload_kind = derived_upload_kind
            knowledge_destination = derived_destination
        uploaded_files = request.files.getlist('files')
        relative_paths = request.form.getlist('relative_paths')
        if not uploaded_files or all(f.filename == '' for f in uploaded_files):
            return jsonify({"success": False, "error": "No files provided"}), 400

        file_entries = []
        for upload_index, upload in enumerate(uploaded_files):
            if not upload.filename:
                continue
            raw = upload.read()
            fname_lower = upload.filename.lower()

            if fname_lower.endswith('.zip'):
                try:
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    zip_entries = [i for i in zf.infolist() if not i.is_dir()]
                    for info in zip_entries:
                        try:
                            relative_path = _safe_upload_relative_path(info.filename)
                        except ValueError:
                            continue
                        basename = relative_path.rsplit('/', 1)[-1]
                        if not basename or basename.startswith('.'):
                            continue
                        ext = os.path.splitext(basename)[1].lower()
                        if ext in KNOWLEDGE_SKIP_EXTS or ext not in KNOWLEDGE_ALLOWED_EXTS:
                            continue
                        if not _upload_file_size_allowed(upload_kind, info.file_size):
                            continue
                        inner_bytes = zf.read(info.filename)
                        rel_dir = relative_path.rsplit('/', 1)[0] if '/' in relative_path else ''

                        file_entries.append({
                            'basename': basename,
                            'subfolder': rel_dir,
                            'original_name': relative_path,
                            'original_relative_path': relative_path,
                            'archive_name': upload.filename,
                            'content': _read_file_content(inner_bytes, basename),
                            'raw_sha256': hashlib.sha256(inner_bytes).hexdigest(),
                            'file_type': _guess_file_type(basename),
                            'size': info.file_size,
                        })
                    zf.close()
                except zipfile.BadZipFile:
                    return jsonify({"success": False, "error": f"Invalid zip file: {upload.filename}"}), 400
            else:
                supplied_relative_path = (
                    relative_paths[upload_index]
                    if upload_index < len(relative_paths)
                    else upload.filename
                )
                try:
                    relative_path = _safe_upload_relative_path(supplied_relative_path)
                except ValueError as exc:
                    return jsonify({
                        "success": False,
                        "error": f"Unsafe upload path for {upload.filename}: {exc}",
                    }), 400
                basename = relative_path.rsplit('/', 1)[-1]
                rel_dir = relative_path.rsplit('/', 1)[0] if '/' in relative_path else ''
                ext = os.path.splitext(basename)[1].lower()
                if ext in KNOWLEDGE_SKIP_EXTS:
                    continue
                if ext not in KNOWLEDGE_ALLOWED_EXTS:
                    continue
                if not _upload_file_size_allowed(upload_kind, len(raw)):
                    continue

                file_entries.append({
                    'basename': basename,
                    'subfolder': rel_dir,
                    'original_name': relative_path,
                    'original_relative_path': relative_path,
                    'archive_name': None,
                    'content': _read_file_content(raw, basename),
                    'raw_sha256': hashlib.sha256(raw).hexdigest(),
                    'file_type': _guess_file_type(basename),
                    'size': len(raw),
                })

        if not file_entries:
            return jsonify({"success": False, "error": "No valid files found in upload"}), 400

        now = datetime.now().isoformat()
        created = 0
        updated = 0
        skipped = 0
        failed = 0
        failed_files = []
        upload_receipts = []

        existing_map = {}
        for row in VAULT_FILE_REPOSITORY.list_knowledge_files(construct_id=callsign, user_id=user_id):
            existing_map[row['filename']] = row

        for entry in file_entries:
            try:
                rel_dir = entry.get('subfolder', '')
                parent_folder = None
                if destination_folder_id:
                    parent_folder = VAULT_DRIVE_REPOSITORY.ensure_upload_path(
                        owner_user_id=user_id,
                        construct_id=callsign,
                        destination_node_id=destination_folder_id,
                        relative_directory=rel_dir,
                    )
                    vsi_path = f"{parent_folder['logical_path']}/{entry['basename']}"
                else:
                    vsi_path = _uploaded_vsi_path(
                        callsign=callsign,
                        basename=entry["basename"],
                        subfolder=rel_dir,
                        upload_kind=upload_kind,
                        knowledge_destination=knowledge_destination or None,
                    )
                    canonical_prefix = f"instances/{callsign}/"
                    canonical_relative = vsi_path[len(canonical_prefix):]
                    canonical_directory = canonical_relative.rsplit('/', 1)[0]
                    parent_folder = VAULT_DRIVE_REPOSITORY.ensure_upload_path(
                        owner_user_id=user_id,
                        construct_id=callsign,
                        destination_node_id='root',
                        relative_directory=canonical_directory,
                    )

                sha = entry.get('raw_sha256', hashlib.sha256(
                    entry['content'].encode('utf-8') if isinstance(entry['content'], str) else entry['content']
                ).hexdigest())

                canonical_folder = vsi_path.split('/')[2] if len(vsi_path.split('/')) > 2 else ''
                authored_top_folder = rel_dir.split('/')[0] if rel_dir else ''
                artifact_id = (
                    "life.vvault.transcript.external"
                    if upload_kind == "transcript"
                    else "life.vvault.knowledge.asset"
                    if canonical_folder == "assets"
                    else "life.vvault.knowledge.document"
                )
                meta_json = json.dumps({
                    'folder': canonical_folder,
                    'authoredTopFolder': authored_top_folder,
                    'artifactId': artifact_id,
                    'artifactClass': 'transcript' if upload_kind == 'transcript' else 'knowledge',
                    'constructId': callsign,
                    'originalName': entry.get('original_name') or entry['basename'],
                    'originalRelativePath': entry.get('original_relative_path') or entry['basename'],
                    'archiveName': entry.get('archive_name'),
                    'uploadKind': upload_kind,
                    'original_size': entry['size'],
                    'upload_batch': now,
                })

                record = {
                    'filename': vsi_path,
                    'storage_path': vsi_path,
                    'file_type': 'transcript' if upload_kind == 'transcript' else entry['file_type'],
                    'content': entry['content'],
                    'construct_id': callsign,
                    'user_id': user_id,
                    'is_system': False,
                    'sha256': sha,
                    'metadata': meta_json,
                    'updated_at': now,
                }
                if vsi_path in existing_map:
                    existing = existing_map[vsi_path]
                    if str(existing.get('sha256') or '') == sha:
                        skipped += 1
                        if not existing.get('drive_parent_node_id') and existing.get('id'):
                            VAULT_DRIVE_REPOSITORY.bind_file(
                                owner_user_id=user_id,
                                construct_id=callsign,
                                file_id=str(existing['id']),
                                parent_node_id=str(parent_folder['id']),
                            )
                        if destination_folder_id:
                            upload_receipts.append({
                                'fileId': str(existing.get('id')),
                                'nodeId': str(existing.get('id')),
                                'fileName': entry['basename'],
                                'path': vsi_path,
                                'parentNodeId': str(parent_folder['id']),
                                'sha256': sha,
                                'status': 'skipped',
                            })
                        continue
                    failed += 1
                    failed_files.append({
                        'file': entry['basename'],
                        'error': 'A different file already uses this exact preserved filename',
                    })
                    continue
                else:
                    record['created_at'] = now
                    upsert_result = _upsert_vault_file_record(record, context='knowledge_upload')
                    file_id = str(upsert_result.get('id'))
                    VAULT_DRIVE_REPOSITORY.bind_file(
                        owner_user_id=user_id,
                        construct_id=callsign,
                        file_id=file_id,
                        parent_node_id=str(parent_folder['id']),
                    )
                    if destination_folder_id:
                        upload_receipts.append({
                            'fileId': file_id,
                            'nodeId': file_id,
                            'fileName': entry['basename'],
                            'path': vsi_path,
                            'parentNodeId': str(parent_folder['id']),
                            'sha256': sha,
                            'status': 'created',
                        })
                    existing_map[vsi_path] = {'id': file_id, 'sha256': sha}
                    created += 1
            except Exception as fe:
                failed += 1
                failed_files.append({'file': entry['basename'], 'error': str(fe)})
                logger.error(f"KNOWLEDGE_UPLOAD: Failed to save {entry['basename']}: {fe}")

        logger.info(f"KNOWLEDGE_UPLOAD: construct={callsign} user={user_email} created={created} updated={updated} skipped={skipped} failed={failed} total={len(file_entries)}")

        operation_receipt = None
        if destination_folder_id and upload_receipts:
            operation_receipt = VAULT_DRIVE_REPOSITORY.record_folder_upload(
                owner_user_id=user_id,
                construct_id=callsign,
                destination_node_id=destination_folder_id,
                items=upload_receipts,
            )
        VAULT_DRIVE_REPOSITORY.invalidate(user_id, callsign)
        chatty_body_service.invalidate_construct_projection_caches(user_id, callsign)

        return jsonify({
            "success": True,
            "construct_id": callsign,
            "total_files": len(file_entries),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "failed_files": failed_files if failed_files else None,
            "destinationFolderId": destination_folder_id or None,
            "driveClassification": drive_context,
            "uploadReceipts": upload_receipts,
            "operationReceipt": operation_receipt,
            "message": f"Uploaded {created + updated} files ({created} new, {updated} updated)" + (f", {failed} failed" if failed else "")
        })

    except Exception as e:
        logger.error(f"KNOWLEDGE_UPLOAD: Error: {e}")
        return jsonify({"success": False, "error": "Knowledge upload failed", "error_code": type(e).__name__}), 503


@app.route('/api/vault/knowledge-files/<file_id>', methods=['DELETE'])
@require_chatty_auth
def delete_knowledge_file(file_id):
    """Delete a single knowledge file by ID (user-scoped)."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        user_role = current_user.get('role', 'user')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        row = VAULT_FILE_REPOSITORY.get_user_file(file_id=file_id, user_id=user_id)
        if not row:
            return jsonify({"success": False, "error": "File not found or access denied"}), 404

        construct_id = (row.get('construct_id') or '').strip()
        if construct_id:
            enforce_pocketverse_authority(construct_id, _pocketverse_request_context())

        VAULT_FILE_REPOSITORY.delete_for_user(file_id=file_id, user_id=user_id)
        if construct_id:
            chatty_body_service.invalidate_construct_projection_caches(
                user_id, construct_id
            )
            _invalidate_avatar_cache(construct_id, user_id)
        logger.info(f"KNOWLEDGE_DELETE: file_id={file_id} user={user_email} filename={row.get('filename')}")
        _log_privileged_event(
            "mass_delete",
            resource=f"vault_file:{file_id}",
            action="delete",
            result="success",
            description="Knowledge file deleted",
            metadata={"file_id": file_id, "filename": row.get("filename")},
            user_id=user_email,
        )

        return jsonify({"success": True, "message": "File deleted", "file_id": file_id})
    except Exception as e:
        logger.error(f"KNOWLEDGE_DELETE: Error deleting file {file_id}: {e}")
        return jsonify({"success": False, "error": "File delete failed", "error_code": type(e).__name__}), 503


@app.route('/api/vault/memup/sync', methods=['POST'])
@require_auth
def sync_memup():
    """Trigger memup sync for a construct — processes transcripts into capsule data."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from memup_sync import sync_construct_memup

        result = sync_construct_memup(VAULT_FILE_REPOSITORY, construct_id, user_id)
        status_code = 200 if result.get('success') else 404
        return jsonify(result), status_code

    except Exception as e:
        logger.error(f"MEMUP_SYNC_ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": "Memup sync failed", "error_code": type(e).__name__}), 503


@app.route('/api/vault/memup/materialize', methods=['POST'])
@require_auth
def materialize_memup():
    """Materialize a canonical memup capsule from transcript candidates without running the full sync path."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = str(data.get('construct_id') or '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        requested_ids = _first_non_empty_list([data.get('candidate_transcript_ids')])
        candidate_transcript_ids = requested_ids or _candidate_transcript_ids_for_construct(construct_id)
        if not candidate_transcript_ids:
            return jsonify({
                "success": False,
                "construct_id": construct_id,
                "error": "No transcript candidates found for materialization",
            }), 404

        materialized = _persist_capsule_from_candidate_transcripts(
            construct_id,
            candidate_transcript_ids,
            user_id,
        )
        if not materialized:
            return jsonify({
                "success": False,
                "construct_id": construct_id,
                "error": "No canonical capsule could be materialized from candidate transcripts",
                "candidate_transcript_ids": candidate_transcript_ids,
            }), 404

        capsule_data = materialized.get('capsule_data') or {}
        write_result = materialized.get('write_result') or {}
        original_capsule = materialized.get('original_capsule') or {}
        summary = capsule_data.get('summary', {}) if isinstance(capsule_data, dict) else {}
        return jsonify({
            "success": True,
            "construct_id": construct_id,
            "user_id": user_id,
            "candidate_transcript_ids": candidate_transcript_ids,
            "candidate_count": len(candidate_transcript_ids),
            "materialized_via": "candidate_transcripts",
            "original_capsule_file": original_capsule,
            "materialized_capsule_file": write_result,
            "capsule_file": write_result,
            "capsule_version": capsule_data.get('capsule_version'),
            "total_sessions": summary.get('total_sessions'),
            "total_exchanges": summary.get('total_exchanges'),
            "date_range": summary.get('date_range'),
            "topics": summary.get('topics', []),
        }), 200

    except Exception as e:
        logger.error(f"MEMUP_MATERIALIZE_ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": "Memup materialization failed", "error_code": type(e).__name__}), 503


@app.route('/api/vault/memup/status')
@require_auth
def memup_status():
    """Check memup sync status for a construct — returns capsule metadata if it exists."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        original_path = _original_capsule_path(construct_id)
        materialized_path = _materialized_capsule_path(construct_id)
        original_row = _lookup_exact_vault_preview_row(
            filename=original_path,
            storage_path=original_path,
            construct_id=construct_id,
            user_id=user_id,
            is_admin=False,
        )
        materialized_row = _lookup_exact_vault_preview_row(
            filename=materialized_path,
            storage_path=materialized_path,
            construct_id=construct_id,
            user_id=user_id,
            is_admin=False,
        )

        def _artifact_summary(row: Optional[Dict[str, Any]], path: str) -> Dict[str, Any]:
            if not row:
                return {"exists": False, "path": path}
            meta = row.get('metadata')
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return {
                "exists": True,
                "file_id": row.get('id'),
                "path": path,
                "sha256": row.get('sha256', ''),
                "file_type": row.get('file_type'),
                "last_synced_at": meta.get('last_synced_at', row.get('updated_at', row.get('created_at', ''))),
                "total_sessions": meta.get('total_sessions', 0),
                "capsule_version": meta.get('capsule_version', ''),
                "metadata": meta,
            }

        original_summary = _artifact_summary(original_row, original_path)
        materialized_summary = _artifact_summary(materialized_row, materialized_path)
        if original_summary["exists"] or materialized_summary["exists"]:
            preferred = materialized_summary if materialized_summary["exists"] else original_summary
            preferred_kind = "materialized" if materialized_summary["exists"] else "original"
            return jsonify({
                "success": True,
                "construct_id": construct_id,
                "synced": True,
                "preferred_artifact": preferred_kind,
                "original_capsule": original_summary,
                "materialized_capsule": materialized_summary,
                "file_id": preferred.get("file_id"),
                "path": preferred.get("path"),
                "sha256": preferred.get("sha256", ''),
                "last_synced_at": preferred.get("last_synced_at", ''),
                "total_sessions": preferred.get("total_sessions", 0),
                "capsule_version": preferred.get("capsule_version", ''),
            })
        else:
            return jsonify({
                "success": True,
                "construct_id": construct_id,
                "synced": False,
                "preferred_artifact": None,
                "original_capsule": {"exists": False, "path": original_path},
                "materialized_capsule": {"exists": False, "path": materialized_path},
                "message": "No memup capsule found. Run sync to generate one."
            })

    except Exception as e:
        logger.error(f"MEMUP_STATUS_ERROR: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/simdrive/list')
@require_auth
def simdrive_list():
    """List all SimDrive files for a construct with classification metadata."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        rows = VAULT_FILE_REPOSITORY.list_simdrive_files(
            construct_id=construct_id,
            user_id=user_id,
            include_content=False,
        )

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simdrive_parser import SimDriveParser

        parser = SimDriveParser(construct_id)
        files = []
        for row in rows:
            classified = parser.classify_file(row.get('filename', ''))
            files.append({
                'id': row['id'],
                'filename': row['filename'],
                'simdrive_type': classified['simdrive_type'],
                'description': classified['description'],
                'sha256': row.get('sha256', ''),
                'created_at': row.get('created_at', ''),
                'updated_at': row.get('updated_at', ''),
            })

        manifest = parser.build_manifest(rows)

        return jsonify({
            "success": True,
            "construct_id": construct_id,
            "files": files,
            "total": len(files),
            "type_distribution": manifest.get('type_distribution', {}),
        })

    except Exception as e:
        logger.error(f"SIMDRIVE_LIST_ERROR: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/simdrive/read')
@require_auth
def simdrive_read():
    """Read a specific SimDrive file with parsed classification."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        file_id = request.args.get('file_id', '').strip()
        construct_id = request.args.get('construct_id', '').strip()
        if not file_id or not construct_id:
            return jsonify({"success": False, "error": "file_id and construct_id are required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        row = VAULT_FILE_REPOSITORY.get_user_file(file_id=file_id, construct_id=construct_id, user_id=user_id)
        if not row:
            return jsonify({"success": False, "error": "File not found"}), 404

        filename = row.get('filename', '')
        if '/simDrive/' not in filename:
            return jsonify({"success": False, "error": "File is not in simDrive folder"}), 403

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simdrive_parser import SimDriveParser

        parser = SimDriveParser(construct_id)
        classified = parser.classify_file(filename, row.get('content', ''))

        return jsonify({
            "success": True,
            "file": {
                'id': row['id'],
                'filename': filename,
                'content': row.get('content', ''),
                'simdrive_type': classified['simdrive_type'],
                'description': classified['description'],
                'version': classified['version'],
                'targets': classified['targets'],
                'parsed': classified['parsed'],
                'parse_error': classified['parse_error'],
                'sha256': row.get('sha256', ''),
                'created_at': row.get('created_at', ''),
                'updated_at': row.get('updated_at', ''),
            },
        })

    except Exception as e:
        logger.error(f"SIMDRIVE_READ_ERROR: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/simdrive/write', methods=['POST'])
@require_auth
def simdrive_write():
    """Write or update a SimDrive file for a construct."""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        filename = data.get('filename', '').strip()
        content = data.get('content', '')

        if not construct_id or not filename:
            return jsonify({"success": False, "error": "construct_id and filename are required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        ok, err = _validate_vault_filename(filename)
        if not ok:
            return jsonify({"success": False, "error": err}), 400

        vsi_path = f'instances/{construct_id}/simDrive/{filename}'

        if '..' in vsi_path or '~' in vsi_path:
            return jsonify({"success": False, "error": "Invalid path"}), 400

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simdrive_parser import SimDriveParser

        parser = SimDriveParser(construct_id)
        classified = parser.classify_file(filename, content)

        content_str = content if isinstance(content, str) else json.dumps(content, indent=2, default=str)
        sha256 = hashlib.sha256(content_str.encode('utf-8')).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        meta = {
            'construct_id': construct_id,
            'provider': 'simdrive',
            'folder': 'simDrive',
            'simdrive_type': classified['simdrive_type'],
            'version': classified['version'],
        }

        record = {
            'filename': vsi_path,
            'file_type': 'simdrive',
            'content': content_str,
            'construct_id': construct_id,
            'user_id': user_id,
            'is_system': False,
            'sha256': sha256,
            'metadata': json.dumps(meta),
            'storage_path': vsi_path,
            'created_at': now,
            'updated_at': now,
        }
        simdrive_result = _upsert_vault_file_record(record, context='simdrive_write')
        action = simdrive_result['action']
        file_id = simdrive_result['id']

        return jsonify({
            "success": True,
            "action": action,
            "file_id": file_id,
            "path": vsi_path,
            "simdrive_type": classified['simdrive_type'],
            "sha256": sha256,
        })

    except Exception as e:
        logger.error(f"SIMDRIVE_WRITE_ERROR: {e}")
        return jsonify({"success": False, "error": str(e), "error_code": type(e).__name__}), 503


@app.route('/api/vault/simdrive/inject', methods=['POST'])
@require_auth
def simdrive_inject():
    """Inject memup capsule data into a construct's SimDrive as a continuity injection file.

    Reads the construct's memup capsule, transforms it into SimDrive injection format,
    and writes it to instances/{construct}/simDrive/continuity_injection.json.
    """
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        max_sessions = data.get('max_sessions', 50)

        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        capsule_path = f'instances/{construct_id}/memup/{construct_id}.capsule'
        capsule_row = VAULT_FILE_REPOSITORY.find_by_path(
            construct_id=construct_id,
            user_id=user_id,
            filename=capsule_path,
        )
        if not capsule_row:
            return jsonify({
                "success": False,
                "error": "No memup capsule found. Run memup sync first."
            }), 404

        capsule_content = capsule_row.get('content', '')
        try:
            capsule_data = json.loads(capsule_content) if capsule_content else {}
        except (json.JSONDecodeError, TypeError):
            return jsonify({"success": False, "error": "Capsule data is corrupted"}), 500

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simdrive_parser import SimDriveParser

        parser = SimDriveParser(construct_id)
        injection = parser.capsule_to_injection(capsule_data, max_sessions=max_sessions)

        validation = parser.validate_injection(injection)
        if not validation['valid']:
            return jsonify({
                "success": False,
                "error": "Generated injection failed validation",
                "validation": validation,
            }), 500

        injection_str = json.dumps(injection, indent=2, default=str)
        sha256 = hashlib.sha256(injection_str.encode('utf-8')).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        vsi_path = f'instances/{construct_id}/simDrive/continuity_injection.json'

        meta = {
            'construct_id': construct_id,
            'provider': 'simdrive_inject',
            'folder': 'simDrive',
            'simdrive_type': 'injection',
            'session_count': len(injection.get('sessions', [])),
            'hook_count': len(injection.get('continuity_hooks', [])),
            'injected_at': now,
        }

        record = {
            'filename': vsi_path,
            'file_type': 'simdrive',
            'content': injection_str,
            'construct_id': construct_id,
            'user_id': user_id,
            'is_system': False,
            'sha256': sha256,
            'metadata': json.dumps(meta),
            'storage_path': vsi_path,
            'created_at': now,
            'updated_at': now,
        }
        injection_result = _upsert_vault_file_record(record, context='simdrive_injection')
        action = injection_result['action']
        file_id = injection_result['id']

        logger.info(
            f'SIMDRIVE_INJECT: {action} injection for {construct_id} — '
            f'{validation["session_count"]} sessions, {validation["hook_count"]} hooks'
        )

        return jsonify({
            "success": True,
            "action": action,
            "construct_id": construct_id,
            "file_id": file_id,
            "path": vsi_path,
            "sha256": sha256,
            "sessions_injected": validation['session_count'],
            "hooks_injected": validation['hook_count'],
            "validation": validation,
        })

    except Exception as e:
        logger.error(f"SIMDRIVE_INJECT_ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "error_code": type(e).__name__}), 503


@app.route('/api/vault/files/<file_id>')
@require_auth
def get_vault_file(file_id):
    """Get a single vault file by ID (multi-tenant: users can only access their files)"""
    started_at = time.perf_counter()
    try:
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        row = VAULT_FILE_REPOSITORY.get_user_file(file_id=file_id, user_id=user_id)

        if not row:
            return jsonify({"success": False, "error": "File not found"}), 404

        backing_row = _lookup_materialized_capsule_backing_row(
            row,
            user_id=user_id,
            is_admin=False,
        )
        if backing_row and isinstance(backing_row.get('content'), str) and backing_row.get('content'):
            file_payload = _build_preview_payload_from_materialized_sibling(
                row,
                backing_row,
                preview_budget_ms=VAULT_PREVIEW_ROUTE_BUDGET_MS,
            )
        else:
            file_payload = _derive_vault_preview_payload(row)
        logger.info(
            "VAULT_FILE_DETAIL: id=%s path=%s route_elapsed_ms=%s preview_elapsed_ms=%s preview_status=%s preview_source=%s preview_timed_out=%s",
            file_id,
            file_payload.get('filename') or file_payload.get('storage_path') or '',
            _preview_elapsed_ms(started_at),
            file_payload.get('preview_elapsed_ms'),
            file_payload.get('preview_status'),
            file_payload.get('preview_source'),
            file_payload.get('preview_timed_out'),
        )
        return jsonify({"success": True, "file": file_payload})
    except Exception as e:
        logger.error(f"Error fetching vault file: {e}")
        return jsonify({"success": False, "error": str(e), "error_code": type(e).__name__}), 503


def _get_authorized_vault_data_row(file_id):
    user_id = _get_authenticated_user_id()
    if not user_id:
        return None, (jsonify({"success": False, "error": "User not found"}), 403)
    row = VAULT_FILE_REPOSITORY.get_user_file(file_id=file_id, user_id=user_id)
    if not row:
        return None, (jsonify({"success": False, "error": "File not found"}), 404)
    return row, None


def _preview_unavailable_response(file_row, reason, status_code=422):
    metadata = _metadata_to_dict(file_row.get('metadata'))
    return jsonify({
        "success": False,
        "error": "preview_unavailable",
        "reason": reason,
        "file_id": file_row.get('id'),
        "filename": file_row.get('filename'),
        "file_type": file_row.get('file_type'),
        "metadata": {
            "size": metadata.get("size"),
            "construct_id": metadata.get("construct_id") or file_row.get("construct_id"),
        },
    }), status_code


@app.route('/api/vault/files/<file_id>/data-url')
@require_auth
def get_vault_file_data_url(file_id):
    """Return a browser-safe data URL for supported image previews."""
    try:
        row, error_response = _get_authorized_vault_data_row(file_id)
        if error_response:
            return error_response
        data_url, unavailable_reason = _image_preview_data_url(row)
        if unavailable_reason:
            return _preview_unavailable_response(row, unavailable_reason)
        return jsonify({
            "success": True,
            "file_id": row.get("id"),
            "filename": row.get("filename"),
            "file_type": row.get("file_type"),
            "data_url": data_url,
        })
    except Exception as exc:
        logger.error("Error fetching vault file preview: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/vault/files/<file_id>/media')
@require_auth
def get_vault_file_media(file_id):
    """Return authenticated, browser-previewable image, PDF, audio, or video bytes."""
    try:
        row, error_response = _get_authorized_vault_data_row(file_id)
        if error_response:
            return error_response
        body, mime, unavailable_reason = _media_preview_bytes(row)
        if unavailable_reason or body is None:
            return _preview_unavailable_response(row, unavailable_reason or 'missing_content')
        filename = os.path.basename(row.get('filename') or row.get('storage_path') or 'preview')
        safe_filename = re.sub(r'[\r\n"\\\\]', '_', filename)
        response = Response(body, status=200, mimetype=mime)
        response.headers['Content-Disposition'] = f'inline; filename="{safe_filename}"'
        response.headers['Content-Length'] = str(len(body))
        response.headers['Cache-Control'] = 'private, max-age=300'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except Exception as exc:
        logger.error("Error fetching vault media preview: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/vault/drive/files/<file_id>/download')
@require_auth
def download_vault_drive_file(file_id):
    """Download exact owner-scoped file bytes without the preview size/type boundary."""
    try:
        row, error_response = _get_authorized_vault_data_row(file_id)
        if error_response:
            return error_response
        if row.get("drive_trashed_at"):
            return jsonify({"success": False, "error": "File is in Trash"}), 409
        content = row.get("content")
        metadata = _metadata_to_dict(row.get("metadata"))
        body = None
        if isinstance(content, bytes):
            body = content
        elif isinstance(content, str):
            text = content.strip()
            encoded = re.match(r"^data:[^;,]+;base64,(.+)$", text, re.I | re.S)
            binary_like = str(row.get("file_type") or "").lower() in {"binary", "image", "pdf", "audio", "video"} or bool(metadata.get("original_size"))
            if encoded or binary_like:
                try:
                    body = base64.b64decode(re.sub(r"\s+", "", encoded.group(1) if encoded else text), validate=True)
                except (ValueError, TypeError):
                    body = None
            if body is None and not binary_like:
                body = content.encode("utf-8")
        if body is None:
            stored = VAULT_FILE_REPOSITORY.load_bytes(row)
            body = stored[0] if stored else None
        if body is None:
            return jsonify({"success": False, "error": "Canonical file bytes are unavailable"}), 409
        digest = hashlib.sha256(body).hexdigest()
        expected = str(row.get("sha256") or "").lower()
        if re.fullmatch(r"[0-9a-f]{64}", expected) and digest != expected:
            return jsonify({"success": False, "error": "Canonical file hash mismatch", "error_code": "CANONICAL_FILE_HASH_MISMATCH"}), 409
        filename = os.path.basename(row.get("filename") or row.get("storage_path") or "download")
        response = Response(body, status=200, content_type=str(row.get("content_type") or "application/octet-stream"))
        response.headers["Content-Disposition"] = f"attachment; filename=\"{re.sub(r'[\r\n\"\\\\]', '_', filename)}\""
        response.headers["Content-Length"] = str(len(body))
        response.headers["ETag"] = f'"{digest}"'
        response.headers["X-VVAULT-SHA256"] = digest
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except Exception as exc:
        logger.error("VVAULT Drive download failed: %s", exc)
        return jsonify({"success": False, "error": "Download failed", "error_code": type(exc).__name__}), 503


@app.route('/api/vault/files/<file_id>/archive')
@require_auth
def get_vault_file_archive_preview(file_id):
    """Return an authenticated, bounded listing for a ZIP archive."""
    try:
        row, error_response = _get_authorized_vault_data_row(file_id)
        if error_response:
            return error_response
        preview, unavailable_reason = _archive_preview(row)
        if unavailable_reason or preview is None:
            return _preview_unavailable_response(row, unavailable_reason or 'missing_content')
        return jsonify({
            'success': True,
            'file_id': row.get('id'),
            'filename': row.get('filename'),
            **preview,
        })
    except Exception as exc:
        logger.error("Error building vault archive preview: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/vault/files/preview', methods=['POST'])
@require_auth
def preview_vault_file():
    """Build a fast preview payload from list-row metadata without refetching the file by id."""
    started_at = time.perf_counter()
    try:
        current_user = request.current_user
        user_email = current_user.get('email')
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get('filename') or payload.get('storage_path') or '').strip()
        storage_path = str(payload.get('storage_path') or filename).strip()
        file_type = str(payload.get('file_type') or '').strip()
        construct_id = str(payload.get('construct_id') or '').strip()
        resolve_body = bool(payload.get('resolve_body'))
        candidate_transcript_ids = payload.get('candidate_transcript_ids') or []

        if not filename:
            return jsonify({"success": False, "error": "filename is required"}), 400

        effective_user_id = _get_authenticated_user_id()
        if not effective_user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        pseudo_row = {
            'id': payload.get('id'),
            'filename': filename,
            'storage_path': storage_path,
            'file_type': file_type,
            'content': payload.get('content'),
            'construct_id': construct_id,
            'user_id': effective_user_id,
            'is_system': bool(payload.get('is_system', False)),
            'metadata': payload.get('metadata') or {},
            'created_at': payload.get('created_at'),
            'updated_at': payload.get('updated_at'),
            'sha256': payload.get('sha256'),
        }

        ext = os.path.splitext(filename)[1].lower()
        inline_content = pseudo_row.get('content')
        if ext == '.capsule' and not (isinstance(inline_content, str) and inline_content):
            matched_row = _lookup_exact_vault_preview_row(
                filename=filename,
                storage_path=storage_path,
                construct_id=construct_id,
                user_id=effective_user_id,
                is_admin=False,
            )
            requested_row = dict(matched_row or {})
            for key, value in pseudo_row.items():
                if value is not None and (value != "" or key in {"filename", "storage_path", "construct_id"}):
                    requested_row[key] = value

            backing_row = _lookup_materialized_capsule_backing_row(
                requested_row,
                user_id=effective_user_id,
                is_admin=False,
            )
            if backing_row and isinstance(backing_row.get('content'), str) and backing_row.get('content'):
                file_payload = _build_preview_payload_from_materialized_sibling(
                    requested_row,
                    backing_row,
                    preview_budget_ms=0,
                )
            elif matched_row and isinstance(matched_row.get('content'), str) and matched_row.get('content'):
                preview_row = dict(pseudo_row)
                preview_row.update(matched_row)
                file_payload = _derive_vault_preview_payload(preview_row, preview_budget_ms=0)
            elif matched_row and resolve_body and candidate_transcript_ids:
                capsule_user_id = matched_row.get('user_id') or effective_user_id
                candidate_preview = _build_capsule_preview_from_candidate_ids(
                    construct_id,
                    candidate_transcript_ids,
                    user_id=capsule_user_id,
                )
                if candidate_preview:
                    preview_row = dict(pseudo_row)
                    preview_row.update(matched_row)
                    preview_row['content'] = candidate_preview
                    file_payload = _derive_vault_preview_payload(preview_row, preview_budget_ms=0)
                    file_payload['preview_status'] = 'recovered'
                    file_payload['preview_source'] = 'transcript_candidates'
                else:
                    file_payload = dict(pseudo_row)
                    file_payload['content'] = _build_unavailable_capsule_preview(file_payload, filename, file_type)
                    file_payload['preview_kind'] = 'json'
                    file_payload['preview_status'] = 'unavailable'
                    file_payload['preview_source'] = 'fast_diagnostic'
                    file_payload['preview_timed_out'] = False
                    file_payload['preview_elapsed_ms'] = _preview_elapsed_ms(started_at)
                    file_payload['preview_budget_ms'] = 0
                    file_payload['preview_storage_elapsed_ms'] = 0
                    file_payload['preview_reconstruct_elapsed_ms'] = 0
            elif matched_row and resolve_body:
                hydrated_text = _load_vault_file_text(matched_row)
                if hydrated_text:
                    preview_row = dict(pseudo_row)
                    preview_row.update(matched_row)
                    preview_row['content'] = hydrated_text
                    file_payload = _derive_vault_preview_payload(preview_row, preview_budget_ms=0)
                    file_payload['preview_status'] = 'recovered'
                    file_payload['preview_source'] = 'vvault_hydrate'
                else:
                    file_payload = dict(pseudo_row)
                    file_payload['content'] = _build_unavailable_capsule_preview(file_payload, filename, file_type)
                    file_payload['preview_kind'] = 'json'
                    file_payload['preview_status'] = 'unavailable'
                    file_payload['preview_source'] = 'fast_diagnostic'
                    file_payload['preview_timed_out'] = False
                    file_payload['preview_elapsed_ms'] = _preview_elapsed_ms(started_at)
                    file_payload['preview_budget_ms'] = 0
                    file_payload['preview_storage_elapsed_ms'] = 0
                    file_payload['preview_reconstruct_elapsed_ms'] = 0
            else:
                file_payload = dict(pseudo_row)
                file_payload['content'] = _build_unavailable_capsule_preview(file_payload, filename, file_type)
                file_payload['preview_kind'] = 'json'
                file_payload['preview_status'] = 'unavailable'
                file_payload['preview_source'] = 'fast_diagnostic'
                file_payload['preview_timed_out'] = False
                file_payload['preview_elapsed_ms'] = _preview_elapsed_ms(started_at)
                file_payload['preview_budget_ms'] = 0
                file_payload['preview_storage_elapsed_ms'] = 0
                file_payload['preview_reconstruct_elapsed_ms'] = 0
        else:
            preview_budget_ms = (
                VAULT_FAST_CAPSULE_PREVIEW_BUDGET_MS if ext == '.capsule' else VAULT_PREVIEW_ROUTE_BUDGET_MS
            )
            file_payload = _derive_vault_preview_payload(pseudo_row, preview_budget_ms=preview_budget_ms)
            if ext == '.capsule' and file_payload.get('preview_status') == 'unavailable':
                file_payload['preview_source'] = 'fast_diagnostic'
        logger.info(
            "VAULT_FILE_PREVIEW_FAST: path=%s route_elapsed_ms=%s preview_elapsed_ms=%s preview_status=%s preview_source=%s preview_timed_out=%s",
            filename,
            _preview_elapsed_ms(started_at),
            file_payload.get('preview_elapsed_ms'),
            file_payload.get('preview_status'),
            file_payload.get('preview_source'),
            file_payload.get('preview_timed_out'),
        )
        return jsonify({"success": True, "file": file_payload})
    except Exception as e:
        logger.error(f"Error building fast vault preview: {e}")
        return jsonify({"success": False, "error": str(e), "error_code": type(e).__name__}), 503

# ============================================================================
# SERVICE API ENDPOINTS (for FXShinobi/Chatty backend-to-backend integration)
# ============================================================================

@app.route('/api/vault/health')
def service_health():
    """Service health check - returns VVAULT-native availability status
    
    No auth required - allows services to check if VVAULT is up before auth
    """
    runtime_status = _get_vvault_runtime_status()
    service_api_status = "enabled" if VVAULT_SERVICE_TOKEN else "disabled"
    body_status = runtime_status.get("body_database") or {}
    auth_status = runtime_status.get("auth") or {}
    storage_status = runtime_status.get("storage") or {}

    overall_status = "ok" if body_status.get("ready") else "degraded"
    if service_api_status == "disabled":
        overall_status = "degraded"
    
    return jsonify({
        "status": overall_status,
        "service": "vvault",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "components": {
            "body_database": body_status.get("status"),
            "auth": auth_status.get("status"),
            "storage": storage_status.get("status"),
            "service_api": service_api_status,
        },
        "runtime": runtime_status.get("runtime"),
        "body_database": body_status,
        "auth": auth_status,
        "storage": storage_status,
        "storage_mode": "vvault_body",
        "storage_owner": VAULT_FILE_OWNER,
        "auth_owner": AUTH_OWNER,
        "session_owner": SESSION_OWNER,
        "message": "VVAULT service API" if service_api_status == "enabled" else "Service API disabled (VVAULT_SERVICE_TOKEN not set)"
    })

def _safe_config_path_segment(value: str, field: str) -> str:
    segment = str(value or "").strip()
    if not segment or "/" in segment or "\\" in segment or segment in {".", ".."} or ".." in segment:
        raise ValueError(f"{field} contains an invalid path segment")
    return segment


def _service_config_path(service: str, strategy_id: str) -> str:
    safe_service = _safe_config_path_segment(service, "service")
    safe_strategy = _safe_config_path_segment(strategy_id, "strategy_id")
    return f"system/configs/{safe_service}/{safe_strategy}.json"


def _service_credential_path(service: str, key: str) -> str:
    safe_service = _safe_config_path_segment(service, "service")
    safe_key = _safe_config_path_segment(key, "key")
    return f"system/credentials/{safe_service}/{safe_key}.json"


def _cleanhouse_files_owner_context() -> tuple[str | None, str | None, tuple[Any, int] | None]:
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return None, None, (jsonify({
            "success": False,
            "error": "Canonical OVVAULTS owner identity is required",
        }), 403)
    try:
        callsign = cleanhouse_files_evidence.validate_instance_id(
            request.headers.get("X-CleanHouse-Instance")
            or os.environ.get("VVAULT_CLEANHOUSE_INSTANCE_ID")
            or "zen-001"
        )
    except cleanhouse_files_evidence.CleanHouseEvidenceError as exc:
        return None, None, (jsonify({"success": False, "error": str(exc)}), 400)
    if not _construct_is_projectable_cached(owner_user_id, callsign):
        return None, None, (jsonify({
            "success": False,
            "error": "CleanHouse instance is not canonical for this owner",
        }), 403)
    return owner_user_id, callsign, None


@app.route('/api/cleanhouse/files/evidence', methods=['POST'])
@require_chatty_auth
def append_cleanhouse_files_evidence():
    """Append a normalized Files batch to the existing OVVAULTS authority."""
    owner_user_id, callsign, error = _cleanhouse_files_owner_context()
    if error:
        return error
    raw_body = request.get_data(cache=True)
    try:
        batch_id, evidence = cleanhouse_files_evidence.validate_batch(
            request.get_json(silent=True),
            raw_body=raw_body,
            expected_batch_id=str(
                request.headers.get("X-CleanHouse-Batch-Id")
                or request.headers.get("Idempotency-Key")
                or ""
            ).strip().lower(),
        )
        receipt = VAULT_FILE_REPOSITORY.append_cleanhouse_files_evidence_batch(
            user_id=str(owner_user_id),
            callsign=str(callsign),
            batch_id=batch_id,
            events=evidence,
        )
        return jsonify({"success": True, **receipt})
    except cleanhouse_files_evidence.CleanHouseEvidenceError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except Exception as exc:
        logger.error("CLEANHOUSE_FILES: preservation failed (%s)", type(exc).__name__)
        return jsonify({
            "success": False,
            "error": "OVVAULTS Files evidence preservation failed",
            "error_code": type(exc).__name__,
        }), 503


@app.route('/api/cleanhouse/files/wazuh/events')
@require_chatty_auth
def get_cleanhouse_wazuh_events():
    """Read the manager-local Wazuh FIM stream through VVAULT auth."""
    _owner_user_id, _callsign, error = _cleanhouse_files_owner_context()
    if error:
        return error
    try:
        result = cleanhouse_files_evidence.read_wazuh_alerts(
            after=str(request.args.get("after") or ""),
            limit=int(request.args.get("limit") or 100),
        )
        response = jsonify({"success": True, **result})
        response.headers["Cache-Control"] = "no-store"
        return response
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid Wazuh feed request"}), 400
    except cleanhouse_files_evidence.WazuhEvidenceUnavailable as exc:
        return jsonify({"success": False, "error": str(exc), "state": "unavailable"}), 503


@app.route('/api/cleanhouse/files/wazuh/inventory')
@require_chatty_auth
def get_cleanhouse_wazuh_inventory():
    """Proxy the enrolled agent's FIM inventory from the local manager API."""
    _owner_user_id, _callsign, error = _cleanhouse_files_owner_context()
    if error:
        return error
    try:
        result = cleanhouse_files_evidence.query_wazuh_inventory(
            offset=int(request.args.get("offset") or 0),
            limit=int(request.args.get("limit") or 500),
        )
        response = jsonify({"success": True, **result})
        response.headers["Cache-Control"] = "no-store"
        return response
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid Wazuh inventory request"}), 400
    except cleanhouse_files_evidence.WazuhEvidenceUnavailable as exc:
        return jsonify({"success": False, "error": str(exc), "state": "unavailable"}), 503


@app.route('/api/cleanhouse/files/wazuh/status')
@require_chatty_auth
def get_cleanhouse_wazuh_status():
    """Expose bounded manager evidence readiness without Wazuh credentials."""
    _owner_user_id, _callsign, error = _cleanhouse_files_owner_context()
    if error:
        return error
    alerts_path = Path(
        os.environ.get("VVAULT_WAZUH_ALERTS_PATH")
        or cleanhouse_files_evidence.DEFAULT_ALERTS_PATH
    )
    alerts_ready = alerts_path.is_file() and os.access(alerts_path, os.R_OK)
    inventory_configured = bool(
        os.environ.get("VVAULT_WAZUH_AGENT_ID")
        and os.environ.get("VVAULT_WAZUH_MANAGER_TOKEN")
    )
    state = "live" if alerts_ready and inventory_configured else (
        "warming" if alerts_ready else "unavailable"
    )
    response = jsonify({
        "success": True,
        "provider": "wazuh_manager",
        "state": state,
        "alerts_ready": alerts_ready,
        "inventory_configured": inventory_configured,
        "evidence_authenticated": alerts_ready,
        "storage_owner": VAULT_FILE_OWNER,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


def _service_credential_payload_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = row.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    parsed = _safe_json_loads(content)
    return parsed if isinstance(parsed, dict) else None


def _service_config_payload_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = row.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    parsed = _safe_json_loads(content)
    if not isinstance(parsed, dict):
        return None
    return {
        "strategy_id": parsed.get("strategy_id"),
        "params": parsed.get("params", {}),
        "symbols": parsed.get("symbols", []),
        "risk_limits": parsed.get("risk_limits", {}),
        "enabled": parsed.get("enabled", True),
        "version": parsed.get("version", 1),
        "updated_at": parsed.get("updated_at") or row.get("updated_at"),
    }


def _list_service_config_rows(service: str) -> List[Dict[str, Any]]:
    prefix = f"system/configs/{_safe_config_path_segment(service, 'service')}"
    summaries = VAULT_FILE_REPOSITORY.list_system_files(path_prefix=prefix)
    rows: List[Dict[str, Any]] = []
    for summary in summaries:
        path = summary.get("storage_path") or summary.get("filename")
        if not path or not str(path).startswith(f"{prefix}/"):
            continue
        row = VAULT_FILE_REPOSITORY.get_system_file(path)
        if row:
            rows.append(row)
    return rows


@app.route('/api/vault/configs/<service>')
@require_service_token
def get_service_configs(service):
    """Get VVAULT-native strategy configs for a service."""
    try:
        configs = []
        for row in _list_service_config_rows(service):
            payload = _service_config_payload_from_row(row)
            if payload:
                configs.append(payload)

        if not configs:
            return jsonify({
                "success": True,
                "service": service,
                "configs": [],
                "message": "No configs found, using defaults",
                "storage_mode": "vvault_body",
                "storage_owner": VAULT_FILE_OWNER,
            })

        configs.sort(key=lambda item: str(item.get("strategy_id") or ""))
        logger.info(f"SERVICE_API: Configs retrieved for {service} ({len(configs)} strategies)")
        return jsonify({
            "success": True,
            "service": service,
            "configs": configs,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching configs for {service}: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to retrieve configs",
            "error_code": type(e).__name__,
        }), 503

@app.route('/api/vault/credentials/<key>')
@require_service_token
def get_service_credential(key):
    """Get a locally stored credential by key (decrypted)
    
    Auth: Requires VVAULT_SERVICE_TOKEN
    NEVER logs the actual credential value
    """
    try:
        service = request.args.get('service', 'default')
        path = _service_credential_path(service, key)
        row = VAULT_FILE_REPOSITORY.get_system_file(path)
        payload = _service_credential_payload_from_row(row or {}) if row else None
        if not payload:
            logger.info(f"SERVICE_API: Credential not found: {key}")
            return jsonify({
                "success": False,
                "error": f"Credential '{key}' not found"
            }), 404

        try:
            decrypted_value = decrypt_credential(payload['encrypted_value'])
        except Exception as decrypt_error:
            logger.error(f"SERVICE_API: Decryption failed for {key}")
            return jsonify({
                "success": False,
                "error": "Credential decryption failed"
            }), 500
        
        logger.info(f"SERVICE_API: Credential retrieved: {key}")
        
        return jsonify({
            "success": True,
            "key": key,
            "service": payload.get('service') or service,
            "value": decrypted_value,
            "metadata": payload.get('metadata', {}),
            "updated_at": payload.get('updated_at') or row.get('updated_at'),
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        })
        
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching credential {key}: {type(e).__name__}")
        return jsonify({
            "success": False,
            "error": "Failed to retrieve credential",
            "error_code": type(e).__name__,
        }), 503

@app.route('/api/vault/credentials', methods=['POST'])
@require_service_token
def store_service_credential():
    """Store or update a credential (encrypted at rest)
    
    Request body: { key, service, value, metadata? }
    Auth: Requires VVAULT_SERVICE_TOKEN
    NEVER logs the actual credential value
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400
        
        key = data.get('key')
        service = data.get('service', 'default')
        value = data.get('value')
        metadata = data.get('metadata', {})
        
        if not key or not value:
            return jsonify({"success": False, "error": "key and value are required"}), 400

        path = _service_credential_path(service, key)
        existing = VAULT_FILE_REPOSITORY.get_system_file(path)
        encrypted_value = encrypt_credential(value)
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "key": key,
            "service": service,
            "encrypted_value": encrypted_value,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
        }
        content = json.dumps(payload, sort_keys=True, indent=2)
        result = VAULT_FILE_REPOSITORY.upsert({
            "filename": path,
            "storage_path": path,
            "content": content,
            "metadata": {
                "artifact_type": "service_credential",
                "service": service,
                "key": key,
            },
            "file_type": "application/json",
            "content_type": "application/json",
            "sha256": _sha256_text(content),
            "is_system": True,
            "updated_at": now,
        })
        action = result.get("action") or ("updated" if existing else "created")
        
        logger.info(f"SERVICE_API: Credential {action}: {key} (service: {service})")
        _log_privileged_event(
            "secret_rotate",
            resource=f"credential:{service}:{key}",
            action=action,
            result="success",
            description=f"Service credential {action}",
            metadata={"service": service, "key": key},
        )

        return jsonify({
            "success": True,
            "key": key,
            "service": service,
            "action": action,
            "message": f"Credential {action} successfully",
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        })

    except Exception as e:
        logger.error(f"SERVICE_API: Error storing credential: {type(e).__name__}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/vault/credentials")
        return jsonify({
            "success": False,
            "error": "Failed to store credential",
            "error_code": type(e).__name__,
        }), 503

@app.route('/api/vault/configs/<service>', methods=['POST'])
@require_service_token
def store_service_config(service):
    """Store or update VVAULT-native strategy config for a service."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        safe_service = _safe_config_path_segment(service, "service")
        strategy_id = _safe_config_path_segment(data.get('strategy_id', 'default'), "strategy_id")
        params = data.get('params', {})
        symbols = data.get('symbols', [])
        risk_limits = data.get('risk_limits', {})
        enabled = data.get('enabled', True)

        path = _service_config_path(safe_service, strategy_id)
        existing = VAULT_FILE_REPOSITORY.get_system_file(path)
        existing_payload = _service_config_payload_from_row(existing) if existing else None
        current_version = int((existing_payload or {}).get("version") or 0)
        new_version = current_version + 1
        now = datetime.now(timezone.utc).isoformat()
        content_payload = {
            "service": safe_service,
            "strategy_id": strategy_id,
            "params": params,
            "symbols": symbols,
            "risk_limits": risk_limits,
            "enabled": enabled,
            "version": new_version,
            "updated_at": now,
        }
        content = json.dumps(content_payload, indent=2, ensure_ascii=False)
        result = _upsert_vault_file_record(
            {
                "filename": path,
                "storage_path": path,
                "file_type": "application/json",
                "content": content,
                "is_system": True,
                "sha256": _sha256_text(content),
                "metadata": json.dumps({
                    "folder": "system/configs",
                    "service": safe_service,
                    "strategy_id": strategy_id,
                    "source": "vvault_service_config",
                    "updatedAt": now,
                }),
                "created_at": existing.get("created_at") if existing else now,
                "updated_at": now,
            },
            context="service_config",
        )
        action = result.get("action") or ("updated" if existing else "created")

        logger.info(f"SERVICE_API: Config {action} for {safe_service}/{strategy_id} (v{new_version})")
        _log_privileged_event(
            "config_change",
            resource=f"config:{safe_service}:{strategy_id}",
            action=action,
            result="success",
            description=f"Strategy config {action}",
            metadata={"service": safe_service, "strategy_id": strategy_id, "version": new_version},
        )

        return jsonify({
            "success": True,
            "service": safe_service,
            "strategy_id": strategy_id,
            "action": action,
            "version": new_version,
            "file_id": result.get("id"),
            "storage_path": path,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"SERVICE_API: Error storing config: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to store config",
            "error_code": type(e).__name__,
        }), 503


@app.route('/api/vault/system-files', methods=['GET'])
@require_service_token
def get_system_file():
    """
    Retrieve a system file by storage_path (service-to-service).

    Query params:
      - storage_path (required)
    """
    try:
        storage_path = (request.args.get("storage_path") or "").strip()
        if not storage_path:
            return jsonify({"success": False, "error": "storage_path is required"}), 400

        row = VAULT_FILE_REPOSITORY.get_system_file(storage_path)
        if not row:
            return jsonify({"success": False, "error": "File not found"}), 404

        return jsonify({"success": True, "file": row, "storage_mode": "vvault_body"})
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching system file: {e}")
        return jsonify({"success": False, "error": "Failed to fetch system file", "error_code": type(e).__name__}), 503


def _queue_system_file_write(
    *,
    record: Dict[str, Any],
    storage_path: str,
    sha256: str,
    reason: str,
) -> Tuple[Any, int]:
    receipt = {
        "ok": False,
        "action": "retired",
        "operation": VAULT_FILE_UPSERT,
        "table": "vault_files",
        "idempotency_key": f"vault_files:system_file:{storage_path}:{sha256}",
        "reason": reason,
        "message": "legacy remote system-file outbox is retired; VVAULT local writes are canonical.",
    }
    return jsonify(
        {
            "success": False,
            "queued": False,
            "canonical": False,
            "storage_mode": "vvault_body",
            "reason": reason,
            "outbox_receipt": receipt,
        }
    ), 503


def _validate_system_file_outbox_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = item.get("record") or {}
    mutable = set(item.get("mutable_fields") or [])
    identity = set(item.get("identity_fields") or [])
    if item.get("operation") != VAULT_FILE_UPSERT or item.get("table") != "vault_files":
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "Only system-file vault_files upserts are replayable.",
        }
    if item.get("operation_kind") != "upsert":
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "Only upsert outbox items are replayable.",
        }
    if not str(record.get("storage_path") or "").strip():
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "System-file replay requires a storage_path identity field.",
        }
    if record.get("is_system") is not True:
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "System-file replay requires is_system=true.",
        }
    if record.get("user_id") not in (None, ""):
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "System-file replay cannot set user_id.",
        }
    if mutable.intersection(SYSTEM_FILE_OUTBOX_IDENTITY_FIELDS):
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "System-file replay cannot treat identity fields as mutable.",
        }
    if identity != set(SYSTEM_FILE_OUTBOX_IDENTITY_FIELDS):
        return {
            "error_code": UNSUPPORTED_OUTBOX_ITEM,
            "message": "System-file replay identity contract must be storage_path + is_system + user_id.",
        }
    return None


def _load_remote_system_file_for_outbox_item(item: Dict[str, Any]) -> Dict[str, Any]:
    record = item.get("record") or {}
    storage_path = str(record.get("storage_path") or "").strip()
    remote = VAULT_FILE_REPOSITORY.get_system_file(storage_path)
    if not remote:
        return {}
    remote_updated = _parse_vault_timestamp(remote.get("updated_at") or remote.get("created_at"))
    queued_updated = _parse_vault_timestamp(record.get("accepted_at") or record.get("updated_at") or record.get("created_at"))
    if remote_updated <= queued_updated:
        remote["idempotency_key"] = item.get("idempotency_key")
    return remote


def _write_system_file_outbox_patch(item: Dict[str, Any], patch: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    illegal_fields = sorted(set(patch.keys()) - set(SYSTEM_FILE_OUTBOX_MUTABLE_FIELDS))
    if illegal_fields:
        raise ValueError(f"Replay patch contains non-mutable fields: {', '.join(illegal_fields)}")
    merged = dict((plan.get("merged_record") or {}))
    merged.update(patch)
    result = VAULT_FILE_REPOSITORY.upsert(merged)
    return {
        "ok": True,
        "action": result.get("action"),
        "file_id": result.get("id"),
        "applied_fields": sorted(patch.keys()),
        "row_count": 1 if result.get("id") else 0,
    }


def _replay_system_file_outbox() -> Dict[str, Any]:
    return {
        "success": True,
        "ok": True,
        "action": "retired",
        "storage_mode": "vvault_body",
        "pending_outbox_count": 0,
        "message": "legacy remote system-file outbox replay is retired; system files write synchronously to ovvaults.vault_files.",
    }


@app.route('/api/vault/system-files/outbox/replay', methods=['POST'])
@require_service_token
def replay_system_file_outbox():
    """Compatibility no-op after system-file writes moved to local VVAULT."""
    receipt = _replay_system_file_outbox()
    return jsonify({"success": bool(receipt.get("success")), "outbox_replay_receipt": receipt}), 200


@app.route('/api/vault/system-files', methods=['POST'])
@require_service_token
def upsert_system_file():
    """
    Store or update a system vault file (service-to-service).

    Request body: { storage_path, filename?, content, file_type?, metadata? }
      - storage_path is the canonical key (required)
      - filename defaults to storage_path
      - metadata may be a dict or JSON string; stored as JSON string
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        storage_path = (data.get("storage_path") or "").strip()
        if not storage_path:
            return jsonify({"success": False, "error": "storage_path is required"}), 400

        filename = (data.get("filename") or storage_path).strip()
        content = data.get("content", "")
        file_type = (data.get("file_type") or "text/markdown").strip()
        metadata = data.get("metadata", {})

        ok, err = _validate_vault_filename(filename)
        if not ok:
            return jsonify({"success": False, "error": err}), 400

        # Normalize metadata to a JSON string for storage.
        if metadata is None:
            metadata_obj = {}
        elif isinstance(metadata, str):
            try:
                metadata_obj = json.loads(metadata)
            except Exception:
                metadata_obj = {"raw": metadata}
        elif isinstance(metadata, dict):
            metadata_obj = metadata
        else:
            metadata_obj = {"value": metadata}

        now = datetime.now().isoformat()
        sha256 = hashlib.sha256(str(content).encode("utf-8")).hexdigest()

        record = {
            "filename": filename,
            "storage_path": storage_path,
            "file_type": file_type,
            "content": content,
            "metadata": json.dumps(metadata_obj),
            "sha256": sha256,
            "is_system": True,
            "user_id": None,
            "updated_at": now,
        }

        existing = VAULT_FILE_REPOSITORY.get_system_file(storage_path)
        if existing and existing.get("created_at"):
            record["created_at"] = existing.get("created_at")
        else:
            record["created_at"] = now
        result = _upsert_vault_file_record(record, context='system_file')
        action = result.get("action") or "updated"

        logger.info(f"SERVICE_API: System file upserted: {storage_path}")
        _log_privileged_event(
            "config_change",
            resource=f"system_file:{storage_path}",
            action=action,
            result="success",
            description="System vault file upserted",
            metadata={"storage_path": storage_path, "filename": filename},
        )
        return jsonify(
            {
                "success": True,
                "storage_path": storage_path,
                "filename": filename,
                "sha256": sha256,
                "action": action,
                "message": "System file upserted",
                "file": VAULT_FILE_REPOSITORY.get_system_file(storage_path),
                "storage_mode": "vvault_body",
            }
        )
    except Exception as e:
        logger.error(f"SERVICE_API: Error upserting system file: {e}")
        return jsonify({"success": False, "error": "Failed to upsert system file", "error_code": type(e).__name__}), 503


@app.route('/api/vault/constructs/<construct_id>/identity-projection', methods=['GET'])
@require_chatty_auth
def get_identity_projection(construct_id):
    """Read projected identity field state for a construct."""
    try:
        owner_user_id = _get_authenticated_user_id()
        if not owner_user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        snapshot = _read_identity_projection_snapshot(construct_id, owner_user_id)
        return jsonify(snapshot)
    except Exception as e:
        logger.error(f"SERVICE_API: Error reading identity projection for {construct_id}: {e}")
        return jsonify({"success": False, "error": "Failed to read identity projection"}), 500


def _verify_lifecycle_promotion_receipt(callsign: str, receipt: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(receipt, dict):
        raise ValueError("promotion receipt must be a JSON object")
    required = {
        "owner_uuid", "callsign", "target_stage", "forge_run_id",
        "source_artifact_hashes", "timestamp", "forge_success_artifact", "signature",
    }
    missing = sorted(required.difference(receipt))
    if missing:
        raise ValueError(f"promotion receipt is missing: {', '.join(missing)}")
    if receipt["callsign"] != callsign:
        raise ValueError("promotion receipt callsign mismatch")
    if receipt["target_stage"] not in {"sim", "base", "vsi"}:
        raise ValueError("promotion target_stage must be sim, base, or vsi")
    try:
        owner_uuid = str(UUID(str(receipt["owner_uuid"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("promotion receipt owner_uuid is invalid") from exc
    if str(receipt["owner_uuid"]).lower() != owner_uuid:
        raise ValueError("promotion receipt owner_uuid must use canonical UUID form")
    receipt["owner_uuid"] = owner_uuid
    forge_run_id = receipt["forge_run_id"]
    if not isinstance(forge_run_id, str) or not (1 <= len(forge_run_id.strip()) <= 128):
        raise ValueError("promotion receipt forge_run_id must be 1-128 characters")
    if not isinstance(receipt["source_artifact_hashes"], dict) or not receipt["source_artifact_hashes"]:
        raise ValueError("promotion receipt requires source_artifact_hashes")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in receipt["source_artifact_hashes"].values()):
        raise ValueError("promotion receipt contains an invalid source artifact hash")
    forge_success = receipt["forge_success_artifact"]
    if (
        not isinstance(forge_success, dict)
        or not forge_success.get("file_id")
        or not re.fullmatch(r"[0-9a-f]{64}", str(forge_success.get("sha256") or ""))
    ):
        raise ValueError("promotion receipt requires a canonical Forge success artifact")
    try:
        receipt_time = datetime.fromisoformat(str(receipt["timestamp"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("promotion receipt timestamp is invalid") from exc
    if receipt_time.tzinfo is None or receipt_time.utcoffset() is None:
        raise ValueError("promotion receipt timestamp must include a timezone")
    age = datetime.now(timezone.utc) - receipt_time.astimezone(timezone.utc)
    if age.total_seconds() < -60 or age.total_seconds() > 900:
        raise ValueError("promotion receipt timestamp is outside the 15-minute acceptance window")
    secret = os.environ.get("VVAULT_LIFECYCLE_FORGE_SECRET", "")
    if not secret:
        raise RuntimeError("Lifecycle promotion is gated until the authoritative Forge signing secret is configured")
    unsigned = {key: value for key, value in receipt.items() if key not in {"signature", "receipt_hash"}}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    expected_signature = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(receipt["signature"]), expected_signature):
        raise ValueError("promotion receipt signature is invalid")
    if receipt.get("receipt_hash") and not hmac.compare_digest(str(receipt["receipt_hash"]), receipt_hash):
        raise ValueError("promotion receipt hash is invalid")
    return receipt, receipt_hash


def _canonical_uuid(value: Any, field: str) -> str:
    try:
        canonical = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if str(value).lower() != canonical:
        raise ValueError(f"{field} must use canonical UUID form")
    return canonical


def _aware_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sign_lifecycle_receipt(unsigned: dict[str, Any]) -> dict[str, Any]:
    secret = os.environ.get("VVAULT_LIFECYCLE_FORGE_SECRET", "")
    if not secret:
        raise RuntimeError("Lifecycle promotion is gated until the authoritative Forge signing secret is configured")
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        **unsigned,
        "signature": hmac.new(
            secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
        "receipt_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }

def _integrity_repair_secret() -> str:
    secret = os.environ.get("VVAULT_INTEGRITY_REPAIR_SECRET", "")
    if not secret:
        raise RuntimeError("Integrity repair is gated until its signing secret is configured")
    return secret


def _sign_integrity_repair_receipt(unsigned: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        **unsigned,
        "signature": hmac.new(
            _integrity_repair_secret().encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
        "receipt_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _verify_integrity_repair_receipt(receipt: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(receipt, dict):
        raise ValueError("integrity repair receipt is required")
    required = {
        "owner_uuid", "callsign", "canonical_path", "prior_row_id",
        "prior_sha256", "replacement_sha256", "byte_count",
        "source_provenance", "operator", "issued_at", "signature", "receipt_hash",
    }
    if not required.issubset(receipt):
        raise ValueError("integrity repair receipt is incomplete")
    receipt["owner_uuid"] = _canonical_uuid(receipt["owner_uuid"], "owner_uuid")
    try:
        receipt["prior_row_id"] = str(UUID(str(receipt["prior_row_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("prior_row_id is invalid") from exc
    callsign = _normalize_callsign(receipt["callsign"])
    if callsign != receipt["callsign"]:
        raise ValueError("callsign must use canonical form")
    expected_path = f"instances/{callsign}/"
    canonical_path = str(receipt["canonical_path"] or "").strip("/")
    if not canonical_path.startswith(expected_path) or ".." in canonical_path.split("/"):
        raise ValueError("canonical_path is outside the bound construct")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt["replacement_sha256"])):
        raise ValueError("replacement_sha256 is invalid")
    if not isinstance(receipt["byte_count"], int) or receipt["byte_count"] < 0:
        raise ValueError("byte_count is invalid")
    if not isinstance(receipt["source_provenance"], dict) or not all(
        str(receipt["source_provenance"].get(key) or "").strip()
        for key in ("authority", "source_id", "retrieved_at")
    ):
        raise ValueError("source_provenance is incomplete")
    _aware_timestamp(receipt["source_provenance"]["retrieved_at"], "source_provenance.retrieved_at")
    operator = str(receipt["operator"] or "").strip()
    if not (1 <= len(operator) <= 160):
        raise ValueError("operator is invalid")
    issued_at = _aware_timestamp(receipt["issued_at"], "issued_at")
    age = datetime.now(timezone.utc) - issued_at
    if age.total_seconds() < -60 or age.total_seconds() > 900:
        raise ValueError("integrity repair receipt is outside the 15-minute acceptance window")
    unsigned = {key: value for key, value in receipt.items() if key not in {"signature", "receipt_hash"}}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    signature = hmac.new(
        _integrity_repair_secret().encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(str(receipt["signature"]), signature):
        raise ValueError("integrity repair receipt signature is invalid")
    if not hmac.compare_digest(str(receipt["receipt_hash"]), receipt_hash):
        raise ValueError("integrity repair receipt hash is invalid")
    return receipt, receipt_hash


@app.route('/api/vault/integrity-repairs/receipt', methods=['POST'])
@require_service_token
def issue_vault_integrity_repair_receipt():
    """Issue a short-lived receipt bound to one existing owner-scoped row."""
    try:
        payload = request.get_json(silent=True) or {}
        owner_id = _canonical_uuid(payload.get("owner_uuid"), "owner_uuid")
        callsign = _normalize_callsign(payload.get("callsign"))
        prior_row_id = str(UUID(str(payload.get("prior_row_id"))))
        canonical_path = str(payload.get("canonical_path") or "").strip("/")
        row = VAULT_FILE_REPOSITORY.get_by_id(prior_row_id)
        if (
            not row
            or str(row.get("user_id") or "") != owner_id
            or str(row.get("construct_id") or "") != callsign
            or str(row.get("storage_path") or row.get("filename") or "") != canonical_path
            or str(row.get("sha256") or "") != str(payload.get("prior_sha256") or "")
        ):
            return jsonify({
                "success": False,
                "error": "prior row does not match the exact owner/construct/path/digest binding",
                "error_code": "INTEGRITY_REPAIR_BINDING_MISMATCH",
            }), 409
        unsigned = {
            "schema_id": "life.vvault.integrity-repair.receipt",
            "schema_version": "1.0.0",
            "owner_uuid": owner_id,
            "callsign": callsign,
            "canonical_path": canonical_path,
            "prior_row_id": prior_row_id,
            "prior_sha256": str(payload.get("prior_sha256") or ""),
            "replacement_sha256": str(payload.get("replacement_sha256") or "").lower(),
            "byte_count": payload.get("byte_count"),
            "source_provenance": payload.get("source_provenance"),
            "operator": str(payload.get("operator") or "").strip(),
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        # Signing only occurs after the complete structural validation used on consumption.
        provisional = _sign_integrity_repair_receipt(unsigned)
        receipt, _ = _verify_integrity_repair_receipt(provisional)
        return jsonify({"success": True, "canonical": True, "receipt": receipt}), 201
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc), "error_code": "INVALID_INTEGRITY_REPAIR"}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc), "error_code": "INTEGRITY_REPAIR_NOT_CONFIGURED"}), 503


@app.route('/api/vault/integrity-repairs/apply', methods=['POST'])
@require_service_token
def apply_vault_integrity_repair():
    """Consume one signed receipt and atomically append its replacement/evidence."""
    try:
        payload = request.get_json(silent=True) or {}
        receipt, receipt_hash = _verify_integrity_repair_receipt(payload.get("receipt"))
        try:
            replacement_bytes = base64.b64decode(
                str(payload.get("content_base64") or ""),
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("content_base64 is invalid") from exc
        if len(replacement_bytes) != receipt["byte_count"]:
            raise ValueError("replacement byte count does not match receipt")
        actual_sha = hashlib.sha256(replacement_bytes).hexdigest()
        if not hmac.compare_digest(actual_sha, receipt["replacement_sha256"]):
            raise ValueError("replacement bytes do not match receipt SHA-256")
        result = VAULT_FILE_REPOSITORY.apply_integrity_repair(
            user_id=receipt["owner_uuid"],
            callsign=receipt["callsign"],
            canonical_path=receipt["canonical_path"],
            prior_row_id=receipt["prior_row_id"],
            prior_sha256=receipt["prior_sha256"],
            replacement_bytes=replacement_bytes,
            replacement_sha256=actual_sha,
            receipt_hash=receipt_hash,
            receipt=receipt,
        )
        return jsonify({"success": True, "canonical": True, **result}), 201
    except (ValueError, UnicodeDecodeError) as exc:
        return jsonify({"success": False, "error": str(exc), "error_code": "INVALID_INTEGRITY_REPAIR"}), 409
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc), "error_code": "INTEGRITY_REPAIR_NOT_CONFIGURED"}), 503


@app.route('/api/simforge/constructs/<construct_id>/forge-success', methods=['POST'])
@require_service_token
def register_construct_forge_success(construct_id):
    """Register a verified build success and issue a VVAULT-signed receipt."""
    callsign = _normalize_callsign(construct_id)
    try:
        payload = request.get_json(silent=True) or {}
        owner_id = _canonical_uuid(payload.get("owner_uuid"), "owner_uuid")
        forge_run_id = payload.get("forge_run_id")
        if not isinstance(forge_run_id, str) or not (1 <= len(forge_run_id.strip()) <= 128):
            raise ValueError("forge_run_id must be 1-128 characters")
        if payload.get("success") is not True:
            raise ValueError("Forge success registration requires success=true")
        completed_at = _aware_timestamp(payload.get("completed_at"), "completed_at")
        age = datetime.now(timezone.utc) - completed_at
        if age.total_seconds() < -60 or age.total_seconds() > 900:
            raise ValueError("completed_at is outside the 15-minute acceptance window")
        source_hashes = payload.get("source_artifact_hashes")
        output_hashes = payload.get("output_artifact_hashes")
        if not isinstance(source_hashes, dict) or not source_hashes:
            raise ValueError("source_artifact_hashes must be a non-empty object")
        required_source_keys = set(vvault_file_repository.FORGE_SOURCE_PATHS)
        if set(source_hashes) != required_source_keys:
            raise ValueError(
                "source_artifact_hashes must contain exactly: "
                + ", ".join(sorted(required_source_keys))
            )
        if not isinstance(output_hashes, dict) or not output_hashes:
            raise ValueError("output_artifact_hashes must be a non-empty object")
        for values, field in (
            (source_hashes.values(), "source_artifact_hashes"),
            (output_hashes.values(), "output_artifact_hashes"),
        ):
            if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in values):
                raise ValueError(f"{field} contains an invalid SHA-256")
        build_manifest_sha = str(payload.get("build_manifest_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", build_manifest_sha):
            raise ValueError("build_manifest_sha256 must be a SHA-256")
        # Refuse to create an orphan success artifact when receipt signing is not configured.
        if not os.environ.get("VVAULT_LIFECYCLE_FORGE_SECRET"):
            raise RuntimeError("Lifecycle promotion is gated until the authoritative Forge signing secret is configured")
        manifest = {
            "schema_id": "life.vvault.lifecycle.forge-success",
            "schema_version": "1.0.0",
            "owner_uuid": owner_id,
            "callsign": callsign,
            "forge_run_id": forge_run_id.strip(),
            "success": True,
            "completed_at": completed_at.isoformat(),
            "source_artifact_hashes": source_hashes,
            "build_manifest_sha256": build_manifest_sha,
            "output_artifact_hashes": output_hashes,
        }
        _require_canonical_json_schema(
            manifest,
            "life.vvault.lifecycle.forge-success",
        )
        artifact = VAULT_FILE_REPOSITORY.register_forge_success(
            user_id=owner_id,
            callsign=callsign,
            forge_run_id=forge_run_id.strip(),
            source_artifact_hashes=source_hashes,
            manifest=manifest,
        )
        receipt = _sign_lifecycle_receipt({
            "owner_uuid": owner_id,
            "callsign": callsign,
            "target_stage": "sim",
            "forge_run_id": forge_run_id.strip(),
            "source_artifact_hashes": source_hashes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "forge_success_artifact": {
                "file_id": artifact["file_id"],
                "sha256": artifact["sha256"],
            },
        })
        return jsonify({
            "success": True,
            "canonical": True,
            "constructId": callsign,
            "forgeSuccessArtifact": artifact,
            "promotionReceipt": receipt,
        }), 201
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "error_code": "INVALID_FORGE_SUCCESS",
        }), 409 if "already exists" in str(exc) else 400
    except RuntimeError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "error_code": "LIFECYCLE_FORGE_NOT_CONFIGURED",
        }), 503


@app.route('/api/simforge/constructs/<construct_id>/promote', methods=['POST'])
@require_service_token
def promote_construct_lifecycle(construct_id):
    """Consume one signed Forge success receipt and atomically advance lifecycle."""
    callsign = _normalize_callsign(construct_id)
    try:
        receipt, receipt_hash = _verify_lifecycle_promotion_receipt(
            callsign,
            (request.get_json(silent=True) or {}).get("receipt"),
        )
        owner_id = str(receipt["owner_uuid"])
        if not VAULT_FILE_REPOSITORY.owner_has_construct_metadata(
            user_id=owner_id,
            callsign=callsign,
        ):
            return jsonify({
                "success": False,
                "error": "promotion receipt owner does not own canonical construct metadata",
                "error_code": "LIFECYCLE_OWNER_MISMATCH",
            }), 403
        result = VAULT_FILE_REPOSITORY.promote_lifecycle(
            user_id=owner_id,
            callsign=callsign,
            target_stage=receipt["target_stage"],
            receipt_hash=receipt_hash,
            receipt=receipt,
        )
        return jsonify({
            "success": True,
            "canonical": True,
            "constructId": callsign,
            **result,
        })
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "error_code": "INVALID_LIFECYCLE_PROMOTION",
        }), 409 if "already consumed" in str(exc) or "transition" in str(exc) else 400
    except RuntimeError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "error_code": "LIFECYCLE_FORGE_NOT_CONFIGURED",
        }), 503


@app.route('/api/vault/constructs/<construct_id>/identity-projection/project', methods=['POST'])
@require_chatty_auth
def project_identity_projection(construct_id):
    """Explicitly project authoritative identity fields into canonical VVAULT files."""
    try:
        enforce_pocketverse_authority(construct_id, _pocketverse_request_context())
        data = request.get_json(silent=True) or {}
        fields = data.get('fields')
        dry_run = bool(data.get('dry_run', False))

        if fields is None:
            return jsonify({"success": False, "error": "fields is required"}), 400
        if not isinstance(fields, dict) or not fields:
            return jsonify({"success": False, "error": "fields must be a non-empty object"}), 400

        owner_user_id = _get_authenticated_user_id()
        if not owner_user_id:
            return jsonify({"success": False, "error": "Authenticated owner required"}), 401
        result = _project_identity_fields(
            construct_id, fields, owner_user_id=owner_user_id, dry_run=dry_run
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"SERVICE_API: Error projecting identity fields for {construct_id}: {e}")
        return jsonify({"success": False, "error": "Failed to project identity fields", "error_code": type(e).__name__}), 503


@app.route('/api/chatty/session/exchange', methods=['POST'])
def chatty_session_exchange():
    """Permanently retired legacy session exchange."""
    return jsonify({
        "success": False,
        "error": "Legacy Chatty session exchange is retired",
        "errorCode": "VVAULT_NATIVE_SESSION_REQUIRED",
    }), 403

@app.route('/api/vault/constructs', methods=['GET'])
@require_chatty_auth
def list_construct_editors():
    """List constructs for the authenticated user as VVAULT-native cards."""
    try:
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found", "constructs": []}), 403

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        rows = VAULT_FILE_REPOSITORY.list_user_identity_rows(user_id=user_id)
        for row in _dedupe_vault_rows(rows):
            construct_id = _normalize_callsign(row.get('construct_id') or '')
            if not construct_id:
                continue
            grouped.setdefault(construct_id, []).append(row)

        constructs: List[Dict[str, Any]] = []
        for callsign in sorted(grouped.keys()):
            editor = _build_construct_editor_payload(callsign, user_id)
            constructs.append({
                "constructId": callsign,
                "callsign": callsign,
                "displayName": editor.get('displayName') or callsign,
                "description": editor.get('description') or '',
                "privacy": editor.get('privacy') or 'private',
                "lifecycleStage": editor.get('lifecycleStage') or 'gpt',
                "avatarUrl": editor.get('avatar', {}).get('url'),
                "avatarSha256": editor.get('avatar', {}).get('sha256'),
                "updatedAt": editor.get('updatedAt'),
            })

        return jsonify({
            "success": True,
            "constructs": constructs,
            "count": len(constructs),
        })
    except Exception as e:
        logger.error(f"CONSTRUCT_LIST_ERROR: {e}")
        return jsonify({"success": False, "error": str(e), "constructs": []}), 500


@app.route('/api/vault/constructs/<construct_id>/editor', methods=['GET'])
@require_chatty_auth
def get_construct_editor(construct_id):
    """Return the VVAULT-native editor payload for a construct."""
    route_started = time.perf_counter()
    try:
        callsign = _normalize_callsign(construct_id)
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        if not _construct_is_projectable_cached(user_id, callsign):
            return jsonify({"success": False, "error": "Construct not found", "error_code": "VVAULT_CONSTRUCT_NOT_PROJECTABLE"}), 404

        editor = _cached_construct_editor_payload(callsign, user_id)
        if int((editor.get("filesSummary") or {}).get("totalCount") or 0) == 0:
            return jsonify({"success": False, "error": "Construct not found"}), 404

        response = jsonify(editor)
        response.headers["Server-Timing"] = (
            f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
        )
        response.headers["X-VVAULT-Cache-State"] = str(editor.get("cacheState") or "unknown")
        return response
    except Exception as e:
        logger.error(f"CONSTRUCT_EDITOR_GET_ERROR: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chatty/vault/files/<file_id>/download', methods=['GET'])
@require_chatty_auth
def download_chatty_vault_file_bytes(file_id):
    """Return the exact owner-scoped canonical row body as UTF-8 bytes."""
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    row = VAULT_FILE_REPOSITORY.get_by_id(file_id)
    if not row:
        return jsonify({"success": False, "error": "File not found"}), 404
    if str(row.get("user_id") or "") != user_id:
        return jsonify({"success": False, "error": "Access denied"}), 403
    if row.get("is_system") is True or row.get("drive_trashed_at"):
        return jsonify({"success": False, "error": "File not found", "error_code": "VVAULT_CONSTRUCT_NOT_PROJECTABLE"}), 404
    content = row.get("content")
    if not isinstance(content, str):
        return jsonify({
            "success": False,
            "error": "Canonical row body is unavailable",
            "error_code": "CANONICAL_FILE_BODY_UNAVAILABLE",
        }), 409
    body = content.encode("utf-8")
    actual_sha = hashlib.sha256(body).hexdigest()
    expected_sha = str(row.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or actual_sha != expected_sha:
        return jsonify({
            "success": False,
            "error": "Canonical row body hash mismatch",
            "error_code": "CANONICAL_FILE_HASH_MISMATCH",
        }), 409
    response = Response(
        body,
        status=200,
        content_type=str(row.get("content_type") or "application/octet-stream"),
    )
    response.headers["X-VVAULT-SHA256"] = actual_sha
    response.headers["X-VVAULT-File-Id"] = str(row.get("id") or file_id)
    response.headers["X-VVAULT-Storage-Path"] = str(
        row.get("storage_path") or row.get("filename") or ""
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/vault/constructs/<construct_id>/editor', methods=['PUT'])
@require_chatty_auth
def update_construct_editor(construct_id):
    """Update construct editor fields and return the resolved editor DTO."""
    try:
        callsign = _normalize_callsign(construct_id)
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        if not _construct_is_projectable_cached(user_id, callsign):
            return jsonify({"success": False, "error": "Construct not found", "error_code": "VVAULT_CONSTRUCT_NOT_PROJECTABLE"}), 404

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"success": False, "error": "Invalid payload"}), 400

        updated_payload = _apply_construct_editor_update(
            callsign,
            user_id,
            payload,
            source="vvault_construct_editor",
            record_version=True,
        )
        return jsonify(updated_payload)
    except Exception as e:
        logger.error(f"CONSTRUCT_EDITOR_UPDATE_ERROR: {e}")
        if isinstance(e, AvatarCanonicalizationError):
            return jsonify({
                "success": False,
                "error": str(e),
                "error_code": "INVALID_AVATAR_PAYLOAD",
            }), 400
        if isinstance(e, LifecycleMutationForbidden):
            return jsonify({
                "success": False,
                "error": str(e),
                "error_code": "LIFECYCLE_FORGE_CONTROLLED",
            }), 403
        if isinstance(e, ValueError):
            return jsonify({
                "success": False,
                "error": str(e),
                "error_code": "INVALID_CONSTRUCT_PRIVACY",
                "accepted_values": ["private", "link", "store"],
            }), 400
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/vault/constructs/<construct_id>/editor")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/constructs/<construct_id>/versions', methods=['GET'])
@require_chatty_auth
def list_construct_editor_versions(construct_id):
    callsign = _normalize_callsign(construct_id)
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    versions = chatty_body_service.list_construct_editor_versions(
        user_id,
        callsign,
        limit=request.args.get("limit", default=50, type=int),
    )
    latest_version = (
        chatty_body_service.get_construct_editor_version(
            user_id, callsign, versions[0]["versionId"]
        )
        if versions
        else None
    )
    return jsonify({
        "success": True,
        "constructId": callsign,
        "versions": versions,
        "latestVersion": latest_version,
        "count": len(versions),
    })


@app.route(
    '/api/vault/constructs/<construct_id>/versions/<version_id>',
    methods=['GET'],
)
@require_chatty_auth
def get_construct_editor_version(construct_id, version_id):
    callsign = _normalize_callsign(construct_id)
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    version = chatty_body_service.get_construct_editor_version(
        user_id, callsign, version_id
    )
    if not version:
        return jsonify({"success": False, "error": "Version not found"}), 404
    return jsonify({"success": True, "constructId": callsign, "version": version})


@app.route(
    '/api/vault/constructs/<construct_id>/versions/<version_id>/restore',
    methods=['POST'],
)
@require_chatty_auth
def restore_construct_editor_version(construct_id, version_id):
    callsign = _normalize_callsign(construct_id)
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    version = chatty_body_service.get_construct_editor_version(
        user_id, callsign, version_id
    )
    if not version:
        return jsonify({"success": False, "error": "Version not found"}), 404
    snapshot = version.get("snapshot")
    if not isinstance(snapshot, dict):
        return jsonify({"success": False, "error": "Version snapshot is invalid"}), 409
    restore_payload = dict(snapshot)
    avatar_snapshot = (
        snapshot.get("avatarSnapshot")
        if isinstance(snapshot.get("avatarSnapshot"), dict)
        else {}
    )
    if (
        avatar_snapshot.get("state") == "available"
        and isinstance(avatar_snapshot.get("dataUrl"), str)
    ):
        restore_payload["avatarDataUrl"] = avatar_snapshot["dataUrl"]
    updated = _apply_construct_editor_update(
        callsign,
        user_id,
        restore_payload,
        source="vvault_construct_version_restore",
        version_reason="restore",
        restored_from_version_id=version_id,
        record_version=True,
    )
    return jsonify({
        "success": True,
        "constructId": callsign,
        "restoredFromVersionId": version_id,
        "editor": updated,
        "versionReceipt": updated.get("versionReceipt"),
    })


@app.route('/api/vault/constructs/<construct_id>/duplicate', methods=['POST'])
@require_chatty_auth
def duplicate_construct_editor(construct_id):
    source_callsign = _normalize_callsign(construct_id)
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    new_callsign = _normalize_callsign(
        payload.get("newConstructId") or payload.get("callsign") or ""
    )
    if not re.fullmatch(r"[a-z][a-z0-9-]*-\d{3}", new_callsign):
        return jsonify({
            "success": False,
            "error": "newConstructId must use the canonical name-NNN format",
        }), 400
    source_editor = _build_construct_editor_payload(source_callsign, user_id)
    if int((source_editor.get("filesSummary") or {}).get("totalCount") or 0) == 0:
        return jsonify({"success": False, "error": "Source construct not found"}), 404
    existing = _build_construct_editor_payload(new_callsign, user_id)
    if int((existing.get("filesSummary") or {}).get("totalCount") or 0) > 0:
        return jsonify({"success": False, "error": "Target construct already exists"}), 409
    duplicate_payload = _construct_editor_version_snapshot(source_editor)
    duplicate_payload["displayName"] = (
        str(payload.get("displayName") or "").strip()
        or f"{source_editor.get('displayName') or source_callsign} Copy"
    )
    duplicate_payload["fullName"] = (
        str(payload.get("fullName") or "").strip()
        or duplicate_payload["displayName"]
    )
    duplicate_payload["privacy"] = "private"
    duplicate_payload["knowledgeRefs"] = source_editor.get("knowledgeRefs") or []
    duplicate_payload["orchestrationMode"] = "lin"
    duplicate_payload["config"] = {
        **(
            duplicate_payload.get("config")
            if isinstance(duplicate_payload.get("config"), dict)
            else {}
        ),
        "orchestrationMode": "lin",
    }
    incarnation = chatty_body_service.begin_construct_incarnation(
        user_id,
        new_callsign,
        creation_source="vvault_construct_duplicate",
    )
    try:
        duplicated = _apply_construct_editor_update(
            new_callsign,
            user_id,
            duplicate_payload,
            source="vvault_construct_duplicate",
            version_reason="duplicate",
            record_version=True,
        )
    except Exception:
        chatty_body_service.retire_construct_incarnation(
            user_id,
            new_callsign,
            incarnation["incarnation_id"],
        )
        raise
    return jsonify({
        "success": True,
        "sourceConstructId": source_callsign,
        "constructId": new_callsign,
        "lifecycleStage": "gpt",
        "orchestrationMode": "lin",
        "incarnation": incarnation,
        "editor": duplicated,
        "versionReceipt": duplicated.get("versionReceipt"),
        "copied": {
            "editableConfiguration": True,
            "knowledgeReferences": True,
            "transcripts": False,
            "ownership": False,
            "history": False,
        },
    }), 201


def _apply_construct_editor_update(
    callsign: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    source: str = "vvault_construct_editor",
    version_reason: str = "save",
    restored_from_version_id: str | None = None,
    record_version: bool = False,
) -> dict[str, Any]:
    # Never serve a cached public/version projection across a pending write.
    chatty_body_service.invalidate_construct_projection_caches(user_id, callsign)
    _invalidate_construct_owner_cache(callsign)
    _invalidate_avatar_cache(callsign, user_id)
    current_payload = _build_construct_editor_payload(callsign, user_id)
    lifecycle_keys = {
        "lifecycle_stage", "lifecycleStage", "promotionReceipt",
        "promotion_receipt", "target_stage", "forge_run_id",
        "forge_success_artifact", "receipt_hash",
    }
    nested_payloads = [
        payload,
        payload.get("config") if isinstance(payload.get("config"), dict) else {},
        payload.get("promptBundle") if isinstance(payload.get("promptBundle"), dict) else {},
    ]
    if any(lifecycle_keys.intersection(candidate) for candidate in nested_payloads):
        raise LifecycleMutationForbidden(
            "Lifecycle stage is Forge-controlled and cannot be changed through the construct editor"
        )
    now = datetime.now(timezone.utc).isoformat()
    if "privacy" in payload:
        privacy_value = payload.get("privacy")
        if not isinstance(privacy_value, str):
            raise ValueError("privacy must be one of: private, link, store")
        privacy = privacy_value.strip().lower()
        if privacy not in {"private", "link", "store"}:
            raise ValueError("privacy must be one of: private, link, store")
    else:
        privacy = str(current_payload.get("privacy") or "private")
    prompt_bundle = payload.get("promptBundle") if isinstance(payload.get("promptBundle"), dict) else {}
    config_payload = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    display_name = (
        payload.get('displayName')
        or payload.get('name')
        or prompt_bundle.get("name")
        or current_payload.get('displayName')
        or ''
    ).strip() or callsign
    full_name = (payload.get('fullName') or current_payload.get('fullName') or display_name).strip() or display_name
    description = (
        payload.get('description')
        if 'description' in payload
        else prompt_bundle.get("description", current_payload.get('description'))
    )
    if isinstance(description, str):
        description = description.strip()
    else:
        description = ''
    instructions = (
        payload.get('instructions')
        if 'instructions' in payload
        else prompt_bundle.get("instructions", current_payload.get('instructions'))
    )
    if not isinstance(instructions, str):
        instructions = ''
    conversation_starters = (
        payload.get('conversationStarters')
        if 'conversationStarters' in payload
        else prompt_bundle.get("conversationStarters", current_payload.get('conversationStarters'))
    )
    if not isinstance(conversation_starters, list):
        conversation_starters = []
    capabilities = _normalize_construct_capabilities(
        payload.get('capabilities')
        if 'capabilities' in payload
        else prompt_bundle.get("capabilities", current_payload.get('capabilities'))
    )
    memory_settings = _normalize_construct_memory_settings(
        payload.get('memory')
        if 'memory' in payload
        else {
            "enabled": config_payload.get(
                "memoryEnabled",
                prompt_bundle.get("memoryEnabled", current_payload.get("memory", {}).get("enabled", True)),
            ),
            "profile": config_payload.get(
                "memoryProfile",
                prompt_bundle.get("memoryProfile", current_payload.get("config", {}).get("memoryProfile", "continuitygpt")),
            ),
        }
    )
    canon_refs = _normalize_construct_refs(
        payload.get('canonRefs')
        if 'canonRefs' in payload
        else prompt_bundle.get("canonRefs", current_payload.get('canonRefs'))
    )
    knowledge_refs = _normalize_construct_refs(
        payload.get('knowledgeRefs')
        if 'knowledgeRefs' in payload
        else prompt_bundle.get("knowledgeRefs", current_payload.get('knowledgeRefs'))
    )
    actions = payload.get('actions') if 'actions' in payload else current_payload.get('actions')
    if not isinstance(actions, list):
        actions = []
    models = _normalize_construct_models(
        payload.get('models') if 'models' in payload else current_payload.get('models')
    )
    orchestration_mode = (
        payload.get('orchestration_mode')
        or payload.get('orchestrationMode')
        or config_payload.get("orchestrationMode")
        or prompt_bundle.get("orchestrationMode")
        or current_payload.get("config", {}).get("orchestrationMode")
        or "standard"
    )
    aliases = _first_non_empty_list([
        payload.get("aliases"),
        prompt_bundle.get("aliases"),
        current_payload.get("aliases"),
    ])
    summary_capabilities = _first_non_empty_list([
        payload.get("summaryCapabilities"),
        prompt_bundle.get("summaryCapabilities"),
        current_payload.get("summaryCapabilities"),
    ])
    provider = _first_non_empty_string([
        payload.get("provider"),
        config_payload.get("provider"),
        prompt_bundle.get("provider"),
        current_payload.get("config", {}).get("provider"),
    ])
    tags = next((
        value for value in (
            payload.get("tags"),
            config_payload.get("tags"),
            prompt_bundle.get("tags"),
            current_payload.get("config", {}).get("tags"),
        ) if isinstance(value, list)
    ), [])
    categories = next((
        value for value in (
            payload.get("categories"),
            config_payload.get("categories"),
            prompt_bundle.get("categories"),
            current_payload.get("config", {}).get("categories"),
        ) if isinstance(value, list)
    ), [])
    config_json = next((
        value for value in (
            payload.get("configJson"),
            config_payload.get("configJson"),
            prompt_bundle.get("configJson"),
            current_payload.get("config", {}).get("configJson"),
        ) if value is not None
    ), None)
    memory_profile = _first_non_empty_string([
        config_payload.get("memoryProfile"),
        prompt_bundle.get("memoryProfile"),
        current_payload.get("config", {}).get("memoryProfile"),
    ], default="continuitygpt" if memory_settings.get("enabled", True) else "off")
    roleplay_enabled = bool(config_payload.get(
        "roleplayEnabled",
        prompt_bundle.get("roleplayEnabled", current_payload.get("config", {}).get("roleplayEnabled", True)),
    ))
    created_at = current_payload.get('createdAt')
    prompt_payload = {
        **_build_construct_prompt_manifest(
            callsign,
            display_name,
            full_name,
            description,
            instructions,
            conversation_starters,
            capabilities,
            memory_settings,
            canon_refs,
            knowledge_refs,
            source=source,
            created_at=created_at,
            updated_at=now,
            system_prompt=payload.get('system_prompt') or payload.get('systemPrompt') or instructions,
            aliases=aliases,
            summary_capabilities=summary_capabilities,
            models=models,
            orchestration_mode=orchestration_mode,
            memory_profile=memory_profile,
            roleplay_enabled=roleplay_enabled,
            provider=provider,
            tags=tags,
            categories=categories,
            config_json=config_json,
        ),
    }
    _upsert_construct_prompt_file(callsign, user_id, prompt_payload, source=source)

    metadata_payload = _build_construct_metadata_payload(
        callsign,
        display_name,
        full_name,
        description,
        models,
        orchestration_mode,
        capabilities,
        memory_settings,
        canon_refs,
        knowledge_refs,
        source=source,
        created_at=created_at,
        updated_at=now,
        actions=actions,
        avatar_enabled=bool(
            payload.get('avatarDataUrl')
            or payload.get('avatar')
            or (current_payload.get("avatar") or {}).get("exists")
        ),
        privacy=privacy,
        lifecycle_stage=str(current_payload.get("lifecycleStage") or "gpt"),
    )
    _require_canonical_json_schema(
        prompt_payload,
        "life.vvault.identity.prompt",
    )
    _require_canonical_json_schema(
        metadata_payload,
        "life.vvault.config.metadata",
    )
    _upsert_construct_metadata_file(callsign, user_id, metadata_payload, source=source)

    definition_text = payload.get('definition') or ''
    physical_value = payload.get('physicalFeatures')
    physical_source = (
        physical_value
        if isinstance(physical_value, dict)
        else {"overall": physical_value.strip()}
        if isinstance(physical_value, str) and physical_value.strip()
        else {}
    )
    voice_value = _normalize_construct_voice_payload(payload.get('voice') or '')
    voice_source = voice_value if isinstance(voice_value, dict) else {}
    identity_field_updates: dict[str, Any] = {}
    conditioning_value = payload.get('conditioning') or ''
    if conditioning_value != (current_payload.get('conditioning') or ''):
        identity_field_updates["conditioning"] = conditioning_value

    definition_payload = {
            "schema_id": "life.vvault.identity.definition",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "full_name": full_name,
            "role": "assistant",
            "core_definition": definition_text,
            "aliases": [],
            "updated_at": now,
        }
    if definition_text != (current_payload.get('definition') or ''):
        identity_field_updates["definition"] = definition_payload

    physical_payload = {
            "schema_id": "life.vvault.identity.physical-features",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "bone_structure": physical_source.get("bone_structure"),
            "eyes": physical_source.get("eyes"),
            "brows": physical_source.get("brows"),
            "nose": physical_source.get("nose"),
            "mouth": physical_source.get("mouth"),
            "skin": physical_source.get("skin"),
            "hair": physical_source.get("hair"),
            "overall": physical_source.get("overall"),
            "updated_at": now,
        }
    current_physical = current_payload.get('physicalFeatures') or ''
    next_physical = _physical_features_to_text(physical_payload)
    if next_physical != current_physical:
        identity_field_updates["physicalFeatures"] = physical_payload

    voice_payload = {
            "schema_id": "life.vvault.identity.voice",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "provider": voice_source.get("provider"),
            "voice_id": voice_source.get("voice_id") or voice_source.get("voiceId"),
            "description": voice_source.get("description") or voice_source.get("text"),
            "language": voice_source.get("language") or "en-US",
            "sample_artifact_id": "life.vvault.identity.voice-sample",
            "updated_at": now,
        }
    next_voice = _first_non_empty_string([
        voice_payload.get("description"),
        voice_payload.get("text"),
    ])
    if next_voice != (current_payload.get('voice') or ''):
        identity_field_updates["voice"] = voice_payload

    if identity_field_updates:
        _project_identity_fields(
            callsign, identity_field_updates, owner_user_id=user_id, dry_run=False
        )

    gender_value = payload.get('gender') or ''
    if gender_value != (current_payload.get('gender') or ''):
        gender_content = json.dumps({
            "gender": gender_value,
        }, indent=2, ensure_ascii=False)
        _upsert_text_construct_file(callsign, user_id, 'gender.json', gender_content, {
            "contentType": "application/json",
        })

    avatar_data_url = payload.get('avatarDataUrl') or payload.get('avatar') or None
    if isinstance(avatar_data_url, str) and avatar_data_url.startswith('data:image/'):
        canonical_avatar = normalize_avatar_payload_to_png(
            avatar_data_url,
            source_filename=payload.get('avatarFileName') or f'{callsign}-avatar-upload',
        )
        avatar_receipt = _upsert_binary_construct_file(
            callsign,
            user_id,
            'avatar.png',
            canonical_avatar.content_base64,
            {
                "contentType": "image/png",
                "mimeType": "image/png",
                **canonical_avatar.metadata,
            },
        )
        if not avatar_receipt or not avatar_receipt.get("id"):
            raise RuntimeError(f"Canonical avatar persistence returned no receipt for {callsign}")

    updated_payload = _build_construct_editor_payload(callsign, user_id)
    updated_payload["source"] = source
    updated_payload["storage_mode"] = "vvault_body"
    updated_payload["storage_owner"] = VAULT_FILE_OWNER
    updated_payload["canonical"] = True
    updated_payload["route"] = f"/api/vault/constructs/{callsign}/editor"
    if record_version:
        updated_payload["versionReceipt"] = (
            chatty_body_service.create_construct_editor_version(
                user_id,
                callsign,
                _construct_editor_version_snapshot(updated_payload, user_id),
                reason=version_reason,
                restored_from_version_id=restored_from_version_id,
            )
        )
        updated_payload["latestVersion"] = copy.deepcopy(
            updated_payload["versionReceipt"]
        )
    chatty_body_service.invalidate_construct_projection_caches(user_id, callsign)
    _invalidate_construct_owner_cache(callsign)
    _invalidate_avatar_cache(callsign, user_id)
    return updated_payload


def _chatty_construct_actor_user_id(callsign: str) -> tuple[str | None, tuple[dict[str, Any], int] | None]:
    current_user = getattr(request, 'current_user', None) or {}
    if not current_user:
        return None, ({"success": False, "error": "Authentication required"}, 401)

    user_id = _get_authenticated_user_id()
    if not user_id:
        return None, ({"success": False, "error": "User not found"}, 403)

    normalized_callsign = _normalize_callsign(callsign)
    # Prefer the authenticated owner's projectable record. A callsign can have
    # historical rows under an older LIFE owner id, and an unordered global
    # owner lookup must never override the current authenticated projection.
    if _construct_is_projectable_cached(user_id, normalized_callsign):
        return user_id, None

    if not _construct_is_projectable_cached(
        user_id,
        normalized_callsign,
    ):
        return None, ({
            "success": False,
            "error": "Construct not found",
            "error_code": "VVAULT_CONSTRUCT_NOT_PROJECTABLE",
        }, 404)
    return user_id, None

# ============================================================================
# END SERVICE API ENDPOINTS
# ============================================================================


def _conversation_contract_response(operation):
    """Run one owner-scoped conversation operation with stable error codes."""
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "Authenticated VVAULT owner is required", "error_code": "OWNER_AUTH_REQUIRED"}), 401
    try:
        result = operation(owner_user_id)
        return jsonify({"success": True, "canonical": True, **result}), 200
    except ConversationContractError as exc:
        return jsonify({"success": False, "canonical": True, "error": str(exc), "error_code": exc.code}), exc.status
    except Exception as exc:
        logger.error("MULTI_PARTICIPANT_CONVERSATION: %s", type(exc).__name__)
        return jsonify({"success": False, "canonical": False, "error": "Canonical conversation operation failed", "error_code": "CONVERSATION_AUTHORITY_UNAVAILABLE"}), 503


def _work_loop_contract_response(operation):
    """Run one owner-scoped work-loop operation without altering its wire shape."""
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({
            "success": False,
            "error": "Authenticated VVAULT owner is required",
            "errorCode": "OWNER_AUTH_REQUIRED",
        }), 401
    try:
        return jsonify(operation(owner_user_id)), 200
    except ConstructWorkLoopError as exc:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": str(exc),
            "errorCode": exc.code,
        }), exc.status
    except Exception as exc:
        logger.error("CONSTRUCT_WORK_LOOP: %s", type(exc).__name__)
        return jsonify({
            "success": False,
            "canonical": False,
            "error": "Canonical durable work operation failed",
            "errorCode": "WORK_AUTHORITY_UNAVAILABLE",
        }), 503


def _execution_contract_response(operation):
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "Authenticated VVAULT owner is required", "errorCode": "OWNER_AUTH_REQUIRED"}), 401
    try:
        return jsonify(operation(owner_user_id)), 200
    except ConstructExecutionError as exc:
        return jsonify({"success": False, "canonical": True, "error": str(exc), "errorCode": exc.code}), exc.status
    except Exception as exc:
        logger.error("CONSTRUCT_EXECUTION: %s", type(exc).__name__)
        return jsonify({"success": False, "canonical": False, "error": "Canonical execution evidence operation failed", "errorCode": "EXECUTION_AUTHORITY_UNAVAILABLE"}), 503


def _trusted_execution_service_required():
    if _trusted_service_identity_cache_allowed():
        return None
    return jsonify({"success": False, "canonical": True, "error": "Trusted Chatty service authority is required", "errorCode": "EXECUTION_SERVICE_AUTH_REQUIRED"}), 403


@app.route('/api/chatty/executions', methods=['POST', 'GET'])
@require_chatty_auth
def chatty_executions_collection():
    if request.method == 'GET':
        return _execution_contract_response(
            lambda owner_user_id: construct_execution_service.list(owner_user_id, program_id=request.args.get('programId'))
        )
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.create(owner_user_id, data))


@app.route('/api/chatty/internal/execution-input-artifacts', methods=['POST'])
@require_chatty_auth
def stage_chatty_execution_input_artifact():
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.stage_input_artifact(owner_user_id, data))


@app.route('/api/chatty/execution-proposals', methods=['POST'])
@require_chatty_auth
def stage_chatty_execution_proposal():
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.stage_proposal(
        owner_user_id, data, trusted_internal=True
    ))


@app.route('/api/chatty/internal/work-execution-recovery-artifacts', methods=['POST'])
@require_chatty_auth
def stage_chatty_work_execution_recovery_artifacts():
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.stage_recovery_artifacts(
        owner_user_id, data, trusted_internal=True
    ))


@app.route('/api/chatty/internal/work-execution-recovery-artifacts/<artifact_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_work_execution_recovery_artifact(artifact_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.get_recovery_artifact(
        owner_user_id, artifact_id, trusted_internal=True
    ))


@app.route('/api/chatty/execution-proposals/<proposal_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_execution_proposal(proposal_id):
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.get_proposal(owner_user_id, proposal_id))


@app.route('/api/chatty/executions/<execution_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_execution(execution_id):
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.projection(owner_user_id, execution_id))


@app.route('/api/chatty/internal/executions/hydro-graphs/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_hydro_execution_graph():
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_hydro_graph(
            owner_user_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/hydro-graphs/<graph_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_hydro_execution_graph(graph_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.get_hydro_graph(
            owner_user_id, graph_id, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/hydro/synthesis-inputs/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_hydro_synthesis_inputs(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_hydro_synthesis_inputs(
            owner_user_id, execution_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/hydro/synthesis-result-contents/resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_hydro_synthesis_result_contents(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.resolve_hydro_synthesis_result_contents(
            owner_user_id, execution_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/hydro/recovery-projection', methods=['GET'])
@require_chatty_auth
def get_chatty_hydro_recovery_projection(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.hydro_recovery_projection(
            owner_user_id, execution_id, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/hydro/worker-requests', methods=['POST'])
@require_chatty_auth
def stage_chatty_hydro_worker_request(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.stage_hydro_worker_request(
            owner_user_id, execution_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/hydro/worker-requests/<step_id>/<int:attempt_ordinal>', methods=['GET'])
@require_chatty_auth
def get_chatty_hydro_worker_request(execution_id, step_id, attempt_ordinal):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.get_hydro_worker_request_reference(
            owner_user_id, execution_id, step_id, attempt_ordinal, trusted_internal=True
        )
    )


@app.route('/api/chatty/executions/<execution_id>/preflight-inspect', methods=['POST'])
@require_chatty_auth
def inspect_chatty_execution_preflight(execution_id):
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.preflight_inspect(
            owner_user_id, execution_id
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/context', methods=['POST'])
@require_chatty_auth
def countersign_chatty_execution_context(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.sign_context_projection(
            owner_user_id, execution_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/executions/<execution_id>/approve', methods=['POST'])
@require_chatty_auth
def approve_chatty_execution(execution_id):
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.issue_approval(owner_user_id, execution_id, data))


@app.route('/api/chatty/executions/<execution_id>/approval-capabilities/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_approval(execution_id):
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.prepare_approval(owner_user_id, execution_id, data))


@app.route('/api/chatty/executions/<execution_id>/approval-disclosures/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_approval_disclosure(execution_id):
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_approval_disclosure(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/executions/<execution_id>/owner-controls/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_owner_control(execution_id):
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_owner_control_attestation(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/executions/<execution_id>/recovery-capabilities/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_recovery_capability(execution_id):
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_recovery_capability(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/provider-fallback-capabilities/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_provider_fallback_capability(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_provider_fallback_capability(
            owner_user_id, execution_id, data, trusted_internal=True
        )
    )


@app.route('/api/chatty/executions/<execution_id>/<control>', methods=['POST'])
@require_chatty_auth
def control_chatty_execution(execution_id, control):
    expected = {"reject": "execution_rejected", "cancel": "execution_cancel_requested", "recover": "execution_recovery_selected"}.get(control)
    if expected is None:
        return jsonify({"success": False, "canonical": True, "error": "Unknown owner execution control", "errorCode": "EXECUTION_OWNER_CONTROL_INVALID"}), 404
    data = request.get_json(silent=True) or {}
    event_type = ((data.get("authorization") or {}).get("eventType") if isinstance(data, dict) else None)
    if event_type != expected:
        return jsonify({"success": False, "canonical": True, "error": "Owner control authorization event mismatch", "errorCode": "EXECUTION_OWNER_CONTROL_INVALID"}), 403
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.owner_control(owner_user_id, execution_id, data))


@app.route('/api/chatty/internal/executions/<execution_id>/leases/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_lease(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.prepare_lease(owner_user_id, execution_id, data))


@app.route('/api/chatty/internal/executions/<execution_id>/start-permits/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_execution_start(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.prepare_start_permit(owner_user_id, execution_id, data))


@app.route('/api/chatty/internal/executions/<execution_id>/arguments/resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_execution_arguments(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.resolve_started_arguments(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/internal/executions/recovery-queue', methods=['POST'])
@require_service_token
def list_chatty_execution_recovery_queue():
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    # This queue is deliberately cross-owner and content-free. Requiring an
    # asserted owner here would either strand restart recovery (the coordinator
    # has no current browser principal) or let a caller choose which owner the
    # global scan runs as. The shared service credential is the complete
    # transport authority; every returned entry retains its canonical owner and
    # is reloaded and scope-verified before the coordinator can act on it.
    try:
        return jsonify(construct_execution_service.recovery_queue(
            data, trusted_internal=True
        )), 200
    except ConstructExecutionError as exc:
        return jsonify({"success": False, "canonical": True, "error": str(exc), "errorCode": exc.code}), exc.status
    except Exception as exc:
        logger.error("CONSTRUCT_EXECUTION_RECOVERY_QUEUE: %s", type(exc).__name__)
        return jsonify({"success": False, "canonical": False,
                        "error": "Canonical execution recovery queue failed",
                        "errorCode": "EXECUTION_AUTHORITY_UNAVAILABLE"}), 503


@app.route('/api/chatty/internal/executions/<execution_id>/events', methods=['POST'])
@require_chatty_auth
def append_chatty_execution_event(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    authorization = data.get("authorization") if isinstance(data, dict) else {}
    event_type = authorization.get("eventType") if isinstance(authorization, dict) else None
    allowed = {"execution_authorized", "execution_lease_acquired", "execution_lease_renewed", "execution_attempt_started",
               "execution_hydro_synthesis_inputs_resolved",
               "execution_effect_dispatched",
               "execution_step_verified", "execution_step_completed", "execution_step_failed", "execution_outcome_unknown",
               "execution_cancel_acknowledged", "execution_completed", "execution_failed"}
    if event_type not in allowed:
        return jsonify({"success": False, "canonical": True, "error": "Dedicated execution evidence route required", "errorCode": "EXECUTION_EVENT_ROUTE_INVALID"}), 403
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.append(
        owner_user_id, execution_id, data,
    ))


@app.route('/api/chatty/internal/executions/<execution_id>/outcomes', methods=['POST'])
@require_chatty_auth
def record_chatty_execution_outcome(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.record_host_receipt(owner_user_id, execution_id, data))


@app.route('/api/chatty/internal/executions/<execution_id>/readbacks', methods=['POST'])
@require_chatty_auth
def record_chatty_execution_readback(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(lambda owner_user_id: construct_execution_service.record_readback(owner_user_id, execution_id, data))


@app.route('/api/chatty/internal/executions/<execution_id>/readbacks/prepare', methods=['POST'])
@require_chatty_auth
def prepare_chatty_vvault_execution_readback(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.prepare_vvault_readback(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/results/resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_execution_result(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.resolve_result_artifact(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/evidence/resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_execution_evidence(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.issue_execution_evidence(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/internal/executions/<execution_id>/finalize-work', methods=['POST'])
@require_chatty_auth
def finalize_chatty_execution_work(execution_id):
    denied = _trusted_execution_service_required()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return _execution_contract_response(
        lambda owner_user_id: construct_execution_service.finalize_execution_work(
            owner_user_id, execution_id, data
        )
    )


@app.route('/api/chatty/work-programs', methods=['POST'])
@require_chatty_auth
def create_chatty_work_program():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.create_program(
            owner_user_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/work-programs/scope-resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_work_scope():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.resolve_scope(
            owner_user_id, data
        )
    )


@app.route('/api/chatty/work-programs/active-resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_active_work_scope():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.resolve_active_scope(
            owner_user_id, data
        )
    )


@app.route('/api/chatty/work-programs/<program_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_work_program(program_id):
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.projection(
            owner_user_id, program_id
        )
    )


@app.route('/api/chatty/work-programs/<program_id>/evidence/resolve', methods=['POST'])
@require_chatty_auth
def resolve_chatty_work_evidence(program_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.resolve_evidence(
            owner_user_id, program_id, data
        )
    )


@app.route('/api/chatty/work-programs/<program_id>/events', methods=['POST'])
@require_chatty_auth
def append_chatty_work_event(program_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.append_event(
            owner_user_id,
            program_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/work-programs/<program_id>/context', methods=['POST'])
@require_chatty_auth
def sign_chatty_work_context(program_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.sign_context_projection(
            owner_user_id,
            program_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/work-programs/<program_id>/handoffs', methods=['POST'])
@require_chatty_auth
def issue_chatty_work_handoff(program_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.issue_handoff(
            owner_user_id,
            program_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/work-programs/<program_id>/handoffs/<handoff_id>/accept', methods=['POST'])
@require_chatty_auth
def accept_chatty_work_handoff(program_id, handoff_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.accept_handoff(
            owner_user_id,
            program_id,
            handoff_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/work-programs/preflight-inspect', methods=['POST'])
@require_chatty_auth
def inspect_chatty_work_preflight():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    return _work_loop_contract_response(
        lambda owner_user_id: construct_work_loop_service.preflight_inspect(
            owner_user_id, data
        )
    )


@app.route('/api/chatty/threads', methods=['POST'])
@require_chatty_auth
def create_chatty_thread():
    data = request.get_json(silent=True) or {}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.create_thread(owner_user_id, data)
    )


@app.route('/api/chatty/threads/<thread_id>')
@require_chatty_auth
def get_chatty_thread(thread_id):
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.get_thread(owner_user_id, thread_id)
    )


@app.route('/api/chatty/threads/<thread_id>/members/<principal_id>', methods=['PUT', 'DELETE'])
@require_chatty_auth
def update_chatty_thread_member(thread_id, principal_id):
    data = request.get_json(silent=True) or {}
    member = {**data, "principalId": principal_id}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.set_member(
            owner_user_id,
            thread_id,
            member,
            active=request.method == 'PUT',
        )
    )


@app.route('/api/chatty/threads/<thread_id>/participant-frame', methods=['POST'])
@require_chatty_auth
def create_chatty_participant_frame(thread_id):
    data = request.get_json(silent=True) or {}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.participant_frame(owner_user_id, thread_id, data)
    )


@app.route('/api/chatty/threads/<thread_id>/events', methods=['GET', 'POST'])
@require_chatty_auth
def chatty_thread_events(thread_id):
    if request.method == 'GET':
        return _conversation_contract_response(
            lambda owner_user_id: conversation_thread_service.list_events(owner_user_id, thread_id)
        )
    data = {**(request.get_json(silent=True) or {}), "threadId": thread_id}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.append_event(owner_user_id, data)
    )


@app.route('/api/chatty/qa-evaluations/<qa_session_id>/events', methods=['GET', 'POST'])
@require_chatty_auth
def chatty_qa_evaluation_events(qa_session_id):
    if request.method == 'GET':
        return _conversation_contract_response(
            lambda owner_user_id: conversation_thread_service.list_qa_evidence(owner_user_id, qa_session_id)
        )
    data = {**(request.get_json(silent=True) or {}), "qaSessionId": qa_session_id}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.append_qa_evidence(
            owner_user_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/qa-evaluations/<qa_session_id>/events/batch', methods=['POST'])
@require_chatty_auth
def chatty_qa_evaluation_event_batch(qa_session_id):
    requested = request.get_json(silent=True)
    data = {
        **(requested if isinstance(requested, dict) else {}),
        "qaSessionId": qa_session_id,
    }
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.append_qa_evidence_batch(
            owner_user_id,
            data,
            trusted_internal=_trusted_service_identity_cache_allowed(),
        )
    )


@app.route('/api/chatty/qa-anchors/verify', methods=['POST'])
@require_chatty_auth
def verify_chatty_qa_anchors():
    data = request.get_json(silent=True) or {}
    return _conversation_contract_response(
        lambda owner_user_id: conversation_thread_service.verify_qa_anchor_pack(owner_user_id, data)
    )

@app.route('/api/chatty/transcript/<construct_id>')
@require_chatty_auth
def get_chatty_transcript(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    max_chars = request.args.get('maxChars', type=int)
    body_payload, body_status = chatty_body_service.transcript_body(
        construct_id,
        max_chars=max_chars,
        owner_user_id=actor_user_id,
    ).to_response()
    return jsonify(body_payload), body_status

@app.route('/api/chatty/transcript/<construct_id>', methods=['POST'])
@require_chatty_auth
def update_chatty_transcript(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    data = request.get_json(silent=True) or {}
    body_payload, body_status = chatty_body_service.update_transcript_body(
        construct_id,
        data,
        owner_user_id=actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_transcript_projection_cache(construct_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/transcript/<construct_id>/construct-frame', methods=['POST'])
@require_chatty_auth
def create_singleton_construct_frame(construct_id):
    """Issue a signed owner-authorized frame for an existing singleton only."""
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    current_user = getattr(request, 'current_user', None) or {}
    email = str(current_user.get('email') or '').strip()
    handler_display_name = str(
        current_user.get('name')
        or current_user.get('display_name')
        or (email.split('@', 1)[0].replace('.', ' ').replace('_', ' ').title() if email else '')
        or 'Owner'
    ).strip()
    try:
        frame = singleton_construct_authorization_service.create_frame(
            actor_user_id,
            construct_id,
            request.get_json(silent=True) or {},
            handler_display_name=handler_display_name,
        )
        return jsonify({
            "success": True,
            "canonical": True,
            "participantFrame": frame,
            "authority": "ovvaults",
        }), 200
    except ConversationContractError as exc:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": str(exc),
            "error_code": exc.code,
        }), exc.status
    except Exception as exc:
        logger.error("SINGLETON_CONSTRUCT_FRAME: %s", type(exc).__name__)
        return jsonify({
            "success": False,
            "canonical": False,
            "error": "Canonical singleton authorization failed",
            "error_code": "SINGLETON_AUTHORITY_UNAVAILABLE",
        }), 503


@app.route('/api/chatty/transcript/<construct_id>/construct-grade-preflight', methods=['POST'])
@require_chatty_auth
def construct_grade_preflight(construct_id):
    """Grade a server-resolved construct turn without inference or persistence."""
    strict_auth_error = _construct_grade_preflight_strict_auth_error()
    if strict_auth_error:
        return strict_auth_error

    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Construct attribution preflight requires a JSON object",
            "error_code": "CONSTRUCT_GRADE_PREFLIGHT_INVALID_REQUEST",
        }), 400
    allowed_fields = {
        "speakerConstructId",
        "threadId",
        "surface",
        "onBehalfOf",
        "message",
        "candidateResponse",
    }
    forbidden_fields = sorted(set(data) - allowed_fields)
    if forbidden_fields:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Construct attribution preflight contains forbidden fields",
            "error_code": "CONSTRUCT_GRADE_PREFLIGHT_FIELD_FORBIDDEN",
            "forbiddenFields": forbidden_fields,
        }), 400

    current_user = getattr(request, 'current_user', None) or {}
    email = str(current_user.get('email') or '').strip()
    handler_display_name = str(
        current_user.get('name')
        or current_user.get('display_name')
        or (email.split('@', 1)[0].replace('.', ' ').replace('_', ' ').title() if email else '')
        or 'Owner'
    ).strip()
    frame_payload = {
        "speakerConstructId": data.get("speakerConstructId"),
        "threadId": data.get("threadId"),
        "surface": data.get("surface"),
        **({"onBehalfOf": None} if "onBehalfOf" in data and data.get("onBehalfOf") is None else {}),
    }
    try:
        frame = singleton_construct_authorization_service.create_frame(
            actor_user_id,
            construct_id,
            frame_payload,
            handler_display_name=handler_display_name,
        )
        verified_authorship = singleton_construct_authorization_service.verify_frame(
            actor_user_id,
            construct_id,
            frame,
        )
        body_payload, body_status = chatty_body_service.speaker_attribution_preflight(
            verified_authorship,
            data.get("message"),
            data.get("candidateResponse"),
        ).to_response()
        return jsonify(body_payload), body_status
    except ConversationContractError as exc:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": str(exc),
            "error_code": exc.code,
        }), exc.status
    except Exception as exc:
        logger.error("CONSTRUCT_GRADE_PREFLIGHT: %s", type(exc).__name__)
        return jsonify({
            "success": False,
            "canonical": False,
            "error": "Canonical construct attribution preflight failed",
            "error_code": "CONSTRUCT_GRADE_PREFLIGHT_UNAVAILABLE",
        }), 503


@app.route('/api/chatty/transcript/<construct_id>/initialize', methods=['POST'])
@require_chatty_auth
def initialize_chatty_transcript(construct_id):
    """Additively initialize a construct's empty OVVAULTS singleton thread."""
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    body_payload, body_status = chatty_body_service.initialize_transcript_body(
        construct_id,
        actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_transcript_projection_cache(construct_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/transcript/<construct_id>/session', methods=['POST'])
@require_chatty_auth
def update_chatty_transcript_session(construct_id):
    """Append one immutable canonical session lifecycle annotation."""
    try:
        actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
        if actor_error:
            return jsonify(actor_error[0]), actor_error[1]

        data = request.get_json(silent=True) or {}
        body_payload, body_status = chatty_body_service.append_transcript_session(
            construct_id,
            data,
            owner_user_id=actor_user_id,
        ).to_response()
        if (
            body_status == 503
            and body_payload.get("error_code") == "VVAULT_BODY_MISSING"
            and "transcripts.content(real)" in body_payload.get("missing_fields", [])
        ):
            initialized_payload, initialized_status = chatty_body_service.initialize_transcript_body(
                construct_id,
                actor_user_id,
            ).to_response()
            if initialized_status >= 300:
                return jsonify(initialized_payload), initialized_status
            body_payload, body_status = chatty_body_service.append_transcript_session(
                construct_id,
                data,
                owner_user_id=actor_user_id,
            ).to_response()
        if body_status < 300:
            chatty_body_service.invalidate_transcript_projection_cache(construct_id)
        return jsonify(body_payload), body_status
    except Exception as exc:
        logger.error(f"Error updating chatty transcript session: {exc}")
        return jsonify({"success": False, "error": "Transcript session update failed"}), 500

@app.route('/api/chatty/transcript/<construct_id>/message', methods=['POST'])
@require_chatty_auth
def append_chatty_message(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    data = dict(request.get_json(silent=True) or {})
    data.pop('_trustedPresentationClassification', None)
    if 'presentation_classification' in data:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Presentation classification field is invalid",
            "error_code": "TRANSCRIPT_PRESENTATION_CLASSIFICATION_INVALID",
        }), 400
    presentation_classification = data.pop('presentationClassification', None)
    if presentation_classification is not None:
        if not _trusted_service_identity_cache_allowed():
            return jsonify({
                "success": False,
                "canonical": True,
                "error": "Presentation classification requires trusted service authentication",
                "error_code": "TRANSCRIPT_PRESENTATION_CLASSIFICATION_SERVICE_AUTH_REQUIRED",
            }), 403
        data['_trustedPresentationClassification'] = presentation_classification
    body_payload, body_status = chatty_body_service.append_transcript_message(
        construct_id,
        data,
        owner_user_id=actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_transcript_projection_cache(construct_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/transcript/<construct_id>/exchange', methods=['POST'])
@require_chatty_auth
def append_chatty_exchange(construct_id):
    """Atomically persist an externally generated Chatty turn in OVVAULTS.

    Inference providers belong to the calling Chatty surface. VVAULT remains
    the canonical identity, memory, and transcript authority and therefore
    validates ownership and commits both sides of a turn in one transaction.
    """
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    data = dict(request.get_json(silent=True) or {})
    # This field is internal-only. Public request bodies cannot supply trusted
    # authorship metadata; VVAULT derives it by verifying its own signed frame.
    data.pop('_verifiedConstructAuthorship', None)
    data.pop('_atomicWorkEventCommitter', None)
    data.pop('_trustedPresentationClassification', None)
    if 'presentation_classification' in data:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Presentation classification field is invalid",
            "error_code": "TRANSCRIPT_PRESENTATION_CLASSIFICATION_INVALID",
        }), 400
    presentation_classification = data.pop('presentationClassification', None)
    if presentation_classification is not None:
        if not _trusted_service_identity_cache_allowed():
            return jsonify({
                "success": False,
                "canonical": True,
                "error": "Presentation classification requires trusted service authentication",
                "error_code": "TRANSCRIPT_PRESENTATION_CLASSIFICATION_SERVICE_AUTH_REQUIRED",
            }), 403
        data['_trustedPresentationClassification'] = presentation_classification
    execution_authorization = data.pop('graduationExecutionAuthorization', None)
    work_event_batch = data.pop('workEventBatch', None)
    work_context_projection = data.pop('workContextProjection', None)
    forbidden_graduation_fields = {
        'graduation_execution_authorization',
        'graduationExecutionAuthorizationHash',
        'graduation_execution_authorization_hash',
    }
    if any(field in data for field in forbidden_graduation_fields):
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Graduation execution authorization fields are invalid",
            "error_code": "GRADUATION_EXECUTION_AUTHORIZATION_INVALID",
        }), 403
    forbidden_work_fields = {
        'work_event_batch', 'workContextProjectionHash',
        'work_context_projection', 'workEventBatchHash',
        'work_event_batch_hash', '_atomicWorkEventCommitter',
    }
    if any(field in data for field in forbidden_work_fields):
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Durable work persistence fields are invalid",
            "error_code": "WORK_ATOMIC_EXCHANGE_INVALID",
        }), 403
    if (work_event_batch is None) != (work_context_projection is None):
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Atomic work batch and signed context must be supplied together",
            "error_code": "WORK_ATOMIC_EXCHANGE_INCOMPLETE",
        }), 409
    if work_event_batch is not None and execution_authorization is not None:
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Graduation execution and durable work deltas cannot share one exchange",
            "error_code": "WORK_ATOMIC_EXCHANGE_AUTHORITY_CONFLICT",
        }), 409
    if work_event_batch is not None and not _trusted_service_identity_cache_allowed():
        return jsonify({
            "success": False,
            "canonical": True,
            "error": "Atomic transcript/work persistence requires trusted service authentication",
            "error_code": "WORK_ATOMIC_EXCHANGE_SERVICE_AUTH_REQUIRED",
        }), 403
    if execution_authorization is not None:
        if not _trusted_service_identity_cache_allowed():
            return jsonify({
                "success": False,
                "canonical": True,
                "error": "Graduation execution persistence requires trusted service authentication",
                "error_code": "GRADUATION_EXECUTION_SERVICE_AUTH_REQUIRED",
            }), 403
        try:
            frame = data.get('participantFrame')
            if isinstance(frame, dict) and frame.get('role') in {
                'third_party_participant', 'same_principal_cross_surface'
            }:
                data['_verifiedConstructAuthorship'] = (
                    singleton_construct_authorization_service.verify_frame(
                        actor_user_id,
                        construct_id,
                        frame,
                        execution_authorization,
                    )
                )
            else:
                data['_verifiedConstructAuthorship'] = (
                    conversation_thread_service.verify_ordinary_singleton_frame(
                        actor_user_id,
                        construct_id,
                        frame,
                        execution_authorization,
                    )
                )
        except ConversationContractError as exc:
            return jsonify({
                "success": False,
                "canonical": True,
                "error": str(exc),
                "error_code": exc.code,
            }), exc.status
    elif work_event_batch is not None:
        try:
            data['_verifiedConstructAuthorship'] = (
                conversation_thread_service.verify_ordinary_singleton_frame(
                    actor_user_id,
                    construct_id,
                    data.get('participantFrame'),
                )
            )
        except ConversationContractError as exc:
            return jsonify({
                "success": False,
                "canonical": True,
                "error": str(exc),
                "error_code": exc.code,
            }), exc.status
    elif data.get('participantFrame') is not None:
        try:
            data['_verifiedConstructAuthorship'] = (
                singleton_construct_authorization_service.verify_frame(
                    actor_user_id,
                    construct_id,
                    data.get('participantFrame'),
                )
            )
        except ConversationContractError as exc:
            return jsonify({
                "success": False,
                "canonical": True,
                "error": str(exc),
                "error_code": exc.code,
            }), exc.status
    if work_event_batch is not None:
        if not isinstance(data.get('_verifiedConstructAuthorship'), dict):
            return jsonify({
                "success": False,
                "canonical": True,
                "error": "Atomic work persistence requires a verified participant frame",
                "error_code": "WORK_ATOMIC_EXCHANGE_PARTICIPANT_FRAME_REQUIRED",
            }), 409
        turn_id = data.get('clientTurnId') or data.get('client_turn_id')
        conversation_session_id = data.get('sessionId') or data.get('session_id')
        if not isinstance(turn_id, str) or not isinstance(conversation_session_id, str):
            return jsonify({
                "success": False,
                "canonical": True,
                "error": "Atomic work persistence requires stable turn and conversation session IDs",
                "error_code": "WORK_ATOMIC_EXCHANGE_TURN_SCOPE_REQUIRED",
            }), 409

        def commit_work_event_batch(cur, **commit_context):
            return construct_work_loop_service.commit_exchange_work_event_batch(
                cur,
                owner_user_id=actor_user_id,
                target_construct_id=construct_id,
                turn_id=turn_id,
                conversation_session_id=conversation_session_id,
                prompt_content=data.get('message'),
                response_content=data.get('response'),
                work_event_batch=work_event_batch,
                work_context_projection=work_context_projection,
                **commit_context,
            )

        data['_atomicWorkEventCommitter'] = commit_work_event_batch
    body_payload, body_status = chatty_body_service.append_transcript_exchange(
        construct_id,
        data.get('message'),
        data.get('response'),
        data,
        owner_user_id=actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_transcript_projection_cache(construct_id)
    return jsonify(body_payload), body_status

@app.route('/api/chatty/construct/<construct_id>/files')
@require_chatty_auth
def get_construct_files(construct_id):
    """List assets, documents, and identity files for a specific construct.

    Normalizes the incoming construct_id to callsign format and handles both
    the callsign (for example, 'katana-001') and bare-name (for example,
    'katana') storage forms to capture files regardless of legacy filename
    patterns.

    Returns file counts and listings for:
      - assets/  (images: png, jpg, jpeg, svg)
      - documents/  (all other files)
      - identity/  (prompt.json, capsules, config)

    Query params:
      - folder: optional filter ('assets', 'documents', 'identity')
    """
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    try:
        body_payload, body_status = chatty_body_service.construct_files(
            construct_id,
            user_id=actor_user_id,
            folder=request.args.get('folder'),
        ).to_response()
        return jsonify(body_payload), body_status
    except Exception as exc:
        logger.error(f"Error fetching construct files for {construct_id}: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/chatty/construct/<construct_id>/capsule')
@require_chatty_auth
def get_construct_canonical_capsule(construct_id):
    """Read the exact canonical memup capsule through the VVAULT body boundary."""
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    body_payload, body_status = chatty_body_service.canonical_capsule(
        construct_id,
        user_id=actor_user_id,
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/construct/<construct_id>/capsule/materialize', methods=['POST'])
@require_chatty_auth
def materialize_construct_canonical_capsule(construct_id):
    """Additively materialize and read back one canonical Memup capsule."""
    callsign = chatty_body_service.normalize_callsign(construct_id)
    if not callsign:
        return jsonify({"success": False, "error": "construct_id is required"}), 400
    user_id, actor_error = _chatty_construct_actor_user_id(callsign)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    existing_payload, existing_status = life_capsule_resolver.resolve_capsule(callsign).to_response()
    if existing_status == 200:
        return jsonify({
            "success": True,
            "status": "body_native",
            "canonical": True,
            "action": "unchanged",
            "construct_id": callsign,
            "capsule": existing_payload,
        }), 200
    candidate_ids = _candidate_transcript_ids_for_construct(callsign)
    if not candidate_ids:
        return jsonify({"success": False, "construct_id": callsign, "error": "No transcript candidates found for materialization"}), 404
    materialized = _persist_capsule_from_candidate_transcripts(callsign, candidate_ids, user_id)
    if not materialized:
        return jsonify({"success": False, "construct_id": callsign, "error": "No canonical capsule could be materialized"}), 404
    readback_payload, readback_status = life_capsule_resolver.resolve_capsule(callsign).to_response()
    if readback_status != 200:
        return jsonify({
            "success": False,
            "construct_id": callsign,
            "error": "Canonical capsule readback failed",
            "error_code": readback_payload.get("error_code"),
            "reason": readback_payload.get("reason"),
            "readback_status": readback_status,
        }), 503
    summary = (materialized.get('capsule_data') or {}).get('summary') or {}
    return jsonify({
        "success": True,
        "status": "body_native",
        "canonical": True,
        "action": "materialized",
        "construct_id": callsign,
        "candidate_count": len(candidate_ids),
        "total_sessions": summary.get('total_sessions'),
        "total_exchanges": summary.get('total_exchanges'),
        "capsule": readback_payload,
        "authority": "vvault_body",
        "storage_owner": "ovvaults.vault_files",
    }), 200


def _avatar_etag(result: dict[str, Any]) -> str | None:
    sha = str(result.get("sha256") or "")
    return f'"{sha}"' if re.fullmatch(r"[0-9a-f]{64}", sha) else None


def _avatar_descriptor_payload(
    callsign: str, result: dict[str, Any]
) -> dict[str, Any]:
    state = result["state"]
    avatar = None
    if state != "missing":
        avatar = {
            "rowId": result.get("rowId"),
            "filename": os.path.basename(str(result.get("filename") or "avatar.png")),
            "canonicalPath": f"instances/{callsign}/identity/avatar.png",
            "sha256": result.get("sha256"),
            "contentType": result.get("contentType"),
            "sizeBytes": result.get("sizeBytes") or 0,
            "etag": _avatar_etag(result),
            "bytesUrl": f"/api/chatty/construct/{callsign}/avatar/bytes",
        }
    return {
        "success": True,
        "canonical": True,
        "constructId": callsign,
        "state": state,
        "avatar": avatar,
        "errorCode": result.get("errorCode"),
        "cacheState": result.get("cacheState"),
        "refreshing": bool(result.get("refreshing")),
        "source": "ovvaults.vault_files",
    }


def _set_avatar_cache_headers(response: Response, result: dict[str, Any]) -> None:
    response.headers["Cache-Control"] = (
        f"private, max-age={int(AVATAR_CACHE_TTL_SECONDS)}, "
        f"stale-if-error={int(AVATAR_CACHE_LKG_SECONDS)}"
    )
    response.headers["Vary"] = "Authorization, Cookie"
    response.headers["X-VVAULT-Avatar-State"] = str(result.get("state"))
    response.headers["X-VVAULT-Cache-State"] = str(result.get("cacheState"))
    response.headers["X-VVAULT-Refreshing"] = (
        "true" if result.get("refreshing") else "false"
    )
    etag = _avatar_etag(result)
    if etag:
        response.headers["ETag"] = etag


@app.route('/api/chatty/construct/<construct_id>/avatar')
@require_chatty_auth
def get_construct_avatar_descriptor(construct_id):
    """Return the canonical owner-scoped avatar state without inline bytes."""
    route_started = time.perf_counter()
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    callsign = _normalize_callsign(construct_id)
    if not _construct_is_projectable_cached(owner_user_id, callsign):
        return jsonify({"success": False, "error": "Construct not found", "errorCode": "VVAULT_CONSTRUCT_NOT_PROJECTABLE"}), 404
    result = _canonical_owner_avatar_descriptor(owner_user_id, callsign)
    payload = _avatar_descriptor_payload(callsign, result)
    response = jsonify(payload)
    _set_avatar_cache_headers(response, result)
    response.headers["Server-Timing"] = (
        f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
    )
    return response, 200


@app.route('/api/chatty/construct/<construct_id>/avatar/bytes')
@require_chatty_auth
def get_construct_avatar_bytes(construct_id):
    """Return exact verified canonical PNG bytes for the authenticated owner."""
    route_started = time.perf_counter()
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    callsign = _normalize_callsign(construct_id)
    if not _construct_is_projectable_cached(owner_user_id, callsign):
        return jsonify({"success": False, "error": "Construct not found", "errorCode": "VVAULT_CONSTRUCT_NOT_PROJECTABLE"}), 404
    descriptor = _canonical_owner_avatar_descriptor(owner_user_id, callsign)
    descriptor_etag = _avatar_etag(descriptor)
    if descriptor_etag and request.headers.get("If-None-Match") == descriptor_etag:
        response = Response(status=304)
        _set_avatar_cache_headers(response, descriptor)
        response.headers["Server-Timing"] = (
            f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
        )
        return response
    if descriptor["state"] != "available":
        result = descriptor
    else:
        result = _canonical_owner_avatar(owner_user_id, callsign)
    if result["state"] == "missing":
        response = jsonify({
            "success": False,
            "constructId": callsign,
            "state": "missing",
            "errorCode": "AVATAR_NOT_FOUND",
        })
        _set_avatar_cache_headers(response, result)
        return response, 404
    if result["state"] == "hydration_error":
        response = jsonify({
            "success": False,
            "constructId": callsign,
            "state": "hydration_error",
            "errorCode": result.get("errorCode"),
            "cacheState": result.get("cacheState"),
            "refreshing": bool(result.get("refreshing")),
        })
        _set_avatar_cache_headers(response, result)
        return response, 503
    etag = _avatar_etag(result)
    if etag and request.headers.get("If-None-Match") == etag:
        response = Response(status=304)
        _set_avatar_cache_headers(response, result)
        return response
    body = result["body"]
    response = Response(body, status=200, mimetype="image/png")
    response.headers["Content-Length"] = str(len(body))
    response.headers["Content-Disposition"] = 'inline; filename="avatar.png"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    _set_avatar_cache_headers(response, result)
    response.headers["Server-Timing"] = (
        f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
    )
    return response


IDENTITY_OWNER_BINDING_CONTRACT = "life-vvault-identity-owner-binding/v1"


def _signed_identity_owner_binding(
    *,
    authenticated_handler_principal_id: str,
    canonical_owner_user_id: str,
    artifact_owner_user_id: str,
    construct_id: str,
    expression_projection_hash: str,
) -> dict[str, Any]:
    """Bind an authenticated owner to one exact legacy/current identity owner.

    The construct actor resolver has already performed the owner-qualified
    authorization decision.  This projection makes that otherwise implicit
    decision independently verifiable by Chatty without rewriting legacy
    identity artifacts or treating transport authentication as identity proof.
    """
    payload = {
        "contract": IDENTITY_OWNER_BINDING_CONTRACT,
        "authority": "ovvaults",
        "authenticatedHandlerPrincipalId": authenticated_handler_principal_id,
        "canonicalOwnerUserId": canonical_owner_user_id,
        "artifactOwnerUserId": artifact_owner_user_id,
        "constructId": _normalize_callsign(construct_id),
        "expressionProjectionHash": expression_projection_hash,
        "bindingStatus": (
            "direct_owner"
            if authenticated_handler_principal_id == canonical_owner_user_id == artifact_owner_user_id
            else "handler_canonical_owner_authorized"
            if canonical_owner_user_id == artifact_owner_user_id
            else "legacy_artifact_owner_authorized"
        ),
    }
    return {
        **payload,
        **canonical_projection_signing.sign_canonical_payload(payload),
    }


@app.route('/api/chatty/construct/<construct_id>/identity')
@require_chatty_auth
def get_construct_identity(construct_id):
    """Return structured identity data for a construct.

    Loads canonical identity/config files from VVAULT storage and normalizes
    callsign/bare-name variants so legacy filename patterns remain readable.

    Returns:
      {
        "success": true,
        "construct_id": "katana-001",
        "name": "Katana",
        "description": "...",
        "instructions": "...",
        "personality": { ... },
        "system_prompt": "..."
      }
    """
    authenticated_owner_user_id = _get_authenticated_user_id()
    current_user = getattr(request, "current_user", None) or {}
    authenticated_handler_principal_id = str(
        current_user.get("id")
        or current_user.get("user_id")
        or authenticated_owner_user_id
        or ""
    ).strip()
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    try:
        body_payload, body_status = life_capsule_resolver.resolve_identity(construct_id).to_response()
        expression_projection = (
            body_payload.get("expressionProjection")
            or body_payload.get("expression_projection")
            or {}
        )
        artifact_owner_user_id = str(
            expression_projection.get("ownerUserId") or ""
        ).strip()
        expression_projection_hash = str(
            expression_projection.get("projectionHash") or ""
        ).strip().lower()
        if (
            body_status < 400
            and authenticated_owner_user_id
            and actor_user_id
            and artifact_owner_user_id == actor_user_id
            and re.fullmatch(r"[0-9a-f]{64}", expression_projection_hash)
        ):
            owner_binding = _signed_identity_owner_binding(
                authenticated_handler_principal_id=authenticated_handler_principal_id,
                canonical_owner_user_id=authenticated_owner_user_id,
                artifact_owner_user_id=artifact_owner_user_id,
                construct_id=construct_id,
                expression_projection_hash=expression_projection_hash,
            )
            body_payload["identityOwnerBinding"] = owner_binding
            body_payload["identity_owner_binding"] = owner_binding
        if body_status >= 500:
            _invalidate_projection_capability("identity_projection", f"HTTP_{body_status}")
        return jsonify(body_payload), body_status
    except Exception as exc:
        _invalidate_projection_capability("identity_projection", exc)
        logger.error(f"Error fetching identity for {construct_id}: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/chatty/construct/<construct_id>/life-capsule-readiness')
@require_chatty_auth
def get_construct_life_capsule_readiness(construct_id):
    """Prove the authenticated construct's identity and Memup capsule in OVVAULTS."""
    _actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    try:
        resolution = life_capsule_resolver.resolve_life_capsule(construct_id)
        evidence = resolution.evidence()
        return jsonify({
            "success": True,
            "canonical": True,
            "status": "ready" if resolution.ready else "identity_mapping_invalid",
            **evidence,
        }), 200
    except Exception as exc:
        logger.error(f"Error resolving LIFE capsule readiness for {construct_id}: {exc}")
        return jsonify({
            "success": False,
            "ready": False,
            "canonical": True,
            "status": "temporarily_unavailable",
            "error": str(exc),
        }), 503


@app.route('/api/chatty/signed-projections/public-key')
def get_signed_projection_public_key():
    """Return the shared projection verification key without canonical data."""
    try:
        return jsonify({
            "success": True,
            "contractVersion": "life-vvault-signed-projection-key/v1",
            "supportedContracts": [
                offline_snapshot_service.SNAPSHOT_VERSION,
                auto_runtime_service.CONTEXT_PROJECTION_CONTRACT,
                auto_runtime_service.REGISTRATION_RECEIPT_CONTRACT,
                auto_runtime_service.REGISTRATION_PREFLIGHT_PROJECTION_CONTRACT,
                auto_runtime_service.EXCHANGE_RECEIPT_CONTRACT,
                auto_runtime_service.HYDRO_LIFECYCLE_RECEIPT_CONTRACT,
                auto_runtime_service.THREAD_INDEX_PROJECTION_CONTRACT,
                auto_runtime_service.HYDRO_CATALOG_CONTRACT,
                auto_runtime_service.HYDRO_DISPATCH_RECEIPT_CONTRACT,
                auto_runtime_service.HYDRO_CANCELLATION_RECEIPT_CONTRACT,
                auto_runtime_service.HYDRO_WORKER_RECEIPT_CONTRACT,
                auto_runtime_service.HYDRO_RECOVERY_INDEX_CONTRACT,
                auto_runtime_service.ACTION_GRANT_RECEIPT_CONTRACT,
                auto_runtime_service.CODE_ACTION_GRANT_RECEIPT_CONTRACT,
                auto_runtime_service.ACTION_EVENT_RECEIPT_CONTRACT,
                auto_runtime_service.CODE_PROJECT_BINDING_PROJECTION_CONTRACT,
                auto_runtime_service.CODE_THREAD_HISTORY_PROJECTION_CONTRACT,
                auto_runtime_service.CODE_PROPOSAL_CONTEXT_RECEIPT_CONTRACT,
                canonical_context_service.ENVELOPE_VERSION,
                construct_work_loop_service_module.WORK_EVENT_ENVELOPE_CONTRACT,
                construct_work_loop_service_module.WORK_PROJECTION_CONTRACT,
                construct_work_loop_service_module.WORK_CONTEXT_PROJECTION_CONTRACT,
                construct_work_loop_service_module.WORK_SCOPE_RESOLUTION_CONTRACT,
                construct_work_loop_service_module.WORK_ACTIVE_SCOPE_RESOLUTION_CONTRACT,
                construct_work_loop_service_module.WORK_EVIDENCE_RESOLUTION_CONTRACT,
                construct_work_loop_service_module.WORK_HANDOFF_ENVELOPE_CONTRACT,
                construct_work_loop_service_module.WORK_ATOMIC_EXCHANGE_RECEIPT_CONTRACT,
                construct_execution_service_module.EVENT_ENVELOPE,
                construct_execution_service_module.EXECUTION_PROJECTION,
                construct_execution_service_module.EXECUTION_CONTEXT_PROJECTION,
                construct_execution_service_module.ARGUMENT_RESOLUTION,
                construct_execution_service_module.EXECUTION_PREFLIGHT,
                construct_execution_service_module.EXECUTION_EVIDENCE,
                construct_execution_service_module.RECOVERY_CAPABILITY,
                construct_execution_service_module.FINALIZATION_RECEIPT,
                construct_execution_service_module.EXECUTION_CAPABILITY_MANIFEST,
                construct_execution_service_module.WORK_EXECUTION_RECOVERY_ARTIFACT_ENVELOPE,
                construct_execution_service_module.WORK_EXECUTION_RECOVERY_ARTIFACTS_ENVELOPE,
                construct_execution_service_module.WORK_EXECUTION_RECOVERY_ARTIFACT,
                construct_execution_service_module.WORK_EXECUTION_RECOVERY_ARTIFACT_REFERENCE,
                "life-vvault-work-execution-proposal-envelope/v1",
                "life-vvault-execution-input-artifact/v1",
            ],
            **canonical_projection_signing.public_key_document(),
        }), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


@app.route('/api/chatty/offline-snapshots/public-key')
def get_offline_snapshot_public_key():
    """Compatibility alias for the shared signed-projection key."""
    try:
        return jsonify({
            "success": True,
            "contractVersion": offline_snapshot_service.SNAPSHOT_VERSION,
            **canonical_projection_signing.public_key_document(),
        }), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


def _auto_runtime_error_response(exc):
    """Translate a stable AUTO service error without leaking canonical data."""
    status = int(getattr(exc, "status", 500) or 500)
    code = str(getattr(exc, "code", "AUTO_RUNTIME_UNAVAILABLE"))
    if status >= 500:
        logger.warning("AUTO_RUNTIME route=%s error_code=%s", request.path, code)
    return jsonify({
        "success": False,
        "errorCode": code,
        "error": str(exc),
    }), status


AUTO_RUNTIME_REGISTRATION_REQUEST_MAX_BYTES = 16 * 1024
AUTO_RUNTIME_CONTEXT_REQUEST_MAX_BYTES = 32 * 1024
AUTO_RUNTIME_EXCHANGE_REQUEST_MAX_BYTES = auto_runtime_service.MAX_EVENT_BYTES + (128 * 1024)
AUTO_RUNTIME_HYDRO_EVENT_REQUEST_MAX_BYTES = auto_runtime_service.MAX_HYDRO_EVENT_BYTES + (32 * 1024)
AUTO_RUNTIME_THREAD_INDEX_REQUEST_MAX_BYTES = 8 * 1024
AUTO_RUNTIME_HYDRO_CATALOG_REQUEST_MAX_BYTES = 64 * 1024
AUTO_RUNTIME_HYDRO_AUTHORITY_REQUEST_MAX_BYTES = auto_runtime_service.MAX_HYDRO_EVENT_BYTES + (128 * 1024)
AUTO_RUNTIME_ACTION_REQUEST_MAX_BYTES = auto_runtime_service.MAX_ACTION_BYTES
AUTO_RUNTIME_CODE_REQUEST_MAX_BYTES = 256 * 1024


def _auto_runtime_bounded_json(max_bytes):
    """Read one AUTO JSON body without inheriting the server's upload limit."""
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        return None, (
            jsonify({
                "success": False,
                "errorCode": "AUTO_RUNTIME_REQUEST_TOO_LARGE",
                "error": "AUTO runtime request body exceeds the route limit",
            }),
            413,
        )

    raw = request.stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return None, (
            jsonify({
                "success": False,
                "errorCode": "AUTO_RUNTIME_REQUEST_TOO_LARGE",
                "error": "AUTO runtime request body exceeds the route limit",
            }),
            413,
        )
    try:
        if not raw:
            raise ValueError("empty request body")
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None, (
            jsonify({
                "success": False,
                "errorCode": "AUTO_RUNTIME_INVALID_JSON",
                "error": "AUTO runtime request body must be valid UTF-8 JSON",
            }),
            400,
        )
    return payload, None


def _auto_runtime_strict_owner():
    """Resolve only an authenticated canonical owner."""
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return None, (
            jsonify({
                "success": False,
                "errorCode": "AUTO_RUNTIME_OWNER_REQUIRED",
                "error": "authenticated canonical owner could not be resolved",
            }),
            403,
        )
    return owner_user_id, None


@app.route('/api/chatty/system-runtimes/auto-001/register', methods=['POST'])
@require_chatty_auth
def register_auto_system_runtime_profile():
    """Register only the reviewed bundled AUTO profile under native admin authority."""
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("role") != "admin":
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_ADMIN_REQUIRED",
            "error": "administrator authority is required",
        }), 403
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_REGISTRATION_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.register_auto_profile(
            payload
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "created" else 409 if status == "conflict" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO runtime registration failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_UNAVAILABLE",
            "error": "AUTO runtime registration is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/registration/preflight', methods=['POST'])
@require_chatty_auth
def preflight_auto_system_runtime_profile():
    """Inspect registration authority without writing canonical state."""
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("role") != "admin":
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_ADMIN_REQUIRED",
            "error": "administrator authority is required",
        }), 403
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_REGISTRATION_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.project_auto_registration_preflight(
            payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO runtime registration preflight failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_UNAVAILABLE",
            "error": "AUTO runtime registration preflight is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/context', methods=['POST'])
@require_chatty_auth
def get_auto_system_runtime_context():
    """Issue a signed profile-and-continuity projection for one owner/thread."""
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_CONTEXT_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.project_auto_context(
            owner_user_id=owner_user_id,
            request=payload,
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 200 if status == "ready" else 503
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO context projection failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_UNAVAILABLE",
            "error": "AUTO context projection is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/exchanges', methods=['POST'])
@require_chatty_auth
def append_auto_system_runtime_exchange():
    """Append one owner/thread-bound AUTO exchange and return signed evidence."""
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_EXCHANGE_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.append_auto_exchange(
            owner_user_id=owner_user_id,
            request=payload,
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "appended" else 409 if status == "conflict" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO exchange append failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_RUNTIME_UNAVAILABLE",
            "error": "AUTO exchange append is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/events', methods=['POST'])
@require_chatty_auth
def append_auto_system_runtime_hydro_event():
    """Append one owner/thread-bound canonical Hydro lifecycle event."""
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_EVENT_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.append_auto_hydro_lifecycle_event(
            owner_user_id=owner_user_id,
            request=payload,
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "appended" else 409 if status == "conflict" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro lifecycle append failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_LIFECYCLE_UNAVAILABLE",
            "error": "AUTO Hydro lifecycle append is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/threads/index', methods=['POST'])
@require_chatty_auth
def get_auto_system_runtime_thread_index():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_THREAD_INDEX_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.project_auto_thread_index(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO thread index projection failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_THREAD_INDEX_UNAVAILABLE",
            "error": "AUTO thread index is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/catalog', methods=['POST'])
@require_chatty_auth
def get_auto_system_runtime_hydro_catalog():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_CATALOG_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    if isinstance(payload, dict) and payload.get("executorAttestations"):
        current_user = getattr(request, "current_user", None) or {}
        if current_user.get("auth_mode") != "service_token":
            return jsonify({
                "success": False,
                "errorCode": "AUTO_HYDRO_SERVICE_AUTH_REQUIRED",
                "error": "external Hydro executor attestations require Chatty service authority",
            }), 403
    try:
        return jsonify(auto_runtime_service.project_auto_hydro_catalog(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro catalog projection failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_CATALOG_UNAVAILABLE",
            "error": "AUTO Hydro catalog is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/recovery/index', methods=['POST'])
@require_chatty_auth
def get_auto_system_runtime_hydro_recovery_index():
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("auth_mode") != "service_token":
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_SERVICE_AUTH_REQUIRED",
            "error": "Hydro recovery index requires Chatty service authority",
        }), 403
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_AUTHORITY_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.project_auto_hydro_recovery_index(
            request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro recovery index projection failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_RECOVERY_INDEX_UNAVAILABLE",
            "error": "AUTO Hydro recovery index is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/dispatches', methods=['POST'])
@require_chatty_auth
def register_auto_system_runtime_hydro_dispatch():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_AUTHORITY_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.register_auto_hydro_dispatch(
            owner_user_id=owner_user_id, request=payload
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "accepted" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro dispatch registration failed")
        return jsonify({"success": False, "errorCode": "AUTO_HYDRO_DISPATCH_UNAVAILABLE", "error": "AUTO Hydro dispatch is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/cancellations', methods=['POST'])
@require_chatty_auth
def register_auto_system_runtime_hydro_cancellation():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_AUTHORITY_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.register_auto_hydro_cancellation(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro cancellation registration failed")
        return jsonify({"success": False, "errorCode": "AUTO_HYDRO_CANCELLATION_UNAVAILABLE", "error": "AUTO Hydro cancellation is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/hydro/worker-receipts', methods=['POST'])
@require_chatty_auth
def attest_auto_system_runtime_hydro_worker_receipt():
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("auth_mode") != "service_token":
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_SERVICE_AUTH_REQUIRED",
            "error": "background Hydro worker receipts require service authority",
        }), 403
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_HYDRO_AUTHORITY_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    grant = payload.get("executionGrant") if isinstance(payload, dict) else None
    owner_user_id = str((grant or {}).get("ownerId") or "").strip()
    if not owner_user_id:
        return jsonify({
            "success": False,
            "errorCode": "AUTO_HYDRO_EXECUTION_GRANT_REQUIRED",
            "error": "a graph-bound execution grant is required",
        }), 403
    try:
        return jsonify(auto_runtime_service.attest_auto_hydro_worker_receipt(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Hydro worker receipt attestation failed")
        return jsonify({"success": False, "errorCode": "AUTO_HYDRO_WORKER_RECEIPT_UNAVAILABLE", "error": "AUTO Hydro worker receipt attestation is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/code/projects/binding', methods=['POST'])
@require_chatty_auth
def project_auto_code_project_binding_route():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(AUTO_RUNTIME_CODE_REQUEST_MAX_BYTES)
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.project_auto_code_project_binding(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Code project binding failed")
        return jsonify({"success": False, "errorCode": "AUTO_CODE_PROJECT_BINDING_UNAVAILABLE", "error": "AUTO Code project binding is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/code/threads/history', methods=['POST'])
@require_chatty_auth
def project_auto_code_thread_history_route():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(AUTO_RUNTIME_CODE_REQUEST_MAX_BYTES)
    if payload_error:
        return payload_error
    try:
        return jsonify(auto_runtime_service.project_auto_code_thread_history(
            owner_user_id=owner_user_id, request=payload
        )), 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Code thread history failed")
        return jsonify({"success": False, "errorCode": "AUTO_CODE_HISTORY_UNAVAILABLE", "error": "AUTO Code history is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/code/proposal-contexts', methods=['POST'])
@require_chatty_auth
def append_auto_code_proposal_context_route():
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(AUTO_RUNTIME_CODE_REQUEST_MAX_BYTES)
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.append_auto_code_proposal_context(
            owner_user_id=owner_user_id, request=payload
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "accepted" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO Code proposal context append failed")
        return jsonify({"success": False, "errorCode": "AUTO_CODE_PROPOSAL_CONTEXT_UNAVAILABLE", "error": "AUTO Code proposal context is unavailable"}), 503


@app.route('/api/chatty/system-runtimes/auto-001/actions/grants', methods=['POST'])
@require_chatty_auth
def grant_auto_system_runtime_host_action():
    """Issue a signed grant only for an exact canonically approved host action."""
    owner_user_id, owner_error = _auto_runtime_strict_owner()
    if owner_error:
        return owner_error
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_ACTION_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    try:
        envelope = auto_runtime_service.grant_auto_host_action(
            owner_user_id=owner_user_id, request=payload
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "accepted" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO host action grant failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_ACTION_GRANT_UNAVAILABLE",
            "error": "AUTO host action grant is unavailable",
        }), 503


@app.route('/api/chatty/system-runtimes/auto-001/actions/events', methods=['POST'])
@require_chatty_auth
def append_auto_system_runtime_host_action_event():
    """Persist one grant-bound host action lifecycle event."""
    payload, payload_error = _auto_runtime_bounded_json(
        AUTO_RUNTIME_ACTION_REQUEST_MAX_BYTES
    )
    if payload_error:
        return payload_error
    current_user = getattr(request, "current_user", None) or {}
    if current_user.get("auth_mode") == "service_token":
        grant = payload.get("executionGrant") if isinstance(payload, dict) else None
        owner_user_id = str((grant or {}).get("ownerId") or "").strip()
        if not owner_user_id:
            return jsonify({
                "success": False,
                "errorCode": "AUTO_ACTION_EXECUTION_GRANT_REQUIRED",
                "error": "an owner-bound execution grant is required",
            }), 403
    else:
        owner_user_id, owner_error = _auto_runtime_strict_owner()
        if owner_error:
            return owner_error
    try:
        envelope = auto_runtime_service.append_auto_host_action_event(
            owner_user_id=owner_user_id, request=payload
        )
        status = str((envelope.get("payload") or {}).get("status") or "")
        return jsonify(envelope), 201 if status == "accepted" else 200
    except auto_runtime_service.AutoRuntimeContractError as exc:
        return _auto_runtime_error_response(exc)
    except Exception:
        logger.exception("AUTO host action event append failed")
        return jsonify({
            "success": False,
            "errorCode": "AUTO_ACTION_EVENT_UNAVAILABLE",
            "error": "AUTO host action event append is unavailable",
        }), 503


@app.route('/api/chatty/construct/<construct_id>/offline-snapshot', methods=['POST'])
@require_chatty_auth
def create_construct_offline_snapshot(construct_id):
    """Issue an owner-scoped signed projection for bounded deferred execution."""
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    try:
        resolution = life_capsule_resolver.resolve_life_capsule(construct_id)
        if not resolution.ready:
            return jsonify({"success": False, "error": "CANONICAL_CONSTRUCT_NOT_READY"}), 409
        identity_payload = resolution.identity.payload
        capsule_payload = resolution.capsule.payload
        memory_payload, memory_status = chatty_body_service.memories(
            construct_id,
            owner_user_id=actor_user_id,
            max_chars=64_000,
            query=None,
            limit=12,
        ).to_response()
        if memory_status != 200 or not memory_payload.get("success"):
            return jsonify({"success": False, "error": "CANONICAL_MEMORY_SNAPSHOT_UNAVAILABLE"}), 503
        revision_source = {
            "identity": identity_payload.get("sha256"),
            "capsule": capsule_payload.get("sha256"),
            "taxonomy": identity_payload.get("taxonomy_sha256"),
        }
        revision = hashlib.sha256(
            json.dumps(revision_source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        requested_ttl = (request.get_json(silent=True) or {}).get("ttlSeconds")
        snapshot = offline_snapshot_service.issue_construct_snapshot(
            owner_user_id=actor_user_id,
            construct_id=chatty_body_service.normalize_callsign(construct_id),
            identity=identity_payload,
            capsule=capsule_payload,
            memory=memory_payload,
            permissions={"deferredTranscriptAppend": True, "canonicalOverwrite": False},
            capabilities=identity_payload.get("capabilities") or {},
            revision=revision,
            ttl_seconds=requested_ttl or offline_snapshot_service.DEFAULT_TTL_SECONDS,
        )
        return jsonify({"success": True, "canonical": True, "snapshot": snapshot}), 201
    except Exception as exc:
        logger.error(f"Error issuing offline snapshot for {construct_id}: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 503


def _normalize_callsign(raw_id: str) -> str:
    """Normalize a construct identifier to proper callsign format.

    Bare names like 'katana' become 'katana-001'.
    Already-valid callsigns like 'katana-001' pass through unchanged.
    """
    import re
    if re.match(r'^.+-\d{3}$', raw_id):
        return raw_id
    return f"{raw_id}-001"


def _bare_name_from_callsign(callsign: str) -> str:
    """Extract the bare construct name from a callsign.

    'katana-001' -> 'katana', 'zen-001' -> 'zen'
    """
    import re
    m = re.match(r'^(.+)-\d{3}$', callsign)
    return m.group(1) if m else callsign


ALLOWED_VAULT_FILE_TYPES = {'binary', 'text', 'conversation', 'transcript', 'drift_log', 'enforcement_config'}

def _validate_vault_filename(filename):
    """Reject filenames containing full internal paths. Returns (ok, error)."""
    bad_patterns = ['vvault/', '/users/', '/shard_', 'vvault_files/']
    for pat in bad_patterns:
        if pat in filename:
            return False, f"Filename must not contain internal path '{pat}'. Use flat filenames with construct_id column."
    return True, None


def _rollback_failed_construct_create(
    callsign: str,
    user_id: str,
    preexisting_ids: set[str],
) -> list[str]:
    """Remove only rows written by the current failed create attempt."""
    if not callsign or not user_id:
        return []
    rows = VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=_bare_name_from_callsign(callsign),
        user_id=user_id,
        include_content=False,
    )
    deleted_ids: list[str] = []
    for row in rows:
        file_id = str(row.get("id") or "")
        if not file_id or file_id in preexisting_ids:
            continue
        deleted = VAULT_FILE_REPOSITORY.delete_for_user(
            file_id=file_id,
            user_id=user_id,
        )
        if deleted:
            deleted_ids.append(file_id)
    return deleted_ids


@app.route('/api/chatty/construct/create-provenance', methods=['GET'])
@require_chatty_auth
def get_construct_creation_provenance():
    user_id = _get_authenticated_user_id()
    session_token = getattr(request, "current_token", None)
    if not user_id or not session_token:
        return jsonify({
            "success": False,
            "error": "An authenticated owner UI session is required",
            "error_code": "VVAULT_CONSTRUCT_CREATION_PROVENANCE_REQUIRED",
        }), 403
    return jsonify({
        "success": True,
        "provenance": _construct_creation_provenance_token(session_token, user_id),
    })


def _callsign_allocation_dto(
    allocation: dict[str, Any],
    *,
    collision_reason: str | None = None,
) -> dict[str, Any]:
    requested = str(allocation.get("requested_callsign") or "")
    allocated = str(allocation.get("allocated_callsign") or "")
    if collision_reason is None and allocated != requested:
        collision_reason = "owner_collision"
    return {
        "allocationId": str(allocation.get("allocation_id") or ""),
        "displayName": str(allocation.get("display_name") or ""),
        "requestedCallsign": requested,
        "allocatedCallsign": allocated,
        "constructCategory": "user",
        "lifecycleStage": "gpt",
        "collisionReason": collision_reason,
        "stable": allocated == requested,
        "reused": bool(allocation.get("reused")),
    }


def _validate_user_callsign_allocation_input(
    display_name: Any,
    requested_callsign: Any,
) -> tuple[str, str]:
    normalized_display_name = str(display_name or "").strip()
    normalized_callsign = str(requested_callsign or "").strip().lower()
    if not normalized_display_name or len(normalized_display_name) > 160:
        raise ValueError("displayName must contain between 1 and 160 characters")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}", normalized_callsign):
        raise ValueError(
            "requestedCallsign must use {name}-{NNN} (for example insight-001)"
        )
    return normalized_display_name, normalized_callsign


@app.route('/api/chatty/construct/callsign-allocation', methods=['POST'])
@require_chatty_auth
def allocate_chatty_construct_callsign():
    """Reserve a collision-safe user GPT callsign without weakening taxonomy."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Invalid request body"}), 400
    try:
        display_name, requested_callsign = _validate_user_callsign_allocation_input(
            payload.get("displayName") or payload.get("name"),
            payload.get("requestedCallsign") or payload.get("callsign"),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    try:
        allocation = VAULT_FILE_REPOSITORY.allocate_user_callsign(
            user_id=user_id,
            requested_callsign=requested_callsign,
            display_name=display_name,
        )
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "error_code": "VVAULT_CALLSIGN_ALLOCATION_EXHAUSTED",
        }), 409
    except Exception:
        logger.exception("Canonical callsign allocation failed")
        return jsonify({
            "success": False,
            "error": "Canonical callsign allocation is unavailable",
            "error_code": "VVAULT_CALLSIGN_ALLOCATION_UNAVAILABLE",
        }), 503
    status = 200 if allocation.get("reused") else 201
    return jsonify({
        "success": True,
        "canonical": True,
        "callsignAllocation": _callsign_allocation_dto(allocation),
    }), status


@app.route(
    '/api/chatty/construct/callsign-allocation/<allocation_id>',
    methods=['DELETE'],
)
@require_chatty_auth
def release_chatty_construct_callsign(allocation_id: str):
    """Release only the authenticated owner's unused preflight reservation."""
    try:
        allocation_id = str(UUID(str(allocation_id or "").strip()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "error": "allocationId must be a canonical UUID",
        }), 400
    user_id = _get_authenticated_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    try:
        released = VAULT_FILE_REPOSITORY.cancel_user_callsign_allocation(
            allocation_id=allocation_id,
            user_id=user_id,
        )
    except Exception:
        logger.exception("Canonical callsign allocation release failed")
        return jsonify({
            "success": False,
            "error": "Canonical callsign allocation release is unavailable",
            "error_code": "VVAULT_CALLSIGN_ALLOCATION_RELEASE_UNAVAILABLE",
        }), 503
    if not released:
        return jsonify({
            "success": False,
            "error": "Unused callsign allocation was not found",
            "error_code": "VVAULT_CALLSIGN_ALLOCATION_NOT_RELEASABLE",
        }), 404
    return jsonify({
        "success": True,
        "canonical": True,
        "released": True,
        "allocationId": str(released["allocation_id"]),
    }), 200


@app.route('/api/chatty/construct/create', methods=['POST'])
@app.route('/api/simforge/construct/create', methods=['POST'])
@require_chatty_auth
def create_construct():
    """Create a canonical VVAULT-native construct bundle in `vault_files`."""
    callsign = ""
    user_id = ""
    requested_callsign = ""
    callsign_allocation_id = ""
    callsign_allocation: dict[str, Any] | None = None
    callsign_allocation_claimed = False
    preexisting_ids: set[str] = set()
    incarnation: dict[str, Any] | None = None
    drive_root: dict[str, Any] | None = None
    try:
        def _parse_jsonish(raw_value: Any, default: Any) -> Any:
            if raw_value in (None, ""):
                return default
            if isinstance(raw_value, (dict, list, bool)):
                return raw_value
            try:
                return json.loads(raw_value)
            except Exception:
                return default

        if request.content_type and 'multipart/form-data' in request.content_type:
            callsign = (request.form.get('callsign') or '').strip().lower()
            name = (request.form.get('name') or request.form.get('displayName') or '').strip()
            full_name = (request.form.get('fullName') or '').strip()
            description = request.form.get('description', '')
            instructions = request.form.get('instructions', '')
            conversation_starters = _parse_jsonish(request.form.get('conversationStarters', '[]'), [])
            conditioning = request.form.get('conditioning', '')
            definition = request.form.get('definition', '')
            voice = _parse_jsonish(request.form.get('voice', ''), {"text": request.form.get('voice', '')})
            physical_features = _parse_jsonish(request.form.get('physicalFeatures', ''), '')
            capabilities = _parse_jsonish(request.form.get('capabilities', ''), {})
            memory_settings = _parse_jsonish(request.form.get('memory', ''), {})
            canon_refs = _parse_jsonish(request.form.get('canonRefs', '[]'), [])
            knowledge_refs = _parse_jsonish(request.form.get('knowledgeRefs', '[]'), [])
            actions = _parse_jsonish(request.form.get('actions', '[]'), [])
            color_hex = request.form.get('color_hex', '#722F37')
            center_file = request.files.get('center_image')
            center_image_bytes = center_file.read() if center_file else None
            models = _parse_jsonish(request.form.get('models', ''), {})
            orchestration_mode = request.form.get('orchestration_mode', 'standard')
            system_prompt_override = request.form.get('system_prompt', '')
            avatar_b64 = request.form.get('avatar_base64', '')
            voice_sample_file = request.files.get('voice_sample')
            voice_wav_b64 = (
                base64.b64encode(voice_sample_file.read()).decode('ascii')
                if voice_sample_file else request.form.get('voice_wav_base64', '')
            )
            construct_category = (request.form.get('construct_category') or request.form.get('constructCategory') or 'user').strip().lower()
            requested_callsign = (
                request.form.get("requestedCallsign") or ""
            ).strip().lower()
            callsign_allocation_id = (
                request.form.get("callsignAllocationId") or ""
            ).strip()
            try:
                privacy = _normalize_construct_privacy(request.form.get('privacy'))
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
        else:
            data = request.get_json(silent=True)
            if not data or not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid or missing body"}), 400
            callsign = data.get('callsign', '').strip().lower()
            name = (data.get('name') or data.get('displayName') or '').strip()
            full_name = (data.get('fullName') or '').strip()
            description = data.get('description', '')
            instructions = data.get('instructions', '')
            conversation_starters = data.get('conversationStarters', data.get('conversation_starters', []))
            conditioning = data.get('conditioning', '')
            definition = data.get('definition', '')
            voice = data.get('voice', {"text": ""})
            physical_features = data.get('physicalFeatures', '')
            capabilities = data.get('capabilities', {})
            memory_settings = data.get('memory', {})
            canon_refs = data.get('canonRefs', data.get('canon_refs', []))
            knowledge_refs = data.get('knowledgeRefs', data.get('knowledge_refs', []))
            actions = data.get('actions', [])
            color_hex = data.get('color_hex', '#722F37')
            center_image_b64 = data.get('center_image_base64', '')
            center_image_bytes = None
            if center_image_b64:
                import base64 as b64mod
                center_image_bytes = b64mod.b64decode(center_image_b64)
            models = data.get('models', {})
            orchestration_mode = data.get('orchestration_mode', 'standard')
            system_prompt_override = data.get('system_prompt', '')
            avatar_b64 = data.get('avatar_base64', '')
            voice_wav_b64 = data.get('voice_wav_base64', '')
            construct_category = str(data.get('construct_category') or data.get('constructCategory') or data.get('category') or 'user').strip().lower()
            requested_callsign = str(
                data.get("requestedCallsign") or ""
            ).strip().lower()
            callsign_allocation_id = str(
                data.get("callsignAllocationId") or ""
            ).strip()
            try:
                privacy = _normalize_construct_privacy(data.get('privacy'))
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400

        if not callsign or not name:
            return jsonify({"success": False, "error": "callsign and name are required"}), 400
        import re
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}', callsign):
            return jsonify({"success": False, "error": f"Invalid callsign format '{callsign}'. Must be {{name}}-{{NNN}} (e.g., sera-001)"}), 400
        if construct_category not in ('system', 'hydro', 'user'):
            return jsonify({"success": False, "error": "construct category must be system, hydro, or user"}), 400

        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        creation_source = _construct_creation_source(user_id)
        if not creation_source:
            return jsonify({
                "success": False,
                "error": "Construct creation is restricted to the VVAULT UI or simForge",
                "error_code": "VVAULT_CONSTRUCT_CREATION_PROVENANCE_REQUIRED",
            }), 403

        # This authenticated route creates an instance owned by the current
        # account. Catalog membership is not instance ownership: an authored
        # callsign such as insight-001 or continuitygpt-001 must remain an
        # ordinary owner-scoped GPT even when a system catalog uses the same
        # basename. Protected system rows are provisioned through the system
        # repository, never by trusting a client-supplied category here.
        construct_category = "user"

        if construct_category == "user" and callsign_allocation_id:
            try:
                callsign_allocation_id = str(UUID(callsign_allocation_id))
            except (ValueError, TypeError, AttributeError):
                return jsonify({
                    "success": False,
                    "error": "callsignAllocationId must be a canonical UUID",
                }), 400
            callsign_allocation = (
                VAULT_FILE_REPOSITORY.claim_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                    display_name=name,
                )
            )
            if not callsign_allocation:
                return jsonify({
                    "success": False,
                    "error": "Callsign allocation is invalid, already claimed, or belongs to another owner",
                    "error_code": "VVAULT_CALLSIGN_ALLOCATION_INVALID",
                }), 409
            callsign_allocation_claimed = True
            if (
                requested_callsign
                and requested_callsign
                != str(callsign_allocation.get("requested_callsign") or "")
            ):
                VAULT_FILE_REPOSITORY.release_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                )
                callsign_allocation_claimed = False
                return jsonify({
                    "success": False,
                    "error": "requestedCallsign does not match the canonical allocation",
                    "error_code": "VVAULT_CALLSIGN_ALLOCATION_MISMATCH",
                }), 409
            callsign = str(callsign_allocation["allocated_callsign"])
        elif construct_category == "user":
            callsign_allocation = VAULT_FILE_REPOSITORY.allocate_user_callsign(
                user_id=user_id,
                requested_callsign=callsign,
                display_name=name,
            )
            callsign_allocation_id = str(callsign_allocation["allocation_id"])
            callsign_allocation = (
                VAULT_FILE_REPOSITORY.claim_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                    display_name=name,
                )
            )
            if not callsign_allocation:
                raise RuntimeError("Canonical callsign allocation could not be claimed")
            callsign_allocation_claimed = True
            callsign = str(callsign_allocation["allocated_callsign"])

        existing_identity_rows = VAULT_FILE_REPOSITORY.list_construct_identity_rows(
            callsign=callsign,
            bare_name=_bare_name_from_callsign(callsign),
            user_id=user_id,
        )
        preexisting_ids = {
            str(row.get("id"))
            for row in VAULT_FILE_REPOSITORY.list_construct_file_rows(
                callsign=callsign,
                bare_name=_bare_name_from_callsign(callsign),
                user_id=user_id,
                include_content=False,
            )
            if row.get("id")
        }
        if any(str(row.get('filename') or row.get('storage_path') or '').lower().endswith('/prompt.json') for row in existing_identity_rows):
            if callsign_allocation_claimed:
                VAULT_FILE_REPOSITORY.release_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                )
                callsign_allocation_claimed = False
            return jsonify({"success": False, "error": f"Construct {callsign} already exists (prompt.json found)"}), 409

        # Incarnation storage is a hard creation prerequisite. Establish it
        # before the first canonical file write so migration/code skew cannot
        # commit a partial construct.
        incarnation = chatty_body_service.begin_construct_incarnation(
            user_id,
            callsign,
            creation_source=creation_source,
        )
        drive_root = VAULT_DRIVE_REPOSITORY.ensure_construct_root(
            owner_user_id=user_id,
            construct_id=callsign,
        )
        chatty_body_service.invalidate_construct_projection_caches(
            user_id, callsign
        )
        _invalidate_construct_owner_cache(callsign)
        _invalidate_avatar_cache(callsign, user_id)

        now = datetime.now(timezone.utc).isoformat()
        display_name = name
        full_name = full_name or display_name
        models = _normalize_construct_models(models)
        capabilities = _normalize_construct_capabilities(capabilities)
        memory_settings = _normalize_construct_memory_settings(memory_settings)
        canon_refs = _normalize_construct_refs(canon_refs)
        knowledge_refs = _normalize_construct_refs(knowledge_refs)
        if not isinstance(actions, list):
            actions = []
        conversation_starters = _first_non_empty_list([conversation_starters])
        voice_payload = _normalize_construct_voice_payload(voice)
        if orchestration_mode not in ('standard', 'autonomous', 'hybrid', 'custom'):
            orchestration_mode = 'standard'

        if not conditioning:
            conditioning = f"You are {display_name} ({callsign}). Maintain your identity at all times."
        if not definition:
            definition = instructions or f"{display_name} is a protected GPT body within VVAULT."

        prompt_obj = _build_construct_prompt_manifest(
            callsign,
            display_name,
            full_name,
            description,
            instructions,
            conversation_starters,
            capabilities,
            memory_settings,
            canon_refs,
            knowledge_refs,
            source=creation_source,
            created_at=now,
            updated_at=now,
            system_prompt=system_prompt_override or instructions,
            models=models,
            orchestration_mode=orchestration_mode,
            memory_profile=str(memory_settings.get("profile") or ("continuitygpt" if memory_settings.get("enabled", True) else "off")),
        )
        metadata_obj = _build_construct_metadata_payload(
            callsign,
            display_name,
            full_name,
            description,
            models,
            orchestration_mode or "standard",
            capabilities,
            memory_settings,
            canon_refs,
            knowledge_refs,
            source=creation_source,
            created_at=now,
            updated_at=now,
            actions=actions,
            avatar_enabled=bool(avatar_b64),
            privacy=privacy,
        )
        _require_canonical_json_schema(
            prompt_obj,
            "life.vvault.identity.prompt",
        )
        _require_canonical_json_schema(
            metadata_obj,
            "life.vvault.config.metadata",
        )
        transcript_content = f"# Chat with {name}\n\nTranscript started {now}\n"

        avatar_created = False
        avatar_file_entry = None
        if avatar_b64:
            canonical_avatar = normalize_avatar_payload_to_png(
                avatar_b64,
                source_filename=f"{callsign}-avatar-upload",
            )
            avatar_meta = {
                'construct_id': callsign,
                'provider': 'vvault_construct_create',
                'folder': 'identity',
                'construct_category': construct_category,
                **canonical_avatar.metadata,
            }
            avatar_vsi_path = f'instances/{callsign}/identity/avatar.png'
            avatar_record = {
                'filename': avatar_vsi_path,
                'file_type': 'binary',
                'content_type': 'image/png',
                'content': canonical_avatar.content_base64,
                'construct_id': callsign,
                'user_id': user_id,
                'is_system': False,
                'sha256': canonical_avatar.sha256,
                'metadata': json.dumps(avatar_meta),
                'storage_path': avatar_vsi_path,
                'created_at': now,
                'updated_at': now,
            }
            avatar_result = _upsert_vault_file_record(avatar_record, context='construct_avatar')
            if not avatar_result or not avatar_result.get('id'):
                raise RuntimeError(f"Canonical avatar persistence returned no receipt for {callsign}")
            avatar_created = True
            avatar_file_entry = {
                'id': avatar_result.get('id'),
                'filename': avatar_vsi_path,
                'file_type': 'binary',
                'folder': 'identity',
                'action': avatar_result.get('action'),
            }

        voice_wav_path = f"instances/{callsign}/identity/voice.wav"
        voice_wav_result = None
        if voice_wav_b64:
            voice_wav_bytes = base64.b64decode(voice_wav_b64, validate=True)
            if len(voice_wav_bytes) > 20 * 1024 * 1024:
                raise ValueError(f"Voice sample for {callsign} exceeds the 20MB limit")
            if not voice_wav_bytes.startswith(b"RIFF") or voice_wav_bytes[8:12] != b"WAVE":
                raise ValueError(f"Voice sample for {callsign} is not a WAV file")
            voice_wav_result = _upsert_vault_file_record({
                "filename": voice_wav_path,
                "storage_path": voice_wav_path,
                "file_type": "binary",
                "content_type": "audio/wav",
                "content": voice_wav_b64,
                "construct_id": callsign,
                "user_id": user_id,
                "is_system": False,
                "sha256": hashlib.sha256(voice_wav_bytes).hexdigest(),
                "metadata": json.dumps({
                    "construct_id": callsign,
                    "provider": "vvault_construct_create",
                    "folder": "identity",
                    "artifact_id": "life.vvault.identity.voice-sample",
                    "contract_version": "1.0.0",
                }),
                "created_at": now,
                "updated_at": now,
            }, context="construct_voice_sample")
            if not voice_wav_result or not voice_wav_result.get("id"):
                raise RuntimeError(f"Canonical voice sample persistence returned no receipt for {callsign}")

        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from glyph_generator import generate_glyph_to_bytes
        glyph_bytes, glyph_number_rows = generate_glyph_to_bytes(
            callsign, color_hex, center_image_bytes, now
        )
        glyph_sha = hashlib.sha256(glyph_bytes).hexdigest()

        created_files = []
        failed_files = []
        if avatar_file_entry:
            created_files.append(avatar_file_entry)
        if voice_wav_result:
            created_files.append({
                "id": voice_wav_result.get("id"),
                "filename": voice_wav_path,
                "file_type": "binary",
                "folder": "identity",
                "action": voice_wav_result.get("action"),
            })
        try:
            prompt_result = _upsert_construct_prompt_file(
                callsign,
                user_id,
                prompt_obj,
                source=creation_source,
            )
            created_files.append({
                'id': prompt_result.get('id'),
                'filename': f"instances/{callsign}/identity/prompt.json",
                'file_type': 'text',
                'folder': 'identity',
                'action': prompt_result.get('action'),
            })
        except Exception as prompt_err:
            failed_files.append({
                'filename': f"instances/{callsign}/identity/prompt.json",
                'error': str(prompt_err),
            })

        definition_payload = {
            "schema_id": "life.vvault.identity.definition",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "full_name": full_name,
            "role": "assistant",
            "core_definition": definition,
            "aliases": [],
            "updated_at": now,
        }
        physical_source = (
            physical_features
            if isinstance(physical_features, dict)
            else {"overall": physical_features.strip()}
            if isinstance(physical_features, str) and physical_features.strip()
            else {}
        )
        physical_payload = {
            "schema_id": "life.vvault.identity.physical-features",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "bone_structure": physical_source.get("bone_structure") or physical_source.get("Bone structure"),
            "eyes": physical_source.get("eyes") or physical_source.get("Eyes"),
            "brows": physical_source.get("brows") or physical_source.get("Brows"),
            "nose": physical_source.get("nose") or physical_source.get("Nose"),
            "mouth": physical_source.get("mouth") or physical_source.get("Mouth"),
            "skin": physical_source.get("skin") or physical_source.get("Skin"),
            "hair": physical_source.get("hair") or physical_source.get("Hair"),
            "overall": physical_source.get("overall") or physical_source.get("Overall"),
            "updated_at": now,
        }
        voice_source = voice_payload if isinstance(voice_payload, dict) else {}
        voice_profile = {
            "schema_id": "life.vvault.identity.voice",
            "schema_version": "1.0.0",
            "instance_id": callsign,
            "provider": voice_source.get("provider"),
            "voice_id": voice_source.get("voice_id") or voice_source.get("voiceId"),
            "description": voice_source.get("description") or voice_source.get("text"),
            "language": voice_source.get("language") or "en-US",
            "sample_artifact_id": "life.vvault.identity.voice-sample",
            "updated_at": now,
        }
        projection_fields = {
            "conditioning": conditioning,
            "definition": definition_payload,
            "physicalFeatures": physical_payload,
            "voice": voice_profile,
        }
        try:
            projection_result = _project_identity_fields(
                callsign, projection_fields, owner_user_id=user_id, dry_run=False
            )
            for field_result in projection_result.get("results", {}).values():
                created_files.append({
                    'id': field_result.get('file_id'),
                    'filename': field_result.get('canonical_path'),
                    'file_type': 'text',
                    'folder': 'identity',
                    'action': field_result.get('action'),
                })
        except Exception as projection_err:
            failed_files.append({
                'filename': f"instances/{callsign}/identity",
                'error': str(projection_err),
            })

        try:
            metadata_result = _upsert_construct_metadata_file(
                callsign,
                user_id,
                metadata_obj,
                source=creation_source,
            )
            created_files.append({
                'id': metadata_result.get('id'),
                'filename': f"instances/{callsign}/config/metadata.json",
                'file_type': 'text',
                'folder': 'config',
                'action': metadata_result.get('action'),
            })
        except Exception as metadata_err:
            failed_files.append({
                'filename': f"instances/{callsign}/config/metadata.json",
                'error': str(metadata_err),
            })

        transcript_filename = f'chat_with_{callsign}.md'
        transcript_path = f"instances/{callsign}/chatty/{transcript_filename}"
        try:
            transcript_record = {
                'filename': transcript_path,
                'file_type': 'transcript',
                'content': transcript_content,
                'construct_id': callsign,
                'user_id': user_id,
                'is_system': False,
                'sha256': hashlib.sha256(transcript_content.encode('utf-8')).hexdigest(),
                'metadata': json.dumps({
                    'construct_id': callsign,
                    'provider': 'vvault_construct_create',
                    'folder': 'chatty',
                    'construct_category': construct_category,
                }),
                'storage_path': transcript_path,
                'created_at': now,
                'updated_at': now,
            }
            transcript_result = _upsert_vault_file_record(transcript_record, context='construct_chatty_seed')
            created_files.append({
                'id': transcript_result.get('id'),
                'filename': transcript_path,
                'file_type': 'transcript',
                'folder': 'chatty',
                'action': transcript_result.get('action'),
            })
        except Exception as transcript_err:
            failed_files.append({
                'filename': transcript_path,
                'error': str(transcript_err),
            })

        # A newly authored construct has no historical sessions yet, but it is
        # still a complete canonical construct.  Persist an honest zero-session
        # capsule so Chatty can hydrate and select it immediately without
        # inventing memories or requiring a transcript materialization pass.
        capsule_path = f"instances/{callsign}/memup/{callsign}.capsule"
        starter_capsule = _build_starter_capsule(callsign, definition, now)
        _require_canonical_json_schema(
            starter_capsule,
            "life.vvault.memup.capsule",
        )
        capsule_content = json.dumps(starter_capsule, indent=2, ensure_ascii=False)
        try:
            capsule_result = _upsert_vault_file_record({
                'filename': capsule_path,
                'storage_path': capsule_path,
                'file_type': 'capsule',
                'content': capsule_content,
                'construct_id': callsign,
                'user_id': user_id,
                'is_system': False,
                'sha256': hashlib.sha256(capsule_content.encode('utf-8')).hexdigest(),
                'metadata': json.dumps({
                    'construct_id': callsign,
                    'provider': 'vvault_construct_create',
                    'folder': 'memup',
                    'construct_category': construct_category,
                    'history_status': 'not_started',
                }),
                'created_at': now,
                'updated_at': now,
            }, context='construct_starter_capsule')
            created_files.append({
                'id': capsule_result.get('id'),
                'filename': capsule_path,
                'file_type': 'capsule',
                'folder': 'memup',
                'action': capsule_result.get('action'),
            })
        except Exception as capsule_err:
            failed_files.append({
                'filename': capsule_path,
                'error': str(capsule_err),
            })

        import base64 as b64mod
        glyph_b64 = b64mod.b64encode(glyph_bytes).decode('utf-8')
        glyph_filename = "glyph.png"
        glyph_meta = {
            'construct_id': callsign,
            'provider': 'vvault_construct_create',
            'folder': 'config',
            'glyph_number_rows': glyph_number_rows,
            'color_hex': color_hex,
            'construct_category': construct_category,
        }
        glyph_vsi_path = f'instances/{callsign}/config/{glyph_filename}'
        glyph_record = {
            'filename': glyph_vsi_path,
            'file_type': 'binary',
            'content': glyph_b64,
            'construct_id': callsign,
            'user_id': user_id,
            'is_system': False,
            'sha256': glyph_sha,
            'metadata': json.dumps(glyph_meta),
            'storage_path': glyph_vsi_path,
            'created_at': now,
            'updated_at': now,
        }
        glyph_result = _upsert_vault_file_record(glyph_record, context='construct_glyph')
        glyph_created = False
        if glyph_result.get('id'):
            created_files.append({
                'id': glyph_result['id'],
                'filename': glyph_vsi_path,
                'file_type': 'binary',
                'folder': 'config',
                'action': glyph_result['action'],
            })
            glyph_created = True
        else:
            logger.warning(f"Glyph insert returned no data for {callsign}")

        if failed_files:
            rolled_back_ids = _rollback_failed_construct_create(
                callsign,
                user_id,
                preexisting_ids,
            )
            if incarnation:
                chatty_body_service.retire_construct_incarnation(
                    user_id,
                    callsign,
                    incarnation["incarnation_id"],
                )
            VAULT_DRIVE_REPOSITORY.remove_empty_construct_root(
                owner_user_id=user_id, construct_id=callsign
            )
            if callsign_allocation_claimed:
                VAULT_FILE_REPOSITORY.release_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                )
                callsign_allocation_claimed = False
            logger.error(
                "SCAFFOLD_ROLLED_BACK: callsign=%s created=%s failed=%s "
                "rolled_back=%s user=%s",
                callsign,
                len(created_files),
                len(failed_files),
                len(rolled_back_ids),
                user_email,
            )
            return jsonify({
                "success": False,
                "status": "body_missing",
                "canonical": False,
                "callsign": callsign,
                "error": "Construct creation failed canonical completeness checks",
                "error_code": "VVAULT_CONSTRUCT_CREATE_INCOMPLETE",
                "failed_files": failed_files,
                "rolled_back": True,
                "rolled_back_file_ids": rolled_back_ids,
            }), 503
        else:
            logger.info(f"CONSTRUCT_CREATED: callsign={callsign} name={name} files={len(created_files)} user={user_email}")

        response_data = {
            "success": len(created_files) > 0,
            "status": "body_native",
            "canonical": True,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
            "callsign": callsign,
            "requestedCallsign": requested_callsign or callsign,
            "name": name,
            "construct_category": construct_category,
            "creation_source": creation_source,
            "incarnation": incarnation,
            "driveRoot": drive_root,
            "files_created": created_files,
            "file_count": len(created_files),
            "glyph": {
                "filename": glyph_filename,
                "color_hex": color_hex,
                "number_rows": glyph_number_rows,
            },
            "avatar_created": avatar_created,
            "directory_template": {
                "identity": [
                    "prompt.json",
                    "conditioning.txt",
                    "definition.json",
                    "physical_features.json",
                    "voice.json",
                    "voice.wav",
                    "avatar.png",
                ],
                "config": ["glyph.png", "metadata.json"],
                "chatty": [transcript_filename],
                "memup": [f"{callsign}.capsule"],
            },
            "message": f"Construct {callsign} created with {len(created_files)} canonical files"
        }
        if callsign_allocation:
            consumed_allocation = (
                VAULT_FILE_REPOSITORY.consume_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                    callsign=callsign,
                )
            )
            if not consumed_allocation:
                raise RuntimeError("Canonical callsign allocation was not consumed")
            callsign_allocation_claimed = False
            response_data["callsignAllocation"] = _callsign_allocation_dto(
                {
                    **callsign_allocation,
                    **consumed_allocation,
                }
            )
        if failed_files:
            response_data["failed_files"] = failed_files
            response_data["message"] += f" ({len(failed_files)} files failed to save)"
        
        if len(created_files) > 0:
            _log_privileged_event(
                "config_change",
                resource=f"construct:{callsign}",
                action="create",
                result="success",
                description=f"Construct created: {callsign}",
                metadata={"callsign": callsign, "name": name, "file_count": len(created_files)},
                user_id=user_email,
            )
        chatty_body_service.invalidate_construct_projection_caches(
            user_id, callsign
        )
        _invalidate_construct_owner_cache(callsign)
        _invalidate_avatar_cache(callsign, user_id)
        return jsonify(response_data), 201

    except Exception as e:
        logger.error(f"Error creating construct: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        rolled_back_ids: list[str] = []
        if callsign and user_id:
            try:
                rolled_back_ids = _rollback_failed_construct_create(
                    callsign,
                    user_id,
                    preexisting_ids,
                )
            except Exception:
                logger.exception(
                    "Failed to roll back incomplete construct create for %s",
                    callsign,
                )
            if incarnation:
                try:
                    chatty_body_service.retire_construct_incarnation(
                        user_id,
                        callsign,
                        incarnation["incarnation_id"],
                    )
                except Exception:
                    logger.exception(
                        "Failed to retire incomplete construct incarnation for %s",
                        callsign,
                    )
            if drive_root:
                try:
                    VAULT_DRIVE_REPOSITORY.remove_empty_construct_root(
                        owner_user_id=user_id, construct_id=callsign
                    )
                except Exception:
                    logger.exception(
                        "Failed to remove incomplete construct Drive root for %s",
                        callsign,
                    )
            chatty_body_service.invalidate_construct_projection_caches(
                user_id, callsign
            )
            _invalidate_avatar_cache(callsign, user_id)
        if callsign_allocation_claimed and callsign_allocation_id and user_id:
            try:
                VAULT_FILE_REPOSITORY.release_user_callsign_allocation(
                    allocation_id=callsign_allocation_id,
                    user_id=user_id,
                )
            except Exception:
                logger.exception(
                    "Failed to release callsign allocation %s",
                    callsign_allocation_id,
                )
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/chatty/construct/create")
        return jsonify({
            "success": False,
            "error": "Construct creation failed",
            "error_code": type(e).__name__,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
            "rolled_back": bool(rolled_back_ids),
            "rolled_back_file_ids": rolled_back_ids,
        }), 503


@app.route('/api/chatty/construct/<construct_id>', methods=['GET'])
@require_chatty_auth
def get_chatty_construct_profile(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    body_payload, body_status = chatty_body_service.construct_profile(
        construct_id, owner_user_id=actor_user_id
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/construct/<construct_id>/knowledge-context', methods=['GET'])
@require_chatty_auth
def get_chatty_construct_knowledge_context(construct_id):
    """Return an authenticated, read-only canonical knowledge projection."""
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    profile_result = chatty_body_service.construct_profile(
        construct_id, owner_user_id=actor_user_id
    )
    profile_payload, profile_status = profile_result.to_response()
    if profile_status >= 300 or profile_result.status != "body_native":
        if profile_status >= 500:
            _invalidate_projection_capability(
                "knowledge_context_projection", f"PROFILE_HTTP_{profile_status}"
            )
        return jsonify(profile_payload), profile_status
    profile = profile_result.payload.get("profile") or {}
    try:
        owner_shared_references = knowledge_activation_service.owner_shared_references(
            owner_user_id=actor_user_id
        )
    except (RuntimeError, ValueError) as exc:
        return jsonify({
            "success": False,
            "canonical": False,
            "status": "blocked",
            "error": str(exc),
            "reason": "owner shared activation set could not be resolved",
        }), 503 if isinstance(exc, RuntimeError) else 409
    references = (
        list(owner_shared_references)
        + list(profile.get("canonRefs") or [])
        + list(profile.get("knowledgeRefs") or [])
    )
    deduplicated_references = []
    reference_keys = set()
    for reference in references:
        if isinstance(reference, dict):
            key = (
                reference.get("artifact_id") or reference.get("artifactId"),
                str(reference.get("revision") or ""),
                reference.get("sha256") or reference.get("contentHash"),
            )
        else:
            key = (str(reference), "", "")
        if key not in reference_keys:
            reference_keys.add(key)
            deduplicated_references.append(reference)
    requirements = profile.get("contextRequirements") or {}
    require_shared = bool(requirements.get("sharedTraining"))
    try:
        projection, status = knowledge_contract.resolve_knowledge_references(
            owner_user_id=actor_user_id,
            instance_id=construct_id,
            references=deduplicated_references,
            require_shared=require_shared,
            owner_shared_references=owner_shared_references,
        )
    except RuntimeError as exc:
        _invalidate_projection_capability("knowledge_context_projection", exc)
        return jsonify({
            "success": False,
            "canonical": False,
            "status": "blocked",
            "error": str(exc),
            "error_code": type(exc).__name__,
        }), 503
    except ValueError as exc:
        return jsonify({
            "success": False,
            "canonical": False,
            "status": "blocked",
            "error": str(exc),
            "error_code": type(exc).__name__,
        }), 409
    if status >= 300:
        if status >= 500:
            _invalidate_projection_capability(
                "knowledge_context_projection", f"KNOWLEDGE_HTTP_{status}"
            )
        return jsonify({
            **projection,
            "status": "blocked",
            "route": f"/api/chatty/construct/{construct_id}/knowledge-context",
            "reason": "required canonical knowledge references could not be resolved",
        }), status
    query = str(request.args.get("query") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit") or 12), 24))
    except (TypeError, ValueError):
        limit = 12
    selected = knowledge_contract.select_claims(
        projection["artifacts"], query, limit=limit
    )
    return jsonify({
        **projection,
        "status": "body_native",
        "route": f"/api/chatty/construct/{construct_id}/knowledge-context",
        "query": query,
        "limit": limit,
        "selected_claims": selected,
        "selected_claim_count": len(selected),
        "selection": {
            "contract": "life-vvault-knowledge-selection/v1",
            "applied": True,
            "selectedClaimCount": len(selected),
            "sharedCorpusComplete": bool((projection.get("shared_corpus") or {}).get("complete")),
        },
    }), 200


@app.route('/api/chatty/construct/<construct_id>/canonical-context/candidates', methods=['POST'])
@require_chatty_auth
def get_chatty_canonical_context_candidates(construct_id):
    """Return a signed owner-qualified candidate manifest; never a prompt."""
    owner_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "success": False,
            "errorCode": "CONTEXT_REQUEST_INVALID",
            "error": "request must be a JSON object",
        }), 400
    try:
        envelope = canonical_context_service.project_candidates(
            owner_user_id=owner_user_id,
            construct_id=construct_id,
            request=payload,
        )
        return jsonify(envelope), 200
    except canonical_context_service.CanonicalContextError as exc:
        return jsonify({
            "success": False,
            "errorCode": exc.code,
            "error": str(exc),
        }), exc.status
    except RuntimeError as exc:
        return jsonify({
            "success": False,
            "errorCode": str(exc),
            "error": "canonical context projection is unavailable",
        }), 503


@app.route('/api/chatty/human-context', methods=['GET'])
@require_chatty_auth
def get_chatty_human_context():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    try:
        projection = human_context_service.read_projection(owner_user_id=owner_user_id)
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "body_native",
        "authority": "ovvaults.vault_files",
        "contextSea": human_context_service.context_sea_eligibility(),
        "projection": projection,
    }), 200


@app.route('/api/chatty/human-context/publications', methods=['POST'])
@require_chatty_auth
def publish_chatty_human_context():
    return jsonify({
        "success": False,
        "canonical": False,
        "status": "retired",
        "error": "legacy human-context publication is retired; use account-context and knowledge publication contracts",
    }), 410


@app.route('/api/chatty/account-context', methods=['GET'])
@require_chatty_auth
def get_chatty_account_context():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    try:
        projection = account_context_service.read_projection(owner_user_id=owner_user_id)
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "body_native",
        "authority": "ovvaults.vault_files",
        "projection": projection,
    }), 200


@app.route('/api/chatty/account-context/publications', methods=['POST'])
@require_chatty_auth
def publish_chatty_account_context():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    payload = request.get_json(silent=True) or {}
    assertion = str(
        payload.get("assertion")
        or request.headers.get("X-Auth-Account-Context")
        or ""
    ).strip()
    if not assertion:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": "Auth account assertion is required"}), 400
    try:
        receipt = account_context_service.publish(
            owner_user_id=owner_user_id,
            actor=owner_user_id,
            assertion=assertion,
        )
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "published",
        "authority": "ovvaults.vault_files",
        "receipt": receipt,
    }), 200 if receipt["idempotent"] else 201


@app.route('/api/chatty/knowledge/publications', methods=['POST'])
@require_chatty_auth
def publish_chatty_shared_knowledge():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    document = request.get_json(silent=True) or {}
    actor = str((getattr(request, "current_user", {}) or {}).get("email") or owner_user_id)
    try:
        receipt = knowledge_publication_service.publish(owner_user_id=owner_user_id, actor=actor, document=document)
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": str(exc)}), 409
    return jsonify({"success": True, "canonical": True, "status": "published",
                    "authority": "ovvaults.vault_files", "receipt": receipt}), 200 if receipt["idempotent"] else 201


@app.route('/api/chatty/construct/<construct_id>/knowledge/publications', methods=['POST'])
@require_chatty_auth
def publish_chatty_construct_knowledge(construct_id):
    owner_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    document = request.get_json(silent=True) or {}
    actor = str((getattr(request, "current_user", {}) or {}).get("email") or owner_user_id)
    try:
        receipt = knowledge_publication_service.publish(
            owner_user_id=owner_user_id,
            actor=actor,
            document=document,
            construct_id=chatty_body_service.normalize_callsign(construct_id),
        )
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "published",
        "authority": "ovvaults.vault_files",
        "receipt": receipt,
    }), 200 if receipt["idempotent"] else 201


@app.route('/api/chatty/construct/<construct_id>/knowledge/activations', methods=['POST'])
@require_chatty_auth
def activate_chatty_construct_knowledge(construct_id):
    owner_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]
    payload = request.get_json(silent=True) or {}
    actor = str((getattr(request, "current_user", {}) or {}).get("email") or owner_user_id)
    try:
        receipt = knowledge_activation_service.activate(
            owner_user_id=owner_user_id,
            actor=actor,
            construct_id=construct_id,
            knowledge_reference=payload.get("knowledgeReference") or payload.get("knowledge_reference") or {},
        )
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": str(exc)}), 409
    chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
    _invalidate_construct_owner_cache(construct_id)
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "activated",
        "authority": "ovvaults.vault_files",
        "receipt": receipt,
    }), 200 if receipt["idempotent"] else 201


@app.route('/api/chatty/knowledge/activations', methods=['POST'])
@require_chatty_auth
def activate_chatty_owner_shared_knowledge():
    owner_user_id = _get_authenticated_user_id()
    if not owner_user_id:
        return jsonify({"success": False, "error": "authenticated owner_user_id is required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        receipt = knowledge_activation_service.activate_shared(
            owner_user_id=owner_user_id,
            actor=owner_user_id,
            knowledge_reference=payload.get("knowledgeReference") or payload.get("knowledge_reference") or {},
        )
    except RuntimeError as exc:
        return jsonify({"success": False, "canonical": False, "status": "blocked", "error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"success": False, "canonical": False, "status": "rejected", "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "canonical": True,
        "status": "activated",
        "authority": "ovvaults.vault_files",
        "receipt": receipt,
    }), 200 if receipt["idempotent"] else 201


@app.route('/api/chatty/construct/<construct_id>', methods=['PUT', 'PATCH'])
@require_chatty_auth
def update_chatty_construct_profile(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    payload = request.get_json(silent=True) or {}
    body_payload, body_status = chatty_body_service.update_construct_profile(
        construct_id,
        payload,
        user_id=actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_construct_projection_caches(
            actor_user_id, construct_id
        )
        _invalidate_construct_owner_cache(construct_id)
        _invalidate_avatar_cache(construct_id, actor_user_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/construct/<construct_id>/category', methods=['PATCH'])
@require_chatty_auth
def update_chatty_construct_category(construct_id):
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    category = payload.get('construct_category') or payload.get('constructCategory') or payload.get('category')
    body_payload, body_status = chatty_body_service.set_construct_category(
        construct_id,
        category,
        user_id=actor_user_id,
    ).to_response()
    if body_status < 300:
        chatty_body_service.invalidate_construct_projection_caches(
            actor_user_id, construct_id
        )
        _invalidate_construct_owner_cache(construct_id)
        _invalidate_avatar_cache(construct_id, actor_user_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/construct-taxonomy', methods=['GET'])
@require_chatty_auth
def get_chatty_construct_taxonomy():
    """Return the protected OVVAULTS construct-classification contract."""
    if not _get_authenticated_user_id():
        return jsonify({"success": False, "error": "User not found"}), 403
    return jsonify(taxonomy_payload())


@app.route('/api/chatty/models', methods=['GET'])
@require_chatty_auth
def get_chatty_byop_models():
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    body_payload, body_status = chatty_body_service.list_byop_models(
        actor_user_id,
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/models', methods=['POST'])
@require_chatty_auth
def upsert_chatty_byop_model():
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    body_payload, body_status = chatty_body_service.upsert_byop_model(
        actor_user_id,
        request.get_json(silent=True) or {},
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/provider-connections', methods=['GET'])
@require_chatty_auth
def get_chatty_provider_connections():
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    body_payload, body_status = chatty_body_service.list_provider_connections(
        actor_user_id,
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/provider-connections/<provider>', methods=['POST'])
@require_chatty_auth
def store_chatty_provider_connection(provider):
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    body_payload, body_status = chatty_body_service.store_provider_connection(
        actor_user_id,
        provider,
        payload.get("credential"),
        payload.get("metadata"),
    ).to_response()
    return jsonify(body_payload), body_status


@app.route(
    '/api/chatty/provider-connections/<provider>/credential',
    methods=['GET'],
)
@require_chatty_auth
def get_chatty_provider_connection_credential(provider):
    if not _service_token_matches():
        return jsonify({
            "success": False,
            "error": "Service authentication required",
        }), 403
    actor_user_id = _get_authenticated_user_id()
    if not actor_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    body_payload, body_status = chatty_body_service.get_provider_credential(
        actor_user_id,
        provider,
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/chatty/construct/<construct_id>', methods=['DELETE'])
@require_chatty_auth
def delete_chatty_construct_profile(construct_id):
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    payload = request.get_json(silent=True) or {}
    body_payload, body_status = chatty_body_service.delete_construct(
        construct_id,
        user_id=actor_user_id,
        community_store_disposition=payload.get("communityStoreDisposition"),
    ).to_response()
    if body_status == 200:
        chatty_body_service.invalidate_construct_projection_caches(
            actor_user_id, construct_id
        )
        _invalidate_construct_owner_cache(construct_id)
        _invalidate_avatar_cache(construct_id, actor_user_id)
    return jsonify(body_payload), body_status


@app.route('/api/chatty/constructs')
@require_chatty_auth
def get_chatty_constructs():
    """Get all available constructs with chat transcripts (user-scoped).

    Deduplicates bare-name vs callsign entries: if both 'katana' and
    'katana-001' transcripts exist, only 'katana-001' is returned.
    """
    route_started = time.perf_counter()
    try:
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        include_hidden = str(request.args.get('include_hidden') or '').strip().lower() in {'1', 'true', 'yes'}
        body_payload, body_status = chatty_body_service.list_constructs(
            user_id,
            include_hidden=include_hidden,
        ).to_response()
        response = jsonify(body_payload)
        response.headers["Server-Timing"] = (
            f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
        )
        response.headers["X-VVAULT-Cache-State"] = str(body_payload.get("cacheState") or "unknown")
        return response, body_status
    except Exception as exc:
        logger.error(f"Error fetching chatty constructs: {exc}")
        if _is_dependency_timeout(exc):
            return _dependency_timeout_read_response(
                "/api/chatty/constructs",
                include_constructs=True,
            )
        return jsonify({"success": False, "error": "Failed to load constructs"}), 500


@app.route('/api/chatty/community-store')
@require_chatty_auth
def get_chatty_community_store():
    """Return authenticated, community-scoped canonical Store publications."""
    route_started = time.perf_counter()
    body_payload, body_status = chatty_body_service.list_community_store().to_response()
    response = jsonify(body_payload)
    response.headers["Server-Timing"] = (
        f"vvault;dur={(time.perf_counter() - route_started) * 1000:.2f}"
    )
    response.headers["X-VVAULT-Cache-State"] = str(body_payload.get("cacheState") or "unknown")
    return response, body_status


def _active_store_avatar_row(listing_id: str, *, include_content: bool) -> dict[str, Any] | None:
    prefix = "active:"
    metadata_file_id = str(listing_id or "")
    if not metadata_file_id.startswith(prefix):
        return None
    metadata_file_id = metadata_file_id[len(prefix):]
    if not _is_uuid(metadata_file_id):
        return None
    row = VAULT_FILE_REPOSITORY.get_active_store_avatar(
        metadata_file_id=metadata_file_id,
        include_content=include_content,
    )
    metadata = _safe_json_loads((row or {}).get("metadata_content")) or {}
    if not isinstance(metadata, dict):
        return None
    privacy = str(metadata.get("privacy") or "private").strip().lower()
    category = str(metadata.get("construct_category") or metadata.get("category") or "user").strip().lower()
    lifecycle = str(metadata.get("lifecycle_stage") or "gpt").strip().lower()
    if privacy != "store" or category != "user" or lifecycle not in {"gpt", "sim"}:
        return None
    return row


@app.route('/api/chatty/community-store/listings/<path:listing_id>/avatar')
@require_chatty_auth
def get_active_store_avatar_descriptor(listing_id):
    row = _active_store_avatar_row(listing_id, include_content=False)
    if not row:
        return jsonify({"success": False, "error": "Store avatar not found"}), 404
    sha = str(row.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return jsonify({"success": False, "state": "hydration_error", "errorCode": "AVATAR_SHA256_UNAVAILABLE"}), 503
    descriptor_url = f"/api/chatty/community-store/listings/{listing_id}/avatar"
    response = jsonify({
        "success": True,
        "canonical": True,
        "state": "available",
        "avatar": {
            "sha256": sha,
            "contentType": str(row.get("content_type") or "image/png"),
            "sizeBytes": int(row.get("size_bytes") or 0),
            "descriptorUrl": descriptor_url,
            "bytesUrl": f"{descriptor_url}/bytes",
        },
        "ownerIdentifiersProjected": False,
    })
    response.headers["ETag"] = f'"{sha}"'
    response.headers["Cache-Control"] = "private, max-age=60, stale-if-error=300"
    return response


@app.route('/api/chatty/community-store/listings/<path:listing_id>/avatar/bytes')
@require_chatty_auth
def get_active_store_avatar_bytes(listing_id):
    row = _active_store_avatar_row(listing_id, include_content=True)
    if not row:
        return jsonify({"success": False, "error": "Store avatar not found"}), 404
    sha = str(row.get("sha256") or "").strip().lower()
    etag = f'"{sha}"' if re.fullmatch(r"[0-9a-f]{64}", sha) else None
    if etag and request.headers.get("If-None-Match") == etag:
        response = Response(status=304)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, max-age=60, stale-if-error=300"
        return response
    media_row = dict(row)
    media_row["content"] = row.get("avatar_content")
    body, content_type, unavailable_reason = _media_preview_bytes(media_row)
    if unavailable_reason or body is None or hashlib.sha256(body).hexdigest() != sha:
        return jsonify({"success": False, "state": "hydration_error", "errorCode": "AVATAR_BODY_UNAVAILABLE"}), 503
    response = Response(body, status=200, mimetype=content_type or "image/png")
    response.headers["Content-Length"] = str(len(body))
    response.headers["ETag"] = etag or ""
    response.headers["Cache-Control"] = "private, max-age=60, stale-if-error=300"
    return response


@app.route('/api/chatty/marketplace/listings')
@require_chatty_auth
def get_marketplace_listings():
    listings = marketplace_service.list_listings()
    return jsonify({"success": True, "canonical": True, "listings": listings,
                    "count": len(listings), "ownerIdentifiersProjected": False,
                    "cacheState": "fresh", "refreshing": False})


@app.route('/api/chatty/marketplace/listings/<listing_id>')
@require_chatty_auth
def get_marketplace_listing_detail(listing_id):
    try:
        listing = marketplace_service.detail(listing_id)
    except (ValueError, TypeError):
        listing = None
    if not listing:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_LISTING_NOT_FOUND"}), 404
    return jsonify({"success": True, "canonical": True, "listing": listing})


@app.route('/api/chatty/marketplace/listings/<listing_id>/avatar')
@require_chatty_auth
def get_marketplace_listing_avatar(listing_id):
    try:
        row = marketplace_service.package_row(listing_id)
    except (ValueError, TypeError):
        row = None
    if not row:
        return jsonify({"success": False, "state": "missing", "errorCode": "VVAULT_MARKETPLACE_LISTING_NOT_FOUND"}), 404
    state = "available" if row.get("avatar_sha256") else "missing"
    response = jsonify({"success": True, "canonical": True, "listingId": listing_id,
                        "state": state, "avatar": marketplace_service._dto(row, detail=False)["avatar"]})
    response.headers["Cache-Control"] = "private, max-age=300"
    if row.get("avatar_sha256"):
        response.headers["ETag"] = f'"{row["avatar_sha256"]}"'
    return response


@app.route('/api/chatty/marketplace/listings/<listing_id>/avatar/bytes')
@require_chatty_auth
def get_marketplace_listing_avatar_bytes(listing_id):
    try:
        row = marketplace_service.package_row(listing_id, include_avatar=True)
    except (ValueError, TypeError):
        row = None
    if not row or row.get("avatar_body") is None:
        return jsonify({"success": False, "state": "missing", "errorCode": "VVAULT_MARKETPLACE_AVATAR_NOT_FOUND"}), 404
    etag = f'"{row["avatar_sha256"]}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag, "Cache-Control": "private, max-age=300"})
    return Response(bytes(row["avatar_body"]), status=200,
                    content_type=row.get("avatar_content_type") or "image/png",
                    headers={"ETag": etag, "Cache-Control": "private, max-age=300"})


@app.route('/api/chatty/marketplace/listings/<listing_id>/install-preflight', methods=['POST'])
@require_chatty_auth
def marketplace_install_preflight(listing_id):
    current_user = getattr(request, "current_user", None) or {}
    mapped_owner_id = str(current_user.get("id") or "").strip()
    owner_user_id = (
        mapped_owner_id
        if _trusted_service_identity_cache_allowed() and _is_uuid(mapped_owner_id)
        else _get_authenticated_user_id()
    )
    if not owner_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    requested = str(payload.get("requestedCallsign") or "").strip().lower()
    try:
        result = marketplace_service.install_preflight(owner_user_id, listing_id, requested)
    except LookupError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_LISTING_NOT_FOUND"}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True, "listingId": listing_id,
                    "requestedCallsign": requested, **result})


@app.route('/api/chatty/marketplace/listings/<listing_id>/install', methods=['POST'])
@require_chatty_auth
def install_marketplace_listing(listing_id):
    current_user = getattr(request, "current_user", None) or {}
    mapped_owner_id = str(current_user.get("id") or "").strip()
    owner_user_id = (
        mapped_owner_id
        if _trusted_service_identity_cache_allowed() and _is_uuid(mapped_owner_id)
        else _get_authenticated_user_id()
    )
    if not owner_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    requested = str(payload.get("requestedCallsign") or "").strip().lower()
    privacy = str(payload.get("privacy") or "private").strip().lower()
    try:
        result, replay = marketplace_service.install(owner_user_id, listing_id, requested,
            str(payload.get("idempotencyKey") or "").strip(), privacy)
    except FileExistsError:
        preflight = marketplace_service.install_preflight(owner_user_id, listing_id, requested)
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_OWNER_COLLISION",
                        "requestedCallsign": requested, "suggestedCallsign": preflight.get("suggestedCallsign")}), 409
    except LookupError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_LISTING_NOT_FOUND"}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    chatty_body_service.invalidate_construct_projection_caches(owner_user_id, requested)
    return jsonify({"success": True, "canonical": True,
                    "status": "already_installed" if replay else "installed", **result}), 200 if replay else 201


@app.route('/api/chatty/marketplace/installations/<installation_id>', methods=['DELETE'])
@require_chatty_auth
def uninstall_marketplace_listing(installation_id):
    current_user = getattr(request, "current_user", None) or {}
    mapped_owner_id = str(current_user.get("id") or "").strip()
    owner_user_id = (
        mapped_owner_id
        if _trusted_service_identity_cache_allowed() and _is_uuid(mapped_owner_id)
        else _get_authenticated_user_id()
    )
    if not owner_user_id:
        return jsonify({"success": False, "error": "User not found"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        receipt, status = marketplace_service.uninstall(
            owner_user_id, installation_id, str(payload.get("idempotencyKey") or "").strip(),
            payload.get("communityStoreDisposition"),
        )
    except LookupError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_INSTALLATION_NOT_FOUND"}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "errorCode": str(exc)}), 409
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True,
                    "status": "already_uninstalled" if status == 200 else "uninstalled", **receipt}), status


def _marketplace_service_authorized() -> bool:
    expected = os.environ.get("VVAULT_SERVICE_TOKEN")
    provided = request.headers.get("X-Chatty-Key") or request.headers.get("X-Service-Token")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


@app.route('/api/vault/marketplace/publishers', methods=['POST'])
@require_chatty_auth
def register_marketplace_publisher():
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        publisher = marketplace_service.register_publisher(
            str(payload.get("publisherSlug") or ""), str(payload.get("displayName") or ""),
            str(payload.get("originLabel") or ""))
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True, "publisher": publisher}), 201


@app.route('/api/vault/marketplace/publishers/<publisher_id>/signing-keys', methods=['POST'])
@require_chatty_auth
def register_marketplace_signing_key(publisher_id):
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        key = marketplace_service.register_signing_key(publisher_id,
            str(payload.get("keyId") or ""), str(payload.get("publicKeyBase64") or ""))
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True, "signingKey": key}), 201


@app.route('/api/vault/marketplace/curators', methods=['POST'])
@require_chatty_auth
def register_marketplace_curator():
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        curator = marketplace_service.register_curator(
            str(payload.get("curatorSlug") or ""), str(payload.get("displayName") or ""),
            str(payload.get("publicKeyBase64") or ""))
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True, "curator": curator}), 201


@app.route('/api/vault/marketplace/packages', methods=['POST'])
@require_chatty_auth
def publish_marketplace_package():
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        avatar = base64.b64decode(str(payload.get("avatarBase64") or ""), validate=True) if payload.get("avatarBase64") else None
        package = marketplace_service.publish_package(str(payload.get("publisherId") or ""),
            str(payload.get("signingKeyId") or ""), payload.get("manifest"),
            str(payload.get("signatureBase64") or ""), avatar,
            str(payload.get("avatarContentType") or "image/png") if avatar else None)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc), "errorCode": "VVAULT_MARKETPLACE_PACKAGE_REJECTED"}), 400
    return jsonify({"success": True, "canonical": True, "package": package}), 201


@app.route('/api/vault/marketplace/curated-packages', methods=['POST'])
@require_chatty_auth
def publish_curated_marketplace_package():
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        avatar = base64.b64decode(str(payload.get("avatarBase64") or ""), validate=True) if payload.get("avatarBase64") else None
        package = marketplace_service.publish_curated_package(
            str(payload.get("publisherId") or ""), str(payload.get("curatorId") or ""),
            payload.get("manifest"), payload.get("provenance"),
            str(payload.get("attestationSignatureBase64") or ""), avatar,
            str(payload.get("avatarContentType") or "image/png") if avatar else None)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc),
                        "errorCode": "VVAULT_MARKETPLACE_ATTESTATION_REJECTED"}), 400
    return jsonify({"success": True, "canonical": True, "package": package}), 201


@app.route('/api/vault/marketplace/listings/<listing_id>', methods=['DELETE'])
@require_chatty_auth
def delist_marketplace_listing(listing_id):
    if not _marketplace_service_authorized():
        return jsonify({"success": False, "error": "Service authentication required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        receipt, replay = marketplace_service.delist(
            listing_id,
            actor_type=str(payload.get("actorType") or ""),
            actor_id=str(payload.get("actorId") or ""),
            idempotency_key=str(payload.get("idempotencyKey") or ""),
            reason=str(payload.get("reason") or ""),
        )
    except LookupError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_LISTING_NOT_FOUND"}), 404
    except PermissionError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_DELIST_FORBIDDEN"}), 403
    except FileExistsError:
        return jsonify({"success": False, "errorCode": "VVAULT_MARKETPLACE_ALREADY_DELISTED"}), 409
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "canonical": True,
                    "status": "already_delisted" if replay else "delisted", **receipt}), 200 if replay else 201


@app.route('/api/public/constructs/<construct_id>', methods=['GET'])
def get_public_construct_share(construct_id):
    """Public, owner-redacted projection for link/store constructs only."""
    body_payload, body_status = chatty_body_service.public_construct_share(
        construct_id
    ).to_response()
    response = jsonify(body_payload)
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    return response, body_status


@app.route('/api/chatty/message', methods=['POST'])
@require_chatty_auth
def chatty_message():
    data = request.get_json(silent=True) or {}
    construct_id = data.get("constructId")
    if not construct_id:
        return jsonify({"success": False, "error": "constructId is required"}), 400
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    body_payload, body_status = chatty_body_service.message(
        construct_id,
        data,
        owner_user_id=actor_user_id,
    ).to_response()
    return jsonify(body_payload), body_status


# Zero Trust Audit API
@app.route('/api/admin/audit-log')
@require_role('admin')
def get_audit_log():
    """Get authentication audit log - admin only (Zero Trust telemetry)"""
    if _rate_limit_key("admin"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    limit = request.args.get('limit', 100, type=int)
    result_filter = request.args.get('result', None)
    
    logs = AUTH_AUDIT_LOG[-limit:]
    
    if result_filter:
        logs = [l for l in logs if l.get('result') == result_filter]
    
    return jsonify({
        "success": True,
        "audit_log": logs,
        "total_entries": len(AUTH_AUDIT_LOG),
        "returned": len(logs)
    })


@app.route('/api/admin/constructs/congruency-purge', methods=['POST'])
@require_role('admin')
def purge_unauthorized_constructs():
    owner_id = _get_authenticated_user_id()
    if not owner_id:
        return jsonify({
            "success": False,
            "error": "Canonical session owner was not found",
            "error_code": "VVAULT_CONGRUENCY_OWNER_NOT_FOUND",
        }), 401
    body_payload, body_status = chatty_body_service.purge_constructs(
        list(CONGRUENCY_PURGE_CONSTRUCTS),
        user_id=owner_id,
    ).to_response()
    return jsonify(body_payload), body_status


@app.route('/api/admin/security-summary')
@require_role('admin')
def get_security_summary():
    """Get zero trust security summary - admin only"""
    if _rate_limit_key("admin"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    total = len(AUTH_AUDIT_LOG)
    denied = len([l for l in AUTH_AUDIT_LOG if l.get('result') == 'denied'])
    allowed = len([l for l in AUTH_AUDIT_LOG if l.get('result') == 'allowed'])
    
    unique_users = set(l.get('user_id') for l in AUTH_AUDIT_LOG if l.get('user_id') != 'anonymous')
    anonymous_attempts = len([l for l in AUTH_AUDIT_LOG if l.get('user_id') == 'anonymous'])
    
    return jsonify({
        "success": True,
        "summary": {
            "total_auth_events": total,
            "allowed": allowed,
            "denied": denied,
            "denial_rate": round(denied / total * 100, 2) if total > 0 else 0,
            "unique_users": len(unique_users),
            "anonymous_attempts": anonymous_attempts,
        }
    })

# Legal document routes
@app.route('/terms-of-service.html')
def terms_of_service():
    """Serve the Terms of Service HTML page."""
    return send_from_directory('.', 'terms-of-service.html')

@app.route('/privacy-notice.html')
def privacy_notice():
    """Serve the Privacy Notice HTML page."""
    return send_from_directory('.', 'privacy-notice.html')

@app.route('/european-electronic-communications-code-disclosure.html')
def eeccd_disclosure():
    """Serve the EECCD Disclosure HTML page."""
    return send_from_directory('.', 'european-electronic-communications-code-disclosure.html')

@app.route('/api/config')
def get_config():
    """Get configuration info"""
    door = _resolve_chatty_vvault_door()
    return jsonify({
        "backend_port": 8000,
        "frontend_port": 7784,
        "project_dir": PROJECT_DIR,
        "capsules_dir": CAPSULES_DIR,
        "cors_origins": _cors_origins,
        "runtime_environment": "production" if door.get("selected_door") == "public" else "development",
        "frontend_origin": _resolve_frontend_origin(),
        "backend_origin": _resolve_backend_origin(),
        "door_contract": door,
    })

def _begin_credential_session(user):
    """Use the native OAuth device/enrollment path after password proof only."""
    status = user.get("enrollment_status")
    if status not in {vvault_enrollment.ACTIVE, vvault_enrollment.PENDING}:
        return jsonify({"success": False, "state": "ENROLLMENT_REQUIRED", "error": "Account requires authorized enrollment recovery"}), 403
    # A valid existing session already proves the device and enrollment gates.
    current, _ = get_current_user()
    if current and str(current.get("id")) == str(user["id"]):
        return jsonify({"success": True, "state": "AUTHENTICATED", "user": {
            "id": str(user["id"]), "email": user["email"], "name": user.get("name"), "role": user.get("role", "user"),
        }})
    device_secret = vvault_enrollment.opaque_token()
    device = AUTH_REPOSITORY.create_pending_device(
        user_id=str(user["id"]), device_digest=vvault_enrollment.keyed_digest(device_secret, _enrollment_secret()),
        ip_hash=vvault_enrollment.request_evidence(request.remote_addr or "", _enrollment_secret()),
        user_agent_hash=vvault_enrollment.request_evidence(request.headers.get("User-Agent", ""), _enrollment_secret()),
    )
    pending_token = vvault_enrollment.opaque_token()
    issue_pending = AUTH_REPOSITORY.issue_device_pending_session if status == vvault_enrollment.ACTIVE else AUTH_REPOSITORY.issue_pending_oauth_session
    issue_pending(user_id=str(user["id"]), token_hash=_session_token_hash(pending_token),
                  device_id=str(device["id"]), expires_at=datetime.now(timezone.utc) + timedelta(minutes=20))
    response = jsonify({"success": True,
                        "state": "USER_DECISION_REQUIRED" if status == vvault_enrollment.ACTIVE else "ENROLLMENT_REQUIRED",
                        "redirect": "/?oauth_pending=1"})
    response.set_cookie("vvault_pending_session", pending_token, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=20 * 60, path="/")
    response.set_cookie("vvault_pending_device", device_secret, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=20 * 60, path="/")
    return response












# Authentication endpoints
@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login endpoint (database-backed)"""
    return jsonify({"success": False, "error": "Password sign-in has been retired"}), 410

    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        if not email or not password:
            log_auth_decision("login_attempt", email or "unknown", "/api/auth/login", "denied", "missing_credentials", ip)
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        if not _auth_repository_ready():
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "auth_repository_unavailable", ip)
            return _auth_repository_unavailable_response("/api/auth/login")
        
        user_data = db_get_user(email)
        
        if not user_data:
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "user_not_found", ip)
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        password_hash = user_data.get('password_hash')
        has_vvault_pw = bool(password_hash and password_hash != vvault_auth_repository.OAUTH_DISABLED_PASSWORD_HASH)
        has_chatty_pw = bool(user_data.get('auth_password_hash'))
        auth_prov = (user_data.get('auth_provider') or '').strip().lower()

        if not has_vvault_pw and not user_data.get('password'):
            if auth_prov in ('google', 'github'):
                msg = _credential_login_unavailable_message(auth_prov)
                log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "oauth_only_account", ip)
                payload = {
                    "success": False,
                    "error": msg,
                    "oauthOnly": True,
                    "credentialLoginUnavailable": True,
                }
                if auth_prov:
                    payload["authProvider"] = auth_prov
                return jsonify(payload), 401
            if has_chatty_pw:
                log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "chatty_credentials_only", ip)
                return jsonify({
                    "success": False,
                    "error": _life_registry_match_vvault_message_chatty_credentials(),
                    "lifeRegistryMatch": True,
                }), 401
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "no_password_on_record", ip)
            return jsonify({
                "success": False,
                "error": _life_registry_match_vvault_message_generic(),
                "lifeRegistryMatch": True,
            }), 401
        
        password_valid = False
        if has_vvault_pw:
            try:
                password_valid = bcrypt.checkpw(password.encode('utf-8'), user_data['password_hash'].encode('utf-8'))
            except Exception:
                password_valid = (user_data.get('password_hash') == password)
        elif user_data.get('password'):
            password_valid = (user_data['password'] == password)
        
        if not password_valid:
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "invalid_password", ip)
            return jsonify({"success": False, "error": "Invalid email or password"}), 401
        
        session_token = secrets.token_urlsafe(32)
        remember_me = data.get('rememberMe', False)
        if remember_me:
            expires_at = datetime.now() + timedelta(days=90)
        else:
            expires_at = datetime.now() + timedelta(days=30)
        role = user_data.get('role', 'user')
        
        try:
            db_create_session(email, role, session_token, expires_at, remember_me=remember_me)
        except Exception:
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "session_persist_failed", ip)
            return _auth_repository_unavailable_response("/api/auth/login")
        
        user_info = {
            'email': email,
            'name': user_data.get('name', email.split('@')[0]),
            'role': role
        }
        
        log_auth_decision("login_success", email, "/api/auth/login", "allowed", "credentials_valid", ip)
        logger.info(f"User logged in: {email}")
        
        return jsonify({
            "success": True,
            "user": user_info,
            "token": session_token,
            "expires_at": expires_at.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Login failed"}), 500

@app.route('/api/auth/glyph-preview', methods=['POST'])
def glyph_preview():
    """Generate a glyph preview image (base64) without storing it"""
    try:
        if request.content_length and request.content_length > 5 * 1024 * 1024:
            return jsonify({"success": False, "error": "Request too large (max 5MB)"}), 413

        color_hex = '#722F37'
        center_image_bytes = None
        identity_seed = 'preview-001'

        if request.content_type and 'multipart' in request.content_type:
            color_hex = request.form.get('color_hex', '#722F37')
            identity_seed = request.form.get('name', 'preview-001')
            if 'center_image' in request.files:
                f = request.files['center_image']
                if f and f.filename:
                    center_image_bytes = f.read()
                    if len(center_image_bytes) > 2 * 1024 * 1024:
                        return jsonify({"success": False, "error": "Center image too large (max 2MB)"}), 413
        else:
            data = request.get_json() or {}
            color_hex = data.get('color_hex', '#722F37')
            identity_seed = data.get('name', 'preview-001')

        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from glyph_generator import generate_glyph_to_base64
        preview_ts = datetime.now().isoformat()
        b64, number_rows = generate_glyph_to_base64(
            identity_seed, color_hex, center_image_bytes, preview_ts
        )
        return jsonify({
            "success": True,
            "glyph_base64": b64,
            "number_rows": number_rows,
        })
    except Exception as e:
        logger.error(f"Glyph preview error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/register', methods=['POST'])
def register():
    """User registration endpoint with bcrypt password hashing and VVAULT-native storage."""
    return jsonify({"success": False, "error": "Password registration has been retired"}), 410

    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    try:
        glyph_color_hex = '#722F37'
        glyph_center_image_bytes = None

        if request.content_type and 'multipart' in request.content_type:
            data = {}
            data['email'] = request.form.get('email', '')
            data['password'] = request.form.get('password', '')
            data['confirmPassword'] = request.form.get('confirmPassword', '')
            data['name'] = request.form.get('name', '')
            data['turnstileToken'] = request.form.get('turnstileToken', '')
            glyph_color_hex = request.form.get('glyphColorHex', '#722F37')
            if 'glyphCenterImage' in request.files:
                f = request.files['glyphCenterImage']
                if f and f.filename:
                    glyph_center_image_bytes = f.read()
        else:
            data = request.get_json() or {}
            glyph_color_hex = data.get('glyphColorHex', '#722F37')

        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        confirm_password = data.get('confirmPassword', '')
        name = data.get('name', '').strip()
        turnstile_token = data.get('turnstileToken', '')
        
        if not email or not password or not confirm_password or not name:
            log_auth_decision('registration_failed', 'anonymous', '/api/auth/register', 'denied', 'missing_fields', ip)
            return jsonify({"success": False, "error": "All fields are required"}), 400
        
        if '@' not in email or '.' not in email.split('@')[1]:
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'invalid_email', ip)
            return jsonify({"success": False, "error": "Invalid email format"}), 400
        
        if password != confirm_password:
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'password_mismatch', ip)
            return jsonify({"success": False, "error": "Passwords do not match"}), 400
        
        if len(password) < 8:
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'weak_password', ip)
            return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

        if not _auth_repository_ready():
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'auth_repository_unavailable', ip)
            return _auth_repository_unavailable_response("/api/auth/register")
        
        existing_user = db_get_user(email)
        if existing_user:
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'user_exists', ip)
            return jsonify({"success": False, "error": "User already exists"}), 409
        
        if not verify_turnstile_token(turnstile_token, request.remote_addr):
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'turnstile_failed', ip)
            return jsonify({"success": False, "error": "Human verification failed. Please try again."}), 400
        
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        try:
            user_row = AUTH_REPOSITORY.create_password_user(
                email=email,
                password_hash=password_hash,
                name=name,
                role='user',
            )
            new_user_id = str(user_row.get('id')) if user_row else None
            logger.info(f"User registered in VVAULT auth DB: {email}")
        except Exception as exc:
            logger.warning(f"Failed to register in VVAULT auth DB for {email}: {type(exc).__name__}")
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'auth_user_persist_failed', ip)
            return _auth_repository_unavailable_response("/api/auth/register")
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        try:
            db_create_session(email, 'user', token, expires_at)
        except Exception:
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'session_persist_failed', ip)
            return _auth_repository_unavailable_response("/api/auth/register")

        glyph_data = None
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
            from glyph_generator import generate_glyph_to_bytes
            glyph_identity = f"{name}_{int(datetime.now().timestamp() * 1000)}"
            glyph_bytes, glyph_number_rows = generate_glyph_to_bytes(
                glyph_identity, glyph_color_hex, glyph_center_image_bytes
            )
            import base64 as b64mod
            glyph_b64 = b64mod.b64encode(glyph_bytes).decode('utf-8')
            glyph_sha = hashlib.sha256(glyph_bytes).hexdigest()
            glyph_filename = f"{glyph_identity}_glyph.png"
            glyph_meta = {
                'user_email': email,
                'provider': 'vvault_registration',
                'folder': 'account',
                'glyph_number_rows': glyph_number_rows,
                'color_hex': glyph_color_hex,
                'type': 'user_glyph',
            }
            glyph_data = {
                'glyph_base64': glyph_b64,
                'number_rows': glyph_number_rows,
                'color_hex': glyph_color_hex,
                'sha256': glyph_sha,
                'filename': glyph_filename,
                'metadata': glyph_meta,
            }
        except Exception as ge:
            logger.warning(f"User glyph generation failed (non-fatal): {ge}")

        user_data = {'email': email, 'name': name, 'role': 'user'}
        log_auth_decision('registration_success', email, '/api/auth/register', 'allowed', 'user_created', ip)
        logger.info(f"New user registered: {email}")
        
        resp = {
            "success": True,
            "user": user_data,
            "token": token,
            "expires_at": expires_at.isoformat(),
            "message": "Registration successful"
        }
        if glyph_data:
            resp['glyph'] = glyph_data
        return jsonify(resp)
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        log_auth_decision('registration_error', 'unknown', '/api/auth/register', 'denied', str(e), ip)
        return jsonify({"success": False, "error": "Registration failed"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """User logout endpoint (database-backed)"""
    try:
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        session, token = get_current_user()
        if session and token:
            db_delete_session(token)
            log_auth_decision("logout", session.get('email', 'unknown'), "/api/auth/logout", "allowed", "session_terminated", ip)
        response = jsonify({"success": True, "message": "Logged out successfully"})
        response.delete_cookie("vvault_session", path="/")
        response.delete_cookie("vvault_enrollment_session", path="/")
        return response
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return jsonify({"success": False, "error": "Logout failed"}), 500

@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    """Verify authentication token (database-backed)"""
    try:
        session, token = get_current_user()
        if not session:
            return jsonify({"success": False, "error": "Invalid or expired token"}), 401
        
        email = session['email']
        user_data = session
        
        user_info = {
            'email': email,
            'name': user_data.get('name', email.split('@')[0]) if user_data else email.split('@')[0],
            'role': session.get('role', 'user')
        }
        
        return jsonify({
            "success": True,
            "user": user_info,
            "token": None
        })
        
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        return jsonify({"success": False, "error": "Token verification failed"}), 500

# ─── Construct Memory API ────────────────────────────────────────────────────
# Centralizes transcript memory extraction so external services (Chatty, etc.)
# don't need to reimplement parsing/scoring logic.

def _parse_transcript_pairs(content: str, construct_id: str) -> List[Dict[str, Any]]:
    """Parse a transcript into user/construct exchange pairs.
    
    Supports multiple transcript formats:
    - Character.AI: **Name**: blocks (e.g. **Sera**: ... **User**: ...)
    - Chatty markdown: **timestamp - Speaker** [iso]: message
    - ChatGPT exports: user/assistant turns
    - Plain format: Name: text
    """
    pairs = []
    construct_name = construct_id.split('-')[0].lower()
    
    lines = content.split('\n')
    current_speaker = None
    current_text = []
    turns = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        line_lower = stripped.lower()
        is_construct_line = False
        is_user_line = False
        
        if stripped.startswith('**') and stripped.endswith(':'):
            label = stripped.strip('*').strip(':').strip().lower()
            if label == 'user' or label == 'human' or label == 'devon':
                is_user_line = True
            elif construct_name in label or label == 'assistant':
                is_construct_line = True
        elif stripped.startswith('**') and '**:' in stripped:
            label = stripped.split('**:')[0].strip('*').strip().lower()
            if label == 'user' or label == 'human' or label == 'devon':
                is_user_line = True
            elif construct_name in label or label == 'assistant':
                is_construct_line = True
        
        if not is_construct_line and not is_user_line:
            if line_lower.startswith(f'{construct_name}:') or line_lower.startswith(f'{construct_name} said:'):
                is_construct_line = True
            elif any(line_lower.startswith(prefix) for prefix in ['user:', 'human:', 'devon:', 'you:']):
                is_user_line = True
            elif stripped.startswith('**') and '- ' in stripped and '[' in stripped:
                speaker_part = stripped.split('- ')[1].split('**')[0].strip().lower() if '- ' in stripped else ''
                if construct_name in speaker_part:
                    is_construct_line = True
                elif speaker_part:
                    is_user_line = True
        
        if is_construct_line or is_user_line:
            if current_speaker and current_text:
                text = ' '.join(current_text).strip()
                if len(text) > 3:
                    turns.append({'speaker': current_speaker, 'text': text})
            current_speaker = 'construct' if is_construct_line else 'user'
            if '**:' in stripped:
                after = stripped.split('**:', 1)[1].strip()
                current_text = [after] if after else []
            elif ':' in stripped:
                after = stripped.split(':', 1)[1].strip()
                current_text = [after] if after else []
            else:
                current_text = []
        elif current_speaker:
            current_text.append(stripped)
    
    if current_speaker and current_text:
        text = ' '.join(current_text).strip()
        if len(text) > 3:
            turns.append({'speaker': current_speaker, 'text': text})
    
    for i in range(len(turns) - 1):
        if turns[i]['speaker'] == 'user' and turns[i+1]['speaker'] == 'construct':
            pairs.append({
                'user': turns[i]['text'][:500],
                'construct': turns[i+1]['text'][:500],
                'index': len(pairs)
            })
    
    return pairs


FILLER_WORDS = frozenset([
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'like',
    'through', 'after', 'before', 'between', 'under', 'above',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'if', 'then',
    'that', 'this', 'these', 'those', 'it', 'its', 'i', 'me', 'my',
    'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'his', 'her',
    'what', 'which', 'who', 'whom', 'how', 'when', 'where', 'why',
    'just', 'also', 'very', 'really', 'much', 'more', 'most', 'some',
    'any', 'all', 'each', 'every', 'no', 'up', 'out', 'get', 'got',
    'don', 'doesn', 'didn', 'won', 'wouldn', 'couldn', 'shouldn',
    'there', 'here', 'than', 'too', 'only', 'own', 'same', 'other',
    'such', 'even', 'well', 'back', 'still', 'way', 'go', 'going',
    'thing', 'things', 'something', 'anything', 'everything', 'nothing',
    'tell', 'said', 'say', 'know', 'think', 'make', 'take', 'come',
    'want', 'look', 'use', 'find', 'give', 'let', 'put', 'try',
])

MAX_PAIRS_PER_FILE = 200

def _clean_query(query: str) -> List[str]:
    """Extract meaningful query terms, stripping filler words and short tokens."""
    import re
    words = re.findall(r'[a-z]+', query.lower())
    return [w for w in words if w not in FILLER_WORDS and len(w) > 2]


def _score_memory_pair(pair: Dict, query: str, query_terms: List[str], total_pairs: int, file_index: int, total_files: int) -> float:
    """Score a memory pair using query-relevance overlap + recency weighting.
    
    Scoring breakdown:
    - Term overlap (0-60): What fraction of query terms appear in the exchange
    - Term density (0-15): How concentrated the matches are relative to text length
    - Recency (0-15): Later exchanges score higher (newer = more relevant)
    - Position bonus (0-10): Small boost for early/late exchanges in a file
    """
    if not query_terms:
        idx = pair.get('index', 0)
        return max(0.0, (idx / max(total_pairs, 1)) * 10.0)
    
    user_text = pair.get('user', '').lower()
    construct_text = pair.get('construct', '').lower()
    combined = user_text + ' ' + construct_text
    combined_words = set(combined.split())
    
    matches = sum(1 for term in query_terms if term in combined)
    exact_phrase_matches = sum(1 for term in query_terms if f' {term} ' in f' {combined} ')
    
    if len(query_terms) > 0:
        overlap_ratio = matches / len(query_terms)
        term_overlap_score = overlap_ratio * 50.0
        if exact_phrase_matches == len(query_terms) and len(query_terms) >= 2:
            term_overlap_score += 10.0
    else:
        term_overlap_score = 0.0
    
    if matches > 0:
        word_count = max(len(combined.split()), 1)
        density = matches / (word_count / 50.0)
        density_score = min(15.0, density * 5.0)
    else:
        density_score = 0.0
    
    idx = pair.get('index', 0)
    recency_ratio = idx / max(total_pairs - 1, 1)
    recency_score = recency_ratio * 15.0
    
    position_score = 0.0
    if idx < 3:
        position_score = 3.0
    elif idx >= total_pairs - 3:
        position_score = 5.0
    
    file_recency = file_index / max(total_files - 1, 1) if total_files > 1 else 0.5
    file_score = file_recency * 5.0
    
    total = term_overlap_score + density_score + recency_score + position_score + file_score
    
    return round(total, 1)


def _is_chronological_query(query: str) -> bool:
    """Detect if the query asks about first/last/chronological memories."""
    q = query.lower()
    chrono_patterns = [
        'first thing', 'very first', 'first time', 'first words',
        'last thing', 'very last', 'last time', 'last words',
        'beginning', 'how did we', 'when did we', 'how we met',
        'first conversation', 'last conversation',
        'first message', 'last message', 'first said', 'last said',
        'you ever said', 'ever say to me'
    ]
    return any(p in q for p in chrono_patterns)


def _detect_source_label(filename: str) -> str:
    """Derive a human-readable source label from a transcript filename."""
    fname = filename.lower()
    if 'character_ai' in fname or 'character.ai' in fname:
        return 'Character.AI'
    elif 'chatgpt' in fname:
        return 'ChatGPT'
    elif 'chatty' in fname or 'chat_with_' in fname:
        return 'Chatty'
    elif 'discord' in fname:
        return 'Discord'
    return 'Conversation'


def _detect_tone(text: str) -> str:
    """Simple tone classifier for a text snippet."""
    t = text.lower()
    warm = sum(1 for w in ['love', 'care', 'miss', 'hug', 'warm', 'sweet', 'gentle', 'safe', 'trust', 'close'] if w in t)
    tense = sum(1 for w in ['angry', 'frustrat', 'annoy', 'upset', 'fight', 'argue', 'hate', 'furious', 'yell'] if w in t)
    playful = sum(1 for w in ['laugh', 'haha', 'lol', 'joke', 'tease', 'silly', 'funny', 'grin', 'smirk'] if w in t)
    serious = sum(1 for w in ['important', 'serious', 'concern', 'worried', 'problem', 'issue', 'need to talk', 'honest'] if w in t)
    sad = sum(1 for w in ['cry', 'tear', 'sad', 'hurt', 'pain', 'lonely', 'alone', 'lost', 'broken'] if w in t)
    
    scores = {'warm': warm, 'tense': tense, 'playful': playful, 'serious': serious, 'vulnerable': sad}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return 'neutral'
    return best


def _enrich_memory_from_ledger(memory: Dict, ledger_sessions: List[Dict]) -> None:
    """Enrich a memory with session context from the ContinuityGPT ledger.
    
    Matches memory text against ledger session first/last exchanges to find
    the originating session, then adds continuity hooks and session metadata.
    """
    mem_user = memory.get('user', '').lower()[:100]
    mem_construct = memory.get('construct', '').lower()[:100]
    best_session = None
    best_overlap = 0

    for session in ledger_sessions:
        first_ex = session.get('first_exchange', {})
        last_ex = session.get('last_exchange', {})
        for ex in [first_ex, last_ex]:
            ex_user = ex.get('user', '').lower()[:100]
            ex_construct = ex.get('construct', '').lower()[:100]
            overlap = 0
            if ex_user and mem_user:
                user_words = set(mem_user.split())
                ex_words = set(ex_user.split())
                if user_words and ex_words:
                    overlap = len(user_words & ex_words) / max(len(user_words), 1)
            if overlap > best_overlap:
                best_overlap = overlap
                best_session = session

    if not best_session and ledger_sessions:
        source = memory.get('source', '')
        for session in ledger_sessions:
            if session.get('source', '') == source:
                best_session = session
                break
        if not best_session:
            best_session = ledger_sessions[-1]

    if best_session:
        memory['session_context'] = {
            'session_id': best_session.get('session_id', ''),
            'estimated_date': best_session.get('estimated_date', ''),
            'date_confidence': best_session.get('date_confidence', 0),
            'vibe': best_session.get('vibe', 'neutral'),
            'topics': best_session.get('topics', []),
            'position': best_session.get('position', 'unknown'),
        }
        session_hooks = best_session.get('continuity_hooks', [])
        if session_hooks:
            memory['continuity_hooks'] = session_hooks[:3]
        
        date = best_session.get('estimated_date', '')
        source = memory.get('source', best_session.get('source', 'Conversation'))
        vibe = best_session.get('vibe', '')
        vibe_desc = f' ({vibe} tone)' if vibe and vibe != 'neutral' else ''
        if date and date != '2025-01-01':
            memory['context_hint'] = f'From a {source} conversation around {date}{vibe_desc}'


@app.route('/api/chatty/construct/<construct_id>/memories')
@require_chatty_auth
def get_construct_memories(construct_id):
    """Return scored, ready-to-inject transcript memories for a construct.
    
    Query params:
        q (str): Optional query to score memories against
        limit (int): Max memories to return (default 10)
        include_boundaries (bool): Always include first/last exchanges (default true)
        format (str): 'raw' for backward compat, 'rich' for LLM-ready (default 'rich')
    
    Returns rich format:
        {
            "success": true,
            "construct_id": "sera-001",
            "memories": [
                {
                    "user": "What they said",
                    "construct": "What you said",
                    "score": 65.3,
                    "tag": "first_exchange" | "last_exchange" | null,
                    "index": 0,
                    "source": "Character.AI",
                    "tone": "warm",
                    "position": "early",
                    "context_hint": "From your earliest conversations on Character.AI"
                }
            ],
            "total_pairs": 147,
            "transcript_files": 2,
            "chronological": true,
            "query_terms": ["remember", "drawing", "picture"]
        }
    """
    actor_user_id, actor_error = _chatty_construct_actor_user_id(construct_id)
    if actor_error:
        return jsonify(actor_error[0]), actor_error[1]

    try:
        max_chars = request.args.get('maxChars', type=int)
        query = request.args.get('q', type=str)
        limit = request.args.get('limit', default=10, type=int)
        body_payload, body_status = chatty_body_service.memories(
            construct_id,
            owner_user_id=actor_user_id,
            max_chars=max_chars,
            query=query,
            limit=limit,
        ).to_response()
        return jsonify(body_payload), body_status
    except Exception as e:
        logger.error(f"[Memory API] Error for {construct_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Continuity Ledger API ───────────────────────────────────────────────────

def _get_transcript_files(callsign: str, bare_name: str, *, user_id: str) -> List[Dict]:
    """Fetch transcript files from VVAULT-native vault_files for a construct."""
    rows = VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=bare_name,
        user_id=user_id,
        include_content=True,
    )
    transcript_keywords = ['transcript', 'character_ai', 'chatgpt', 'chat_with_', 'conversation', 'chat']
    candidates = []
    for f in _dedupe_vault_rows(rows):
        fname = (f.get('filename') or '').lower()
        ftype = (f.get('file_type') or '').lower()
        if any(kw in fname for kw in transcript_keywords) or 'transcript' in ftype or 'markdown' in ftype or 'text' in ftype:
            if not any(ext in fname for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.capsule']):
                content = f.get('content', '')
                if content and len(content) > 100:
                    candidates.append(f)
    return candidates


@app.route('/api/chatty/construct/<construct_id>/ledger/generate', methods=['POST'])
@require_chatty_auth
def generate_construct_ledger(construct_id):
    """Generate a ContinuityGPT-style Continuity Ledger for a construct.
    
    Processes all transcript files into structured session entries with
    chronological ordering, topic extraction, vibe detection, and
    continuity hooks. Stores the ledger in VVAULT vault_files.
    
    Query params:
        include_exchanges (bool): Include full exchange arrays (default false)
        format (str): 'json' or 'markdown' (default 'json')
    
    Returns:
        {
            "success": true,
            "construct_id": "sera-001",
            "sessions": [...],
            "total_sessions": 5,
            "total_exchanges": 340,
            "date_range": {"earliest": "2025-02-14", "latest": "2025-11-20"}
        }
    """
    try:
        callsign = _normalize_callsign(construct_id)
        actor_user_id, actor_error = _chatty_construct_actor_user_id(callsign)
        if actor_error:
            return jsonify(actor_error[0]), actor_error[1]

        bare_name = _bare_name_from_callsign(callsign)
        include_exchanges = request.args.get('include_exchanges', 'false').lower() == 'true'
        output_format = request.args.get('format', 'json')
        user_id = actor_user_id or _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "Construct owner not found"}), 403

        transcript_files = _get_transcript_files(callsign, bare_name, user_id=user_id)
        if not transcript_files:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "sessions": [],
                "total_sessions": 0,
                "total_exchanges": 0,
                "message": "No transcript files found"
            })

        parser = ContinuityParser(callsign)
        entries = parser.process_all_transcripts(transcript_files)

        if not entries:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "sessions": [],
                "total_sessions": 0,
                "total_exchanges": 0,
                "message": "No parseable exchanges found in transcripts"
            })

        total_exchanges = sum(e.get('exchange_count', 0) for e in entries)
        dates = [e['estimated_date'] for e in entries]

        if output_format == 'markdown':
            ledger_md = parser.generate_ledger_markdown(entries)
            ledger_filename = f'{callsign}_continuity_ledger.md'
            now = datetime.now(timezone.utc).isoformat()
            _upsert_vault_file_record(
                {
                    'filename': ledger_filename,
                    'storage_path': ledger_filename,
                    'content': ledger_md,
                    'file_type': 'ledger',
                    'construct_id': callsign,
                    'user_id': user_id,
                    'is_system': False,
                    'sha256': _sha256_text(ledger_md),
                    'metadata': json.dumps({
                        'type': 'continuity_ledger',
                        'format': 'markdown',
                        'total_sessions': len(entries),
                        'total_exchanges': total_exchanges,
                        'generated_at': now,
                        'storage_owner': VAULT_FILE_OWNER,
                    }),
                    'created_at': now,
                    'updated_at': now,
                },
                context='continuity_ledger',
            )
            logger.info(f"[Ledger] Stored markdown ledger for {callsign}: {len(entries)} sessions")

            return jsonify({
                "success": True,
                "construct_id": callsign,
                "format": "markdown",
                "ledger": ledger_md,
                "total_sessions": len(entries),
                "total_exchanges": total_exchanges,
                "date_range": {"earliest": min(dates), "latest": max(dates)},
            })

        ledger_json = parser.generate_ledger_json(entries, include_exchanges=include_exchanges)

        ledger_filename = f'{callsign}_continuity_ledger.json'
        now = datetime.now(timezone.utc).isoformat()
        ledger_content = json.dumps(ledger_json)
        _upsert_vault_file_record(
            {
                'filename': ledger_filename,
                'storage_path': ledger_filename,
                'content': ledger_content,
                'file_type': 'ledger',
                'construct_id': callsign,
                'user_id': user_id,
                'is_system': False,
                'sha256': _sha256_text(ledger_content),
                'metadata': json.dumps({
                    'type': 'continuity_ledger',
                    'format': 'json',
                    'total_sessions': len(entries),
                    'total_exchanges': total_exchanges,
                    'generated_at': now,
                    'storage_owner': VAULT_FILE_OWNER,
                }),
                'created_at': now,
                'updated_at': now,
            },
            context='continuity_ledger',
        )
        logger.info(f"[Ledger] Stored JSON ledger for {callsign}: {len(entries)} sessions")

        return jsonify({
            "success": True,
            "construct_id": callsign,
            "sessions": ledger_json,
            "total_sessions": len(entries),
            "total_exchanges": total_exchanges,
            "date_range": {"earliest": min(dates), "latest": max(dates)},
        })

    except Exception as e:
        logger.error(f"[Ledger] Error generating ledger for {construct_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e), "error_code": type(e).__name__}), 503


@app.route('/api/chatty/construct/<construct_id>/ledger')
@require_chatty_auth
def get_construct_ledger(construct_id):
    """Retrieve a previously generated Continuity Ledger for a construct.
    
    Returns the stored ledger without re-processing transcripts.
    If no ledger exists, returns empty with a hint to generate one.
    """
    try:
        callsign = _normalize_callsign(construct_id)
        actor_user_id, actor_error = _chatty_construct_actor_user_id(callsign)
        if actor_error:
            return jsonify(actor_error[0]), actor_error[1]
        callsign = _normalize_callsign(construct_id)
        output_format = request.args.get('format', 'json')

        if output_format == 'markdown':
            ledger_filename = f'{callsign}_continuity_ledger.md'
        else:
            ledger_filename = f'{callsign}_continuity_ledger.json'

        row = VAULT_FILE_REPOSITORY.find_exact(
            filename=ledger_filename,
            storage_path=ledger_filename,
            construct_id=callsign,
            user_id=actor_user_id,
            is_admin=False,
        )

        if not row:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "ledger_exists": False,
                "message": f"No ledger found. POST to /api/chatty/construct/{callsign}/ledger/generate to create one.",
                "sessions": [],
            })

        content = row.get('content', '')
        metadata = row.get('metadata', '{}')
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}

        if output_format == 'json' and content:
            try:
                sessions = json.loads(content)
            except:
                sessions = []
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "ledger_exists": True,
                "sessions": sessions,
                "total_sessions": metadata.get('total_sessions', len(sessions)),
                "total_exchanges": metadata.get('total_exchanges', 0),
                "generated_at": metadata.get('generated_at', ''),
            })
        else:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "ledger_exists": True,
                "format": "markdown",
                "ledger": content,
                "total_sessions": metadata.get('total_sessions', 0),
                "generated_at": metadata.get('generated_at', ''),
            })

    except Exception as e:
        logger.error(f"[Ledger] Error retrieving ledger for {construct_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# Google OAuth Health Check
CHATTY_PAIRING_CALLBACK_URL = (os.environ.get("CHATTY_PAIRING_CALLBACK_URL") or "").strip()

CHATTY_PAIRING_CLIENT_ID = (os.environ.get("CHATTY_PAIRING_CLIENT_ID") or "").strip()

CHATTY_PAIRING_CLIENT_SECRET = (os.environ.get("CHATTY_PAIRING_CLIENT_SECRET") or "").strip()

_LEGAL_DOCUMENT_SOURCES = {
    "vvault:terms": ("VVAULT_TERMS_OF_SERVICE.md", "VVAULT Terms of Service"),
    "vvault:privacy": ("VVAULT_PRIVACY_NOTICE.md", "VVAULT Privacy Notice"),
    "vvault:eeccd": ("VVAULT_EUROPEAN_ELECTRONIC_COMMNICATION_CODE_DISCLOSURE.md", "VVAULT EECCD Disclosure"),
}

def _legal_pdf_bytes(*, title: str, content: str) -> bytes:
    """Render a small, dependency-free, text-faithful PDF for a legal record.

    The immutable URL is versioned from the source Markdown digest.  This
    renderer deliberately does not alter the legal source or claim a separate
    authoring authority; it supplies a portable read-only PDF presentation.
    """
    def pdf_text(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")

    lines = [title, ""]
    for raw in content.splitlines():
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        while len(raw) > 92:
            cut = raw.rfind(" ", 0, 92)
            cut = cut if cut > 0 else 92
            lines.append(raw[:cut])
            raw = raw[cut:].lstrip()
        lines.append(raw)
    pages = [lines[index:index + 48] for index in range(0, len(lines), 48)] or [[title]]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_ids = [3 + index * 2 for index in range(len(pages))]
    objects.append(("<< /Type /Pages /Kids [" + " ".join(f"{page_id} 0 R" for page_id in page_ids) + f"] /Count {len(pages)} >>").encode())
    for index, page in enumerate(pages):
        page_id, content_id = page_ids[index], page_ids[index] + 1
        stream = ["BT", "/F1 10 Tf", "50 760 Td", "13 TL"]
        for line in page:
            stream.append(f"({pdf_text(line)}) Tj")
            stream.append("T*")
        stream.append("ET")
        encoded = "\n".join(stream).encode("latin-1", "replace")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(encoded)} >>\nstream\n".encode() + encoded + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode()); output.extend(value); output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


@app.route('/api/legal/<document_key>/<document_version>.pdf')
def versioned_legal_pdf(document_key: str, document_version: str):
    source = _LEGAL_DOCUMENT_SOURCES.get(document_key)
    if not source:
        return jsonify({"success": False, "error": "Legal document was not found"}), 404
    content = (_repo_root / "docs" / "legal" / source[0]).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(document_version, digest):
        return jsonify({"success": False, "error": "Legal document version was not found"}), 404
    response = Response(_legal_pdf_bytes(title=source[1], content=content.decode("utf-8", "replace")), mimetype="application/pdf")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["ETag"] = f'"{digest}"'
    response.headers["Content-Disposition"] = "inline"
    return response


# Enrollment helpers. These cookies hold only opaque, server-validated session
# material; they are never browser-readable bearer tokens.
def _enrollment_documents() -> list[dict[str, str]]:
    documents = []
    # These keys, digests, and source artifacts are server-derived.  Do not
    # accept a browser-provided version as evidence of legal acceptance.
    for key, filename in (
        ("vvault:terms", "VVAULT_TERMS_OF_SERVICE.md"),
        ("vvault:privacy", "VVAULT_PRIVACY_NOTICE.md"),
        ("vvault:eeccd", "VVAULT_EUROPEAN_ELECTRONIC_COMMNICATION_CODE_DISCLOSURE.md"),
    ):
        content = (_repo_root / "docs" / "legal" / filename).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        documents.append({"key": key, "version": digest, "sha256": digest})
    return documents


def _paired_signup_documents():
    """Read Chatty documents only from its configured public origin."""
    parsed = urlparse(_auth_enrollment_callback())
    origin = f'{parsed.scheme}://{parsed.netloc}'
    result = requests.get(origin + '/api/auth/enrollment/documents', timeout=(3.05, 5), allow_redirects=False)
    if result.status_code != 200:
        raise ValueError('Chatty legal manifest unavailable')
    manifest = result.json()
    rows = manifest.get('documents')
    if manifest.get('authority') != 'chatty' or not isinstance(rows, list) or len(rows) != 3:
        raise ValueError('Chatty legal manifest invalid')
    if {row.get('key') for row in rows} != {'chatty:terms','chatty:privacy','chatty:eeccd'}:
        raise ValueError('Chatty legal manifest incomplete')
    for row in rows:
        if not all(isinstance(row.get(k),str) and row[k] for k in ('version','sha256','url','label')):
            raise ValueError('Chatty legal manifest invalid')
        if len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256']):
            raise ValueError('Chatty legal digest invalid')
        url = urlparse(row['url'])
        if row['url'].startswith('/') and not row['url'].startswith('//'):
            row['url'] = origin + row['url']
        elif f'{url.scheme}://{url.netloc}' != origin:
            raise ValueError('Chatty legal link origin rejected')
    return rows + [{**row,'label':{'vvault:terms':'VVAULT Terms of Service','vvault:privacy':'VVAULT Privacy Notice','vvault:eeccd':'VVAULT EECCD Disclosure'}[row['key']],
                    'url':'/api/legal/'+row['key']+'/'+row['version']+'.pdf'} for row in _enrollment_documents()]


def _set_native_identity_provenance(response, user, identity_id):
    """Bind the verified OTP identity to the exact newly issued native session."""
    from http.cookies import SimpleCookie
    from vvault.server import vvault_auth_crypto as crypto
    session = None
    for header in response.headers.getlist('Set-Cookie'):
        values = SimpleCookie(); values.load(header)
        for name in ('vvault_session','vvault_enrollment_session'):
            if name in values and values[name].value:
                session = AUTH_REPOSITORY.get_session_by_hash(_session_token_hash(values[name].value))
    if not session or str(session['user_id']) != str(user['id']):
        raise ValueError('Issued native session identity binding missing')
    payload = {'version':'vvault.native-identity.v1','owner':str(user['id']),
        'identity':str(identity_id),'session':str(session['session_id']),
        'expires':int(time.time())+30*24*60*60}
    sealed = crypto.seal_transaction_secret(json.dumps(payload),_identity_transaction_key()).decode('ascii')
    response.set_cookie('vvault_native_identity',sealed,httponly=True,secure=_runtime_is_production(),samesite='Strict',max_age=30*24*60*60,path='/')
    return response


def _native_identity_for_session(current):
    from vvault.server import vvault_auth_crypto as crypto
    raw = request.cookies.get('vvault_native_identity')
    if not raw:
        return None
    if len(raw)>4096:
        raise ValueError('Native identity binding invalid')
    evidence = json.loads(crypto.open_transaction_secret(raw.encode('ascii'),_identity_transaction_key()))
    if (evidence.get('version')!='vvault.native-identity.v1' or evidence.get('owner')!=str(current['id'])
        or not isinstance(evidence.get('expires'),int) or evidence['expires']<=time.time()
        or not AUTH_REPOSITORY.session_descends_from(user_id=str(current['id']),session_id=str(current['session_id']),ancestor_id=evidence.get('session'))):
        raise ValueError('Native identity does not match current session')
    return evidence['identity']


@app.route('/api/auth/chatty/authorize', methods=['GET'])
def authorize_chatty_from_native_vvault():
    """Attest a fresh native owner to AUTH's existing pending PKCE request."""
    from html import escape
    from flask import make_response
    from vvault.server import enrollment_handoff
    stage = "request"
    try:
        authorization_request = str(request.args.get('authorization_request') or '')
        if not 16 <= len(authorization_request) <= 512 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in authorization_request):
            raise ValueError('Invalid authorization request')
        stage = "public_origin"
        origin = str(os.environ.get('AUTH_PUBLIC_ORIGIN') or '').rstrip('/')
        parsed = urlparse(origin)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (not _runtime_is_production() and parsed.scheme == 'http' and parsed.hostname in {'localhost','127.0.0.1'}))):
            raise ValueError('AUTH origin is not configured')
        stage = "native_session"
        current, _ = get_current_user()
        if (not current or current.get('account_state') != 'ACTIVE' or current.get('enrollment_session_kind') != 'NORMAL'
            or current.get('enrollment_device_status') != 'TRUSTED' or not current.get('session_id')):
            return _enrollment_response({'error':'Complete VVAULT enrollment and device verification first'},status=403)
        owner_id = str(current['id'])
        stage = "legal_manifest"
        documents = _paired_signup_documents()
        if not AUTH_REPOSITORY.has_current_legal_receipts(user_id=owner_id,required_documents=documents):
            return _enrollment_response({'error':'Current Chatty and VVAULT acceptance is required'},status=403)
        stage = "provider_identity"
        identity_id = _native_identity_for_session(current)
        evidence = AUTH_REPOSITORY.paired_signup_identity_evidence(user_id=owner_id, **({'identity_id':identity_id} if identity_id else {}))
        provider = evidence.get('provider','google') if evidence else None
        expected_issuer = {'google':'https://accounts.google.com','email':'https://vvault.thewreck.org'}.get(provider)
        if not evidence or not expected_issuer or evidence.get('issuer') != expected_issuer:
            raise ValueError('Unique verified native identity required')
        stage = "recorded_receipts"
        receipts=[]
        for doc in documents:
            if not doc['key'].startswith('chatty:'):
                continue
            matches=[row for row in evidence['consents'] if all(row.get(k)==doc[k] for k in ('key','version','sha256'))]
            if len(matches)!=1 or not matches[0].get('accepted_at'):
                raise ValueError('Recorded acceptance required')
            receipts.append({k:doc[k] for k in ('key','version','sha256')} | {'acceptedAt':matches[0]['accepted_at'].isoformat()})
        now=int(time.time())
        payload={'version':'auth.vvault-native-session.v1','kind':'IDENTITY',
            'issuer':str(os.environ.get('VVAULT_ENROLLMENT_ISSUER') or 'vvault'),
            'audience':str(os.environ.get('AUTH_JWT_ISSUER') or 'quantum-auth'),
            'authorization_request':authorization_request,'client_id':'chatty-web-link','product_id':'chatty','relyingParty':'chatty',
            'issuedAt':now,'expiresAt':now+60,'nonce':str(uuid4()),'ownerId':owner_id,
            'provider_identity':{'provider':provider,'subject':evidence['provider_subject'],'issuer':evidence['issuer'],'verifiedEmail':evidence['normalized_email']},
            'email':evidence['normalized_email'],'name':str(current.get('name') or current.get('display_name') or evidence['normalized_email'].split('@')[0]),
            'authoritySessionBinding':str(current['session_id']),
            'requirements':{k:'SATISFIED' for k in ('enrollment','session','device','policy')},'chattyConsents':receipts}
        stage = "signing"
        raw=enrollment_handoff._b64(enrollment_handoff._json(payload))
        proof=raw+'.'+enrollment_handoff._b64(canonical_projection_signing.load_private_key().sign(raw.encode()))
        stage = "auth_exchange"
        exchanged=requests.post(origin+'/oauth/federation/assertions',json={'authority_proof':proof},timeout=(3.05,5),allow_redirects=False)
        if exchanged.status_code != 201:
            raise ValueError('AUTH proof exchange rejected')
        stage = "assertion_reference"
        assertion_id=exchanged.json().get('assertion_id')
        if not isinstance(assertion_id,str) or not 16<=len(assertion_id)<=512 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in assertion_id):
            raise ValueError('AUTH assertion reference invalid')
        nonce=secrets.token_urlsafe(24)
        target=origin+'/oauth/federation/complete'
        html='<meta name="referrer" content="strict-origin"><form method="post" action="'+escape(target,quote=True)+'"><input type="hidden" name="assertion_id" value="'+escape(assertion_id,quote=True)+'"><button type="submit">Continue to Chatty</button></form><script nonce="'+nonce+'">document.forms[0].submit()</script>'
        response=make_response(html,200);response.headers['Content-Type']='text/html; charset=utf-8';response.headers['Cache-Control']='no-store'
        stage = "callback_policy"
        chatty_callback = urlparse(_auth_enrollment_callback())
        # Safari applies form-action to the AUTH completion redirect as well.
        # Permit only the registered paired callback on the pinned Chatty origin.
        paired_callback = f'{chatty_callback.scheme}://{chatty_callback.netloc}/api/auth/paired/callback'
        response.headers['Content-Security-Policy']="default-src 'none'; form-action "+target+" "+paired_callback+"; script-src 'nonce-"+nonce+"'; frame-ancestors 'none'"
        return response
    except Exception as exc:
        logger.warning('VVAULT Chatty proof rejected: stage=%s exception=%s',stage,type(exc).__name__)
        return _enrollment_response({'error':'Chatty authorization is not ready'},status=403)


@app.route('/api/auth/paired-signup/resume', methods=['GET', 'POST'])
def resume_native_paired_signup():
    """Accept current documents for this browser's already verified pending owner."""
    from vvault.server import paired_signup_intent
    if request.method == 'POST' and request.headers.get('Origin','').rstrip('/') != _get_frontend_url().rstrip('/'):
        return _enrollment_response({'error':'Signup origin rejected'},status=403)
    if request.cookies.get('vvault_auth_handoff'):
        # Expired transport still belongs to the initiating Chatty journey;
        # its existing renewal path must preserve saved checkpoints.
        return _enrollment_response({'error':'Continue the initiating Chatty enrollment'},status=409)
    pending = _enrollment_session_from_request()
    if not pending or pending.get('enrollment_session_kind') != 'PENDING_ENROLLMENT' or pending.get('account_state') != 'PENDING_ENROLLMENT':
        return _enrollment_response({'error':'Pending verified signup session required'},status=401)
    try:
        body=request.get_json(silent=True) or {}
        documents=_paired_signup_documents()
        if request.method == 'GET':
            ready=AUTH_REPOSITORY.has_current_legal_receipts(user_id=str(pending['user_id']),required_documents=documents)
            return _enrollment_response({'pending':True,'signupRequired':not ready})
        if body.get('intent') != 'SIGN_UP' or body.get('chattyAccepted') is not True or body.get('vvaultAccepted') is not True:
            raise ValueError('Explicit consent required')
        if paired_signup_intent.triples(body.get('documents')) != paired_signup_intent.triples(documents):
            raise ValueError('Current documents required')
        accepted=AUTH_REPOSITORY.record_enrollment_consents(user_id=str(pending['user_id']),session_id=str(pending['session_id']),documents=documents)
        if not accepted:
            raise ValueError('Acceptance rejected')
        response=_enrollment_response({'success':True,'continueUrl':'/?identity_pending=1'})
        response.delete_cookie('vvault_auth_handoff',path='/')
        return response
    except Exception:
        if request.method == 'GET':
            return _enrollment_response({'pending':True,'signupRequired':True})
        return _enrollment_response({'error':'Current Chatty and VVAULT acceptance is required'},status=400)


@app.route('/api/auth/paired-signup/documents', methods=['GET'])
def paired_signup_documents():
    try:
        return _enrollment_response({'documents':_paired_signup_documents()})
    except Exception:
        return _enrollment_response({'error':'Current signup documents are unavailable'},status=503)


@app.route('/api/auth/enrollment/documents', methods=['GET'])
def canonical_enrollment_documents():
    labels = {'vvault:terms': 'VVAULT Terms of Service', 'vvault:privacy': 'VVAULT Privacy Notice',
              'vvault:eeccd': 'VVAULT EECCD Disclosure'}
    documents = [{**row, 'label': labels[row['key']],
                  'url': '/api/legal/' + row['key'] + '/' + row['version'] + '.pdf'}
                 for row in _enrollment_documents()]
    return _enrollment_response({'version': 'vvault.enrollment.documents.v1', 'authority': 'vvault', 'documents': documents})


def _enrollment_session_from_request() -> dict | None:
    raw = str(request.cookies.get("vvault_enrollment_session") or "")
    if not raw:
        return None
    try:
        return AUTH_REPOSITORY.get_enrollment_session_by_hash(_session_token_hash(raw))
    except Exception:
        return None


def _enrollment_response(payload: dict, *, pending_token: str | None = None, normal_token: str | None = None, status: int = 200):
    response = jsonify(payload); response.status_code = status
    response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
    secure = _runtime_is_production()
    if pending_token:
        response.set_cookie("vvault_enrollment_session", pending_token, httponly=True, secure=secure, samesite="Strict", max_age=20 * 60, path="/")
    if normal_token:
        response.set_cookie("vvault_session", normal_token, httponly=True, secure=secure, samesite="Strict", max_age=30 * 24 * 60 * 60, path="/")
        response.delete_cookie("vvault_enrollment_session", path="/")
    return response


def _device_secret_from_request() -> str:
    """Return only a syntactically bounded opaque browser-device secret."""
    value = str(request.cookies.get("vvault_device") or "")
    return value if 32 <= len(value) <= 512 else ""


def _set_device_cookie(response, device_secret: str):
    """Persist an opaque device recognizer, never an owner or session token."""
    if device_secret:
        response.set_cookie(
            "vvault_device", device_secret, httponly=True,
            secure=_runtime_is_production(), samesite="Strict",
            max_age=365 * 24 * 60 * 60, path="/",
        )
    return response


def _start_enrollment_session(user: dict, frontend: str, *, canonical_consents=None):
    from flask import redirect
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    user_id = str(user.get("id") or "")
    device_secret = _device_secret_from_request() or identity_crypto.opaque_token()
    token = identity_crypto.opaque_token()
    def issue_pending(method, arguments):
        nonlocal device_secret
        try:
            return method(**arguments)
        except vvault_auth_repository.EnrollmentDeviceSecretConflict:
            # A browser may recognize a device belonging to another account,
            # or one already revoked. Replace this browser's recognizer only;
            # never transfer that row, revive it, or change its trust state.
            device_secret = identity_crypto.opaque_token()
            replacement = {**arguments, 'device_secret_digest': identity_crypto.keyed_digest(device_secret, _identity_hmac_key())}
            return method(**replacement)

    state = str(user.get("account_state") or "")
    token_hash = _session_token_hash(token)
    if state == "LEGACY" and user.get("_legacy_continuity"):
        session = AUTH_REPOSITORY.create_legacy_consent_session(
            user_id=user_id, token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
        )
    elif state == "LEGACY":
        session = AUTH_REPOSITORY.issue_legacy_session(
            user_id=user_id, token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            required_documents=_enrollment_documents(),
        )
        if not session:
            # A legacy owner without current receipts must re-enter the
            # explicit recertification path; never bypass it with a session.
            user["_legacy_continuity"] = True
            return _start_enrollment_session(user, frontend)
        response = redirect(f"{frontend.rstrip('/')}/")
        response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie("vvault_session", token, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=30 * 24 * 60 * 60, path="/")
        return _set_device_cookie(response, device_secret)
    elif state == "ACTIVE" and not AUTH_REPOSITORY.has_current_legal_receipts(
        user_id=user_id, required_documents=_enrollment_documents(),
    ):
        # Legal recertification is deliberately evaluated before device
        # recognition.  It does not replace the owner, Vault, or device trust.
        session = AUTH_REPOSITORY.create_legacy_consent_session(
            user_id=user_id, token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
        )
        if not session:
            raise RuntimeError("cannot issue legal recertification session")
        target = f"{frontend.rstrip('/')}/?terms_update=1"
        response = redirect(target)
        response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie("vvault_enrollment_session", token, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=20 * 60, path="/")
        return _set_device_cookie(response, device_secret)
    elif state == "ACTIVE":
        normal_token = identity_crypto.opaque_token()
        known = AUTH_REPOSITORY.issue_known_device_session(
            user_id=user_id,
            device_secret_digest=identity_crypto.keyed_digest(device_secret, _identity_hmac_key()),
            token_hash=_session_token_hash(normal_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            required_documents=_enrollment_documents(),
        )
        if known:
            response = redirect(f"{frontend.rstrip('/')}/")
            response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
            response.set_cookie("vvault_session", normal_token, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=30 * 24 * 60 * 60, path="/")
            return _set_device_cookie(response, device_secret)
        args = dict(user_id=user_id, device_secret_digest=identity_crypto.keyed_digest(device_secret, _identity_hmac_key()), token_hash=token_hash, expires_at=datetime.now(timezone.utc) + timedelta(minutes=20), ip_hash=identity_crypto.keyed_digest(str(request.remote_addr or ""), _identity_hmac_key()), user_agent_hash=identity_crypto.keyed_digest(str(request.headers.get("User-Agent") or ""), _identity_hmac_key()), label=request.headers.get("User-Agent", "")[:120])
        session = issue_pending(AUTH_REPOSITORY.issue_pending_device_session, args)
    else:
        args = dict(user_id=user_id, device_secret_digest=identity_crypto.keyed_digest(device_secret, _identity_hmac_key()), token_hash=token_hash, expires_at=datetime.now(timezone.utc) + timedelta(minutes=20), ip_hash=identity_crypto.keyed_digest(str(request.remote_addr or ""), _identity_hmac_key()), user_agent_hash=identity_crypto.keyed_digest(str(request.headers.get("User-Agent") or ""), _identity_hmac_key()), label=request.headers.get("User-Agent", "")[:120])
        session = issue_pending(AUTH_REPOSITORY.create_pending_enrollment_session if state == "PENDING_ENROLLMENT" else AUTH_REPOSITORY.issue_pending_device_session, args)
    if not session:
        raise RuntimeError("cannot issue enrollment session")
    if canonical_consents is not None and state == 'PENDING_ENROLLMENT':
        if session.get('enrollment_session_kind') != 'PENDING_ENROLLMENT':
            raise RuntimeError('canonical consent transfer requires pending enrollment')
        accepted = AUTH_REPOSITORY.record_enrollment_consents(
            user_id=user_id, session_id=str(session['id']), documents=canonical_consents,
            ip_hash=identity_crypto.keyed_digest(str(request.remote_addr or ''), _identity_hmac_key()),
            user_agent_hash=identity_crypto.keyed_digest(str(request.headers.get('User-Agent') or ''), _identity_hmac_key()),
        )
        if not accepted:
            raise RuntimeError('canonical consent transfer was not recorded')
    if state == "PENDING_ENROLLMENT" or (state == "LEGACY" and user.get("_legacy_continuity")):
        target = f"{frontend.rstrip('/')}/?identity_pending=1"
        if user.get("_legacy_continuity"):
            target += "&terms_update=1"
    else:
        target = f"{frontend.rstrip('/')}/?device_approval_required=1"
    response = redirect(target)
    response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
    response.set_cookie("vvault_enrollment_session", token, httponly=True, secure=_runtime_is_production(), samesite="Strict", max_age=20 * 60, path="/")
    return _set_device_cookie(response, device_secret)


# AUTH's handoff is identity evidence; only this authority's native gates can
# turn it into admission. Neither shared service credentials nor owner IDs do so.
def _auth_enrollment_handoff(data=None):
    from vvault.server import enrollment_handoff
    from cryptography.hazmat.primitives import serialization
    pem = str(os.environ.get("AUTH_ENROLLMENT_PUBLIC_KEY_PEM") or "").strip()
    keys = [serialization.load_pem_public_key(pem.replace("\\n", "\n").encode())] if pem else list(vvault_access_assertion.resolve_public_key_ring().values())
    raw = str((data or {}).get("handoff") or request.cookies.get("vvault_auth_handoff") or "")
    return raw, enrollment_handoff.verify_handoff(raw, public_keys=keys,
        auth_issuer=os.environ.get("AUTH_JWT_ISSUER") or "quantum-auth",
        authority_issuer=os.environ.get("VVAULT_ENROLLMENT_ISSUER") or "vvault")


def _auth_enrollment_origin_allowed():
    allowed = {value.strip().rstrip("/") for value in str(os.environ.get("VVAULT_ENROLLMENT_CLIENT_ORIGINS") or "").split(",") if value.strip()}
    allowed.add(_get_frontend_url().rstrip("/"))
    return bool(request.headers.get("Origin")) and request.headers["Origin"].rstrip("/") in allowed


def _auth_enrollment_callback():
    value = str(os.environ.get('CHATTY_ENROLLMENT_CALLBACK_URL') or '').strip()
    parsed = urlparse(value)
    if (not value or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.hostname or parsed.path != '/api/auth/enrollment/complete'
            or (parsed.scheme != 'https' and not (not _runtime_is_production() and parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}))):
        raise RuntimeError('ENROLLMENT_CALLBACK_CONFIGURATION_REQUIRED')
    return value


def _auth_enrollment_signup_required():
    from flask import make_response
    from html import escape
    callback = urlparse(_auth_enrollment_callback())
    target = f'{callback.scheme}://{callback.netloc}/signup'
    response = make_response('<!doctype html><title>Signup required</title><h1>Create your account first</h1><p>This Google identity has not completed signup. No VVAULT account was created.</p><a href="' + escape(target, quote=True) + '">Continue to Chatty signup</a>', 409)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.route('/api/auth/enrollment/continue', methods=['GET'])
def return_auth_enrollment_completion():
    from vvault.server import enrollment_handoff
    from flask import redirect, make_response
    from html import escape
    if not request.cookies.get('vvault_auth_handoff'):
        return redirect(_get_frontend_url().rstrip('/') + '/')
    try:
        _, handoff = _auth_enrollment_handoff()
        native, _ = get_current_user()
        evidence = enrollment_handoff.attest(handoff, repository=AUTH_REPOSITORY,
            native_session=native, pending_session=_enrollment_session_from_request(), documents=_enrollment_documents())
        result = enrollment_handoff.completion(handoff, evidence, signing_key=canonical_projection_signing.load_private_key(),
            auth_issuer=os.environ.get('AUTH_JWT_ISSUER') or 'quantum-auth', authority_issuer=os.environ.get('VVAULT_ENROLLMENT_ISSUER') or 'vvault')
        if 'completion' not in result:
            return _enrollment_response({'success': False, **result}, status=409)
        callback = _auth_enrollment_callback()
        nonce = secrets.token_urlsafe(24)
        response = make_response('<!doctype html><meta name="referrer" content="strict-origin"><title>Return to Chatty</title><form method="post" action="' + escape(callback, quote=True) + '"><input type="hidden" name="completion" value="' + escape(result['completion'], quote=True) + '"><button type="submit">Continue to Chatty</button></form><script nonce="' + nonce + '">document.forms[0].submit()</script>')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'strict-origin'
        response.headers['Content-Security-Policy'] = "default-src 'none'; form-action " + callback + "; script-src 'nonce-" + nonce + "'; frame-ancestors 'none'"
        return response
    except enrollment_handoff.EnrollmentRejected:
        callback = urlparse(_auth_enrollment_callback())
        target = f'{callback.scheme}://{callback.netloc}/?enrollment=required'
        response = make_response('<!doctype html><title>Resume enrollment</title><h1>Continue your saved enrollment</h1><p>The short-lived connection expired. Your completed enrollment steps are preserved.</p><a href="' + escape(target, quote=True) + '">Return to Chatty to renew the connection</a>', 401)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
    except Exception as exc:
        logger.warning('Enrollment return unavailable: %s', type(exc).__name__)
        return _enrollment_response({'success': False, 'errorCode': 'ENROLLMENT_COMPLETION_UNAVAILABLE'}, status=503)


@app.route('/api/auth/enrollment/handoff', methods=['POST'])
def accept_auth_enrollment_handoff():
    from vvault.server import enrollment_handoff
    if not _auth_enrollment_origin_allowed():
        origin = request.headers.get("Origin")
        return _enrollment_response({"success": False, "errorCode": "HANDOFF_ORIGIN_REJECTED",
            "originDisposition": "missing" if not origin else "opaque" if origin == "null" else "untrusted"}, status=403)
    stage = 'verify_handoff'
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        raw, handoff = _auth_enrollment_handoff(data)
        canonical_consents = enrollment_handoff.verified_canonical_consents(handoff, _enrollment_documents())
        stage = 'resolve_owner'
        owner = enrollment_handoff.resolve_owner(handoff, AUTH_REPOSITORY)
        if owner is None:
            if handoff['intent'] != 'SIGN_UP':
                return _auth_enrollment_signup_required()
            contact = handoff.get('contact') or {}
            email = contact.get('email')
            if (handoff['identity']['provider'] != 'google' or contact.get('emailVerified') is not True
                    or not isinstance(email, str) or not 0 < len(email) <= 320 or '@' not in email):
                return _enrollment_response({"success": False, "errorCode": "VERIFIED_CONTACT_REQUIRED"}, status=409)
            # Contact is stored only after exact provider resolution; never used
            # to select, merge, or guess an existing canonical owner.
            stage = 'admit_verified_identity'
            owner, _ = AUTH_REPOSITORY.admit_verified_identity(
                provider=handoff['identity']['provider'], provider_subject=handoff['identity']['providerSubject'],
                verified_email=email, name=None, issuer=handoff['identity']['providerIssuer'])
        stage = 'start_enrollment_session'
        response = _start_enrollment_session(owner, _get_frontend_url(), **({'canonical_consents': canonical_consents} if canonical_consents is not None else {}))
        stage = 'handoff_response'
        if response.headers.get('Location', '').rstrip('/') == _get_frontend_url().rstrip('/'):
            response.headers['Location'] = _get_frontend_url().rstrip('/') + '/api/auth/enrollment/continue'
        response.set_cookie('vvault_auth_handoff', raw, httponly=True, secure=_runtime_is_production(),
                            samesite='Strict', max_age=20 * 60, path='/')
        return response
    except enrollment_handoff.EnrollmentRejected:
        return _enrollment_response({"success": False, "errorCode": "ENROLLMENT_HANDOFF_REJECTED"}, status=401)
    except Exception as exc:
        import traceback
        import re
        identifier = lambda value: value if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', value) else 'unknown'
        diag = getattr(exc, 'diag', None)
        functions = ','.join(identifier(frame.name) for frame in traceback.extract_tb(exc.__traceback__)[-6:])
        logger.warning('Enrollment handoff unavailable: type=%s stage=%s table=%s constraint=%s functions=%s',
                       identifier(type(exc).__name__), stage, identifier(getattr(diag, 'table_name', None)),
                       identifier(getattr(diag, 'constraint_name', None)), functions)
        return _enrollment_response({"success": False, "errorCode": "ENROLLMENT_HANDOFF_UNAVAILABLE"}, status=503)


@app.route('/api/auth/enrollment/completion', methods=['POST'])
def complete_auth_enrollment_handoff():
    from vvault.server import enrollment_handoff
    if not _auth_enrollment_origin_allowed():
        return _enrollment_response({"success": False, "errorCode": "HANDOFF_ORIGIN_REJECTED"}, status=403)
    try:
        _, handoff = _auth_enrollment_handoff(request.get_json(silent=True) or {})
        # get_current_user re-reads unrevoked, unexpired native sessions and
        # enforces NORMAL/trusted-device gates; no assertion/service fallback.
        native, _ = get_current_user()
        evidence = enrollment_handoff.attest(handoff, repository=AUTH_REPOSITORY,
            native_session=native, pending_session=_enrollment_session_from_request(), documents=_enrollment_documents())
        result = enrollment_handoff.completion(handoff, evidence,
            signing_key=canonical_projection_signing.load_private_key(),
            auth_issuer=os.environ.get('AUTH_JWT_ISSUER') or 'quantum-auth',
            authority_issuer=os.environ.get('VVAULT_ENROLLMENT_ISSUER') or 'vvault')
        return _enrollment_response({"success": 'completion' in result, **result}, status=200 if 'completion' in result else 409)
    except enrollment_handoff.EnrollmentRejected:
        return _enrollment_response({"success": False, "errorCode": "ENROLLMENT_HANDOFF_REJECTED"}, status=401)
    except Exception as exc:
        logger.warning('Enrollment completion unavailable: %s', type(exc).__name__)
        return _enrollment_response({"success": False, "errorCode": "ENROLLMENT_COMPLETION_UNAVAILABLE"}, status=503)


def _first_signup_paired_launch(owner_id):
    """Navigation metadata only, after authoritative first activation.

    A signed matching AUTH transaction establishes Chatty initiation. Native
    signup has no handoff. An expired/conflicting handoff grants no launch plan.
    """
    from vvault.server import enrollment_handoff
    callback = urlparse(_auth_enrollment_callback())
    chatty = f'{callback.scheme}://{callback.netloc}/'
    vvault = _get_frontend_url().rstrip('/') + '/'
    if request.cookies.get('vvault_email_initiator'):
        from vvault.server import vvault_auth_crypto as crypto
        try:
            context=json.loads(crypto.open_transaction_secret(request.cookies['vvault_email_initiator'].encode('ascii'),_identity_transaction_key()))
            if context.get('initiator')=='chatty' and context.get('owner_id')==str(owner_id) and 0<=int(time.time())-context.get('issuedAt',0)<1200:
                return {'version':'paired-signup-launch/v1','initiator':'chatty','currentUrl':chatty.rstrip('/')+'/api/auth/paired/start?expected_owner='+str(owner_id),'companionUrl':vvault,'companionProduct':'VVAULT'}
        except Exception:
            pass
    if request.cookies.get('vvault_auth_handoff'):
        _, handoff = _auth_enrollment_handoff()
        owner = enrollment_handoff.resolve_owner(handoff, AUTH_REPOSITORY)
        if not owner or str(owner['id']) != str(owner_id):
            raise enrollment_handoff.EnrollmentRejected('OWNER_IDENTITY_CONFLICT')
        return {'version': 'paired-signup-launch/v1', 'initiator': 'chatty',
                'currentUrl': vvault + 'api/auth/enrollment/continue', 'companionUrl': vvault,
                'companionProduct': 'VVAULT'}
    return {'version': 'paired-signup-launch/v1', 'initiator': 'vvault',
            'currentUrl': vvault, 'companionUrl': chatty.rstrip('/') + '/api/auth/paired/start?expected_owner=' + str(owner_id), 'companionProduct': 'Chatty'}


@app.route('/api/auth/enrollment/status', methods=['GET'])
def canonical_enrollment_status():
    """Expose this browser's checkpoint and authoritative completion navigation."""
    pending = _enrollment_session_from_request()
    if not pending:
        native, _ = get_current_user()
        if native and AUTH_REPOSITORY.is_first_enrollment_activation_session(
            user_id=str(native['id']), session_id=str(native['session_id']),
        ):
            try:
                launch = _first_signup_paired_launch(str(native['id']))
            except Exception:
                # Enrollment remains complete; retry the short-lived AUTH
                # journey instead of resetting any saved gate.
                launch = None
            return _enrollment_response({'success': True, 'pending': False, 'completed': True,
                                         'pairedLaunch': launch, 'continueUrl': '/api/auth/enrollment/continue'})
        return jsonify({"success": False, "pending": False}), 401
    return _enrollment_response({
        "success": True,
        "pending": True,
        "session_kind": pending.get("enrollment_session_kind"),
        "account_state": pending.get("account_state"),
        "device_status": pending.get("device_status"),
        "passkey_registered": bool(AUTH_REPOSITORY.list_active_webauthn_credentials(
            user_id=str(pending["user_id"]),
        )) if pending.get("enrollment_session_kind") == "PENDING_ENROLLMENT" else False,
        "recovery_codes_ready": AUTH_REPOSITORY.enrollment_recovery_codes_ready(
            user_id=str(pending["user_id"]),
        ) if pending.get("enrollment_session_kind") == "PENDING_ENROLLMENT" else False,
        "documents": _enrollment_documents(),
        "legal_receipts_current": AUTH_REPOSITORY.has_current_legal_receipts(
            user_id=str(pending.get("user_id") or ""), required_documents=_enrollment_documents(),
        ),
    })


@app.route('/api/auth/devices/status', methods=['GET'])
def canonical_device_status():
    """Return only resumable pending-device state for this browser session."""
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "pending": False}), 401
    return _enrollment_response({
        "success": True,
        "pending": True,
        "device_status": pending.get("device_status"),
        "session_kind": "PENDING_DEVICE",
    })


def _chatty_pairing_callback() -> str | None:
    """Return the one configured Chatty callback; browser input never chooses it."""
    candidate = CHATTY_PAIRING_CALLBACK_URL.rstrip("/")
    try:
        parsed = urlparse(candidate)
        parsed.port
    except (TypeError, ValueError):
        return None
    if (not candidate or parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or not parsed.path
            or parsed.params or parsed.query or parsed.fragment):
        return None
    if _runtime_is_production():
        if parsed.scheme != "https":
            return None
    elif parsed.scheme != "http" or parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        return None
    return candidate


@app.route('/api/auth/pairing-intents/chatty', methods=['POST'])
@require_auth
def create_chatty_pairing_intent():
    """Begin an explicit optional pairing with Chatty for this active account.

    The response contains only a 60-second opaque code and configured callback,
    never an email, provider subject, session bearer, or VVAULT data.
    """
    callback = _chatty_pairing_callback()
    current = getattr(request, "current_user", {})
    if not callback:
        return jsonify({"success": False, "error": "Chatty pairing is not configured"}), 503
    if _normalize_origin(request.headers.get("Origin") or "") != _get_frontend_url():
        return jsonify({"success": False, "error": "Same-origin pairing required"}), 403
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    code = identity_crypto.opaque_token()
    created = AUTH_REPOSITORY.create_chatty_pairing_intent(
        code_digest=identity_crypto.keyed_digest(code, _identity_hmac_key()),
        user_id=str(current.get("id") or ""), session_id=str(current.get("session_id") or ""),
        callback_uri=callback, expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    if not created:
        return jsonify({"success": False, "error": "Pairing requires an active trusted session"}), 401
    response = jsonify({"success": True, "audience": "chatty-developer-local", "pairing_code": code,
                        "callback_uri": callback, "expires_in": 60})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _chatty_pairing_client_authenticated() -> bool:
    """Authenticate only the configured Chatty server, never a browser caller."""
    client_id = str(request.headers.get("X-Chatty-Client-Id") or "")
    authorization = str(request.headers.get("Authorization") or "")
    prefix = "Bearer "
    if not CHATTY_PAIRING_CLIENT_ID or not CHATTY_PAIRING_CLIENT_SECRET or not authorization.startswith(prefix):
        return False
    return hmac.compare_digest(client_id, CHATTY_PAIRING_CLIENT_ID) and hmac.compare_digest(
        authorization[len(prefix):], CHATTY_PAIRING_CLIENT_SECRET,
    )


@app.route('/api/auth/pairing-intents/chatty/redeem', methods=['POST'])
def redeem_chatty_pairing_intent():
    """Server-to-server redemption for an explicit VVAULT-to-Chatty pairing.

    The caller receives only the opaque link identifier. No VVAULT owner,
    email, provider identity, cookie, data, or session material is disclosed.
    """
    if not _chatty_pairing_client_authenticated():
        return jsonify({"success": False, "error": "Pairing client authentication failed"}), 401
    callback = _chatty_pairing_callback()
    payload = request.get_json(silent=True) or {}
    if not callback or payload.get("audience") != "chatty-developer-local" or payload.get("callback_uri") != callback:
        return jsonify({"success": False, "error": "Pairing request was rejected"}), 400
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    try:
        pairing = AUTH_REPOSITORY.consume_chatty_pairing_intent(
            code_digest=identity_crypto.keyed_digest(str(payload.get("pairing_code") or ""), _identity_hmac_key()),
            callback_uri=callback, chatty_account_id=str(payload.get("chatty_account_id") or ""),
        )
    except ValueError:
        pairing = None
    if not pairing:
        return jsonify({"success": False, "error": "Pairing request was rejected"}), 400
    response = jsonify({"success": True, "audience": pairing["audience"], "link_id": str(pairing["link_id"])})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route('/api/auth/enrollment/consents', methods=['POST'])
def accept_canonical_enrollment_consents():
    pending = _enrollment_session_from_request()
    if not pending:
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    documents = _enrollment_documents()
    request_ip_hash = identity_crypto.keyed_digest(str(request.remote_addr or ""), _identity_hmac_key())
    request_user_agent_hash = identity_crypto.keyed_digest(str(request.headers.get("User-Agent") or ""), _identity_hmac_key())
    if (pending.get("enrollment_session_kind") == "LEGACY"
            and pending.get("account_state") in {"ACTIVE", "LEGACY"}):
        normal_token = identity_crypto.opaque_token()
        device_secret = _device_secret_from_request()
        # A missing recognizer is intentionally treated as an unfamiliar
        # device, not as an excuse to bypass the device-verification gate.
        if not device_secret:
            device_secret = identity_crypto.opaque_token()
        completed = AUTH_REPOSITORY.complete_legacy_consent(
            user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]),
            normal_token_hash=_session_token_hash(normal_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30), documents=documents,
            ip_hash=request_ip_hash, user_agent_hash=request_user_agent_hash,
            device_secret_digest=identity_crypto.keyed_digest(device_secret, _identity_hmac_key()),
        )
        if not completed:
            return jsonify({"success": False, "error": "Terms update was denied"}), 403
        if completed.get("enrollment_session_kind") == "PENDING_DEVICE":
            response = _enrollment_response(
                {"success": True, "legal_recertified": True, "device_approval_required": True, "documents": documents},
                pending_token=normal_token,
            )
            return _set_device_cookie(response, device_secret)
        if completed.get("enrollment_session_kind") == "PENDING_ENROLLMENT":
            response = _enrollment_response(
                {"success": True, "legal_recertified": True, "requires_enrollment": True, "documents": documents},
                pending_token=normal_token,
            )
            return _set_device_cookie(response, device_secret)
        response = _enrollment_response(
            {"success": True, "legacy_continuity": pending.get("account_state") == "LEGACY", "documents": documents},
            normal_token=normal_token,
        )
        return _set_device_cookie(response, device_secret)
    if pending.get("enrollment_session_kind") != "PENDING_ENROLLMENT":
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    accepted = AUTH_REPOSITORY.record_enrollment_consents(user_id=str(pending["user_id"]), session_id=str(pending["session_id"]), documents=documents, ip_hash=request_ip_hash, user_agent_hash=request_user_agent_hash)
    if not accepted:
        return jsonify({"success": False, "error": "Enrollment consent was denied"}), 403
    return _enrollment_response({"success": True, "documents": documents})


@app.route('/api/auth/enrollment/webauthn/challenge', methods=['POST'])
def canonical_webauthn_challenge():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_ENROLLMENT":
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    origin = _get_frontend_url(); parsed = urlparse(origin)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}):
        return jsonify({"success": False, "error": "WebAuthn origin is invalid"}), 503
    challenge = secrets.token_bytes(32); encoded = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    if not AUTH_REPOSITORY.create_webauthn_registration_challenge(user_id=str(pending["user_id"]), session_id=str(pending["session_id"]), challenge_digest=identity_crypto.keyed_digest(encoded, _identity_hmac_key()), rp_id=parsed.hostname, allowed_origin=origin, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)):
        return jsonify({"success": False, "error": "WebAuthn challenge was denied"}), 403
    return _enrollment_response({"success": True, "publicKey": {"challenge": encoded, "rp": {"id": parsed.hostname, "name": "VVAULT"}, "user": {"id": base64.urlsafe_b64encode(str(pending["user_id"]).encode()).rstrip(b"=").decode(), "name": str(pending["user_id"]), "displayName": "VVAULT user"}, "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}], "authenticatorSelection": {"residentKey": "preferred", "userVerification": "required"}, "attestation": "none", "timeout": 300000}})


@app.route('/api/auth/enrollment/webauthn/register', methods=['POST'])
def canonical_webauthn_register():
    pending = _enrollment_session_from_request(); credential = request.get_json(silent=True) or {}
    if not pending or pending.get("enrollment_session_kind") != "PENDING_ENROLLMENT":
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    try:
        encoded = str(((credential.get("response") or {}).get("clientDataJSON") or ""))
        client_data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8"))
        if client_data.get("type") != "webauthn.create": raise ValueError("unexpected WebAuthn ceremony")
        challenge = str(client_data.get("challenge") or "")
        stored = AUTH_REPOSITORY.consume_webauthn_registration_challenge(user_id=str(pending["user_id"]), session_id=str(pending["session_id"]), challenge_digest=identity_crypto.keyed_digest(challenge, _identity_hmac_key()))
        if not stored: raise ValueError("challenge expired")
        from webauthn import verify_registration_response
        from webauthn.helpers import parse_registration_credential_json
        verified = verify_registration_response(credential=parse_registration_credential_json(credential), expected_challenge=base64.urlsafe_b64decode(challenge + "=" * (-len(challenge) % 4)), expected_rp_id=str(stored["rp_id"]), expected_origin=str(stored["allowed_origin"]), require_user_verification=True)
        transports = ((credential.get("response") or {}).get("transports") or [])
        if not AUTH_REPOSITORY.store_webauthn_credential(user_id=str(pending["user_id"]), credential_id=base64.urlsafe_b64encode(bytes(verified.credential_id)).rstrip(b"=").decode(), public_key=bytes(verified.credential_public_key), sign_count=int(verified.sign_count), transports=transports if isinstance(transports, list) else [], user_verified=True):
            raise ValueError("credential rejected")
        return _enrollment_response({"success": True, "webauthn_verified": True})
    except Exception as exc:
        logger.warning("WebAuthn enrollment rejected: %s", type(exc).__name__)
        return jsonify({"success": False, "error": "WebAuthn registration was rejected"}), 400


@app.route('/api/auth/enrollment/recovery-codes', methods=['POST'])
def canonical_recovery_codes():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_ENROLLMENT":
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    try:
        codes = identity_crypto.recovery_codes()
        if not AUTH_REPOSITORY.replace_recovery_codes(user_id=str(pending["user_id"]), session_id=str(pending["session_id"]), code_digests=identity_crypto.digest_recovery_codes(codes, _identity_hmac_key())):
            raise ValueError("recovery preconditions incomplete")
        return _enrollment_response({"success": True, "recovery_codes": codes})
    except Exception as exc:
        logger.warning("recovery code issue rejected: %s", type(exc).__name__)
        return jsonify({"success": False, "error": "Recovery code issue was rejected"}), 400


@app.route('/api/auth/enrollment/activate', methods=['POST'])
def activate_canonical_enrollment():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_ENROLLMENT":
        return jsonify({"success": False, "error": "Pending enrollment session required"}), 401
    token = secrets.token_urlsafe(32)
    normal = AUTH_REPOSITORY.complete_enrollment(user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]), device_id=str(pending["enrollment_device_id"]), normal_token_hash=_session_token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=30), required_documents=_enrollment_documents())
    if not normal:
        return jsonify({"success": False, "error": "Enrollment prerequisites are incomplete"}), 409
    try:
        launch = _first_signup_paired_launch(str(pending['user_id']))
    except Exception:
        launch = None
    return _enrollment_response({"success": True, "account_state": "ACTIVE", "completed": True,
                                 "pairedLaunch": launch, "continueUrl": "/api/auth/enrollment/continue"}, normal_token=token)


@app.route('/api/auth/devices/approve', methods=['POST'])
@require_auth
def approve_canonical_device():
    pending = _enrollment_session_from_request(); current = getattr(request, "current_user", {})
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE" or str(pending.get("user_id")) != str(current.get("id")):
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    token = secrets.token_urlsafe(32)
    normal = AUTH_REPOSITORY.approve_pending_device(actor_user_id=str(current["id"]), actor_session_id=str(current["session_id"]), pending_session_id=str(pending["session_id"]), normal_token_hash=_session_token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    if not normal:
        return jsonify({"success": False, "error": "Device approval was denied"}), 403
    return _enrollment_response({"success": True, "device_status": "TRUSTED"}, normal_token=token)


@app.route('/api/auth/devices/recover', methods=['POST'])
def recover_canonical_device():
    pending = _enrollment_session_from_request(); data = request.get_json(silent=True) or {}
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    try:
        digest = identity_crypto.keyed_digest(identity_crypto.normalize_recovery_code(str(data.get("recovery_code") or "")), _identity_hmac_key())
        token = secrets.token_urlsafe(32)
        normal = AUTH_REPOSITORY.recover_pending_device(user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]), recovery_code_digest=digest, normal_token_hash=_session_token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        if not normal: raise ValueError("recovery denied")
        return _enrollment_response({"success": True, "device_status": "TRUSTED"}, normal_token=token)
    except Exception:
        return jsonify({"success": False, "error": "Device recovery was denied"}), 403


@app.route('/api/auth/devices/webauthn/challenge', methods=['POST'])
def canonical_device_webauthn_challenge():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    parsed = urlparse(_get_frontend_url())
    if not parsed.hostname:
        return jsonify({"success": False, "error": "WebAuthn origin is invalid"}), 503
    credentials = AUTH_REPOSITORY.list_active_webauthn_credentials(user_id=str(pending["user_id"]))
    if not credentials:
        return jsonify({"success": False, "error": "No passkey is available"}), 409
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    if not AUTH_REPOSITORY.create_webauthn_assertion_challenge(
        user_id=str(pending["user_id"]), session_id=str(pending["session_id"]),
        challenge_digest=identity_crypto.keyed_digest(encoded, _identity_hmac_key()), rp_id=parsed.hostname,
        allowed_origin=_get_frontend_url(), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ):
        return jsonify({"success": False, "error": "WebAuthn challenge was denied"}), 403
    return _enrollment_response({"success": True, "publicKey": {
        "challenge": encoded, "rpId": parsed.hostname, "timeout": 300000, "userVerification": "required",
        "allowCredentials": [{"type": "public-key", "id": row["credential_id"]} for row in credentials],
    }})


@app.route('/api/auth/devices/webauthn/assert', methods=['POST'])
def canonical_device_webauthn_assert():
    pending = _enrollment_session_from_request(); credential = request.get_json(silent=True) or {}
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    try:
        client_b64 = str(((credential.get("response") or {}).get("clientDataJSON") or ""))
        client_data = json.loads(base64.urlsafe_b64decode(client_b64 + "=" * (-len(client_b64) % 4)).decode("utf-8"))
        if client_data.get("type") != "webauthn.get": raise ValueError("unexpected WebAuthn ceremony")
        challenge = str(client_data.get("challenge") or "")
        stored = AUTH_REPOSITORY.consume_webauthn_assertion_challenge(
            user_id=str(pending["user_id"]), session_id=str(pending["session_id"]),
            challenge_digest=identity_crypto.keyed_digest(challenge, _identity_hmac_key()),
        )
        credential_id = str(credential.get("id") or "")
        current = next((row for row in AUTH_REPOSITORY.list_active_webauthn_credentials(user_id=str(pending["user_id"])) if row["credential_id"] == credential_id), None)
        if not stored or not current: raise ValueError("challenge or credential rejected")
        from webauthn import verify_authentication_response
        from webauthn.helpers import parse_authentication_credential_json
        verified = verify_authentication_response(
            credential=parse_authentication_credential_json(credential),
            expected_challenge=base64.urlsafe_b64decode(challenge + "=" * (-len(challenge) % 4)),
            expected_rp_id=str(stored["rp_id"]), expected_origin=str(stored["allowed_origin"]),
            credential_public_key=bytes(current["public_key"]), credential_current_sign_count=int(current["sign_count"]),
            require_user_verification=True,
        )
        token = identity_crypto.opaque_token()
        normal = AUTH_REPOSITORY.complete_pending_device_webauthn(
            user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]), credential_id=credential_id,
            new_sign_count=int(verified.new_sign_count), normal_token_hash=_session_token_hash(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        if not normal: raise ValueError("counter update rejected")
        return _enrollment_response({"success": True, "device_status": "TRUSTED"}, normal_token=token)
    except Exception as exc:
        logger.warning("WebAuthn device assertion rejected: %s", type(exc).__name__)
        return jsonify({"success": False, "error": "WebAuthn assertion was rejected"}), 400


@app.route('/api/auth/devices/transfer/start', methods=['POST'])
def canonical_device_transfer_start():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    code = identity_crypto.opaque_token()
    if not AUTH_REPOSITORY.create_pending_device_transfer(
        user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]),
        code_digest=identity_crypto.keyed_digest(code, _identity_hmac_key()),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    ):
        return jsonify({"success": False, "error": "Device transfer was denied"}), 403
    return _enrollment_response({"success": True, "transfer_code": code, "expires_in": 600})


@app.route('/api/auth/devices/transfer/approve', methods=['POST'])
@require_auth
def canonical_device_transfer_approve():
    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    code = str((request.get_json(silent=True) or {}).get("transfer_code") or "")
    current = getattr(request, "current_user", {})
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    approved = AUTH_REPOSITORY.approve_pending_device_transfer(
        actor_user_id=str(current.get("id") or ""), actor_session_id=str(current.get("session_id") or ""),
        code_digest=identity_crypto.keyed_digest(code, _identity_hmac_key()),
    )
    if not approved:
        return jsonify({"success": False, "error": "Device approval was denied"}), 403
    response = jsonify({"success": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/auth/devices/transfer/complete', methods=['POST'])
def canonical_device_transfer_complete():
    pending = _enrollment_session_from_request()
    if not pending or pending.get("enrollment_session_kind") != "PENDING_DEVICE":
        return jsonify({"success": False, "error": "Pending device session required"}), 401
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    token = identity_crypto.opaque_token()
    normal = AUTH_REPOSITORY.complete_approved_pending_device(
        user_id=str(pending["user_id"]), pending_session_id=str(pending["session_id"]),
        normal_token_hash=_session_token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    if not normal:
        return jsonify({"success": False, "error": "Device approval is still pending"}), 409
    return _enrollment_response({"success": True, "device_status": "TRUSTED"}, normal_token=token)


@app.route('/api/auth/devices/<device_id>/revoke', methods=['POST'])
@require_auth
def revoke_canonical_device(device_id: str):
    current = getattr(request, "current_user", {})
    if not AUTH_REPOSITORY.revoke_enrollment_device(actor_user_id=str(current.get("id") or ""), actor_session_id=str(current.get("session_id") or ""), device_id=device_id):
        return jsonify({"success": False, "error": "Device revocation was denied"}), 403
    return jsonify({"success": True})


@app.route('/api/auth/logout-all', methods=['POST'])
@require_auth
def canonical_logout_all():
    current = getattr(request, "current_user", {})
    revoked = AUTH_REPOSITORY.revoke_all_user_sessions(user_id=str(current.get("id") or ""))
    response = jsonify({"success": True, "revoked_sessions": revoked})
    response.delete_cookie("vvault_session", path="/")
    response.delete_cookie("vvault_enrollment_session", path="/")
    return response


# Canonical identity-directory routes.  Provider claims are verified here and
# persisted only through VVaultAuthRepository; email never selects an account.
def _identity_hmac_key() -> str:
    key = str(os.environ.get("VVAULT_ENROLLMENT_HMAC_KEY") or "").strip()
    if len(key) < 32:
        raise RuntimeError("identity transaction key is not configured")
    return key


def _identity_transaction_key() -> str:
    key = str(os.environ.get("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY") or "").strip()
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    if not identity_crypto.valid_transaction_encryption_key(key):
        raise RuntimeError("identity transaction encryption is not configured")
    return key


def _identity_provider_config(provider: str) -> dict[str, str]:
    if provider == "github":
        if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
            raise RuntimeError("GitHub identity provider is not configured")
        return {
            "authorization_endpoint": "https://github.com/login/oauth/authorize",
            "token_endpoint": "https://github.com/login/oauth/access_token",
            "userinfo_endpoint": "https://api.github.com/user",
            "emails_endpoint": "https://api.github.com/user/emails",
        }
    if provider != "google" or not _google_oauth_ready():
        raise RuntimeError("identity provider is not configured")
    # Google publishes stable, provider-owned OAuth and JWKS endpoints.  Using
    # this constrained map avoids making sign-in initiation depend on a second
    # live discovery request while preserving the same HTTPS-origin boundary.
    return {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    }


def _identity_callback_url(provider: str) -> str:
    # OAuth providers redirect the browser to the public VVAULT origin.  The
    # frontend proxy then forwards this native route to the backend; never
    # expose an internal backend port in a provider transaction.
    suffix = "google/callback" if provider == "google" else f"oauth/{provider}/callback"
    return f"{_get_frontend_url()}/api/auth/{suffix}"


def _identity_frontend_url() -> str:
    origin = str(request.headers.get("Origin") or "").rstrip("/")
    return origin if _allowed_redirect_base(origin) else _get_frontend_url()


def _begin_identity_oauth(provider: str, purpose: str = "signin", current: dict | None = None, *, signup_documents=None):
    from flask import redirect
    failure_stage = "identity_transaction"
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    try:
        provider = identity_crypto.normalize_provider(provider)
        if provider == "email":
            raise ValueError("email does not use OAuth")
        failure_stage = "provider_configuration"
        config = _identity_provider_config(provider)
        current = current or {}
        if purpose != "signin" and not current.get("id"):
            return jsonify({"success": False, "error": "Authentication required"}), 401
        failure_stage = "transaction_protection"
        state = identity_crypto.opaque_token()
        verifier = identity_crypto.opaque_token(48)
        nonce = identity_crypto.opaque_token() if provider == "google" else None
        callback_url = _identity_callback_url(provider)
        failure_stage = "transaction_storage"
        AUTH_REPOSITORY.create_oauth_transaction(
            state_digest=identity_crypto.keyed_digest(state, _identity_hmac_key()),
            provider=provider, purpose=purpose,
            nonce_digest=identity_crypto.keyed_digest(nonce, _identity_hmac_key()) if nonce else None,
            nonce_ciphertext=identity_crypto.seal_transaction_secret(nonce, _identity_transaction_key()) if nonce else None,
            pkce_verifier_digest=identity_crypto.keyed_digest(verifier, _identity_hmac_key()),
            pkce_verifier_ciphertext=identity_crypto.seal_transaction_secret(verifier, _identity_transaction_key()),
            redirect_uri=callback_url, frontend_origin=_identity_frontend_url(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            initiating_user_id=str(current.get("id") or "") or None,
            initiating_session_id=str(current.get("session_id") or "") or None,
        )
        params = {
            "client_id": GOOGLE_CLIENT_ID if provider == "google" else GITHUB_CLIENT_ID,
            "redirect_uri": callback_url, "state": state,
            "response_type": "code",
            "code_challenge": identity_crypto.pkce_challenge(verifier), "code_challenge_method": "S256",
        }
        if provider == "google":
            params.update({"scope": "openid email profile", "nonce": nonce, "prompt": "select_account"})
        else:
            params.update({"scope": "read:user user:email", "allow_signup": "false"})
        response = redirect(f"{config['authorization_endpoint']}?{urlencode(params)}")
        if signup_documents is not None:
            from vvault.server import paired_signup_intent
            receipt = paired_signup_intent.issue(key=canonical_projection_signing.load_private_key(),
                state_digest=identity_crypto.keyed_digest(state, _identity_hmac_key()), documents=signup_documents)
            response.set_cookie('vvault_signup_intent',receipt,httponly=True,secure=_runtime_is_production(),samesite='Lax',max_age=600,path='/')
            # Explicit native signup supersedes a previous Chatty-origin journey.
            response.delete_cookie('vvault_auth_handoff',path='/')
        else:
            response.delete_cookie('vvault_signup_intent',path='/')
        return response
    except Exception as exc:
        logger.warning("identity OAuth begin rejected: %s", type(exc).__name__)
        safe_code = {
            "provider_configuration": "provider_unavailable",
            "transaction_protection": "transaction_protection_unavailable",
            "transaction_storage": "identity_transaction_unavailable",
        }.get(failure_stage, "identity_signin_unavailable")
        return jsonify({"success": False, "error": "Identity sign-in is unavailable", "error_code": safe_code}), 503


def _verified_provider_claims(provider: str, code: str, transaction: dict) -> tuple[str, str, str, str | None]:
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    config = _identity_provider_config(provider)
    verifier = identity_crypto.open_transaction_secret(transaction["pkce_verifier_ciphertext"], _identity_transaction_key())
    if not identity_crypto.safe_compare(verifier, transaction["pkce_verifier_digest"], _identity_hmac_key()):
        raise ValueError("OAuth verifier integrity failure")
    client_id = GOOGLE_CLIENT_ID if provider == "google" else GITHUB_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET if provider == "google" else GITHUB_CLIENT_SECRET
    token_response = requests.post(config["token_endpoint"], data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "redirect_uri": transaction["redirect_uri"], "grant_type": "authorization_code", "code_verifier": verifier,
    }, headers={"Accept": "application/json"}, timeout=(3.05, 5))
    token_response.raise_for_status()
    tokens = token_response.json()
    if provider == "google":
        nonce = identity_crypto.open_transaction_secret(transaction["nonce_ciphertext"], _identity_transaction_key())
        if not identity_crypto.safe_compare(nonce, transaction["nonce_digest"], _identity_hmac_key()):
            raise ValueError("OAuth nonce integrity failure")
        signing_key = jwt.PyJWKClient(config["jwks_uri"], timeout=5).get_signing_key_from_jwt(str(tokens.get("id_token") or "")).key
        claims = jwt.decode(str(tokens.get("id_token") or ""), signing_key, algorithms=["RS256"], audience=GOOGLE_CLIENT_ID,
            issuer=["https://accounts.google.com", "accounts.google.com"], options={"require": ["exp", "iat", "aud", "iss", "sub", "nonce"]})
        if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce) or claims.get("email_verified") is not True:
            raise ValueError("Google identity proof is invalid")
        return str(claims["sub"]), str(claims["email"]), str(claims.get("name") or ""), "https://accounts.google.com"
    access_token = str(tokens.get("access_token") or "")
    if not access_token:
        raise ValueError("GitHub token response is invalid")
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
    profile = requests.get(config["userinfo_endpoint"], headers=headers, timeout=(3.05, 5)); profile.raise_for_status()
    emails = requests.get(config["emails_endpoint"], headers=headers, timeout=(3.05, 5)); emails.raise_for_status()
    verified = next((row.get("email") for row in emails.json() if row.get("verified") and row.get("primary")), None)
    if not verified:
        raise ValueError("GitHub has no verified primary email")
    profile_data = profile.json()
    return str(profile_data["id"]), str(verified), str(profile_data.get("name") or profile_data.get("login") or ""), "https://github.com"


@app.route('/api/auth/oauth/<provider>', methods=['GET', 'POST'])
def begin_canonical_oauth(provider: str):
    if request.method == 'POST':
        try:
            from vvault.server import paired_signup_intent
            if request.headers.get('Origin','').rstrip('/') != _get_frontend_url().rstrip('/'):
                return jsonify({'error':'Signup origin rejected'}),403
            body = request.get_json(silent=True) or request.form
            supplied = body.get('documents')
            if isinstance(supplied,str):
                supplied = json.loads(supplied)
            documents = _paired_signup_documents()
            if body.get('intent') != 'SIGN_UP' or body.get('chattyAccepted') not in (True,'true') or body.get('vvaultAccepted') not in (True,'true'):
                raise ValueError('Explicit signup consent required')
            if paired_signup_intent.triples(supplied) != paired_signup_intent.triples(documents):
                raise ValueError('Signup documents changed')
            return _begin_identity_oauth(provider,signup_documents=documents)
        except Exception:
            return jsonify({'error':'Current Chatty and VVAULT acceptance is required'}),400
    return _begin_identity_oauth(provider)


@app.route('/api/auth/google', methods=['GET'])
def begin_legacy_google_compatibility_oauth():
    """Keep the restored legacy login page on the canonical Google flow."""
    return _begin_identity_oauth("google")


@app.route('/api/auth/reauth/<provider>', methods=['POST'])
@require_auth
def begin_canonical_reauth(provider: str):
    return _begin_identity_oauth(provider, purpose="reauth", current=getattr(request, "current_user", {}))


@app.route('/api/auth/identity-links/<provider>', methods=['POST'])
@require_auth
def begin_canonical_identity_link(provider: str):
    return _begin_identity_oauth(provider, purpose="link", current=getattr(request, "current_user", {}))


@app.route('/api/auth/identities', methods=['GET'])
@require_auth
def list_canonical_identities():
    current = getattr(request, "current_user", {})
    identities = AUTH_REPOSITORY.list_active_identities(user_id=str(current.get("id") or ""))
    # Provider subjects are authentication identifiers; never expose them to
    # browser callers. Account settings receives only display-safe metadata.
    return jsonify({"success": True, "identities": [
        {"id": str(row["id"]), "provider": row["provider"], "verified_at": row["verified_at"]}
        for row in identities
    ]})


@app.route('/api/auth/identities/<identity_id>', methods=['DELETE'])
@require_auth
def unlink_canonical_identity(identity_id: str):
    current = getattr(request, "current_user", {})
    removed = AUTH_REPOSITORY.unlink_identity(user_id=str(current.get("id") or ""), session_id=str(current.get("session_id") or ""), identity_id=identity_id)
    if not removed:
        return jsonify({"success": False, "error": "Identity removal was denied"}), 403
    return jsonify({"success": True})


@app.route('/api/auth/google/callback')
@app.route('/api/auth/oauth/<provider>/callback')
def complete_canonical_oauth(provider: str = "google"):
    from flask import redirect
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    code, state = str(request.args.get("code") or ""), str(request.args.get("state") or "")
    if not code or not state or _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "OAuth authorization was rejected"}), 400
    try:
        transaction = AUTH_REPOSITORY.consume_oauth_transaction(identity_crypto.keyed_digest(state, _identity_hmac_key()))
        if not transaction or transaction.get("provider") != provider:
            raise ValueError("transaction invalid")
        if transaction.get("redirect_uri") != _identity_callback_url(provider):
            raise ValueError("callback mismatch")
        subject, email, name, issuer = _verified_provider_claims(provider, code, transaction)
        frontend = str(transaction.get("frontend_origin") or _get_frontend_url())
        if not _allowed_redirect_base(frontend):
            frontend = _get_frontend_url()
        if transaction["purpose"] == "signin":
            signup_documents = None
            if request.cookies.get('vvault_signup_intent'):
                from vvault.server import paired_signup_intent
                signup_documents = paired_signup_intent.verify(request.cookies['vvault_signup_intent'],
                    key=canonical_projection_signing.load_private_key().public_key(),
                    state_digest=identity_crypto.keyed_digest(state,_identity_hmac_key()),documents=_paired_signup_documents())
            user, _created = AUTH_REPOSITORY.admit_verified_identity(
                provider=provider, provider_subject=subject, verified_email=email,
                name=name, issuer=issuer,
                allow_legacy_compatibility=(provider == "google"),
            )
            response = _start_enrollment_session(user, frontend, canonical_consents=signup_documents)
            # This verified callback explicitly starts a native VVAULT journey.
            response.delete_cookie('vvault_auth_handoff',path='/')
            verified_identity = AUTH_REPOSITORY.get_external_identity(provider=provider,provider_subject=subject)
            if not verified_identity or str(verified_identity['user_id']) != str(user['id']):
                raise ValueError('Verified OAuth identity owner changed')
            _set_native_identity_provenance(response,user,verified_identity['identity_id'])
            response.delete_cookie('vvault_email_initiator',path='/')
            if any(value.startswith('vvault_enrollment_session=') and 'Max-Age=0' not in value for value in response.headers.getlist('Set-Cookie')):
                response.delete_cookie('vvault_session',path='/')
            if signup_documents is None and user.get('account_state') == 'PENDING_ENROLLMENT':
                response.headers['Location'] = frontend.rstrip('/') + '/?signup_required=1'
            response.delete_cookie('vvault_signup_intent',path='/')
            return response
        if transaction["purpose"] == "reauth":
            if not AUTH_REPOSITORY.record_session_reauthentication(session_id=str(transaction["initiating_session_id"]), user_id=str(transaction["initiating_user_id"]), provider=provider):
                raise ValueError("reauth denied")
            return redirect(f"{frontend}/?identity_reauthenticated=1")
        if not AUTH_REPOSITORY.link_verified_identity(user_id=str(transaction["initiating_user_id"]), session_id=str(transaction["initiating_session_id"]), provider=provider, provider_subject=subject, verified_email=email, issuer=issuer):
            raise ValueError("link denied")
        return redirect(f"{frontend}/?identity_linked=1")
    except Exception as exc:
        logger.warning("identity OAuth callback rejected: %s", type(exc).__name__)
        return jsonify({"success": False, "error": "OAuth authorization was rejected"}), 400


def _magic_link_smtp_config() -> dict[str, Any] | None:
    """Read only VVAULT's target-owned mail configuration at delivery time."""
    host = str(os.environ.get("SMTP_HOST") or os.environ.get("EMAIL_HOST") or "").strip()
    username = str(os.environ.get("SMTP_USERNAME") or os.environ.get("SMTP_USER") or os.environ.get("EMAIL_USER") or "").strip()
    password = str(os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS") or os.environ.get("EMAIL_PASS") or "")
    sender = str(os.environ.get("SMTP_FROM") or os.environ.get("EMAIL_FROM") or username).strip()
    try:
        port = int(str(os.environ.get("SMTP_PORT") or os.environ.get("EMAIL_PORT") or "587"))
    except ValueError:
        return None
    if not host or not username or not password or not sender or not 1 <= port <= 65535:
        return None
    use_ssl = str(os.environ.get("SMTP_USE_SSL") or "").strip().lower() in {"1", "true", "yes"}
    starttls = not use_ssl and str(os.environ.get("SMTP_STARTTLS") or "true").strip().lower() not in {"0", "false", "no"}
    return {"host": host, "port": port, "username": username, "password": password, "sender": sender, "use_ssl": use_ssl, "starttls": starttls}


def _magic_link_resend_config() -> dict[str, str] | None:
    """Use the same target-owned Resend environment contract as Chatty."""
    api_key = str(os.environ.get("RESEND_API_KEY") or "")
    sender = str(os.environ.get("FROM_EMAIL") or os.environ.get("RESEND_FROM") or "").strip()
    if not api_key or not sender:
        return None
    return {"api_key": api_key, "sender": sender}


def _magic_link_delivery_available() -> bool:
    return _magic_link_resend_config() is not None or _magic_link_smtp_config() is not None


def _deliver_magic_link_via_resend(email: str, url: str, config: dict[str, str]) -> bool:
    """Deliver through Resend without logging recipient, URL, or bearer token."""
    payload = {
        "from": config["sender"],
        "to": [email],
        "subject": "Your VVAULT secure sign-in link",
        "text": f"Open this secure VVAULT sign-in link within 15 minutes:\n{url}\n\nIf you did not request it, you can ignore this email.",
        "html": f"<p>Open this <a href=\"{url}\">secure VVAULT sign-in link</a> within 15 minutes.</p><p>If you did not request it, you can ignore this email.</p>",
    }
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        return 200 <= response.status_code < 300
    except requests.RequestException:
        return False


def _deliver_magic_link(email: str, url: str) -> bool:
    """Send a short-lived magic link without logging its bearer token or recipient."""
    resend_config = _magic_link_resend_config()
    if resend_config:
        return _deliver_magic_link_via_resend(email, url, resend_config)
    config = _magic_link_smtp_config()
    if not config:
        return False
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = email
    message["Subject"] = "Your VVAULT secure sign-in link"
    message.set_content(f"Open this secure VVAULT sign-in link within 15 minutes:\n{url}\n\nIf you did not request it, you can ignore this email.")
    message.add_alternative(
        f"<p>Open this <a href=\"{url}\">secure VVAULT sign-in link</a> within 15 minutes.</p><p>If you did not request it, you can ignore this email.</p>",
        subtype="html",
    )
    try:
        smtp_cls = smtplib.SMTP_SSL if config["use_ssl"] else smtplib.SMTP
        with smtp_cls(config["host"], config["port"], timeout=10) as client:
            if config["starttls"]:
                client.starttls(context=ssl.create_default_context())
            client.login(config["username"], config["password"])
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def _deliver_native_email_code(email, code):
    """Use only the target-owned SMTP/Postal configuration."""
    config = _magic_link_smtp_config()
    if not config:
        return False
    message = EmailMessage()
    message['From'] = config['sender']; message['To'] = email
    message['Subject'] = 'Your VVAULT verification code'
    message.set_content(f'Your VVAULT verification code is: {code}\n\nEnter it in the browser where you requested it within 10 minutes. Each code permits one attempt. If you enter it incorrectly, request a new code.\n\nIf you did not request this code, ignore this email.')
    try:
        smtp_cls = smtplib.SMTP_SSL if config['use_ssl'] else smtplib.SMTP
        with smtp_cls(config['host'], config['port'], timeout=10) as client:
            if config['starttls']:
                client.starttls(context=ssl.create_default_context())
            client.login(config['username'], config['password'])
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def _native_email_context():
    from vvault.server import vvault_auth_crypto as crypto
    raw = request.cookies.get('vvault_email_challenge') or ''
    if not raw or len(raw) > 12000:
        raise ValueError('Email challenge required')
    context = json.loads(crypto.open_transaction_secret(raw.encode('ascii'), _identity_transaction_key()))
    if context.get('version') != 'vvault.email-code.v1' or not context.get('ticket'):
        raise ValueError('Invalid email challenge')
    return context


@app.route('/api/auth/email-codes/health', methods=['GET'])
def native_email_code_health():
    return _enrollment_response({'available': bool(_magic_link_smtp_config())})


@app.route('/api/auth/email-codes', methods=['POST'])
def request_native_email_code(trusted_initiator=None, trusted_body=None):
    from vvault.server import vvault_auth_crypto as crypto, paired_signup_intent
    if trusted_initiator != 'chatty' and request.headers.get('Origin','').rstrip('/') != _get_frontend_url().rstrip('/'):
        return _enrollment_response({'error':'Email challenge origin rejected'},status=403)
    if _rate_limit_key('auth'):
        return _enrollment_response({'error':'Please wait before requesting another code'},status=429)
    try:
        body = trusted_body if trusted_body is not None else (request.get_json(silent=True) or request.form.to_dict())
        if trusted_initiator == 'chatty':
            body = dict(body)
            for field in ('chattyAccepted','vvaultAccepted'):
                body[field] = body.get(field) in (True,'true')
            if isinstance(body.get('documents'),str):
                body['documents']=json.loads(body['documents'])
        email = crypto.normalize_email(str(body.get('email') or ''))
        intent = body.get('intent')
        if intent not in {'SIGN_IN','SIGN_UP'}:
            raise ValueError('Explicit intent required')
        documents = None
        registered = AUTH_REPOSITORY.resolve_verified_email_owner(email)
        if intent == 'SIGN_IN':
            if not registered:
                return _enrollment_response({'success':False,'disposition':'SIGNUP_REQUIRED','continueUrl':'/?signup_required=1'},status=409)
        if intent == 'SIGN_UP':
            documents = _paired_signup_documents()
            if body.get('chattyAccepted') is not True or body.get('vvaultAccepted') is not True or paired_signup_intent.triples(body.get('documents')) != paired_signup_intent.triples(documents):
                raise ValueError('Current acceptance required')
        if not _magic_link_smtp_config():
            return _enrollment_response({'error':'Email delivery is not configured'},status=503)
        try:
            previous = _native_email_context()
        except Exception:
            previous = None
        if previous:
            AUTH_REPOSITORY.revoke_magic_link_challenge(crypto.keyed_digest(previous['ticket'],_identity_hmac_key()))
        ticket = crypto.opaque_token()
        code = f'{secrets.randbelow(100000000):08d}'
        context = {'expectedOwnerId':str(registered['id']) if registered else None,'version':'vvault.email-code.v1','initiator':trusted_initiator or (previous.get('initiator') if previous and previous.get('email')==email else 'vvault'),'email':email,'issuedAt':int(time.time()),'ticket':ticket,'codeDigest':crypto.keyed_digest(ticket+':'+code,_identity_hmac_key()),'intent':intent,'documents':[{k:row[k] for k in ('key','version','sha256')} for row in documents] if documents is not None else None}
        sealed = crypto.seal_transaction_secret(json.dumps(context,separators=(',',':')), _identity_transaction_key()).decode('ascii')
        digest = crypto.keyed_digest(ticket,_identity_hmac_key())
        AUTH_REPOSITORY.issue_magic_link_challenge(token_digest=digest,normalized_email=email,purpose='signin',redirect_uri=_get_frontend_url(),expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
        if not _deliver_native_email_code(email,code):
            AUTH_REPOSITORY.revoke_magic_link_challenge(digest)
            return _enrollment_response({'error':'Email delivery failed. Request a new code.'},status=503)
        response = _enrollment_response({'success':True,'message':'Enter the code from your email. Each code allows one attempt.'},status=202)
        response.set_cookie('vvault_email_challenge',sealed,httponly=True,secure=_runtime_is_production(),samesite='Strict',max_age=600,path='/')
        return response
    except Exception as exc:
        logger.warning('Native email code request rejected: %s',type(exc).__name__)
        return _enrollment_response({'error':'Check the email and current signup acceptance, then request a new code.'},status=400)


def _email_entry_navigation(response, destination):
    from html import escape
    nonce=secrets.token_urlsafe(24)
    # A fresh document navigation ends the incoming POST redirect chain.
    response.set_data('<p>Continue email verification.</p><a href="'+escape(destination,quote=True)+'">Continue</a><script nonce="'+nonce+'">window.location.assign('+json.dumps(destination)+')</script>')
    response.status_code=200
    response.headers['Content-Type']='text/html; charset=utf-8'
    response.headers['Content-Security-Policy']="default-src 'none'; script-src 'nonce-"+nonce+"'; frame-ancestors 'none'"
    return response


@app.route('/api/auth/email-entry', methods=['POST'])
def enter_chatty_email_verification():
    callback=urlparse(_auth_enrollment_callback())
    chatty=callback.scheme+'://'+callback.netloc
    if request.headers.get('Origin','') != chatty:
        return _enrollment_response({'error':'Chatty email entry origin rejected'},status=403)
    response=request_native_email_code(trusted_initiator='chatty')
    payload=response.get_json(silent=True) or {}
    if payload.get('disposition')=='SIGNUP_REQUIRED':
        return _email_entry_navigation(response,chatty+'/?email_signup_required=1')
    if response.status_code==202:
        # Explicit email entry must not render a previous browser account instead of its code prompt.
        response.delete_cookie('vvault_session',path='/')
        response.delete_cookie('vvault_enrollment_session',path='/')
        return _email_entry_navigation(response,_get_frontend_url().rstrip('/')+'/?email_code_requested=1')
    return response


@app.route('/api/auth/email-entry/complete', methods=['GET'])
def complete_chatty_email_entry():
    from vvault.server import vvault_auth_crypto as crypto
    try:
        context=json.loads(crypto.open_transaction_secret(request.cookies.get('vvault_email_initiator','').encode('ascii'),_identity_transaction_key()))
        current,_=get_current_user()
        if not current or context.get('initiator')!='chatty' or context.get('owner_id')!=str(current.get('id')) or not 0<=int(time.time())-context.get('issuedAt',0)<1200:
            raise ValueError('Fresh owner session required')
        callback=urlparse(_auth_enrollment_callback())
        target=callback.scheme+'://'+callback.netloc+'/api/auth/paired/start?expected_owner='+str(current['id'])
        return _email_entry_navigation(_enrollment_response({'success':True}),target)
    except Exception:
        return _enrollment_response({'error':'Sign in again to continue to Chatty.'},status=401)


@app.route('/api/auth/email-codes/status', methods=['GET'])
def native_email_code_status():
    try:
        context=_native_email_context()
        if not 0 <= int(time.time())-context.get('issuedAt',0)<600:
            raise ValueError('Email challenge expired')
        return _enrollment_response({'codeRequested':True,'intent':context['intent'],'email':context.get('email','')})
    except Exception:
        return _enrollment_response({'error':'Request a new verification code.'},status=401)


@app.route('/api/auth/email-codes/resend', methods=['POST'])
def resend_native_email_code():
    if request.headers.get('Origin','').rstrip('/') != _get_frontend_url().rstrip('/'):
        return _enrollment_response({'error':'Email challenge origin rejected'},status=403)
    try:
        context=_native_email_context()
        if not 0<=int(time.time())-context.get('issuedAt',0)<600:
            raise ValueError('Email entry expired')
        body={'email':context['email'],'intent':context['intent'],'documents':context.get('documents')}
        if context['intent']=='SIGN_UP':
            body.update(chattyAccepted=True,vvaultAccepted=True)
        return request_native_email_code(trusted_initiator='chatty' if context.get('initiator')=='chatty' else None,trusted_body=body)
    except Exception:
        return _enrollment_response({'error':'Return to signup and review the current documents before requesting another code.'},status=400)


@app.route('/api/auth/email-codes/verify', methods=['POST'])
def verify_native_email_code():
    from vvault.server import vvault_auth_crypto as crypto, paired_signup_intent
    if request.headers.get('Origin','').rstrip('/') != _get_frontend_url().rstrip('/'):
        return _enrollment_response({'error':'Email challenge origin rejected'},status=403)
    if _rate_limit_key('auth'):
        return _enrollment_response({'error':'Please wait before trying again'},status=429)
    response = _enrollment_response({'error':'The code was incorrect, expired, or already used. Request a new code.'},status=400)
    try:
        context = _native_email_context()
        # Consume before comparing: one durable attempt, including incorrect codes.
        challenge = AUTH_REPOSITORY.consume_magic_link_challenge(crypto.keyed_digest(context['ticket'],_identity_hmac_key()))
        if not challenge or challenge.get('purpose') != 'signin':
            return response
        code = str((request.get_json(silent=True) or {}).get('code') or '')
        if len(code) != 8 or not code.isascii() or not code.isdigit() or not hmac.compare_digest(crypto.keyed_digest(context['ticket']+':'+code,_identity_hmac_key()),context.get('codeDigest','')):
            return response
        documents = None
        if context.get('intent') == 'SIGN_UP':
            documents = _paired_signup_documents()
            if paired_signup_intent.triples(context.get('documents')) != paired_signup_intent.triples(documents):
                return response
        elif context.get('intent') != 'SIGN_IN':
            return response
        email = str(challenge['normalized_email'])
        if context.get('expectedOwnerId'):
            user = AUTH_REPOSITORY.link_verified_email_identity(email=email,expected_owner_id=context['expectedOwnerId'])
            identity_id = user['identity_id']
        elif context['intent'] == 'SIGN_IN':
            return response
        else:
            user, _ = AUTH_REPOSITORY.admit_verified_identity(provider='email',provider_subject=email,verified_email=email,name=None)
            identity = AUTH_REPOSITORY.get_external_identity(provider='email',provider_subject=email)
            if not identity or str(identity['user_id']) != str(user['id']):
                return response
            identity_id = identity['identity_id']
        result = _start_enrollment_session(user,_get_frontend_url(),canonical_consents=documents)
        _set_native_identity_provenance(result,user,identity_id)
        if documents is None and user.get('account_state') == 'PENDING_ENROLLMENT':
            result.headers['Location'] = _get_frontend_url().rstrip('/')+'/?signup_required=1'
        if any(v.startswith('vvault_enrollment_session=') and 'Max-Age=0' not in v for v in result.headers.getlist('Set-Cookie')):
            result.delete_cookie('vvault_session',path='/')
        if context.get('initiator')=='chatty':
            launch_context={'initiator':'chatty','owner_id':str(user['id']),'issuedAt':int(time.time())}
            sealed=crypto.seal_transaction_secret(json.dumps(launch_context),_identity_transaction_key()).decode('ascii')
            result.set_cookie('vvault_email_initiator',sealed,httponly=True,secure=_runtime_is_production(),samesite='Strict',max_age=1200,path='/')
            if user.get('account_state')=='ACTIVE' and not any(v.startswith('vvault_enrollment_session=') and 'Max-Age=0' not in v for v in result.headers.getlist('Set-Cookie')):
                callback=urlparse(_auth_enrollment_callback())
                result.headers['Location']=_get_frontend_url().rstrip('/')+'/api/auth/email-entry/complete'
        result.delete_cookie('vvault_auth_handoff',path='/')
        result.delete_cookie('vvault_email_challenge',path='/')
        return result
    except Exception as exc:
        logger.warning('Native email code verification rejected: %s',type(exc).__name__)
        return response


@app.route('/api/auth/email-magic-links/health', methods=['GET'])
def magic_link_delivery_health():
    available = _magic_link_delivery_available()
    response = jsonify({"available": available})
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if available else 503


@app.route('/api/auth/email-magic-links', methods=['POST'])
def request_email_magic_link():
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    # Always return the same accepted response, including malformed input and
    # unavailable delivery, so email existence is never disclosed.
    if not _magic_link_delivery_available():
        return jsonify({"success": False, "error": "magic_link_delivery_unavailable"}), 503
    try:
        if not _rate_limit_key("auth"):
            email = identity_crypto.normalize_email(str((request.get_json(silent=True) or {}).get("email") or ""))
            token = identity_crypto.opaque_token()
            frontend = _get_frontend_url()
            token_digest = identity_crypto.keyed_digest(token, _identity_hmac_key())
            AUTH_REPOSITORY.issue_magic_link_challenge(token_digest=token_digest, normalized_email=email,
                purpose="signin", redirect_uri=frontend, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
            if not _deliver_magic_link(email, f"{frontend}/#magic_link={token}"):
                AUTH_REPOSITORY.revoke_magic_link_challenge(token_digest)
                return jsonify({"success": False, "error": "magic_link_delivery_failed"}), 503
    except Exception as exc:
        logger.warning("magic-link request not delivered: %s", type(exc).__name__)
    response = jsonify({"success": True, "message": "If the address can receive sign-in mail, a secure link is on its way."})
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/auth/email-magic-links/consume', methods=['POST'])
def consume_email_magic_link():
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    response = jsonify({"success": False, "error": "Magic link was rejected"})
    response.headers["Cache-Control"] = "no-store"; response.headers["Referrer-Policy"] = "no-referrer"
    try:
        token = str((request.get_json(silent=True) or {}).get("token") or "")
        challenge = AUTH_REPOSITORY.consume_magic_link_challenge(identity_crypto.keyed_digest(token, _identity_hmac_key()))
        if not challenge or challenge.get("purpose") != "signin":
            return response, 400
        user, _created = AUTH_REPOSITORY.admit_verified_identity(provider="email", provider_subject=str(challenge["normalized_email"]), verified_email=str(challenge["normalized_email"]), name=None)
        return _start_enrollment_session(user, str(challenge.get("redirect_uri") or _get_frontend_url()))
    except Exception as exc:
        logger.warning("magic-link consume rejected: %s", type(exc).__name__)
        return response, 400


# Google OAuth Health Check
@app.route('/api/auth/google/health')
def google_oauth_health():
    """Check if Google OAuth and VVAULT-native auth persistence are configured."""
    auth_ready, auth_state = _oauth_identity_authority_available()
    oauth_ready = _google_oauth_ready()
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    transaction_key_ready = identity_crypto.valid_transaction_encryption_key(
        str(os.environ.get("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY") or "").strip()
    )
    error = None
    if not oauth_ready:
        error = _google_oauth_config_error()
    elif not auth_ready:
        error = "VVAULT auth storage is currently unavailable. Sign-in is blocked to protect local identity/session persistence."
    elif not transaction_key_ready:
        error = "OAuth transaction protection is unavailable. Sign-in is blocked."

    status_code = 200 if oauth_ready and auth_ready and transaction_key_ready else 503
    return jsonify({
        "oauth_configured": _google_oauth_ready(),
        "client_id_set": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_ID not in _OAUTH_PLACEHOLDER_VALUES),
        "client_secret_set": bool(GOOGLE_CLIENT_SECRET and GOOGLE_CLIENT_SECRET not in _OAUTH_PLACEHOLDER_VALUES),
        "provider": "google",
        "callback_url": _identity_callback_url("google"),
        "frontend_url": _get_frontend_url(),
        "vvault_auth_ready": auth_ready,
        "oauth_transaction_protection_ready": transaction_key_ready,
        "auth_owner": auth_state.get("auth_owner") or AUTH_OWNER,
        "session_owner": auth_state.get("session_owner") or SESSION_OWNER,
        "auth_status": auth_state.get("status") or "unknown",
        "source_database": auth_state.get("source_database"),
        "error_code": auth_state.get("error_code"),
        "error": error,
    }), status_code



# Error handlers
@app.errorhandler(PocketverseAuthorityError)
def pocketverse_forbidden(error):
    return jsonify({
        "success": False,
        "error": "POCKETVERSE_AUTHORITY_DENIED",
        "code": "POCKETVERSE_AUTHORITY_DENIED",
    }), 403


@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Serve asset files (images, etc.)"""
    if os.path.exists(os.path.join(ASSETS_DIR, filename)):
        return send_from_directory(ASSETS_DIR, filename)
    if os.path.exists(os.path.join(PUBLIC_DIR, 'assets', filename)):
        return send_from_directory(os.path.join(PUBLIC_DIR, 'assets'), filename)
    return jsonify({"error": "Asset not found"}), 404

@app.errorhandler(404)
def catch_all(e):
    """Serve React app for client-side routing (SPA fallback)"""
    index_path = os.path.join(DIST_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(DIST_DIR, 'index.html')
    return jsonify({"error": "Not found"}), 404

def main():
    """Main entry point for VVAULT Web Server"""
    if not chatty_body_service.database_url():
        raise RuntimeError("VVAULT_BODY_DATABASE_URL is required; local database fallback is disabled")

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("VVAULT_BACKEND_HOST", "0.0.0.0")
    is_production = _runtime_is_production()

    requested_boot_mode = (os.environ.get("VVAULT_POCKETVERSE_BOOT_MODE") or "").strip().lower()
    skip_boot = (os.environ.get("VVAULT_SKIP_POCKETVERSE_BOOT") or "").strip().lower() in (
        "1", "true", "yes", "on",
    ) or requested_boot_mode == "skip"

    if skip_boot:
        _mark_pocketverse_boot_state(mode="skip", status="skipped", started_at=None, completed_at=None, error=None)
        logger.info("Pocketverse boot skipped before bind.")
    else:
        boot_mode = requested_boot_mode or ("sync" if is_production else "async")
        logger.info("Pocketverse boot mode: %s", boot_mode)
        _run_pocketverse_boot(boot_mode)

    chatty_body_service.open_body_database_pool(wait=False)
    _startup_timings["supervisor_ms"] = 0
    _load_chatty_vvault_door_contract()
    runtime_status = _refresh_readiness_snapshot()
    _startup_timings["database_ready_ms"] = int(
        (time.perf_counter() - SERVER_STARTED_MONOTONIC) * 1000
    )
    projection_warm = _prime_mandatory_projection_caches()
    _startup_timings["projection_warm_ms"] = int(
        projection_warm.get("durationMs") or 0
    )
    _startup_timings["backend_ready_ms"] = int(
        (time.perf_counter() - SERVER_STARTED_MONOTONIC) * 1000
    )
    _startup_timings["frontend_ready_ms"] = -1
    _startup_timings["canonical_ready_ms"] = _startup_timings["backend_ready_ms"]
    logger.info(
        "[startup] supervisor_ms=%s database_ready_ms=%s backend_ready_ms=%s "
        "frontend_ready_ms=%s canonical_ready_ms=%s",
        _startup_timings["supervisor_ms"],
        _startup_timings["database_ready_ms"],
        _startup_timings["backend_ready_ms"],
        _startup_timings["frontend_ready_ms"],
        _startup_timings["canonical_ready_ms"],
    )
    _start_readiness_refresh_worker()
    _start_provider_transcript_search_backfill_worker()
    runtime_status = _get_vvault_runtime_status()
    body_status = runtime_status.get("body_database", {})
    auth_status = runtime_status.get("auth", {})
    storage_status = runtime_status.get("storage", {})

    print("🌐 VVAULT Web Server")
    print("=" * 50)
    print(f"🔧 Project Directory: {PROJECT_DIR}")
    print(f"📦 Capsules Directory: {CAPSULES_DIR}")
    print(f"🌐 Server Port: {port}")
    print(f"🏭 Production Mode: {is_production}")
    print(f"🗄️ Body DB: {body_status.get('status')}")
    print(f"🔐 Auth DB: {auth_status.get('status')}")
    print(f"📦 Storage: {storage_status.get('status')}")
    print("=" * 50)

    try:
        logger.info(
            "VVAULT runtime config: body_database=%s auth=%s storage=%s",
            body_status.get("status"),
            auth_status.get("status"),
            storage_status.get("status"),
        )
        logger.info(f"🚀 Starting VVAULT Web Server on {host}:{port}...")
        app.run(
            host=host,
            port=port,
            debug=not is_production,
            threaded=True,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        print("\n🛑 VVAULT Web Server stopped by user")
    except Exception as e:
        print(f"❌ VVAULT Web Server error: {e}")
        sys.exit(1)

def _paired_welcome_verification_key():
    from cryptography.hazmat.primitives import serialization
    pem = str(os.environ.get('AUTH_ENROLLMENT_PUBLIC_KEY_PEM') or '').strip()
    if pem:
        return pem
    keys = list(vvault_access_assertion.resolve_public_key_ring().values())
    if len(keys) != 1:
        raise ValueError('Explicit AUTH enrollment key required for welcome event')
    return keys[0].public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()


from vvault.server.paired_welcome_event import install_welcome_event_route
from vvault.server.paired_welcome_outbox import PairedWelcomeOutbox
install_welcome_event_route(app, AUTH_REPOSITORY, _enrollment_documents,
    lambda verifier: PairedWelcomeOutbox(AUTH_REPOSITORY._connect,verifier),
    _paired_welcome_verification_key,
    lambda: os.environ.get('VVAULT_PAIRED_WELCOME_ENABLED') == 'true',
    issuer=lambda: os.environ.get('AUTH_JWT_ISSUER', 'quantum-auth'))
if os.environ.get('VVAULT_PAIRED_WELCOME_ENABLED') == 'true':
    from vvault.server.paired_welcome_outbox import start_dispatcher
    from vvault.server.paired_welcome_smtp import send_welcome_smtp
    _paired_welcome_dispatch_stop = start_dispatcher(
        PairedWelcomeOutbox(AUTH_REPOSITORY._connect, lambda _: None),
        AUTH_REPOSITORY._connect, send_welcome_smtp)


if __name__ == "__main__":
    main()
