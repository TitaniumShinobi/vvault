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

import os
import sys
import json

# The deployed service currently loads its protected, untracked runtime .env
# from the checkout.  Deployment owns its group/mode contract; values are never
# committed or emitted by the application.
from pathlib import Path
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass
import re
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from uuid import uuid4

from flask import Flask, request, jsonify, send_from_directory, Response
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
import bcrypt
from datetime import datetime, timedelta, timezone
import requests  # For Turnstile verification
from oauthlib.oauth2 import WebApplicationClient
from urllib.parse import urlencode, urlparse

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
import chatty_body_service
import vvault_auth_repository
import vvault_file_repository
try:
    from vvault.server import cleanhouse_files_evidence
except ImportError:  # pragma: no cover - direct script compatibility
    import cleanhouse_files_evidence
from code_project_repository import CodeProjectRepository, is_internal_code_project_path


def _pocketverse_request_context():
    """Build request context for Pocketverse guard from current request."""
    cu = getattr(request, "current_user", None) or {}
    email = cu.get("email") or (request.headers.get("X-Chatty-User") if request else None)
    return {
        "email": email,
        "user_id": cu.get("id") or email,
        "session_user": cu,
        "metadata_loader": _load_pocketverse_metadata_from_body,
    }

def _is_uuid(value: Optional[str]) -> bool:
    return bool(
        isinstance(value, str)
        and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', value.strip(), re.I)
    )

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

# Fix for OAuthlib InsecureTransportError in local development
# This allows OAuth to work over HTTP on localhost
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

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
    with open(DOOR_CONTRACT_PATH, 'r', encoding='utf-8') as handle:
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
        "auth_origin": _normalize_origin(raw_door.get("authApiOrigin") or ""),
        "auth_public_origin": _normalize_origin(raw_door.get("authPublicOrigin") or raw_door.get("authApiOrigin") or ""),
        "auth_cookie_name": str(raw_door.get("authCookieName") or "").strip(),
        "session_bridge_path": str(raw_door.get("sessionBridgePath") or "").strip(),
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
        "auth_origin": "auth_origin_missing",
        "auth_public_origin": "auth_public_origin_missing",
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
    if door["version"] != 1:
        door["problems"].append("contract_version_invalid")
    if door["name"] != selected_door:
        door["problems"].append("door_name_invalid")
    if door["auth_cookie_name"] != "auth_sid":
        door["problems"].append("auth_cookie_name_invalid")
    if door["session_bridge_path"] != "/api/vault/session-bridge":
        door["problems"].append("session_bridge_path_invalid")
    if door["allow_legacy_exchange"]:
        door["problems"].append("legacy_exchange_not_allowed")

    all_origins = [
        door["chatty_origin"],
        door["chatty_api_origin"],
        door["code_origin"],
        door["code_api_origin"],
        door["vvault_origin"],
        door["auth_origin"],
        door["auth_public_origin"],
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
    return response


def _is_canonical_mutating_request() -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if request.method == "OPTIONS":
        return False
    path = request.path or ""
    if path == "/api/auth/logout":
        return False
    if path == "/api/vault/files/preview" and request.method == "POST":
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
            uid = (cu.get("email") if cu else None) or (request.headers.get("X-Chatty-User") if request else None) or "service"
            sid = getattr(request, "current_token", None) if request else None
            if sid is None and request:
                sid = (request.headers.get("Authorization") or "")[:32] or "service"
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
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
VVAULT_FRONTEND_URL = _resolve_frontend_origin() or "http://localhost:7784"
VVAULT_BACKEND_URL = _resolve_backend_origin() or "http://localhost:8000"
CHATTY_PAIRING_CALLBACK_URL = (os.environ.get("CHATTY_PAIRING_CALLBACK_URL") or "").strip()
CHATTY_PAIRING_CLIENT_ID = (os.environ.get("CHATTY_PAIRING_CLIENT_ID") or "").strip()
CHATTY_PAIRING_CLIENT_SECRET = (os.environ.get("CHATTY_PAIRING_CLIENT_SECRET") or "").strip()
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


def _storage_dependency_metadata() -> Dict[str, Any]:
    """Report VVAULT-native object storage config metadata without probing it yet."""
    s3_keys = ["S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    s3_configured = all(bool(os.environ.get(key)) for key in s3_keys)
    rest_url = (
        os.environ.get("VVAULT_OBJECT_STORAGE_URL")
        or os.environ.get("VVAULT_BODY_DB_URL")
        or os.environ.get("SUPABASE_URL")
    )
    rest_key = (
        os.environ.get("VVAULT_OBJECT_STORAGE_SERVICE_KEY")
        or os.environ.get("VVAULT_BODY_DB_SERVICE_ROLE_KEY")
        or os.environ.get("VVAULT_BODY_DB_ANON_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
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
            or os.environ.get("SUPABASE_STORAGE_BUCKET")
        ),
        "provider": "s3_compatible" if s3_configured else "object_storage_rest" if rest_configured else "unconfigured",
    }


def _auth_dependency_metadata() -> Dict[str, Any]:
    """Report auth-adjacent runtime config metadata without making it readiness-blocking."""
    service_api_configured = bool(globals().get("VVAULT_SERVICE_TOKEN"))
    google_oauth_configured = _google_oauth_ready()
    auth_status = _auth_repository_status()
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
    }


def _get_vvault_runtime_status() -> Dict[str, Any]:
    body_database = _body_database_dependency_status()
    ready = bool(body_database.get("ready"))
    return {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": ready,
        "connection_state": body_database.get("connection_state") or ("connected" if ready else "degraded"),
        "runtime": _runtime_metadata(),
        "body_database": body_database,
        "storage": _storage_dependency_metadata(),
        "auth": _auth_dependency_metadata(),
    }


AUTH_REPOSITORY = vvault_auth_repository.VVaultAuthRepository()
AUTH_OWNER = vvault_auth_repository.AUTH_OWNER
SESSION_OWNER = vvault_auth_repository.SESSION_OWNER
VAULT_FILE_REPOSITORY = vvault_file_repository.VVaultFileRepository(auth_repository=AUTH_REPOSITORY)
VAULT_FILE_OWNER = vvault_file_repository.FILE_OWNER
VAULT_STORAGE_OWNER = vvault_file_repository.STORAGE_OWNER
legacy_remote_client = None
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
        is_admin=is_admin,
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


def _upsert_vault_file_record(record: Dict[str, Any], *, context: str) -> Dict[str, Any]:
    logical_path = (record.get('storage_path') or record.get('filename') or '').strip()
    if not logical_path:
        raise ValueError("Vault file record is missing filename/storage_path")

    record = dict(record)
    record['filename'] = logical_path
    record['storage_path'] = logical_path
    result = VAULT_FILE_REPOSITORY.upsert(record)
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
            "canonical_filename": "definition.txt",
            "legacy_basenames": ["definition.json"],
            "format": "text",
            "comparison_mode": "text",
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


def _query_construct_identity_projection_pool(callsign: str, bare_name: str) -> List[Dict[str, Any]]:
    return VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=bare_name,
        user_id=None,
        include_content=False,
    )


def _load_identity_projection_content(file_id: str) -> Any:
    row = VAULT_FILE_REPOSITORY.get_by_id(file_id)
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


def _build_identity_projection_field_state(field: str, callsign: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        current_content = _load_identity_projection_content(current["id"])
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


def _load_identity_projection_candidates(construct_id: str) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    callsign = _normalize_callsign(construct_id)
    bare_name = _bare_name_from_callsign(callsign)
    specs = _identity_projection_specs()
    pool = _query_construct_identity_projection_pool(callsign, bare_name)

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


def _read_identity_projection_snapshot(construct_id: str) -> Dict[str, Any]:
    callsign, grouped = _load_identity_projection_candidates(construct_id)

    fields: Dict[str, Any] = {}
    fields_present: List[str] = []
    fields_missing: List[str] = []
    conflict_fields: List[str] = []
    invalid_fields: List[str] = []

    for field in _identity_projection_specs():
        state = _build_identity_projection_field_state(field, callsign, grouped.get(field, []))
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


def _infer_construct_owner_user_id(callsign: str) -> Optional[str]:
    return VAULT_FILE_REPOSITORY.first_owner_for_construct(callsign)


def _serialize_projected_field(field: str, value: Any) -> Tuple[str, str]:
    if field in ('conditioning', 'definition'):
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value, "text"

    if field == 'physicalFeatures':
        if isinstance(value, str):
            return value, "text"
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, ensure_ascii=False), "json"
        raise ValueError("physicalFeatures must be a string, object, or array")

    if field == 'voice':
        if isinstance(value, str):
            raise ValueError("voice must be a JSON value, not a plain string")
        try:
            return json.dumps(value, indent=2, ensure_ascii=False), "json"
        except TypeError as exc:
            raise ValueError(f"voice must be JSON-serializable: {exc}") from exc

    raise ValueError(f"Unsupported identity projection field: {field}")


def _find_canonical_identity_projection_rows(callsign: str, canonical_path: str) -> List[Dict[str, Any]]:
    row = VAULT_FILE_REPOSITORY.find_exact(
        filename=canonical_path,
        storage_path=canonical_path,
        construct_id=callsign,
        user_id=None,
        is_admin=True,
    )
    return [row] if row else []


def _upsert_identity_projection_record(record: Dict[str, Any], canonical_path: str) -> Tuple[str, Optional[str], Optional[str]]:
    callsign = record['construct_id']
    existing_rows = _find_canonical_identity_projection_rows(callsign, canonical_path)
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


def _project_identity_fields(construct_id: str, fields: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")

    callsign = _normalize_callsign(construct_id)
    specs = _identity_projection_specs()
    now = datetime.now(timezone.utc).isoformat()
    inferred_user_id = _infer_construct_owner_user_id(callsign)
    results: Dict[str, Any] = {}

    for field, value in fields.items():
        if field not in specs:
            raise ValueError(f"Unsupported identity projection field: {field}")
        if value is None:
            raise ValueError(f"{field} cannot be null")

        content, storage_format = _serialize_projected_field(field, value)
        canonical_path = _identity_projection_canonical_path(callsign, field)
        current_rows = _find_canonical_identity_projection_rows(callsign, canonical_path)
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
            "user_id": current.get('user_id') if current else inferred_user_id,
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
    if _is_uuid(current_user_id):
        return current_user_id.strip()
    return None


def _ensure_vvault_user(email: str, name: Optional[str] = None) -> Dict[str, Any]:
    existing = db_get_user(email)
    if existing:
        return existing

    display_name = (name or email.split('@')[0]).strip() or email.split('@')[0]
    try:
        user = AUTH_REPOSITORY.ensure_external_user(email=email, name=display_name, role='user')
        user['source'] = 'vvault_auth'
        return user
    except Exception as exc:
        logger.warning(f"SESSION_EXCHANGE: failed to upsert VVAULT auth user for {email}: {type(exc).__name__}")
        raise


def _extract_life_id_anchor(user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user:
        return None
    for field in ("life_user_id", "life_id", "lifeUserId"):
        value = str(user.get(field) or "").strip()
        if value:
            return value
    for field in ("id", "user_id"):
        value = str(user.get(field) or "").strip()
        if value and not _is_uuid(value):
            return value
    return None


def _resolve_auth_life_identity(
    *,
    email: str,
    local_user: Optional[Dict[str, Any]] = None,
    fallback_user: Optional[Dict[str, Any]] = None,
    proposed_life_id: Optional[str] = None,
) -> Dict[str, Any]:
    registry_life_id = _extract_life_id_anchor(fallback_user)
    local_life_id = _extract_life_id_anchor(local_user)
    conflict_ids = sorted({value for value in (registry_life_id, local_life_id) if value})
    if len(conflict_ids) > 1:
        return {
            "ok": False,
            "canonical": False,
            "should_mint": False,
            "error_code": "IDENTITY_CONFLICT",
            "email": email,
            "conflict_life_ids": conflict_ids,
            "proposed_life_id": proposed_life_id,
        }
    return {
        "ok": True,
        "canonical": True,
        "should_mint": not bool(conflict_ids),
        "error_code": None,
        "email": email,
        "life_user_id": conflict_ids[0] if conflict_ids else proposed_life_id,
        "proposed_life_id": proposed_life_id,
        "auth_owner": AUTH_OWNER,
    }


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


def _resolve_construct_owner_user_id(construct_id: str) -> Optional[str]:
    if not construct_id:
        return None
    return VAULT_FILE_REPOSITORY.first_owner_for_construct(construct_id)


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
            from vvault.server.memup_sync import persist_construct_memup_from_candidate_transcripts

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
) -> Dict[str, Any]:
    updated = updated_at or datetime.now(timezone.utc).isoformat()
    created = created_at or updated
    resolved_system_prompt = system_prompt or instructions
    return {
        "callsign": callsign,
        "name": display_name,
        "displayName": display_name,
        "display_name": display_name,
        "fullName": full_name,
        "description": description,
        "instructions": instructions,
        "system_prompt": resolved_system_prompt,
        "prompt": resolved_system_prompt,
        "conversationStarters": conversation_starters,
        "conversation_starters": conversation_starters,
        "capabilities": capabilities,
        "memory": memory_settings,
        "canonRefs": canon_refs,
        "knowledgeRefs": knowledge_refs,
        "role": role,
        "createdAt": created,
        "updatedAt": updated,
        "source": source,
    }


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
) -> Dict[str, Any]:
    updated = updated_at or datetime.now(timezone.utc).isoformat()
    created = created_at or updated
    return {
        "construct_id": callsign,
        "display_name": display_name,
        "instance_name": display_name,
        "full_name": full_name,
        "description": description,
        "role": role,
        "status": status,
        "models": models,
        "orchestration_mode": orchestration_mode,
        "capabilities": capabilities,
        "memory": memory_settings,
        "canon_refs": canon_refs,
        "knowledge_refs": knowledge_refs,
        "created_at": created,
        "updated_at": updated,
        "source": source,
    }


def _physical_features_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(f"{key}: {entry}" for key, entry in value.items())
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
}
IMAGE_PREVIEW_MAX_BYTES = int(os.environ.get('VVAULT_IMAGE_PREVIEW_MAX_BYTES', str(10 * 1024 * 1024)))


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


def _build_construct_editor_payload(callsign: str, user_id: Optional[str]) -> Dict[str, Any]:
    files_rows = _query_construct_file_rows(callsign, user_id, include_content=True)

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
    physical_features_text = _load_vault_file_text(source_rows.get('physical_features.txt'))
    voice_md_text = _load_vault_file_text(source_rows.get('voice.md'))

    avatar_row = source_rows.get('avatar.png')
    avatar_url, avatar_content_type = _binary_data_url_from_row(avatar_row)

    total_bytes = 0
    sample_filenames: List[str] = []
    for row in files_rows[:20]:
        sample_filenames.append(row.get('filename'))
    for row in files_rows:
        metadata = _metadata_to_dict(row.get('metadata'))
        size = metadata.get('size') or metadata.get('bytes') or 0
        if isinstance(size, int):
            total_bytes += size

    updated_at = None
    timestamps = [
        max(_parse_vault_timestamp(row.get('updated_at')), _parse_vault_timestamp(row.get('created_at')))
        for row in files_rows
    ]
    if timestamps:
        updated_at = datetime.fromtimestamp(max(timestamps), tz=timezone.utc).isoformat()

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

    canon_refs = _normalize_construct_refs(
        prompt_json.get('canonRefs') if isinstance(prompt_json.get('canonRefs'), list) and prompt_json.get('canonRefs') else metadata_json.get('canon_refs')
    )
    knowledge_refs = _normalize_construct_refs(
        prompt_json.get('knowledgeRefs') if isinstance(prompt_json.get('knowledgeRefs'), list) and prompt_json.get('knowledgeRefs') else metadata_json.get('knowledge_refs')
    )

    models = _normalize_construct_models(metadata_json.get('models'))
    display_name = _first_non_empty_string([
        prompt_json.get('displayName'),
        prompt_json.get('display_name'),
        prompt_json.get('name'),
        metadata_json.get('display_name'),
        metadata_json.get('instance_name'),
    ], default=callsign)
    full_name = _first_non_empty_string([
        prompt_json.get('fullName'),
        metadata_json.get('full_name'),
        display_name,
    ], default=display_name)
    created_at = _first_non_empty_string([
        prompt_json.get('createdAt'),
        metadata_json.get('created_at'),
    ])

    return {
        "ok": True,
        "constructId": callsign,
        "callsign": callsign,
        "displayName": display_name,
        "fullName": full_name,
        "description": _first_non_empty_string([prompt_json.get('description'), metadata_json.get('description')]),
        "instructions": _first_non_empty_string([prompt_json.get('instructions'), prompt_json.get('prompt')]),
        "conversationStarters": _first_non_empty_list([
            prompt_json.get('conversationStarters'),
            prompt_json.get('conversation_starters'),
        ]),
        "conditioning": _first_non_empty_string([conditioning_text]),
        "definition": _first_non_empty_string([
            definition_json.get('instructions'),
            definition_json.get('prompt'),
            definition_text,
        ]),
        "physicalFeatures": _first_non_empty_string([
            _physical_features_to_text(physical_features_json),
            physical_features_text,
        ]),
        "voice": _first_non_empty_string([voice_md_text, voice_json.get('text')]),
        "gender": _first_non_empty_string([gender_json.get('gender')]),
        "avatar": {
            "exists": bool(avatar_row),
            "filename": avatar_row.get('filename') if avatar_row else None,
            "url": avatar_url,
            "sha256": avatar_row.get('sha256') if avatar_row else None,
            "contentType": avatar_content_type,
        },
        "filesSummary": {
            "totalCount": len(files_rows),
            "totalBytes": total_bytes,
            "sampleFilenames": sample_filenames,
            "updatedAt": updated_at,
        },
        "models": models,
        "capabilities": capabilities,
        "memory": memory_settings,
        "canonRefs": canon_refs,
        "knowledgeRefs": knowledge_refs,
        "createdAt": created_at,
        "updatedAt": updated_at,
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
VVAULT_ENCRYPTION_KEY = os.environ.get("VVAULT_ENCRYPTION_KEY", os.environ.get("SECRET_KEY", "default-encryption-key"))

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
        auth_header = request.headers.get('Authorization', '')
        provided_token = None
        
        if auth_header.startswith('Bearer '):
            provided_token = auth_header[7:]
        elif auth_header.startswith('ServiceToken '):
            provided_token = auth_header[13:]
        else:
            provided_token = request.headers.get('X-Service-Token')
        
        if not VVAULT_SERVICE_TOKEN:
            logger.warning("SERVICE_API: VVAULT_SERVICE_TOKEN not configured")
            return jsonify({
                "success": False,
                "error": "Service API not configured"
            }), 503
        
        if provided_token != VVAULT_SERVICE_TOKEN:
            logger.warning(f"SERVICE_API: Invalid service token attempt")
            return jsonify({
                "success": False,
                "error": "Invalid service token"
            }), 401
        
        return f(*args, **kwargs)
    return decorated_function

# Legacy fallback identity references. These are not an auth persistence
# authority after the VVAULT-native auth cutover.
USERS_DB_FALLBACK = {
    'admin@vvault.com': {
        'password': 'admin123',
        'name': 'Admin User',
        'role': 'admin'
    }
}

# In-memory session cache (primary storage when DB table unavailable)
ACTIVE_SESSIONS = {}

def _check_session_table_available() -> bool:
    """Check if VVAULT-native session storage is available."""
    return _auth_repository_ready()

def db_create_session(email: str, role: str, token: str, expires_at: datetime, remember_me: bool = False) -> bool:
    """Create a VVAULT-native session; persist only a token hash."""
    user = AUTH_REPOSITORY.get_user_by_email(email)
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
    try:
        user = AUTH_REPOSITORY.get_user_by_email(email)
        if not user:
            return None
        user['source'] = 'vvault_auth'
        user['role'] = _resolve_user_role(email, local_user=user)
        return user
    except Exception as e:
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

def _b64url_decode_segment(segment: str) -> bytes:
    pad = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def verify_standalone_auth_session_token(raw_token: str, secret: str) -> Optional[Dict[str, Any]]:
    """Verify @quantum/auth cookie token (must match auth/src/auth/session.ts HMAC)."""
    if not raw_token or not secret:
        return None
    try:
        parts = raw_token.split(".")
        if len(parts) != 2:
            return None
        encoded_payload, provided_sig_b64u = parts
        msg = encoded_payload.encode("utf-8")
        expected_mac = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
        provided_sig = _b64url_decode_segment(provided_sig_b64u)
        if len(provided_sig) != len(expected_mac) or not hmac.compare_digest(provided_sig, expected_mac):
            return None
        payload = json.loads(_b64url_decode_segment(encoded_payload).decode("utf-8"))
        exp = int(payload.get("exp") or 0)
        if exp < int(time.time()):
            return None
        email = (payload.get("email") or "").strip().lower()
        if not email:
            return None
        return payload
    except Exception as exc:
        logger.debug("standalone auth token verify failed: %s", exc)
        return None


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
        request.current_token = token
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def deny_untrusted_data_route_access():
    """One owner-bound session is the only browser authority for vault data.

    This runs before legacy `require_chatty_auth` decorators, preventing
    caller-controlled email/header values or admin role from selecting a
    tenant. Internal service contracts are rebuilt in Plan 4 with signed,
    owner-bound assertions rather than this compatibility bypass.
    """
    path = request.path
    if not (path.startswith('/api/vault/') or path.startswith('/api/chatty/')):
        return None
    if path in {'/api/vault/health', '/api/chatty/health'}:
        return None
    session, token = get_current_user()
    if not session:
        return jsonify({"success": False, "error": "Active trusted-device session required"}), 401
    request.current_user = session
    request.current_token = token
    return None

def require_chatty_auth(f):
    """Auth decorator for Chatty integration endpoints.

    Accepts three auth methods in priority order:
    1. VVAULT_SERVICE_TOKEN via X-Chatty-Key or X-Service-Token header (service-to-service).
       User context comes from X-Chatty-User header (email).
    2. Standard Bearer session token (same as require_auth).
    3. Dev mode: if VVAULT_SERVICE_TOKEN env var is not set, endpoints are
       open and X-Chatty-User provides user context (optional).
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        expected_key = os.environ.get("VVAULT_SERVICE_TOKEN")
        provided_key = request.headers.get("X-Chatty-Key") or request.headers.get("X-Service-Token")

        if expected_key and provided_key == expected_key:
            chatty_email = request.headers.get("X-Chatty-User")
            if not chatty_email:
                return jsonify({"success": False, "error": "X-Chatty-User header required with API key auth"}), 400
            chatty_user_id = request.headers.get("X-Chatty-User-Id")
            current_user = {"email": chatty_email}
            if _is_uuid(chatty_user_id):
                current_user["id"] = chatty_user_id.strip()
            log_auth_decision(
                action="access_granted",
                user_id=chatty_email,
                resource=request.path,
                result="allowed",
                reason="chatty_api_key",
                ip=ip
            )
            request.current_user = current_user
            request.current_token = None
            return f(*args, **kwargs)

        if expected_key and provided_key and provided_key != expected_key:
            log_auth_decision(
                action="access_attempt",
                user_id="chatty_service",
                resource=request.path,
                result="denied",
                reason="invalid_chatty_api_key",
                ip=ip
            )
            return jsonify({"success": False, "error": "Unauthorized"}), 401

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
            request.current_user = session
            request.current_token = token
            return f(*args, **kwargs)

        if not expected_key:
            chatty_email = request.headers.get("X-Chatty-User")
            log_auth_decision(
                action="access_granted",
                user_id=chatty_email or "dev_open",
                resource=request.path,
                result="allowed",
                reason="chatty_dev_mode_open",
                ip=ip
            )
            request.current_user = {"email": chatty_email} if chatty_email else {"email": "dev@localhost"}
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

def verify_turnstile_token(token: str, remote_ip: str = None) -> bool:
    """Verify Cloudflare Turnstile token"""
    try:
        # Get secret key from environment
        secret_key = os.getenv('TURNSTILE_SECRET_KEY')
        
        if not secret_key:
            logger.error("TURNSTILE_SECRET_KEY not configured")
            return False
        
        # Prepare verification request
        data = {
            'secret': secret_key,
            'response': token
        }
        
        if remote_ip:
            data['remoteip'] = remote_ip
        
        # Make verification request to Cloudflare
        response = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data=data,
            timeout=10
        )
        
        result = response.json()
        
        if result.get('success'):
            logger.info("Turnstile verification successful")
            return True
        else:
            logger.warning(f"Turnstile verification failed: {result.get('error-codes', [])}")
            return False
            
    except Exception as e:
        logger.error(f"Turnstile verification error: {e}")
        return False

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


@app.route('/api/ready')
def readiness_check():
    """Readiness requires VVAULT-native body database health."""
    runtime_status = _get_vvault_runtime_status()
    door = _resolve_chatty_vvault_door()
    ready = bool(runtime_status["ready"] and door.get("ok"))
    return jsonify({
        "ready": ready,
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
    }), 200 if ready else 503

def _current_vvault_user_id() -> tuple[str | None, tuple[Any, int] | None]:
    current_user = getattr(request, 'current_user', None)
    if not current_user:
        return None, (jsonify({"success": False, "error": "Authentication required"}), 401)
    user_email = current_user.get('email')
    if not user_email:
        return None, (jsonify({"success": False, "error": "Invalid session"}), 401)
    try:
        user = vvault_auth_repository.VVaultAuthRepository().ensure_external_user(
            email=user_email,
            name=current_user.get('name') or user_email.split('@')[0],
            role=current_user.get('role', 'user'),
        )
        return str(user["id"]), None
    except Exception as exc:
        logger.error(f"Failed to resolve VVAULT user identity for Code projects: {exc}")
        return None, (jsonify({"success": False, "error": "Unable to resolve VVAULT user identity"}), 500)


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
        "session_bridge_path": door.get("session_bridge_path"),
        "auth_cookie_name": door.get("auth_cookie_name"),
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
            "transcripts": repo.list_transcript_links(project_instance_id=project_instance_id),
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
    CONFIG_FILES = {'metadata.json', 'personality.json', 'tone_profile.json', 'voice.md'}
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
        return f'instances/{construct_id}/{base}'
    
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
    """Mint a Flask vault Bearer token from a valid standalone @auth HttpOnly cookie."""
    if request.method == "OPTIONS":
        return ("", 204)
    secret = (os.environ.get("AUTH_SESSION_SECRET") or "").strip()
    if not secret:
        return jsonify({"success": False, "error": "Session bridge is not configured (AUTH_SESSION_SECRET)"}), 503
    cookie_name = (os.environ.get("AUTH_COOKIE_NAME") or "auth_sid").strip()
    raw_cookie = request.cookies.get(cookie_name)
    if not raw_cookie:
        return jsonify({"success": False, "error": "No auth session cookie"}), 401
    payload = verify_standalone_auth_session_token(raw_cookie, secret)
    if not payload:
        return jsonify({"success": False, "error": "Invalid or expired auth session"}), 401
    email = payload.get("email", "").strip().lower()
    display_name = (payload.get("name") or email.split("@")[0]).strip()
    try:
        user_row = _ensure_vvault_user(email, display_name)
    except Exception:
        return _auth_repository_unavailable_response("/api/vault/session-bridge")
    role = user_row.get("role", "user")
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=90)
    try:
        db_create_session(email, role, session_token, expires_at, remember_me=True)
    except Exception:
        return _auth_repository_unavailable_response("/api/vault/session-bridge")
    user_info = {
        "email": email,
        "name": user_row.get("name", display_name),
        "role": role,
    }
    return jsonify({
        "success": True,
        "user": user_info,
        "token": session_token,
        "expires_at": expires_at.isoformat(),
    })


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
        user_role = 'user'
        local_user = AUTH_REPOSITORY.get_user_by_email(user_email)
        display_name = (
            (local_user or {}).get('name')
            or current_user.get('name')
            or user_email.split('@')[0].replace('.', ' ').title()
        )
        user_id = str((local_user or {}).get('id') or current_user.get('id') or "")
        role = _resolve_user_role(user_email, local_user=local_user, fallback_user=current_user)
        is_admin = role == 'admin' or user_role == 'admin'
        
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
        # Ordinary account sessions are never cross-owner, including admin
        # roles. Support impersonation requires a separately audited contract.
        is_admin = False
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
            is_admin=is_admin,
            requested_path=requested_path,
        )
        row_fetch_ms = int(round((time.perf_counter() - row_fetch_started_at) * 1000))

        transform_started_at = time.perf_counter()
        files = _transform_files_for_display(rows, is_admin=is_admin, user_id=None if is_admin else user_id)
        if requested_path:
            files = _filter_transformed_vault_files_for_path(files, requested_path)
        transform_ms = int(round((time.perf_counter() - transform_started_at) * 1000))

        logger.info(
            "VAULT_FILES_LIST path=%s mode=%s admin=%s user_lookup_ms=%s row_fetch_ms=%s transform_ms=%s row_count=%s file_count=%s route_elapsed_ms=%s",
            requested_path or "ALL_FILES",
            "scoped" if requested_path else "all_files",
            is_admin,
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
            "user_root": user_name if not is_admin else "Vault (Admin)"
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
KNOWLEDGE_MAX_SINGLE_FILE = 50 * 1024 * 1024
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

def _read_file_content(raw_bytes, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in BINARY_EXTS:
        return base64.b64encode(raw_bytes).decode('ascii')
    try:
        return raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return base64.b64encode(raw_bytes).decode('ascii')


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
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(f.filename == '' for f in uploaded_files):
            return jsonify({"success": False, "error": "No files provided"}), 400

        file_entries = []

        for upload in uploaded_files:
            if not upload.filename:
                continue
            raw = upload.read()
            fname_lower = upload.filename.lower()

            if fname_lower.endswith('.zip'):
                try:
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    zip_entries = [i for i in zf.infolist() if not i.is_dir()]
                    zip_paths = [e.filename.replace('\\', '/') for e in zip_entries]
                    common_prefix = ''
                    if zip_paths:
                        first_parts = zip_paths[0].split('/')
                        if len(first_parts) > 1:
                            candidate = first_parts[0] + '/'
                            if all(p.startswith(candidate) for p in zip_paths):
                                common_prefix = candidate

                    for info in zip_entries:
                        inner_name = info.filename
                        basename = os.path.basename(inner_name)
                        if not basename or basename.startswith('.'):
                            continue
                        ext = os.path.splitext(basename)[1].lower()
                        if ext in KNOWLEDGE_SKIP_EXTS or ext not in KNOWLEDGE_ALLOWED_EXTS:
                            continue
                        if info.file_size > KNOWLEDGE_MAX_SINGLE_FILE:
                            continue
                        inner_bytes = zf.read(info.filename)
                        rel_path = inner_name.replace('\\', '/')
                        if common_prefix and rel_path.startswith(common_prefix):
                            rel_path = rel_path[len(common_prefix):]
                        if '..' in rel_path:
                            continue
                        rel_dir = '/'.join(rel_path.split('/')[:-1])

                        file_entries.append({
                            'basename': basename,
                            'subfolder': rel_dir,
                            'content': _read_file_content(inner_bytes, basename),
                            'raw_sha256': hashlib.sha256(inner_bytes).hexdigest(),
                            'file_type': _guess_file_type(basename),
                            'size': info.file_size,
                        })
                    zf.close()
                except zipfile.BadZipFile:
                    return jsonify({"success": False, "error": f"Invalid zip file: {upload.filename}"}), 400
            else:
                basename = os.path.basename(upload.filename)
                ext = os.path.splitext(basename)[1].lower()
                if ext in KNOWLEDGE_SKIP_EXTS:
                    continue
                if ext not in KNOWLEDGE_ALLOWED_EXTS:
                    continue
                if len(raw) > KNOWLEDGE_MAX_SINGLE_FILE:
                    continue

                file_entries.append({
                    'basename': basename,
                    'subfolder': '',
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

        existing_map = {}
        for row in VAULT_FILE_REPOSITORY.list_knowledge_files(construct_id=callsign, user_id=user_id):
            existing_map[row['filename']] = row['id']

        for entry in file_entries:
            try:
                rel_dir = entry.get('subfolder', '')
                if rel_dir:
                    vsi_path = f'instances/{callsign}/{rel_dir}/{entry["basename"]}'
                else:
                    vsi_path = map_to_vsi_folder(entry['basename'], callsign, None)

                sha = entry.get('raw_sha256', hashlib.sha256(
                    entry['content'].encode('utf-8') if isinstance(entry['content'], str) else entry['content']
                ).hexdigest())

                top_folder = rel_dir.split('/')[0] if rel_dir else (vsi_path.rsplit('/', 1)[0].rsplit('/', 1)[-1] if '/' in vsi_path else '')
                meta_json = json.dumps({
                    'folder': top_folder,
                    'original_size': entry['size'],
                    'upload_batch': now,
                })

                record = {
                    'filename': vsi_path,
                    'storage_path': vsi_path,
                    'file_type': entry['file_type'],
                    'content': entry['content'],
                    'construct_id': callsign,
                    'user_id': user_id,
                    'is_system': False,
                    'sha256': sha,
                    'metadata': meta_json,
                    'updated_at': now,
                }

                if vsi_path in existing_map:
                    file_id = existing_map[vsi_path]
                    _upsert_vault_file_record(record, context='knowledge_upload')
                    updated += 1
                else:
                    record['created_at'] = now
                    _upsert_vault_file_record(record, context='knowledge_upload')
                    existing_map[vsi_path] = True
                    created += 1
            except Exception as fe:
                failed += 1
                failed_files.append({'file': entry['basename'], 'error': str(fe)})
                logger.error(f"KNOWLEDGE_UPLOAD: Failed to save {entry['basename']}: {fe}")

        logger.info(f"KNOWLEDGE_UPLOAD: construct={callsign} user={user_email} created={created} updated={updated} skipped={skipped} failed={failed} total={len(file_entries)}")

        return jsonify({
            "success": True,
            "construct_id": callsign,
            "total_files": len(file_entries),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "failed_files": failed_files if failed_files else None,
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

        is_admin = False
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
            is_admin=is_admin,
        )
        materialized_row = _lookup_exact_vault_preview_row(
            filename=materialized_path,
            storage_path=materialized_path,
            construct_id=construct_id,
            user_id=user_id,
            is_admin=is_admin,
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
        user_role = 'user'

        row = VAULT_FILE_REPOSITORY.get_by_id(file_id)

        if not row:
            return jsonify({"success": False, "error": "File not found"}), 404

        effective_user_id = row.get('user_id')
        if user_role != 'admin':
            user_id = _get_authenticated_user_id()

            file_user_id = row.get('user_id')
            is_system = row.get('is_system', False)

            if file_user_id is None and not is_system:
                log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}", "denied", "unassigned_file")
                return jsonify({"success": False, "error": "Access denied"}), 403

            if file_user_id is not None and file_user_id != user_id:
                log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}", "denied", "not_owner")
                return jsonify({"success": False, "error": "Access denied"}), 403
            effective_user_id = user_id

        backing_row = _lookup_materialized_capsule_backing_row(
            row,
            user_id=effective_user_id,
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
    row = VAULT_FILE_REPOSITORY.get_by_id(file_id)
    if not row:
        return None, (jsonify({"success": False, "error": "File not found"}), 404)
    current_user = request.current_user
    user_email = current_user.get("email")
    if current_user.get("role", "user") != "admin":
        user_id = _get_authenticated_user_id()
        file_user_id = row.get("user_id")
        is_system = row.get("is_system", False)
        if file_user_id is None and not is_system:
            log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}/data-url", "denied", "unassigned_file")
            return None, (jsonify({"success": False, "error": "Access denied"}), 403)
        if file_user_id is not None and file_user_id != user_id:
            log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}/data-url", "denied", "not_owner")
            return None, (jsonify({"success": False, "error": "Access denied"}), 403)
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


@app.route('/api/vault/files/preview', methods=['POST'])
@require_auth
def preview_vault_file():
    """Build a fast preview payload from list-row metadata without refetching the file by id."""
    started_at = time.perf_counter()
    try:
        current_user = request.current_user
        user_email = current_user.get('email')
        user_role = 'user'
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get('filename') or payload.get('storage_path') or '').strip()
        storage_path = str(payload.get('storage_path') or filename).strip()
        file_type = str(payload.get('file_type') or '').strip()
        construct_id = str(payload.get('construct_id') or '').strip()
        resolve_body = bool(payload.get('resolve_body'))
        candidate_transcript_ids = payload.get('candidate_transcript_ids') or []

        if not filename:
            return jsonify({"success": False, "error": "filename is required"}), 400

        effective_user_id = payload.get('user_id')
        if user_role != 'admin':
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
    owner_rows = VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=_bare_name_from_callsign(callsign),
        user_id=owner_user_id,
        include_content=False,
    )
    if not owner_rows:
        return None, None, (jsonify({
            "success": False,
            "error": "CleanHouse instance is not canonical for this owner",
        }), 403)
    return owner_user_id, callsign, None


def require_cleanhouse_files_auth(f):
    """Accept a dedicated owner-scoped CleanHouse credential or legacy auth."""
    from functools import wraps

    legacy = require_chatty_auth(f)

    @wraps(f)
    def decorated_function(*args, **kwargs):
        credential = str(request.headers.get("X-CleanHouse-Key") or "").strip()
        if not credential:
            return legacy(*args, **kwargs)
        email = str(request.headers.get("X-Chatty-User") or "").strip().lower()
        try:
            callsign = cleanhouse_files_evidence.validate_instance_id(
                request.headers.get("X-CleanHouse-Instance") or "zen-001"
            )
        except cleanhouse_files_evidence.CleanHouseEvidenceError:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        user = db_get_user(email) if email else None
        user_id = str((user or {}).get("id") or "")
        valid = bool(user_id) and VAULT_FILE_REPOSITORY.verify_cleanhouse_files_credential(
            user_id=user_id,
            callsign=callsign,
            credential=credential,
        )
        if not valid:
            log_auth_decision(
                action="access_attempt",
                user_id=email or "cleanhouse",
                resource=request.path,
                result="denied",
                reason="invalid_cleanhouse_files_credential",
                ip=request.headers.get("X-Forwarded-For", request.remote_addr),
            )
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        request.current_user = user
        request.current_token = None
        log_auth_decision(
            action="access_granted",
            user_id=email,
            resource=request.path,
            result="allowed",
            reason="cleanhouse_files_credential",
            ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        )
        return f(*args, **kwargs)

    return decorated_function


def _cleanhouse_pairing_same_origin() -> bool:
    origin = str(request.headers.get("Origin") or "").strip().rstrip("/")
    expected = str(_resolve_backend_origin() or request.host_url).strip().rstrip("/")
    fetch_site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    return bool(origin and expected and origin == expected) and fetch_site in {"", "none", "same-origin"}


def require_cleanhouse_pairing_auth(f):
    """Authenticate pairing through Bearer auth or the same-origin auth cookie."""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # The pairing document contains no credential and is intentionally public.
        # Browser-local VVAULT sessions are bearer tokens in localStorage, so the
        # document must load before its same-origin script can attach that token to
        # the credential-issuing POST.  POST remains fail-closed below.
        if request.method == "GET":
            request.current_user = None
            request.current_token = None
            request.cleanhouse_pairing_auth = "public_form"
            return f(*args, **kwargs)

        session, token = get_current_user()
        auth_kind = "bearer"
        if not session:
            secret = str(os.environ.get("AUTH_SESSION_SECRET") or "").strip()
            cookie_name = str(os.environ.get("AUTH_COOKIE_NAME") or "auth_sid").strip()
            payload = verify_standalone_auth_session_token(
                str(request.cookies.get(cookie_name) or ""),
                secret,
            )
            if not payload:
                return jsonify({"success": False, "error": "Authentication required"}), 401
            email = str(payload.get("email") or "").strip().lower()
            display_name = str(payload.get("name") or email.split("@")[0]).strip()
            try:
                session = _ensure_vvault_user(email, display_name)
            except Exception:
                return _auth_repository_unavailable_response(request.path)
            auth_kind = "standalone_cookie"
            token = None

        if request.method == "POST" and auth_kind == "standalone_cookie" and not _cleanhouse_pairing_same_origin():
            return jsonify({"success": False, "error": "Same-origin pairing required"}), 403

        request.current_user = session
        request.current_token = token
        request.cleanhouse_pairing_auth = auth_kind
        return f(*args, **kwargs)

    return decorated_function


@app.route('/api/cleanhouse/files/pair', methods=['GET', 'POST'])
@require_cleanhouse_pairing_auth
def pair_cleanhouse_files_client():
    """Issue an owner-scoped credential encrypted to the requesting client."""
    if request.method == "GET":
        csrf_token = secrets.token_urlsafe(32)
        script_nonce = secrets.token_urlsafe(24)
        pairing_document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair CleanHouse</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; }
body { margin: 0; background: #101114; color: #f3f4f6; }
main { width: min(760px, calc(100% - 40px)); margin: 48px auto; }
form { display: grid; gap: 14px; }
textarea { min-height: 240px; resize: vertical; padding: 12px; color: inherit; background: #181b20; border: 1px solid #3a3f48; border-radius: 10px; }
button { width: fit-content; padding: 10px 16px; border: 0; border-radius: 999px; font-weight: 700; cursor: pointer; }
#pairing-result { min-height: 150px; }
.muted { color: #aeb4bf; }
</style></head>
<body><main><h1>Pair CleanHouse</h1>
<p class="muted">The credential is encrypted to this public key before it leaves VVAULT.</p>
<form id="cleanhouse-pairing-form" method="post" action="/api/cleanhouse/files/pair">
<input type="hidden" name="instance_id" value="zen-001">
<input type="hidden" name="csrf_token" value="__CSRF_TOKEN__">
<label for="public_key_pem">CleanHouse public key</label>
<textarea id="public_key_pem" name="public_key_pem" autocomplete="off" spellcheck="false" required></textarea>
<button type="submit">Pair CleanHouse</button>
</form>
<section id="pairing-output" hidden>
<h2>Encrypted pairing result</h2>
<p id="pairing-status" class="muted"></p>
<textarea id="pairing-result" readonly></textarea>
<button id="copy-pairing-result" type="button">Copy encrypted result</button>
</section>
<script nonce="__SCRIPT_NONCE__">
(() => {
  const form = document.getElementById('cleanhouse-pairing-form');
  const output = document.getElementById('pairing-output');
  const status = document.getElementById('pairing-status');
  const result = document.getElementById('pairing-result');
  const copy = document.getElementById('copy-pairing-result');

  const bearerToken = () => {
    try {
      const saved = JSON.parse(localStorage.getItem('vvault_user') || 'null');
      if (saved && saved.token) return String(saved.token);
    } catch (_) {}
    return String(localStorage.getItem('vvault_token') || '');
  };

  form.addEventListener('submit', async (event) => {
    const token = bearerToken();
    if (!token) return; // Preserve the CSRF-protected auth-cookie form path.
    event.preventDefault();
    output.hidden = false;
    status.textContent = 'Pairing…';
    result.value = '';
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({
          instance_id: form.elements.instance_id.value,
          public_key_pem: form.elements.public_key_pem.value
        })
      });
      const payload = await response.json();
      result.value = JSON.stringify(payload);
      status.textContent = response.ok ? 'CleanHouse is paired. Copy this encrypted result.' : (payload.error || 'Pairing failed.');
    } catch (_) {
      status.textContent = 'Pairing request failed.';
    }
  });

  copy.addEventListener('click', async () => {
    if (!result.value) return;
    await navigator.clipboard.writeText(result.value);
    copy.textContent = 'Copied';
  });
})();
</script></main></body></html>"""
        pairing_document = pairing_document.replace("__CSRF_TOKEN__", csrf_token).replace(
            "__SCRIPT_NONCE__",
            script_nonce,
        )
        response = app.response_class(
            pairing_document,
            mimetype="text/html",
        )
        response.set_cookie(
            "cleanhouse_pair_csrf",
            csrf_token,
            max_age=600,
            secure=True,
            httponly=True,
            samesite="Strict",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{script_nonce}'; "
            "connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    owner_user_id, callsign, error = _cleanhouse_files_owner_context()
    if error:
        return error
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Request body required"}), 400
    if getattr(request, "cleanhouse_pairing_auth", "") == "standalone_cookie":
        csrf_cookie = str(request.cookies.get("cleanhouse_pair_csrf") or "")
        csrf_form = str(payload.get("csrf_token") or "")
        if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_form):
            return jsonify({"success": False, "error": "Pairing CSRF validation failed"}), 403
    try:
        requested_callsign = cleanhouse_files_evidence.validate_instance_id(payload.get("instance_id"))
        if requested_callsign != callsign:
            raise cleanhouse_files_evidence.CleanHouseEvidenceError("CleanHouse instance mismatch")
        credential = cleanhouse_files_evidence.PAIRING_TOKEN_PREFIX + secrets.token_urlsafe(48)
        credential_sha256 = cleanhouse_files_evidence.pairing_token_hash(credential)
        encrypted = cleanhouse_files_evidence.encrypt_pairing_credential(
            credential,
            payload.get("public_key_pem"),
        )
        stored = VAULT_FILE_REPOSITORY.store_cleanhouse_files_credential_hash(
            user_id=str(owner_user_id),
            callsign=str(callsign),
            credential_sha256=credential_sha256,
        )
        _log_privileged_event(
            "secret_rotate",
            resource=f"cleanhouse-files:{callsign}",
            action=str(stored.get("action") or "updated"),
            result="success",
            description="Dedicated CleanHouse Files credential paired",
            metadata={
                "instance_id": callsign,
                "public_key_sha256": encrypted["public_key_sha256"],
                "plaintext_exported": False,
            },
            user_id=str(owner_user_id),
        )
        return jsonify({
            "success": True,
            "instance_id": callsign,
            "credential_type": "cleanhouse_files_pairing",
            **encrypted,
        })
    except cleanhouse_files_evidence.CleanHouseEvidenceError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("CLEANHOUSE_FILES: pairing failed (%s)", type(exc).__name__)
        return jsonify({"success": False, "error": "CleanHouse pairing failed"}), 503


@app.route('/api/cleanhouse/files/evidence', methods=['POST'])
@require_cleanhouse_files_auth
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
@require_cleanhouse_files_auth
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
@require_cleanhouse_files_auth
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
@require_cleanhouse_files_auth
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
    summaries = VAULT_FILE_REPOSITORY.list_for_browser(user_id=None, is_admin=True, requested_path=prefix)
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
@require_service_token
def get_identity_projection(construct_id):
    """Read projected identity field state for a construct."""
    try:
        snapshot = _read_identity_projection_snapshot(construct_id)
        return jsonify(snapshot)
    except Exception as e:
        logger.error(f"SERVICE_API: Error reading identity projection for {construct_id}: {e}")
        return jsonify({"success": False, "error": "Failed to read identity projection"}), 500


@app.route('/api/vault/constructs/<construct_id>/identity-projection/project', methods=['POST'])
@require_service_token
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

        result = _project_identity_fields(construct_id, fields, dry_run=dry_run)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"SERVICE_API: Error projecting identity fields for {construct_id}: {e}")
        return jsonify({"success": False, "error": "Failed to project identity fields", "error_code": type(e).__name__}), 503


@app.route('/api/chatty/session/exchange', methods=['POST'])
@require_chatty_auth
def chatty_session_exchange():
    """Mint or reuse a VVAULT bearer session for an authenticated Chatty user."""
    try:
        legacy_exchange_enabled = (os.environ.get("VVAULT_ENABLE_LEGACY_CHATTY_SESSION_EXCHANGE") or "").strip().lower()
        if legacy_exchange_enabled not in ("1", "true", "yes", "on"):
            return jsonify({
                "success": False,
                "error": "Legacy Chatty session exchange is disabled",
                "errorCode": "AUTH_BRIDGE_MISCONFIGURED",
            }), 403

        email = _get_current_user_email()
        if not email:
            return jsonify({"success": False, "error": "Authenticated Chatty user email required"}), 400

        supplied_name = request.headers.get('X-Chatty-Name') or request.headers.get('X-Chatty-User-Name')
        try:
            user_record = _ensure_vvault_user(email, supplied_name)
        except Exception:
            return _auth_repository_unavailable_response("/api/chatty/session/exchange")
        role = user_record.get('role') or 'user'

        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        try:
            db_create_session(email, role, session_token, expires_at, remember_me=True)
        except Exception:
            return _auth_repository_unavailable_response("/api/chatty/session/exchange")

        return jsonify({
            "success": True,
            "token": session_token,
            "expires_at": expires_at.isoformat(),
            "user": {
                "email": email,
                "name": user_record.get('name') or email.split('@')[0],
                "role": role,
            },
            "api_base_url": f"{_get_backend_url()}/api/vault",
        })
    except Exception as e:
        logger.error(f"SERVICE_API: Error exchanging Chatty session: {e}")
        return jsonify({"success": False, "error": "Failed to exchange Chatty session"}), 500


@app.route('/api/vault/constructs', methods=['GET'])
@require_auth
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
@require_auth
def get_construct_editor(construct_id):
    """Return the VVAULT-native editor payload for a construct."""
    try:
        callsign = _normalize_callsign(construct_id)
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        rows = _query_construct_identity_rows(callsign, user_id)
        if not rows:
            return jsonify({"success": False, "error": "Construct not found"}), 404

        return jsonify(_build_construct_editor_payload(callsign, user_id))
    except Exception as e:
        logger.error(f"CONSTRUCT_EDITOR_GET_ERROR: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/constructs/<construct_id>/editor', methods=['PUT'])
@require_auth
def update_construct_editor(construct_id):
    """Update construct editor fields and return the resolved editor DTO."""
    try:
        callsign = _normalize_callsign(construct_id)
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        payload = request.get_json(silent=True) or {}
        current_payload = _build_construct_editor_payload(callsign, user_id)
        now = datetime.now(timezone.utc).isoformat()
        display_name = (payload.get('displayName') or payload.get('name') or current_payload.get('displayName') or '').strip() or callsign
        full_name = (payload.get('fullName') or current_payload.get('fullName') or display_name).strip() or display_name
        description = payload.get('description') if 'description' in payload else current_payload.get('description')
        if isinstance(description, str):
            description = description.strip()
        else:
            description = ''
        instructions = payload.get('instructions') if 'instructions' in payload else current_payload.get('instructions')
        if not isinstance(instructions, str):
            instructions = ''
        conversation_starters = payload.get('conversationStarters') if 'conversationStarters' in payload else current_payload.get('conversationStarters')
        if not isinstance(conversation_starters, list):
            conversation_starters = []
        capabilities = _normalize_construct_capabilities(
            payload.get('capabilities') if 'capabilities' in payload else current_payload.get('capabilities')
        )
        memory_settings = _normalize_construct_memory_settings(
            payload.get('memory') if 'memory' in payload else current_payload.get('memory')
        )
        canon_refs = _normalize_construct_refs(
            payload.get('canonRefs') if 'canonRefs' in payload else current_payload.get('canonRefs')
        )
        knowledge_refs = _normalize_construct_refs(
            payload.get('knowledgeRefs') if 'knowledgeRefs' in payload else current_payload.get('knowledgeRefs')
        )
        models = _normalize_construct_models(
            payload.get('models') if 'models' in payload else current_payload.get('models')
        )
        orchestration_mode = payload.get('orchestration_mode') or payload.get('orchestrationMode') or "standard"
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
                source="vvault_construct_editor",
                created_at=created_at,
                updated_at=now,
                system_prompt=payload.get('system_prompt') or payload.get('systemPrompt') or instructions,
            ),
        }
        _upsert_construct_prompt_file(callsign, user_id, prompt_payload, source="vvault_construct_editor")

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
            source="vvault_construct_editor",
            created_at=created_at,
            updated_at=now,
        )
        _upsert_construct_metadata_file(callsign, user_id, metadata_payload, source="vvault_construct_editor")

        _project_identity_fields(callsign, {
            "conditioning": payload.get('conditioning') or '',
            "definition": payload.get('definition') or '',
            "physicalFeatures": payload.get('physicalFeatures') or '',
            "voice": _normalize_construct_voice_payload(payload.get('voice') or ''),
        }, dry_run=False)

        gender_content = json.dumps({
            "gender": payload.get('gender') or '',
        }, indent=2, ensure_ascii=False)
        _upsert_text_construct_file(callsign, user_id, 'gender.json', gender_content, {
            "contentType": "application/json",
        })

        avatar_data_url = payload.get('avatarDataUrl') or payload.get('avatar') or None
        if isinstance(avatar_data_url, str) and avatar_data_url.startswith('data:image/'):
            try:
                match = re.match(r'^data:image/[^;]+;base64,(.+)$', avatar_data_url)
                if match:
                    _upsert_binary_construct_file(callsign, user_id, 'avatar.png', match.group(1), {
                        "contentType": "image/png",
                        "mimeType": "image/png",
                    })
            except Exception as avatar_error:
                logger.warning(f"CONSTRUCT_EDITOR_AVATAR_UPDATE_WARN: {callsign}: {avatar_error}")

        return jsonify(_build_construct_editor_payload(callsign, user_id))
    except Exception as e:
        logger.error(f"CONSTRUCT_EDITOR_UPDATE_ERROR: {e}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/vault/constructs/<construct_id>/editor")
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# END SERVICE API ENDPOINTS
# ============================================================================

@app.route('/api/chatty/transcript/<construct_id>')
@require_chatty_auth
def get_chatty_transcript(construct_id):
    """Get chat transcript for a construct - used by Chatty integration
    
    Example: /api/chatty/transcript/zen-001
    Returns the chat_with_zen-001.md content from the vault
    """
    try:
        body_payload, body_status = chatty_body_service.transcript_body(construct_id).to_response()
        return jsonify(body_payload), body_status
        enforce_pocketverse_authority(construct_id, _pocketverse_request_context())
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()

        target = _resolve_chatty_transcript_target(
            construct_id,
            user_id=user_id,
            user_email=user_email,
            project_name=request.args.get('projectName'),
            root_path=request.args.get('rootPath'),
        )

        result_rows = _find_chatty_transcript_rows(target=target, user_id=user_id, columns='*')

        if result_rows:
            file_data = result_rows[0]
            return jsonify({
                "success": True,
                "construct_id": target['construct_id'],
                "filename": file_data.get('filename'),
                "storage_path": file_data.get('storage_path') or file_data.get('filename'),
                "content": file_data.get('content'),
                "sha256": file_data.get('sha256'),
                "updated_at": file_data.get('updated_at') or file_data.get('created_at'),
                "thread_id": target['thread_id'],
                "project_name": target.get('project_name'),
                "title": target['title'],
            })

        if construct_id == 'zen-001' or target.get('is_hydro_project_thread'):
            if not user_id:
                return jsonify({"success": False, "error": "User not found"}), 403

            initial_content = f"# {target['title']}\n\nTranscript started {datetime.now().isoformat()}\n"
            new_file_data = {
                'filename': target['storage_path'],
                'file_type': 'text/markdown',
                'content': initial_content,
                'is_system': False,
                'construct_id': target['construct_id'],
                'user_id': user_id,
                'storage_path': target['storage_path'],
                'metadata': json.dumps({
                    'construct_id': target['construct_id'],
                    'provider': 'chatty',
                    'canonical': True,
                    'sessionId': target['thread_id'],
                    'title': target['title'],
                    'projectName': target.get('project_name'),
                }),
            }
            transcript_result = _upsert_vault_file_record(new_file_data, context='chatty_transcript_hydrate')
            if not transcript_result.get('id'):
                return jsonify({"success": False, "error": f"Failed to hydrate transcript for {target['construct_id']}"}), 500

            return jsonify({
                "success": True,
                "construct_id": target['construct_id'],
                "filename": target['storage_path'],
                "storage_path": target['storage_path'],
                "content": initial_content,
                "created": True,
                "thread_id": target['thread_id'],
                "project_name": target.get('project_name'),
                "title": target['title'],
            })

        return jsonify({
            "success": False,
            "error": f"No chat transcript found for {construct_id}"
        }), 404
    except Exception as e:
        logger.error(f"Error fetching chatty transcript: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/chatty/transcript/<construct_id>', methods=['POST'])
@require_chatty_auth
def update_chatty_transcript(construct_id):
    """Update or create chat transcript for a construct - used by Chatty integration
    
    POST body: { "content": "full markdown content" }
    """
    try:
        data = request.get_json(silent=True) or {}
        body_payload, body_status = chatty_body_service.update_transcript_body(construct_id, data).to_response()
        return jsonify(body_payload), body_status
        if construct_id == 'zen-001' and _is_runtime_lock_active():
            return _runtime_lock_deferred_response(construct_id, 'transcript_update')
        enforce_pocketverse_authority(construct_id, _pocketverse_request_context())
        data = request.get_json()
        content = data.get('content', '')
        force = data.get('force', False)
        
        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400
        
        import hashlib
        sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        target = _resolve_chatty_transcript_target(
            construct_id,
            user_id=user_id,
            user_email=user_email,
            project_name=data.get('projectName'),
            root_path=data.get('rootPath'),
        )
        existing_rows = _find_chatty_transcript_rows(target=target, user_id=user_id, columns='id, user_id')
        
        if existing_rows:
            file_id = existing_rows[0]['id']
            
            protection = _protected_vault_update(
                file_id, content,
                force=force, context=f"update_chatty_transcript:{construct_id}"
            )
            
            if not protection["allowed"]:
                return jsonify({
                    "success": False,
                    "error": protection["error"],
                    "existing_length": protection["existing_length"],
                    "new_length": len(content)
                }), 409
            
            update_row = dict(existing_rows[0])
            update_row.update({
                'content': content,
                'sha256': sha256,
                'storage_path': update_row.get('storage_path') or update_row.get('filename') or target['storage_path'],
                'filename': update_row.get('filename') or update_row.get('storage_path') or target['storage_path'],
                'user_id': update_row.get('user_id') or user_id,
                'construct_id': target['construct_id'],
            })
            _upsert_vault_file_record(update_row, context='chatty_transcript_update')
            
            logger.info(f"CONTENT_UPDATE [update_chatty_transcript]: construct={construct_id} file_id={file_id} before={protection['existing_length']} after={len(content)}")
            
            return jsonify({
                "success": True,
                "action": "updated",
                "construct_id": target['construct_id'],
                "filename": target['storage_path'],
                "thread_id": target['thread_id'],
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {target['construct_id']}. Create it via migration first."
            }), 404
    except Exception as e:
        logger.error(f"Error updating chatty transcript: {e}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/chatty/transcript/<construct_id>")
        return jsonify({"success": False, "error": "Transcript update failed"}), 500

@app.route('/api/chatty/transcript/<construct_id>/message', methods=['POST'])
@require_chatty_auth
def append_chatty_message(construct_id):
    """Append a single message to a construct's transcript
    
    POST body: {
        "role": "user" | "assistant" | "system",
        "content": "message text",
        "timestamp": "2026-01-20T12:00:00Z" (optional, defaults to now),
        "attachments": [                       (optional)
            {
                "filename": "screenshot.png",
                "mime": "image/png",
                "sha256": "<hash>",
                "storagePath": "path/to/file"
            }
        ]
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        body_payload, body_status = chatty_body_service.append_transcript_message(construct_id, data).to_response()
        return jsonify(body_payload), body_status
        if construct_id == 'zen-001' and _is_runtime_lock_active():
            return _runtime_lock_deferred_response(construct_id, 'transcript_append')
        enforce_pocketverse_authority(construct_id, _pocketverse_request_context())
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        
        data = request.get_json()
        role = data.get('role', 'user')
        content = data.get('content', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        attachments = data.get('attachments', [])
        target = _resolve_chatty_transcript_target(
            construct_id,
            user_id=user_id,
            user_email=user_email,
            project_name=data.get('projectName'),
            root_path=data.get('rootPath'),
        )
        
        if not content and not attachments:
            return jsonify({"success": False, "error": "Content or attachments required"}), 400
        
        if role not in ['user', 'assistant', 'system']:
            return jsonify({"success": False, "error": "Role must be 'user', 'assistant', or 'system'"}), 400
        
        existing_rows = _find_chatty_transcript_rows(
            target=target,
            user_id=user_id,
            columns='id, content, filename, storage_path',
        )
        
        if not existing_rows:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {target['construct_id']}. Send a message first to create it."
            }), 404
        
        file_id = existing_rows[0]['id']
        current_content = existing_rows[0].get('content', '')
        actual_filename = existing_rows[0].get('filename') or existing_rows[0].get('storage_path') or target['storage_path']
        
        _backup_before_write(file_id, actual_filename, current_content)
        
        role_label = "**User**" if role == "user" else f"**{construct_id.split('-')[0].title()}**" if role == "assistant" else "**System**"
        
        attachment_block = ""
        if attachments:
            attachment_lines = []
            for att in attachments:
                fname = att.get('filename', 'unknown')
                mime = att.get('mime', 'application/octet-stream')
                att_sha = att.get('sha256', '')
                attachment_lines.append(f"- {fname} ({mime})")
                if att_sha:
                    attachment_lines.append(f"  - sha256: {att_sha}")
            attachment_block = "\U0001F4CE attachments:\n" + "\n".join(attachment_lines) + "\n\n"
        
        message_body = attachment_block + content
        formatted_message = f"\n\n---\n\n{role_label} ({timestamp}):\n\n{message_body}"
        
        updated_content = current_content + formatted_message
        
        import hashlib
        sha256 = hashlib.sha256(updated_content.encode('utf-8')).hexdigest()
        
        update_row = dict(existing_rows[0])
        update_row.update({
            'content': updated_content,
            'sha256': sha256,
            'filename': update_row.get('filename') or actual_filename,
            'storage_path': update_row.get('storage_path') or actual_filename,
            'user_id': update_row.get('user_id') or user_id,
            'construct_id': target['construct_id'],
        })
        _upsert_vault_file_record(update_row, context='chatty_transcript_append')
        
        attachment_count = len(attachments)
        logger.info(f"Appended {role} message to {construct_id} transcript (before={len(current_content)} after={len(updated_content)} attachments={attachment_count})")
        
        return jsonify({
            "success": True,
            "action": "appended",
            "construct_id": target['construct_id'],
            "filename": actual_filename,
            "thread_id": target['thread_id'],
            "role": role,
            "message_length": len(content),
            "attachment_count": attachment_count,
            "total_length": len(updated_content)
        })
        
    except Exception as e:
        logger.error(f"Error appending message to transcript: {e}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/chatty/transcript/<construct_id>/message")
        return jsonify({"success": False, "error": "Transcript append failed"}), 500

@app.route('/api/chatty/construct/<construct_id>/files')
@require_chatty_auth
def get_construct_files(construct_id):
    """List assets, documents, and identity files for a specific construct.

    Normalizes the incoming construct_id to callsign format and queries
    legacy remote using BOTH the callsign (e.g. 'katana-001') and the bare
    name (e.g. 'katana') to capture all files regardless of how their
    construct_id column was originally set.

    Returns file counts and listings for:
      - assets/  (images: png, jpg, jpeg, svg)
      - documents/  (all other files)
      - identity/  (prompt.json, capsules, config)

    Query params:
      - folder: optional filter ('assets', 'documents', 'identity')
    """
    try:
        body_payload, body_status = chatty_body_service.construct_files(construct_id, folder=request.args.get('folder')).to_response()
        return jsonify(body_payload), body_status
        if not legacy_remote_client:
            return jsonify({"success": False, "error": "legacy remote not configured"}), 500

        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()

        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 404

        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        folder_filter = request.args.get('folder')

        all_files = legacy_remote_client.table('vault_files').select(
            'id, filename, file_type, metadata, created_at, construct_id'
        ).or_(f'construct_id.eq.{callsign},construct_id.eq.{bare_name}').execute()

        assets = []
        documents = []
        identity = []

        for f in (all_files.data or []):
            fname = f.get('filename', '')
            entry = {
                "id": f.get('id'),
                "filename": fname.split('/')[-1],
                "path": fname,
                "file_type": f.get('file_type'),
                "created_at": f.get('created_at')
            }

            if '/assets/' in fname or fname.endswith(('.png', '.jpg', '.jpeg', '.svg')):
                assets.append(entry)
            elif '/documents/' in fname:
                documents.append(entry)
            elif '/identity/' in fname or fname.endswith('.capsule'):
                identity.append(entry)
            else:
                documents.append(entry)

        response = {
            "success": True,
            "construct_id": callsign,
            "counts": {
                "assets": len(assets),
                "documents": len(documents),
                "identity": len(identity)
            }
        }

        if not folder_filter or folder_filter == 'assets':
            response["assets"] = assets
        if not folder_filter or folder_filter == 'documents':
            response["documents"] = documents
        if not folder_filter or folder_filter == 'identity':
            response["identity"] = identity

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error fetching construct files for {construct_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chatty/construct/<construct_id>/identity')
@require_chatty_auth
def get_construct_identity(construct_id):
    """Return structured identity data for a construct.

    Searches legacy remote for canonical identity/config files first, then
    compatibility files (prompt.txt, personality.json, voice.md,
    definition.json) using both the callsign and bare name construct_id
    values.

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
    try:
        body_payload, body_status = chatty_body_service.identity(construct_id).to_response()
        return jsonify(body_payload), body_status
        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        display_name = bare_name.capitalize()

        supported_files = {
            'prompt.txt', 'prompt.json', 'personality.json', 'metadata.json',
            'CONTINUITY_GPT_PROMPT.md', 'conditioning.txt', 'definition.json',
            'definition.txt', 'voice.json', 'voice.md',
        }

        result = legacy_remote_client.table('vault_files').select(
            'filename, storage_path, content, file_type, created_at, updated_at'
        ).or_(
            f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
        ).not_.is_('content', 'null').execute()

        rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for row in _dedupe_vault_rows(result.data or []):
            basename = os.path.basename(row.get('filename', '') or row.get('storage_path', ''))
            if basename in supported_files:
                rows_by_name.setdefault(basename, []).append(row)

        source_rows = {
            name: _pick_latest_vault_row(rows)
            for name, rows in rows_by_name.items()
        }

        prompt_text = _load_vault_file_text(source_rows.get('prompt.txt'))
        prompt_json = _safe_json_loads(_load_vault_file_text(source_rows.get('prompt.json'))) or {}
        if not isinstance(prompt_json, dict):
            prompt_json = {}
        metadata_json = _safe_json_loads(_load_vault_file_text(source_rows.get('metadata.json'))) or {}
        if not isinstance(metadata_json, dict):
            metadata_json = {}
        personality = _safe_json_loads(_load_vault_file_text(source_rows.get('personality.json')))
        definition_json = _safe_json_loads(_load_vault_file_text(source_rows.get('definition.json'))) or {}
        if not isinstance(definition_json, dict):
            definition_json = {}
        definition_text = _load_vault_file_text(source_rows.get('definition.txt'))
        voice_json = _safe_json_loads(_load_vault_file_text(source_rows.get('voice.json'))) or {}
        if not isinstance(voice_json, dict):
            voice_json = {}
        voice_md = _load_vault_file_text(source_rows.get('voice.md'))
        conditioning = _load_vault_file_text(source_rows.get('conditioning.txt')).strip()

        name = _first_non_empty_string([
            prompt_json.get('displayName'),
            prompt_json.get('display_name'),
            prompt_json.get('name'),
            metadata_json.get('display_name'),
            metadata_json.get('instance_name'),
            display_name,
        ], default=display_name)
        description = _first_non_empty_string([
            prompt_json.get('description'),
            metadata_json.get('description'),
        ])
        instructions = _first_non_empty_string([
            prompt_json.get('instructions'),
            prompt_json.get('prompt'),
        ])
        system_prompt = _first_non_empty_string([
            prompt_json.get('system_prompt'),
            prompt_json.get('prompt'),
        ])
        conversation_starters = _first_non_empty_list([
            prompt_json.get('conversationStarters'),
            prompt_json.get('conversation_starters'),
        ])

        if prompt_text:
            lines = prompt_text.strip().split('\n')
            for line in lines:
                line_stripped = line.strip().strip('*')
                if line_stripped.startswith('You Are ') and name == display_name:
                    name = line_stripped.replace('You Are ', '').strip()
                elif (line_stripped.startswith('Helps ') or line_stripped.startswith('Description:')) and not description:
                    description = line_stripped.replace('Description:', '').strip()
            if not instructions:
                code_blocks = prompt_text.split('```')
                if len(code_blocks) >= 2:
                    instructions = code_blocks[1].strip()
                    if instructions.startswith('Instructions for'):
                        instructions = '\n'.join(instructions.split('\n')[1:]).strip()
            if not system_prompt:
                system_prompt = prompt_text.strip()

        continuity_prompt = _load_vault_file_text(source_rows.get('CONTINUITY_GPT_PROMPT.md')).strip()
        if continuity_prompt and not system_prompt:
            system_prompt = continuity_prompt

        definition = _first_non_empty_string([
            definition_json.get('instructions'),
            definition_json.get('prompt'),
            definition_text,
        ])
        voice = _first_non_empty_string([
            voice_md,
            voice_json.get('text'),
        ])
        full_name = _first_non_empty_string([
            prompt_json.get('fullName'),
            metadata_json.get('full_name'),
            name,
        ], default=name)
        capabilities = _normalize_construct_capabilities(
            prompt_json.get('capabilities') if isinstance(prompt_json.get('capabilities'), (dict, list)) else metadata_json.get('capabilities')
        )
        memory_settings = _normalize_construct_memory_settings(
            prompt_json.get('memory') if isinstance(prompt_json.get('memory'), (dict, bool)) else metadata_json.get('memory')
        )
        canon_refs = _normalize_construct_refs(
            prompt_json.get('canonRefs') if isinstance(prompt_json.get('canonRefs'), list) and prompt_json.get('canonRefs') else metadata_json.get('canon_refs')
        )
        knowledge_refs = _normalize_construct_refs(
            prompt_json.get('knowledgeRefs') if isinstance(prompt_json.get('knowledgeRefs'), list) and prompt_json.get('knowledgeRefs') else metadata_json.get('knowledge_refs')
        )

        enforcement = None
        enf_result = legacy_remote_client.table('vault_files').select(
            'content'
        ).eq('construct_id', callsign).eq('file_type', 'enforcement_config').not_.is_('content', 'null').execute()
        if enf_result.data:
            try:
                enforcement = json.loads(enf_result.data[0].get('content', '{}'))
            except json.JSONDecodeError:
                pass

        return jsonify({
            "success": True,
            "construct_id": callsign,
            "name": name,
            "displayName": name,
            "fullName": full_name,
            "description": description or f"Helps you with your life problems.",
            "instructions": instructions,
            "system_prompt": system_prompt,
            "conversation_starters": conversation_starters,
            "conversationStarters": conversation_starters,
            "conditioning": conditioning,
            "definition": definition,
            "voice": voice,
            "personality": personality,
            "capabilities": capabilities,
            "memory": memory_settings,
            "canonRefs": canon_refs,
            "knowledgeRefs": knowledge_refs,
            "enforcement": enforcement
        })

    except Exception as e:
        logger.error(f"Error fetching identity for {construct_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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


@app.route('/api/chatty/construct/create', methods=['POST'])
@require_chatty_auth
def create_construct():
    """Create a canonical VVAULT-native construct bundle in `vault_files`."""
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
            color_hex = request.form.get('color_hex', '#722F37')
            center_file = request.files.get('center_image')
            center_image_bytes = center_file.read() if center_file else None
            models = _parse_jsonish(request.form.get('models', ''), {})
            orchestration_mode = request.form.get('orchestration_mode', 'standard')
            system_prompt_override = request.form.get('system_prompt', '')
            avatar_b64 = request.form.get('avatar_base64', '')
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

        if not callsign or not name:
            return jsonify({"success": False, "error": "callsign and name are required"}), 400

        import re
        if not re.match(r'^[a-z]+-\d{3}$', callsign):
            return jsonify({"success": False, "error": f"Invalid callsign format '{callsign}'. Must be {{name}}-{{NNN}} (e.g., sera-001)"}), 400

        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        existing_identity_rows = VAULT_FILE_REPOSITORY.list_construct_identity_rows(
            callsign=callsign,
            bare_name=_bare_name_from_callsign(callsign),
            user_id=user_id,
        )
        if any(str(row.get('filename') or row.get('storage_path') or '').lower().endswith('/prompt.json') for row in existing_identity_rows):
            return jsonify({"success": False, "error": f"Construct {callsign} already exists (prompt.json found)"}), 409

        now = datetime.now(timezone.utc).isoformat()
        display_name = name
        full_name = full_name or display_name
        models = _normalize_construct_models(models)
        capabilities = _normalize_construct_capabilities(capabilities)
        memory_settings = _normalize_construct_memory_settings(memory_settings)
        canon_refs = _normalize_construct_refs(canon_refs)
        knowledge_refs = _normalize_construct_refs(knowledge_refs)
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
            source="vvault_construct_create",
            created_at=now,
            updated_at=now,
            system_prompt=system_prompt_override or instructions,
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
            source="vvault_construct_create",
            created_at=now,
            updated_at=now,
        )

        transcript_content = f"# Chat with {name}\n\nTranscript started {now}\n"

        avatar_created = False
        avatar_file_entry = None
        if avatar_b64:
            import base64 as b64mod_av
            try:
                avatar_bytes = b64mod_av.b64decode(avatar_b64)
                if len(avatar_bytes) > 5 * 1024 * 1024:
                    logger.warning(f"Avatar too large for {callsign}, skipping")
                else:
                    avatar_sha = hashlib.sha256(avatar_bytes).hexdigest()
                    avatar_meta = {
                        'construct_id': callsign,
                        'provider': 'vvault_construct_create',
                        'folder': 'identity',
                    }
                    avatar_vsi_path = f'instances/{callsign}/identity/avatar.png'
                    avatar_record = {
                        'filename': avatar_vsi_path,
                        'file_type': 'binary',
                        'content': avatar_b64,
                        'construct_id': callsign,
                        'user_id': user_id,
                        'is_system': False,
                        'sha256': avatar_sha,
                        'metadata': json.dumps(avatar_meta),
                        'storage_path': avatar_vsi_path,
                        'created_at': now,
                        'updated_at': now,
                    }
                    avatar_result = _upsert_vault_file_record(avatar_record, context='construct_avatar')
                    if avatar_result.get('id'):
                        avatar_created = True
                        avatar_file_entry = {
                            'id': avatar_result.get('id'),
                            'filename': avatar_vsi_path,
                            'file_type': 'binary',
                            'folder': 'identity',
                            'action': avatar_result.get('action'),
                        }
            except Exception as av_err:
                logger.warning(f"Avatar insert failed for {callsign}: {av_err}")

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
        try:
            prompt_result = _upsert_construct_prompt_file(
                callsign,
                user_id,
                prompt_obj,
                source="vvault_construct_create",
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

        projection_fields = {
            "conditioning": conditioning,
            "definition": definition,
            "voice": voice_payload,
        }
        if physical_features not in (None, "", [], {}):
            projection_fields["physicalFeatures"] = physical_features
        try:
            projection_result = _project_identity_fields(callsign, projection_fields, dry_run=False)
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
                source="vvault_construct_create",
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

        import base64 as b64mod
        glyph_b64 = b64mod.b64encode(glyph_bytes).decode('utf-8')
        glyph_filename = f"{callsign}_glyph.png"
        glyph_meta = {
            'construct_id': callsign,
            'provider': 'vvault_construct_create',
            'folder': 'identity',
            'glyph_number_rows': glyph_number_rows,
            'color_hex': color_hex,
        }
        glyph_vsi_path = f'instances/{callsign}/identity/{glyph_filename}'
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
                'filename': glyph_filename,
                'file_type': 'binary',
                'folder': 'identity',
                'action': glyph_result['action'],
            })
            glyph_created = True
        else:
            logger.warning(f"Glyph insert returned no data for {callsign}")

        if failed_files:
            logger.error(f"SCAFFOLD_PARTIAL_FAIL: callsign={callsign} created={len(created_files)} failed={len(failed_files)} user={user_email}")
        else:
            logger.info(f"CONSTRUCT_CREATED: callsign={callsign} name={name} files={len(created_files)} user={user_email}")

        response_data = {
            "success": len(created_files) > 0,
            "callsign": callsign,
            "name": name,
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
                    "definition.txt",
                    "voice.json",
                    glyph_filename,
                ] + (["physical_features.json"] if physical_features not in (None, "", [], {}) else []) + (["avatar.png"] if avatar_created else []),
                "config": ["metadata.json"],
                "chatty": [transcript_filename],
            },
            "message": f"Construct {callsign} created with {len(created_files)} canonical files"
        }
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
        return jsonify(response_data), 201

    except Exception as e:
        logger.error(f"Error creating construct: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/chatty/construct/create")
        return jsonify({
            "success": False,
            "error": "Construct creation failed",
            "error_code": type(e).__name__,
            "storage_mode": "vvault_body",
            "storage_owner": VAULT_FILE_OWNER,
        }), 503


@app.route('/api/chatty/constructs')
@require_chatty_auth
def get_chatty_constructs():
    """Get all available constructs with chat transcripts (user-scoped).

    Deduplicates bare-name vs callsign entries: if both 'katana' and
    'katana-001' transcripts exist, only 'katana-001' is returned.
    """
    try:
        body_payload, body_status = chatty_body_service.list_constructs().to_response()
        return jsonify(body_payload), body_status
        read_allowed, read_state = LEGACY_REMOTE_STEWARD.allow_read()
        if not read_allowed:
            return _vvault_read_block_response("/api/chatty/constructs", state=read_state)

        if not legacy_remote_client:
            return _vvault_unavailable_response(
                "legacy remote is not configured for this backend. Construct transcripts are temporarily unavailable.",
                include_constructs=True,
            )

        current_user = request.current_user
        user_email = current_user.get('email')
        user_role = 'user'
        is_admin = user_role == 'admin'

        if is_admin:
            rows = _fetch_all_rows(
                lambda: legacy_remote_client.table('vault_files').select('filename, metadata, created_at').ilike('filename', '%chat_with_%')
            )
        else:
            user_id = _get_authenticated_user_id()

            if not user_id:
                return jsonify({
                    "success": True,
                    "legacy_remote_available": True,
                    "degraded": False,
                    "canonical": True,
                    "storage_mode": "legacy_remote",
                    "connection_state": LEGACY_REMOTE_STEWARD.snapshot().get("connection_state"),
                    "constructs": [],
                    "count": 0,
                })

            rows = _fetch_all_rows(
                lambda: legacy_remote_client.table('vault_files')
                .select('filename, metadata, created_at')
                .eq('user_id', user_id)
                .ilike('filename', '%chat_with_%')
            )

        special_roles = {
            'lin-001': {'role': 'undertone', 'context': 'gpt_creator_create_tab', 'is_system': True}
        }

        seen = {}
        for file in rows:
            filename = file.get('filename', '')
            basename = filename.split('/')[-1] if '/' in filename else filename
            if not (basename.startswith('chat_with_') and basename.endswith('.md')):
                continue
            raw_id = basename.replace('chat_with_', '').replace('.md', '')
            callsign = _normalize_callsign(raw_id)
            bare_name = _bare_name_from_callsign(callsign)
            display_name = bare_name.capitalize()

            if callsign in seen:
                existing = seen[callsign]
                if existing.get('created_at', '') < file.get('created_at', ''):
                    existing['created_at'] = file.get('created_at')
                continue

            construct_data = {
                "construct_id": callsign,
                "name": display_name,
                "filename": f"chat_with_{callsign}.md",
                "created_at": file.get('created_at')
            }
            if callsign in special_roles:
                construct_data.update(special_roles[callsign])
            seen[callsign] = construct_data

        constructs = list(seen.values())
        if not any(c.get('construct_id') == 'zen-001' for c in constructs):
            constructs.append({
                "construct_id": "zen-001",
                "name": "Zen",
                "filename": "chat_with_zen-001.md",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        return jsonify({
            "success": True,
            "legacy_remote_available": True,
            "degraded": False,
            "canonical": True,
            "storage_mode": "legacy_remote",
            "connection_state": LEGACY_REMOTE_STEWARD.snapshot().get("connection_state"),
            "constructs": constructs,
            "count": len(constructs)
        })
    except Exception as e:
        logger.error(f"Error fetching chatty constructs: {e}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_read_response(
                "/api/chatty/constructs",
                include_constructs=True,
            )
        return jsonify({"success": False, "error": "Failed to load constructs"}), 500


@app.route('/api/chatty/message', methods=['POST'])
@require_chatty_auth
def chatty_message():
    """Handle a message to a construct and return LLM response
    
    POST body: {
        "constructId": "zen-001",
        "message": "user message text",
        "userName": "Devon" (optional, defaults to "User"),
        "timezone": "EST" (optional, defaults to "EST")
    }
    
    This endpoint:
    1. Loads construct identity/system prompt
    2. Calls Ollama for LLM inference
    3. Appends both user and assistant messages to transcript
    4. Returns the assistant response
    """
    try:
        data = request.get_json(silent=True) or {}
        body_payload, body_status = chatty_body_service.message(data.get('constructId'), data).to_response()
        return jsonify(body_payload), body_status
        
        # Parse JSON with error handling
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid or missing JSON body"}), 400
        
        construct_id = data.get('constructId')
        user_message = data.get('message', '')
        user_name = data.get('userName', 'User')
        timezone = data.get('timezone', 'EST')
        project_name = data.get('projectName')
        root_path = data.get('rootPath')
        
        if not construct_id:
            return jsonify({"success": False, "error": "constructId is required"}), 400
        if not user_message:
            return jsonify({"success": False, "error": "message is required"}), 400
        if construct_id == 'zen-001' and _is_runtime_lock_active():
            return _runtime_lock_deferred_response(construct_id, 'message')
        
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()

        construct_name = construct_id.split('-')[0].title()

        system_prompt = _load_construct_identity(construct_id, construct_name)

        try:
            ollama_response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': 'phi3:latest',
                    'prompt': user_message,
                    'system': system_prompt,
                    'stream': False
                },
                timeout=60
            )

            if not ollama_response.ok:
                logger.error(f"Ollama returned {ollama_response.status_code}: {ollama_response.text[:200]}")
                return jsonify({
                    "success": False,
                    "error": f"LLM inference failed with status {ollama_response.status_code}"
                }), 503

            ollama_data = ollama_response.json()
            assistant_response = ollama_data.get('response')
            if not assistant_response:
                return jsonify({
                    "success": False,
                    "error": "LLM returned empty response"
                }), 503

        except requests.RequestException as e:
            logger.error(f"Ollama error: {e}")
            return jsonify({
                "success": False,
                "error": "LLM inference failed. Is Ollama running?"
            }), 503

        from datetime import timezone as tz
        now_utc = datetime.now(tz.utc)
        iso_timestamp = now_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now_utc.microsecond // 1000:03d}Z'

        est_offset = timedelta(hours=-5)
        now_est = now_utc + est_offset
        human_time = now_est.strftime('%I:%M:%S %p').lstrip('0')
        date_header = now_est.strftime('%B %d, %Y')

        file_id = None
        current_content = ''
        target = _resolve_chatty_transcript_target(
            construct_id,
            user_id=user_id,
            user_email=user_email,
            project_name=project_name,
            root_path=root_path,
        )

        if user_id:
            expected_filepath = target['storage_path']
            existing_rows = _find_chatty_transcript_rows(
                target=target,
                user_id=user_id,
                columns='id, content, filename, storage_path',
            )
        else:
            callsign = _normalize_callsign(construct_id)
            bare = _bare_name_from_callsign(callsign)
            expected_filepath = target['storage_path']
            existing = legacy_remote_client.table('vault_files').select('id, content, filename, storage_path').or_(f'construct_id.eq.{callsign},construct_id.eq.{bare}').ilike('filename', f"%{target['filename']}%").execute()
            existing_rows = existing.data or []
            logger.info(f"[Message] Service call for {construct_id} (user {user_email} not in users table), querying by construct_id")

        if existing_rows:
            file_id = existing_rows[0]['id']
            current_content = existing_rows[0].get('content', '')
            actual_transcript_filename = existing_rows[0].get('filename') or existing_rows[0].get('storage_path') or target['storage_path']
        else:
            actual_transcript_filename = target['storage_path']
            new_file_data = {
                'filename': expected_filepath,
                'file_type': 'text/markdown',
                'content': f"# {target['title']}\n\nTranscript started {datetime.now().isoformat()}\n",
                'is_system': False,
                'construct_id': target['construct_id'],
                'metadata': json.dumps({
                    'construct_id': target['construct_id'],
                    'provider': 'chatty',
                    'sessionId': target['thread_id'],
                    'title': target['title'],
                    'projectName': target.get('project_name'),
                })
            }
            if user_id:
                new_file_data['user_id'] = user_id
            new_file_data['storage_path'] = expected_filepath
            transcript_result = _upsert_vault_file_record(new_file_data, context='chatty_transcript_create')
            if transcript_result.get('id'):
                file_id = transcript_result['id']
                current_content = new_file_data['content']
                logger.info(f"Created new transcript at {expected_filepath}")
            else:
                return jsonify({
                    "success": False,
                    "error": f"Failed to create transcript for {construct_id}"
                }), 500
        
        # Check if we need a new date header
        new_content = current_content
        if f"## {date_header}" not in current_content:
            new_content += f"\n\n## {date_header}\n"
        
        # Format and append user message
        user_formatted = f"\n**{human_time} {timezone} - {user_name}** [{iso_timestamp}]: {user_message}\n"
        new_content += user_formatted
        
        # Format and append assistant message (use UTC for consistency)
        now_response_utc = datetime.now(tz.utc)
        iso_timestamp_response = now_response_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now_response_utc.microsecond // 1000:03d}Z'
        now_response_est = now_response_utc + est_offset
        human_time_response = now_response_est.strftime('%I:%M:%S %p').lstrip('0')
        
        assistant_formatted = f"\n**{human_time_response} {timezone} - {construct_name}** [{iso_timestamp_response}]: {assistant_response}\n"
        new_content += assistant_formatted
        
        # Backup before updating transcript in legacy remote
        _backup_before_write(file_id, actual_transcript_filename, current_content)
        
        # Update transcript in legacy remote
        sha256 = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
        update_data = {
            'content': new_content,
            'sha256': sha256,
        }
        legacy_remote_client.table('vault_files').update(update_data).eq('id', file_id).execute()
        
        logger.info(f"Message exchange with {construct_id}: user sent {len(user_message)} chars, got {len(assistant_response)} chars (before={len(current_content)} after={len(new_content)})")
        
        return jsonify({
            "success": True,
            "response": assistant_response,
            "constructId": target['construct_id'],
            "constructName": construct_name,
            "timestamp": iso_timestamp,
            "thread_id": target['thread_id'],
            "filename": actual_transcript_filename,
            "project_name": target.get('project_name'),
        })
        
    except Exception as e:
        logger.error(f"Error in chatty message: {e}")
        if _is_dependency_timeout(e):
            return _dependency_timeout_write_response("/api/chatty/message")
        return jsonify({"success": False, "error": "Chatty message failed"}), 500


def _load_construct_identity(construct_id: str, construct_name: str) -> str:
    """Load the system prompt for a construct from its identity files.

    Searches both callsign and bare name in legacy remote to handle the
    construct_id column inconsistency (some files use 'katana', others
    use 'katana-001').
    """
    try:
        prompt_path = os.path.join(PROJECT_DIR, 'instances', construct_id, 'identity', 'prompt.json')
        if os.path.exists(prompt_path):
            with open(prompt_path, 'r') as f:
                prompt_data = json.load(f)
                return prompt_data.get('system_prompt', '') or prompt_data.get('prompt', '')

        if legacy_remote_client:
            callsign = _normalize_callsign(construct_id)
            bare_name = _bare_name_from_callsign(callsign)

            result = legacy_remote_client.table('vault_files').select('content, filename, storage_path, created_at, updated_at').or_(
                f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
            ).not_.is_('content', 'null').execute()

            for f in _dedupe_vault_rows(result.data or []):
                content = f.get('content', '') or ''
                fname = f.get('filename', '')
                basename = os.path.basename(fname)
                if not content:
                    continue

                if basename == 'prompt.json':
                    try:
                        prompt_data = json.loads(content)
                        prompt = prompt_data.get('system_prompt', '') or prompt_data.get('instructions', '') or prompt_data.get('prompt', '')
                        if prompt:
                            return prompt
                    except json.JSONDecodeError:
                        pass

                elif basename in ('prompt.txt', 'CONTINUITY_GPT_PROMPT.md'):
                    if content.strip():
                        return content.strip()

        return f"You are {construct_name}, an AI assistant. Be helpful, concise, and friendly."
    except Exception as e:
        logger.warning(f"Could not load identity for {construct_id}: {e}")
        return f"You are {construct_name}, an AI assistant. Be helpful, concise, and friendly."


# Vault Backup API
@app.route('/api/vault/backups')
@require_role('admin')
def list_vault_backups():
    """List available local vault_files backups - admin only"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return jsonify({"success": True, "backups": [], "count": 0})
        
        backups = []
        for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                stat = os.stat(fpath)
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                backups.append({
                    "backup_file": fname,
                    "file_id": data.get("file_id"),
                    "filename": data.get("filename"),
                    "content_length": len(data.get("content", "")),
                    "backed_up_at": data.get("backed_up_at"),
                    "size_bytes": stat.st_size
                })
            except Exception as e:
                logger.warning(f"Could not read backup {fname}: {e}")
                continue
        
        return jsonify({
            "success": True,
            "backups": backups,
            "count": len(backups)
        })
    except Exception as e:
        logger.error(f"Error listing vault backups: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/vault/backups/<file_id>')
@require_role('admin')
def get_vault_backups_for_file(file_id):
    """Retrieve backup content for a specific file_id - admin only"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return jsonify({"success": True, "backups": [], "count": 0})
        
        backups = []
        for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not fname.endswith('.json'):
                continue
            if not fname.startswith(file_id.replace('/', '_').replace('\\', '_')):
                continue
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                backups.append({
                    "backup_file": fname,
                    "file_id": data.get("file_id"),
                    "filename": data.get("filename"),
                    "content": data.get("content"),
                    "content_length": len(data.get("content", "")),
                    "backed_up_at": data.get("backed_up_at")
                })
            except Exception as e:
                logger.warning(f"Could not read backup {fname}: {e}")
                continue
        
        if not backups:
            return jsonify({
                "success": False,
                "error": f"No backups found for file_id: {file_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "file_id": file_id,
            "backups": backups,
            "count": len(backups)
        })
    except Exception as e:
        logger.error(f"Error retrieving vault backups for {file_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
            "active_sessions": len(ACTIVE_SESSIONS)
        }
    })

# Legal document routes
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

def _credential_login_unavailable_message(auth_provider: Optional[str]) -> str:
    """Parity with @quantum/auth — OAuth-only accounts should not get a generic invalid-password dead end."""
    p = (auth_provider or '').strip().lower()
    if p == 'google':
        return 'This account uses Google sign-in. Use the Google button to continue.'
    if p == 'github':
        return 'This account uses GitHub sign-in. Use the GitHub button to continue.'
    return 'This account does not use email and password. Sign in with your linked sign-in provider.'


def _life_registry_match_vvault_message_chatty_credentials() -> str:
    return (
        'This account uses LIFE email sign-in (Chatty/Code). Sign in there, or complete VVAULT sign-up to add a vault password.'
    )


def _life_registry_match_vvault_message_generic() -> str:
    return (
        'This email was found in the LIFE Technology user registry. Finish VVAULT sign-up below and your account will be connected.'
    )


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
        user_data = db_get_user(email)
        
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
    try:
        body_payload, body_status = chatty_body_service.memories(construct_id).to_response()
        return jsonify(body_payload), body_status
        
        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        query = request.args.get('q', '')
        limit = int(request.args.get('limit', '10'))
        include_boundaries = request.args.get('include_boundaries', 'true').lower() == 'true'
        output_format = request.args.get('format', 'rich')
        is_chrono = _is_chronological_query(query) if query else False
        
        query_terms = _clean_query(query) if query else []
        
        ledger_sessions = None
        try:
            ledger_result = legacy_remote_client.table('vault_files').select(
                'content'
            ).eq('filename', f'{callsign}_continuity_ledger.json').eq(
                'construct_id', callsign
            ).execute()
            if ledger_result.data and ledger_result.data[0].get('content'):
                ledger_sessions = json.loads(ledger_result.data[0]['content'])
                logger.info(f"[Memory API] Using stored ledger for {callsign}: {len(ledger_sessions)} sessions")
        except Exception as ledger_err:
            logger.debug(f"[Memory API] No ledger available for {callsign}, using raw transcripts: {ledger_err}")
        
        transcript_files = _get_transcript_files(callsign, bare_name)
        
        if not transcript_files:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "memories": [],
                "total_pairs": 0,
                "transcript_files": 0,
                "chronological": is_chrono,
                "query_terms": query_terms
            })
        
        transcript_files.sort(key=lambda f: len(f.get('content', '')))
        
        all_pairs = []
        file_sources = {}
        total_files = len(transcript_files)
        
        for file_idx, tf in enumerate(transcript_files):
            content = tf.get('content', '')
            fname = tf.get('filename', '')
            source_label = _detect_source_label(fname)
            
            pairs = _parse_transcript_pairs(content, callsign)
            
            if len(pairs) > MAX_PAIRS_PER_FILE:
                keep_start = pairs[:10]
                keep_end = pairs[-10:]
                middle = pairs[10:-10]
                step = max(1, len(middle) // (MAX_PAIRS_PER_FILE - 20))
                keep_middle = [middle[i] for i in range(0, len(middle), step)]
                pairs = keep_start + keep_middle + keep_end
            
            for p in pairs:
                p['source'] = source_label
                p['file_index'] = file_idx
                file_sources[file_idx] = source_label
            all_pairs.extend(pairs)
        
        for i, p in enumerate(all_pairs):
            p['index'] = i
        
        total_pairs = len(all_pairs)
        logger.info(f"[Memory API] {callsign}: {total_pairs} pairs from {total_files} files, query_terms={query_terms}")
        
        memories = []
        
        if include_boundaries or is_chrono:
            if total_pairs > 0:
                first = all_pairs[0].copy()
                first['tag'] = 'first_exchange'
                first['score'] = 100.0
                memories.append(first)
                
                if total_pairs > 1:
                    last = all_pairs[-1].copy()
                    last['tag'] = 'last_exchange'
                    last['score'] = 99.0
                    memories.append(last)
        
        if query:
            scored = []
            boundary_indices = {0, total_pairs - 1} if include_boundaries else set()
            for pair in all_pairs:
                if pair['index'] in boundary_indices:
                    continue
                pair_copy = pair.copy()
                pair_copy['score'] = _score_memory_pair(
                    pair, query, query_terms, total_pairs,
                    pair.get('file_index', 0), total_files
                )
                pair_copy['tag'] = None
                scored.append(pair_copy)
            scored.sort(key=lambda x: x['score'], reverse=True)
            remaining = limit - len(memories)
            memories.extend(scored[:max(0, remaining)])
        elif not is_chrono:
            step = max(1, total_pairs // limit) if total_pairs > limit else 1
            boundary_indices = {0, total_pairs - 1} if include_boundaries else set()
            sampled = [p for i, p in enumerate(all_pairs) if i not in boundary_indices and i % step == 0]
            remaining = limit - len(memories)
            for p in sampled[:max(0, remaining)]:
                p_copy = p.copy()
                p_copy['score'] = 1.0
                p_copy['tag'] = None
                memories.append(p_copy)
        
        if output_format == 'rich':
            for mem in memories:
                combined_text = mem.get('user', '') + ' ' + mem.get('construct', '')
                mem['tone'] = _detect_tone(combined_text)
                
                idx = mem.get('index', 0)
                if idx < total_pairs * 0.15:
                    mem['position'] = 'early'
                elif idx > total_pairs * 0.85:
                    mem['position'] = 'recent'
                else:
                    mem['position'] = 'middle'
                
                source = mem.get('source', 'Conversation')
                tag = mem.get('tag')
                position = mem.get('position', 'middle')
                if tag == 'first_exchange':
                    mem['context_hint'] = f'From your earliest conversations on {source}'
                elif tag == 'last_exchange':
                    mem['context_hint'] = f'From your most recent exchange on {source}'
                elif position == 'early':
                    mem['context_hint'] = f'From early in your {source} conversations'
                elif position == 'recent':
                    mem['context_hint'] = f'From a recent {source} conversation'
                else:
                    mem['context_hint'] = f'From a {source} conversation'
                
                mem.pop('file_index', None)
                
                if ledger_sessions:
                    _enrich_memory_from_ledger(mem, ledger_sessions)
        else:
            for mem in memories:
                mem.pop('file_index', None)
                mem.pop('source', None)
        
        response_data = {
            "success": True,
            "construct_id": callsign,
            "memories": memories,
            "total_pairs": total_pairs,
            "transcript_files": total_files,
            "chronological": is_chrono,
            "query_terms": query_terms,
            "ledger_available": ledger_sessions is not None,
        }
        
        if ledger_sessions and output_format == 'rich':
            all_hooks = []
            seen_hook_types = set()
            for session in ledger_sessions:
                for hook in session.get('continuity_hooks', []):
                    if hook.get('type') not in seen_hook_types:
                        all_hooks.append(hook)
                        seen_hook_types.add(hook.get('type'))
            response_data['continuity_hooks'] = all_hooks[:10]
            
            dates = [s.get('estimated_date', '') for s in ledger_sessions if s.get('estimated_date')]
            if dates:
                response_data['date_range'] = {'earliest': min(dates), 'latest': max(dates)}
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"[Memory API] Error for {construct_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Continuity Ledger API ───────────────────────────────────────────────────

def _get_transcript_files(callsign: str, bare_name: str) -> List[Dict]:
    """Fetch transcript files from VVAULT-native vault_files for a construct."""
    rows = VAULT_FILE_REPOSITORY.list_construct_file_rows(
        callsign=callsign,
        bare_name=bare_name,
        user_id=None,
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
        bare_name = _bare_name_from_callsign(callsign)
        include_exchanges = request.args.get('include_exchanges', 'false').lower() == 'true'
        output_format = request.args.get('format', 'json')
        user_id = _get_authenticated_user_id() or _resolve_construct_owner_user_id(callsign)
        if not user_id:
            return jsonify({"success": False, "error": "Construct owner not found"}), 403

        transcript_files = _get_transcript_files(callsign, bare_name)
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
        output_format = request.args.get('format', 'json')

        if output_format == 'markdown':
            ledger_filename = f'{callsign}_continuity_ledger.md'
        else:
            ledger_filename = f'{callsign}_continuity_ledger.json'

        row = VAULT_FILE_REPOSITORY.find_exact(
            filename=ledger_filename,
            storage_path=ledger_filename,
            construct_id=callsign,
            user_id=None,
            is_admin=True,
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


def _start_enrollment_session(user: dict, frontend: str):
    from flask import redirect
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    user_id = str(user.get("id") or "")
    device_secret = _device_secret_from_request() or identity_crypto.opaque_token()
    token = identity_crypto.opaque_token()
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
        session = AUTH_REPOSITORY.issue_pending_device_session(**args)
    else:
        args = dict(user_id=user_id, device_secret_digest=identity_crypto.keyed_digest(device_secret, _identity_hmac_key()), token_hash=token_hash, expires_at=datetime.now(timezone.utc) + timedelta(minutes=20), ip_hash=identity_crypto.keyed_digest(str(request.remote_addr or ""), _identity_hmac_key()), user_agent_hash=identity_crypto.keyed_digest(str(request.headers.get("User-Agent") or ""), _identity_hmac_key()), label=request.headers.get("User-Agent", "")[:120])
        session = AUTH_REPOSITORY.create_pending_enrollment_session(**args) if state == "PENDING_ENROLLMENT" else AUTH_REPOSITORY.issue_pending_device_session(**args)
    if not session:
        raise RuntimeError("cannot issue enrollment session")
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


@app.route('/api/auth/enrollment/status', methods=['GET'])
def canonical_enrollment_status():
    """Expose only the current browser enrollment state, never identity data."""
    pending = _enrollment_session_from_request()
    if not pending:
        return jsonify({"success": False, "pending": False}), 401
    return _enrollment_response({
        "success": True,
        "pending": True,
        "session_kind": pending.get("enrollment_session_kind"),
        "account_state": pending.get("account_state"),
        "device_status": pending.get("device_status"),
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
    return _enrollment_response({"success": True, "account_state": "ACTIVE"}, normal_token=token)


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


def _begin_identity_oauth(provider: str, purpose: str = "signin", current: dict | None = None):
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
        return redirect(f"{config['authorization_endpoint']}?{urlencode(params)}")
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
            user, _created = AUTH_REPOSITORY.admit_verified_identity(
                provider=provider, provider_subject=subject, verified_email=email,
                name=name, issuer=issuer,
                allow_legacy_compatibility=(provider == "google"),
            )
            return _start_enrollment_session(user, frontend)
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


def _deliver_magic_link(_email: str, _url: str) -> bool:
    """Delivery boundary. A deployment supplies an audited mail adapter; never log URL."""
    return False


@app.route('/api/auth/email-magic-links', methods=['POST'])
def request_email_magic_link():
    try:
        from vvault.server import vvault_auth_crypto as identity_crypto
    except ImportError:
        import vvault_auth_crypto as identity_crypto
    # Always return the same accepted response, including malformed input and
    # unavailable delivery, so email existence is never disclosed.
    try:
        if not _rate_limit_key("auth"):
            email = identity_crypto.normalize_email(str((request.get_json(silent=True) or {}).get("email") or ""))
            token = identity_crypto.opaque_token()
            frontend = _get_frontend_url()
            AUTH_REPOSITORY.issue_magic_link_challenge(token_digest=identity_crypto.keyed_digest(token, _identity_hmac_key()), normalized_email=email,
                purpose="signin", redirect_uri=frontend, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
            _deliver_magic_link(email, f"{frontend}/#magic_link={token}")
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


# Google OAuth Routes
@app.route('/api/auth/legacy-google-disabled')
def legacy_google_oauth_login_disabled():
    """Initiate Google OAuth login"""
    return jsonify({"success": False, "error": "Legacy OAuth entry has been retired"}), 410

    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    try:
        from flask import redirect

        if not google_client or not _google_oauth_ready():
            return jsonify({"success": False, "error": _google_oauth_config_error()}), 500

        origin = request.headers.get('Origin', '')
        referer = request.headers.get('Referer', '')
        fwd_host = request.headers.get('X-Forwarded-Host', '')
        req_host = request.headers.get('Host', request.host)
        logger.info(f"OAuth login headers - Origin: {origin}, Referer: {referer}, X-Forwarded-Host: {fwd_host}, Host: {req_host}")

        callback_url = f"{_get_backend_url()}/api/auth/google/callback"

        frontend_origin = origin or ""
        if not frontend_origin and referer:
            frontend_origin = referer.split('/api/auth/google')[0].rstrip('/')
        if not frontend_origin:
            frontend_origin = _get_frontend_url()
        if not _allowed_redirect_base(frontend_origin):
            frontend_origin = _get_frontend_url()

        auth_available, auth_state = _oauth_identity_authority_available()
        if not auth_available:
            return _oauth_identity_authority_redirect(frontend_origin, auth_state)

        # Get Google's OAuth endpoints after identity authority is proven.
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]

        from flask import session as flask_session
        flask_session['oauth_callback_url'] = callback_url
        flask_session['oauth_frontend_url'] = frontend_origin

        # Prepare the OAuth request
        request_uri = google_client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=callback_url,
            scope=["openid", "email", "profile"],
            prompt="select_account",
        )
        
        logger.info(f"Redirecting to Google OAuth with callback: {callback_url}")
        return redirect(request_uri)
        
    except Exception as e:
        logger.error(f"Google OAuth init error: {e}")
        return jsonify({"success": False, "error": "OAuth initialization failed"}), 500

@app.route('/api/auth/legacy-google-callback-disabled')
def legacy_google_oauth_callback_disabled():
    """Handle Google OAuth callback"""
    return jsonify({"success": False, "error": "Legacy OAuth callback has been retired"}), 410

    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    try:
        from flask import redirect
        from urllib.parse import quote

        if not google_client or not _google_oauth_ready():
            return jsonify({"success": False, "error": _google_oauth_config_error()}), 500
        
        # Get the authorization code from Google
        code = request.args.get("code")
        if not code:
            error = request.args.get("error", "Unknown error")
            error_desc = request.args.get("error_description", "")
            logger.error(f"OAuth error: {error} - {error_desc}")
            return jsonify({"success": False, "error": f"OAuth failed: {error} - {error_desc}"}), 400
        
        from flask import session as flask_session
        stored_callback = flask_session.pop('oauth_callback_url', None)
        stored_frontend = flask_session.pop('oauth_frontend_url', None)
        
        if stored_callback:
            callback_url = stored_callback
        else:
            callback_url = f"{_get_backend_url()}/api/auth/google/callback"
        
        from urllib.parse import urlparse
        parsed = urlparse(callback_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        authorization_response = f"{base}{request.full_path}"
        
        oauth_origin_base = base
        candidate_frontend = stored_frontend or oauth_origin_base
        frontend_url = _get_frontend_url(candidate_frontend) if _allowed_redirect_base(candidate_frontend) else _get_frontend_url()

        auth_available, auth_state = _oauth_identity_authority_available()
        if not auth_available:
            return _oauth_identity_authority_redirect(frontend_url, auth_state)

        # Get Google's OAuth endpoints after identity authority is proven.
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        token_endpoint = google_provider_cfg["token_endpoint"]
        
        logger.info(f"Processing OAuth callback with redirect_url: {callback_url}")
        
        # Exchange authorization code for tokens
        token_url, headers, body = google_client.prepare_token_request(
            token_endpoint,
            authorization_response=authorization_response,
            redirect_url=callback_url,
            code=code,
        )
        
        token_response = requests.post(
            token_url,
            headers=headers,
            data=body,
            auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
        )

        if not token_response.ok:
            logger.error(
                "Google OAuth token exchange failed: status=%s body=%s",
                token_response.status_code,
                token_response.text[:500],
            )
            error_message = quote("Google sign-in failed during token exchange", safe='')
            return redirect(f"{frontend_url}/?oauth_error={error_message}")

        # Parse the token response
        google_client.parse_request_body_response(json.dumps(token_response.json()))
        
        # Get user info from Google
        userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
        uri, headers, body = google_client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)
        if not userinfo_response.ok:
            logger.error(
                "Google OAuth userinfo request failed: status=%s body=%s",
                userinfo_response.status_code,
                userinfo_response.text[:500],
            )
            error_message = quote("Google sign-in failed while loading account profile", safe='')
            return redirect(f"{frontend_url}/?oauth_error={error_message}")
        userinfo = userinfo_response.json()
        
        # Verify email
        if not userinfo.get("email_verified"):
            return jsonify({"success": False, "error": "Email not verified by Google"}), 400
        
        users_email = userinfo["email"]
        users_name = userinfo.get("given_name", userinfo.get("name", "User"))
        resolved_role = _resolve_user_role(users_email, fallback_user=USERS_DB_FALLBACK.get(users_email))
        try:
            user_row = AUTH_REPOSITORY.upsert_oauth_user(
                email=users_email,
                name=users_name,
                role=resolved_role,
                oauth_provider="google",
                oauth_subject=str(userinfo.get("sub") or users_email),
                avatar_url=userinfo.get("picture"),
            )
            resolved_role = user_row.get("role") or resolved_role
            logger.info(f"OAuth user persisted in VVAULT auth DB: {users_email} (id={user_row.get('id')})")
        except Exception as db_err:
            logger.warning(f"VVAULT auth user upsert failed: {type(db_err).__name__}")
            error_message = quote("Google sign-in failed while storing VVAULT identity", safe='')
            return redirect(f"{frontend_url}/?oauth_error={error_message}")
        
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        
        try:
            db_create_session(users_email, resolved_role, session_token, expires_at, remember_me=True)
        except Exception as session_err:
            logger.warning(f"VVAULT auth session create failed: {type(session_err).__name__}")
            error_message = quote("Google sign-in failed while storing VVAULT session", safe='')
            return redirect(f"{frontend_url}/?oauth_error={error_message}")
        
        logger.info(f"Google OAuth login successful: {users_email}")
        
        encoded_email = quote(users_email, safe='')
        encoded_name = quote(users_name, safe='')
        redirect_url = f"{frontend_url}/?token={session_token}&email={encoded_email}&name={encoded_name}"
        logger.info(f"Redirecting to: {redirect_url}")
        return redirect(redirect_url)
        
    except Exception as e:
        logger.exception(f"Google OAuth callback error: {e}")
        return jsonify({"success": False, "error": "OAuth callback failed"}), 500

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

if __name__ == "__main__":
    main()
