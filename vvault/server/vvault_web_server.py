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
import re
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from uuid import uuid4

from flask import Flask, request, jsonify, send_from_directory
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

# Supabase client for vault files
try:
    from supabase import create_client
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    )
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
except Exception as e:
    SUPABASE_SERVICE_ROLE_KEY = ""
    SUPABASE_ANON_KEY = ""
    supabase_client = None
    print(f"Supabase not configured: {e}")

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


def _pocketverse_request_context():
    """Build request context for Pocketverse guard from current request."""
    cu = getattr(request, "current_user", None) or {}
    email = cu.get("email") or (request.headers.get("X-Chatty-User") if request else None)
    return {
        "email": email,
        "user_id": cu.get("id") or email,
        "session_user": cu,
        "supabase_client": supabase_client,
    }

def _is_supabase_uuid(value: Optional[str]) -> bool:
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

app = Flask(__name__, static_folder=DIST_DIR, static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'vvault-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
_cors_origins = ["http://localhost:7784", "http://localhost:5173", "http://localhost:5000", "https://vvault.thewreck.org"]
_replit_domain = os.environ.get("REPLIT_DEV_DOMAIN") or os.environ.get("REPL_SLUG")
if _replit_domain:
    _cors_origins.append(f"https://{_replit_domain}")
CORS(app, origins=_cors_origins)

# Security headers (resilience hardening)
@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

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
VVAULT_FRONTEND_URL = os.environ.get("VVAULT_FRONTEND_URL", "http://localhost:7784")
VVAULT_BACKEND_URL = os.environ.get("VVAULT_BACKEND_URL", "http://localhost:8000")
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
    frontend_url = VVAULT_FRONTEND_URL or default or "http://localhost:7784"
    return frontend_url.rstrip("/")


def _get_backend_url(default: str = None) -> str:
    backend_url = OAUTH_BASE_URL or VVAULT_BACKEND_URL or default or "http://localhost:8000"
    return backend_url.rstrip("/")


def _get_supabase_mode() -> str:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        return "healthy"
    if SUPABASE_URL and SUPABASE_ANON_KEY:
        return "degraded"
    return "broken"


def _get_supabase_status() -> Dict[str, Any]:
    mode = _get_supabase_mode()
    return {
        "mode": mode,
        "available": bool(supabase_client),
        "configured": bool(SUPABASE_URL and SUPABASE_KEY),
        "using_service_role": bool(SUPABASE_SERVICE_ROLE_KEY),
        "using_anon_key": bool(SUPABASE_ANON_KEY and not SUPABASE_SERVICE_ROLE_KEY),
    }


def _is_admin_email(email: Optional[str]) -> bool:
    return bool(email and email.strip().lower() in VVAULT_ADMIN_EMAILS)


def _resolve_user_role(email: Optional[str], supabase_user: Optional[Dict] = None, fallback_user: Optional[Dict] = None) -> str:
    for candidate in (supabase_user, fallback_user):
        if candidate and candidate.get('role'):
            return candidate['role']
    if _is_admin_email(email):
        return 'admin'
    return 'user'


def _upsert_supabase_user_record(user_id: str, email: str, name: str, role: str) -> None:
    record = {
        'id': user_id,
        'email': email,
        'name': name,
    }
    if role:
        record['role'] = role

    try:
        supabase_client.table('users').insert(record).execute()
    except Exception as insert_err:
        if 'role' in str(insert_err):
            record.pop('role', None)
            supabase_client.table('users').insert(record).execute()
        else:
            raise


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
    global _VAULT_FILES_HAS_UPDATED_AT
    logical_path = (record.get('storage_path') or record.get('filename') or '').strip()
    if not logical_path:
        raise ValueError("Vault file record is missing filename/storage_path")

    record = dict(record)
    record['filename'] = logical_path
    record['storage_path'] = logical_path

    def _existing_rows_query(include_updated_at: bool):
        columns = _select_with_optional_updated_at(
            'id, created_at, updated_at, content, filename, storage_path',
            include_updated_at,
        )
        query = supabase_client.table('vault_files').select(columns)
        query = query.eq('filename', logical_path)

        construct_id = record.get('construct_id')
        if construct_id:
            query = query.eq('construct_id', construct_id)

        user_id = record.get('user_id')
        if user_id is None:
            query = query.is_('user_id', 'null')
        else:
            query = query.eq('user_id', user_id)
        return query.execute().data or []

    try:
        existing_rows = _existing_rows_query(_VAULT_FILES_HAS_UPDATED_AT is not False)
        if _VAULT_FILES_HAS_UPDATED_AT is None:
            _VAULT_FILES_HAS_UPDATED_AT = True
    except Exception as err:
        if _is_missing_updated_at_error(err):
            _VAULT_FILES_HAS_UPDATED_AT = False
            existing_rows = _existing_rows_query(False)
        else:
            raise
    if existing_rows:
        canonical = existing_rows[0]
        for row in existing_rows[1:]:
            canonical = _choose_preferred_vault_row(canonical, row)
        duplicate_ids = [row['id'] for row in existing_rows if row['id'] != canonical['id']]
        update_payload = dict(record)
        update_payload.pop('created_at', None)
        _vault_files_write_with_optional_updated_at('update', update_payload, record_id=canonical['id'])
        for duplicate_id in duplicate_ids:
            supabase_client.table('vault_files').delete().eq('id', duplicate_id).execute()
        if duplicate_ids:
            logger.warning(
                "VFILE_DEDUP: context=%s path=%s canonical_id=%s removed_duplicates=%s",
                context,
                logical_path,
                canonical['id'],
                len(duplicate_ids),
            )
        return {
            'action': 'updated',
            'id': canonical['id'],
            'deduped': len(duplicate_ids),
            'path': logical_path,
        }

    insert_result = _vault_files_write_with_optional_updated_at('insert', record)
    inserted = (insert_result.data or [{}])[0]
    return {
        'action': 'created',
        'id': inserted.get('id'),
        'deduped': 0,
        'path': logical_path,
    }


def _supabase_unavailable_response(message: str, *, include_constructs: bool = False):
    payload = {
        "success": True,
        "supabase_available": False,
        "degraded": True,
        "error_code": "SUPABASE_UNAVAILABLE",
        "message": message,
    }
    if include_constructs:
        payload.update({"constructs": [], "count": 0})
    else:
        payload.update({"files": [], "count": 0, "user_root": "Vault"})
    return jsonify(payload)


def _is_supabase_upstream_timeout(error: Exception) -> bool:
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
            "supabase.co",
            "supabase timeout",
        )
        if any(signal in lowered for signal in signals):
            return True

    return False


def _supabase_timeout_message() -> str:
    return (
        "Supabase is temporarily unreachable (522 timeout). "
        "Vault data is temporarily unavailable; please retry in a few minutes."
    )


def _log_supabase_outage(route: str, contract: str, status_code: int, error_code: str) -> None:
    logger.warning(
        "SUPABASE_OUTAGE route=%s upstream_class=supabase_timeout_522 contract=%s status=%s error_code=%s ts=%s",
        route,
        contract,
        status_code,
        error_code,
        datetime.now(timezone.utc).isoformat(),
    )


def _supabase_timeout_response(
    route: str,
    *,
    status_code: int,
    contract: str,
    include_success: bool = False,
    include_constructs: bool = False,
    include_files: bool = False,
    extra: Optional[Dict[str, Any]] = None,
):
    error_code = "SUPABASE_TIMEOUT_522"
    payload = {
        "supabase_available": False,
        "degraded": True,
        "error_code": error_code,
        "message": _supabase_timeout_message(),
    }
    if include_success:
        payload["success"] = status_code < 400
    if include_constructs:
        payload.update({"constructs": [], "count": 0})
    if include_files:
        payload.update({"files": [], "count": 0, "user_root": "Vault"})
    if extra:
        payload.update(extra)

    _log_supabase_outage(route, contract, status_code, error_code)
    return jsonify(payload), status_code


def _supabase_timeout_read_response(
    route: str,
    *,
    include_constructs: bool = False,
    include_files: bool = False,
    extra: Optional[Dict[str, Any]] = None,
):
    return _supabase_timeout_response(
        route,
        status_code=200,
        contract="soft_degrade",
        include_success=True,
        include_constructs=include_constructs,
        include_files=include_files,
        extra=extra,
    )


def _supabase_timeout_write_response(route: str, *, extra: Optional[Dict[str, Any]] = None):
    return _supabase_timeout_response(
        route,
        status_code=503,
        contract="strict_503",
        extra=extra,
    )


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
    query = (
        supabase_client.table('vault_files')
        .select(_identity_projection_select_columns())
        .or_(f'construct_id.eq.{callsign},construct_id.eq.{bare_name}')
        .not_.is_('filename', 'null')
        .limit(5000)
    )
    return query.execute().data or []


def _load_identity_projection_content(file_id: str) -> Any:
    result = (
        supabase_client.table('vault_files')
        .select('content')
        .eq('id', file_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0].get('content')
    return None


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
    bare_name = _bare_name_from_callsign(callsign)
    result = (
        supabase_client.table('vault_files')
        .select('id, user_id, created_at')
        .or_(f'construct_id.eq.{callsign},construct_id.eq.{bare_name}')
        .not_.is_('user_id', 'null')
        .execute()
    )
    rows = [row for row in (result.data or []) if row.get('user_id')]
    if not rows:
        return None
    rows.sort(key=lambda row: _identity_projection_candidate_sort_key(row), reverse=True)
    return rows[0].get('user_id')


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
    by_filename = (
        supabase_client.table('vault_files')
        .select(_identity_projection_select_columns())
        .eq('construct_id', callsign)
        .eq('filename', canonical_path)
        .execute()
        .data or []
    )
    by_storage = (
        supabase_client.table('vault_files')
        .select(_identity_projection_select_columns())
        .eq('construct_id', callsign)
        .eq('storage_path', canonical_path)
        .execute()
        .data or []
    )
    merged: Dict[str, Dict[str, Any]] = {}
    for row in by_filename + by_storage:
        merged[row['id']] = row
    return list(merged.values())


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
        _vault_files_write_with_optional_updated_at('update', update_record, record_id=current['id'])
        return 'updated', current['id'], previous_sha

    insert_result = _vault_files_write_with_optional_updated_at('insert', record)
    inserted = (insert_result.data or [{}])[0]
    return 'created', inserted.get('id'), None


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


def _vault_files_record_without_optional_updated_at(record: Dict[str, Any]) -> Dict[str, Any]:
    global _VAULT_FILES_HAS_UPDATED_AT
    record_copy = dict(record)
    if _VAULT_FILES_HAS_UPDATED_AT is False:
        record_copy.pop('updated_at', None)
    return record_copy


def _vault_files_write_with_optional_updated_at(action: str, payload: Dict[str, Any], *, record_id: Optional[str] = None):
    global _VAULT_FILES_HAS_UPDATED_AT
    prepared = _vault_files_record_without_optional_updated_at(payload)
    try:
        if action == 'insert':
            result = supabase_client.table('vault_files').insert(prepared).execute()
        elif action == 'update':
            result = supabase_client.table('vault_files').update(prepared).eq('id', record_id).execute()
        else:
            raise ValueError(f"Unsupported vault_files write action: {action}")
        if _VAULT_FILES_HAS_UPDATED_AT is None and 'updated_at' in payload:
            _VAULT_FILES_HAS_UPDATED_AT = True
        return result
    except Exception as exc:
        if not _is_missing_updated_at_error(exc):
            raise
        _VAULT_FILES_HAS_UPDATED_AT = False
        logger.warning(
            "VFILE_SCHEMA_FALLBACK: action=%s target=%s removed_field=updated_at",
            action,
            payload.get('storage_path') or payload.get('filename') or record_id or 'unknown',
        )
        fallback_payload = dict(payload)
        fallback_payload.pop('updated_at', None)
        if action == 'insert':
            return supabase_client.table('vault_files').insert(fallback_payload).execute()
        return supabase_client.table('vault_files').update(fallback_payload).eq('id', record_id).execute()


def _get_current_user_email() -> Optional[str]:
    current_user = getattr(request, 'current_user', None)
    if not current_user:
        return None
    return current_user.get('email')


def _get_authenticated_user_id() -> Optional[str]:
    current_user = getattr(request, 'current_user', None) or {}
    current_user_id = current_user.get('id') or current_user.get('supabase_user_id')
    if _is_supabase_uuid(current_user_id):
        return current_user_id.strip()

    user_email = _get_current_user_email()
    if not user_email or not supabase_client:
        return None
    result = supabase_client.table('users').select('id').eq('email', user_email).limit(1).execute()
    if not result.data:
        return None
    return result.data[0].get('id')


def _ensure_vvault_user(email: str, name: Optional[str] = None) -> Dict[str, Any]:
    existing = db_get_user(email)
    if existing:
        return existing

    display_name = (name or email.split('@')[0]).strip() or email.split('@')[0]
    now = datetime.now(timezone.utc).isoformat()
    if supabase_client:
        try:
            insert_result = supabase_client.table('users').insert({
                'email': email,
                'name': display_name,
                'role': 'user',
                'created_at': now,
            }).execute()
            if insert_result.data:
                user_id = insert_result.data[0].get('id')
                if user_id:
                    _create_default_user_folders(user_id, email)
            created = db_get_user(email)
            if created:
                return created
        except Exception as exc:
            logger.warning(f"SESSION_EXCHANGE: failed to upsert VVAULT user for {email}: {exc}")

    USERS_DB_FALLBACK[email] = {
        'email': email,
        'name': display_name,
        'role': 'user',
        'source': 'fallback',
    }
    return USERS_DB_FALLBACK[email]


def _load_vault_file_text(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""
    content = row.get('content')
    if isinstance(content, str) and content:
        return content

    storage_path = row.get('storage_path') or row.get('filename')
    if not storage_path or not supabase_client:
        return ""

    try:
        bucket = supabase_client.storage.from_('vault-files')
    except AttributeError:
        bucket = supabase_client.storage.from_('vault-files') if hasattr(supabase_client.storage, 'from_') else supabase_client.storage.from_('vault-files')

    try:
        data = supabase_client.storage.from_('vault-files').download(storage_path)
        blob = data[0] if isinstance(data, tuple) else getattr(data, 'data', None)
        error = data[1] if isinstance(data, tuple) and len(data) > 1 else getattr(data, 'error', None)
        if error or not blob:
            return ""
        if hasattr(blob, 'read'):
            raw = blob.read()
        else:
            raw = blob
        if isinstance(raw, bytes):
            return raw.decode('utf-8', errors='ignore')
        return str(raw)
    except Exception:
        return ""


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


def _physical_features_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(f"{key}: {entry}" for key, entry in value.items())
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return ""


def _infer_mime_type(row: Dict[str, Any], filename: str) -> str:
    metadata = _metadata_to_dict(row.get('metadata'))
    mime = metadata.get('mimeType') or metadata.get('contentType')
    if isinstance(mime, str) and mime:
        return mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or 'application/octet-stream'


def _binary_data_url_from_row(row: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if not row:
        return None, None

    content = row.get('content')
    filename = row.get('filename') or row.get('storage_path') or ''
    mime = _infer_mime_type(row, filename)

    if isinstance(content, str) and content.startswith('data:'):
        return content, mime

    if isinstance(content, str) and content:
        return f"data:{mime};base64,{content}", mime

    storage_path = row.get('storage_path') or row.get('filename')
    if not storage_path or not supabase_client:
        return None, mime

    try:
        download = supabase_client.storage.from_('vault-files').download(storage_path)
        blob = download[0] if isinstance(download, tuple) else getattr(download, 'data', None)
        error = download[1] if isinstance(download, tuple) and len(download) > 1 else getattr(download, 'error', None)
        if error or not blob:
            return None, mime
        if hasattr(blob, 'read'):
            raw = blob.read()
        else:
            raw = blob
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        encoded = base64.b64encode(raw).decode('utf-8')
        return f"data:{mime};base64,{encoded}", mime
    except Exception:
        return None, mime


def _query_construct_identity_rows(callsign: str, user_id: Optional[str]) -> List[Dict[str, Any]]:
    global _VAULT_FILES_HAS_UPDATED_AT
    bare_name = _bare_name_from_callsign(callsign)
    def _run(include_updated_at: bool) -> List[Dict[str, Any]]:
        columns = _select_with_optional_updated_at(
            'id, user_id, construct_id, filename, storage_path, content, file_type, metadata, sha256, created_at, updated_at',
            include_updated_at,
        )
        query = (
            supabase_client.table('vault_files')
            .select(columns)
            .or_(f'construct_id.eq.{callsign},construct_id.eq.{bare_name}')
            .ilike('filename', f'instances/{callsign}/identity/%')
        )
        if user_id:
            query = query.eq('user_id', user_id)
        return _dedupe_vault_rows(query.execute().data or [])

    try:
        rows = _run(_VAULT_FILES_HAS_UPDATED_AT is not False)
        if _VAULT_FILES_HAS_UPDATED_AT is None:
            _VAULT_FILES_HAS_UPDATED_AT = True
        return rows
    except Exception as err:
        if _is_missing_updated_at_error(err):
            _VAULT_FILES_HAS_UPDATED_AT = False
            return _run(False)
        raise


def _query_construct_file_rows(callsign: str, user_id: Optional[str]) -> List[Dict[str, Any]]:
    global _VAULT_FILES_HAS_UPDATED_AT
    bare_name = _bare_name_from_callsign(callsign)
    def _run(include_updated_at: bool) -> List[Dict[str, Any]]:
        columns = _select_with_optional_updated_at(
            'id, user_id, construct_id, filename, storage_path, metadata, created_at, updated_at',
            include_updated_at,
        )
        query = (
            supabase_client.table('vault_files')
            .select(columns)
            .or_(f'construct_id.eq.{callsign},construct_id.eq.{bare_name}')
            .ilike('filename', f'instances/{callsign}/%')
        )
        if user_id:
            query = query.eq('user_id', user_id)
        return _dedupe_vault_rows(query.execute().data or [])

    try:
        rows = _run(_VAULT_FILES_HAS_UPDATED_AT is not False)
        if _VAULT_FILES_HAS_UPDATED_AT is None:
            _VAULT_FILES_HAS_UPDATED_AT = True
        return rows
    except Exception as err:
        if _is_missing_updated_at_error(err):
            _VAULT_FILES_HAS_UPDATED_AT = False
            return _run(False)
        raise


def _build_construct_editor_payload(callsign: str, user_id: Optional[str]) -> Dict[str, Any]:
    identity_rows = _query_construct_identity_rows(callsign, user_id)
    files_rows = _query_construct_file_rows(callsign, user_id)

    rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for row in identity_rows:
        rows_by_name.setdefault(os.path.basename(row.get('filename') or ''), []).append(row)

    source_rows = {
        name: _pick_latest_vault_row(rows)
        for name, rows in rows_by_name.items()
    }

    prompt_json = _safe_json_loads(_load_vault_file_text(source_rows.get('prompt.json'))) or {}
    definition_json = _safe_json_loads(_load_vault_file_text(source_rows.get('definition.json'))) or {}
    physical_features_json = _safe_json_loads(_load_vault_file_text(source_rows.get('physical_features.json')))
    voice_json = _safe_json_loads(_load_vault_file_text(source_rows.get('voice.json'))) or {}
    gender_json = _safe_json_loads(_load_vault_file_text(source_rows.get('gender.json'))) or {}

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

    return {
        "ok": True,
        "constructId": callsign,
        "callsign": callsign,
        "displayName": _first_non_empty_string([prompt_json.get('name')], default=callsign),
        "description": _first_non_empty_string([prompt_json.get('description')]),
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
        "models": {
            "primary": "openrouter:meta-llama/llama-3.3-70b-instruct",
            "conversation": "openrouter:meta-llama/llama-3.3-70b-instruct",
            "creative": "openrouter:mistralai/mistral-7b-instruct",
            "coding": "openrouter:deepseek/deepseek-coder-33b-instruct",
        },
        "capabilities": {
            "webSearch": False,
            "canvas": False,
            "imageGeneration": False,
            "codeInterpreter": True,
        },
        "updatedAt": updated_at,
    }


def _upsert_construct_prompt_file(callsign: str, user_id: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
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
            "source": "vvault_construct_editor",
            "updatedAt": now,
        }),
        "created_at": now,
        "updated_at": now,
    }
    return _upsert_vault_file_record(record, context='construct_editor_prompt')


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

# Get Replit domain for OAuth callbacks
REPLIT_DEV_DOMAIN = os.environ.get("REPLIT_DEV_DOMAIN", "localhost:5000")
OAUTH_BASE_URL = os.environ.get("OAUTH_BASE_URL", "")

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

def _protected_vault_update(supabase_client, file_id: str, new_content: str, force: bool = False, context: str = "unknown") -> dict:
    """Wrap vault_files update operations with delete protection.
    
    Before performing a full content replacement:
    1. Reads existing content from Supabase
    2. If existing content is longer than new content by more than 50%, rejects the update
    3. Accepts force parameter to bypass the check
    4. Logs all content updates with before/after lengths
    
    Returns: {"allowed": True/False, "error": str or None, "existing_content": str, "existing_length": int}
    """
    result = {"allowed": True, "error": None, "existing_content": "", "existing_length": 0}
    
    try:
        existing = supabase_client.table('vault_files').select('content, filename').eq('id', file_id).execute()
        
        if not existing.data or len(existing.data) == 0:
            logger.warning(f"PROTECTED_UPDATE [{context}]: file_id={file_id} not found in Supabase")
            result["allowed"] = True
            return result
        
        existing_content = existing.data[0].get('content', '') or ''
        existing_filename = existing.data[0].get('filename', '')
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

# Database-backed user management (Zero Trust: no hardcoded credentials)
# Fallback mock DB only used if database is unavailable
USERS_DB_FALLBACK = {
    'admin@vvault.com': {
        'password': 'admin123',
        'name': 'Admin User',
        'role': 'admin'
    }
}

# In-memory session cache (primary storage when DB table unavailable)
ACTIVE_SESSIONS = {}

# Flag to track if session table exists (auto-detected on first use)
_SESSION_TABLE_AVAILABLE = None

def _check_session_table_available() -> bool:
    """Check if user_sessions table exists in Supabase (cached)"""
    global _SESSION_TABLE_AVAILABLE
    if _SESSION_TABLE_AVAILABLE is not None:
        return _SESSION_TABLE_AVAILABLE
    
    if not supabase_client:
        _SESSION_TABLE_AVAILABLE = False
        return False
    
    try:
        supabase_client.table('user_sessions').select('id').limit(1).execute()
        _SESSION_TABLE_AVAILABLE = True
        logger.info("Session table available in Supabase")
        return True
    except Exception as e:
        if 'PGRST205' in str(e) or '404' in str(e):
            _SESSION_TABLE_AVAILABLE = False
            logger.info("Session table not available in Supabase - using in-memory sessions")
            return False
        _SESSION_TABLE_AVAILABLE = False
        return False

def db_create_session(email: str, role: str, token: str, expires_at: datetime, remember_me: bool = False) -> bool:
    """Create session (in-memory, with optional database persistence)"""
    ACTIVE_SESSIONS[token] = {
        'email': email,
        'role': role,
        'expires_at': expires_at,
        'created_at': datetime.now(),
        'remember_me': remember_me
    }
    
    if not _check_session_table_available():
        return True
    
    try:
        result = supabase_client.table('users').select('id').eq('email', email).execute()
        user_id = result.data[0]['id'] if result.data else None
        
        supabase_client.table('user_sessions').insert({
            'user_id': user_id,
            'token': token,
            'email': email,
            'remember_me': remember_me,
            'expires_at': expires_at.isoformat(),
            'created_at': datetime.now().isoformat()
        }).execute()
        logger.info(f"Session persisted to database for {email} (remember_me={remember_me})")
    except Exception as e:
        logger.debug(f"Session DB persistence failed (using in-memory): {e}")
    
    return True

def db_delete_session(token: str) -> bool:
    """Delete session from cache and database"""
    if token in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[token]
    
    if not _check_session_table_available():
        return True
    
    try:
        supabase_client.table('user_sessions').delete().eq('token', token).execute()
    except Exception as e:
        logger.debug(f"Session DB delete failed (already removed from memory): {e}")
    
    return True

def db_get_session(token: str) -> Optional[Dict]:
    """Get session from cache (primary) or database (fallback)"""
    if token in ACTIVE_SESSIONS:
        session = ACTIVE_SESSIONS[token]
        if datetime.now() > session['expires_at']:
            db_delete_session(token)
            return None
        return session
    
    if not _check_session_table_available():
        return None
    
    try:
        result = supabase_client.table('user_sessions').select('*').eq('token', token).execute()
        if not result.data:
            return None
        
        session_data = result.data[0]
        expires_at = datetime.fromisoformat(session_data['expires_at'].replace('Z', '+00:00').replace('+00:00', ''))
        
        if datetime.now() > expires_at:
            db_delete_session(token)
            return None
        
        user_result = supabase_client.table('users').select('*').eq('email', session_data['email']).execute()
        supabase_user = user_result.data[0] if user_result.data else None
        role = _resolve_user_role(session_data['email'], supabase_user=supabase_user, fallback_user=USERS_DB_FALLBACK.get(session_data['email']))
        
        session = {
            'email': session_data['email'],
            'role': role,
            'expires_at': expires_at,
            'created_at': datetime.fromisoformat(session_data['created_at'].replace('Z', '+00:00').replace('+00:00', ''))
        }
        ACTIVE_SESSIONS[token] = session
        return session
    except Exception as e:
        logger.debug(f"Session DB lookup failed: {e}")
        return None

def db_get_user(email: str) -> Optional[Dict]:
    """Get user from database with fallback to local storage"""
    try:
        supabase_user = None
        fallback_user = USERS_DB_FALLBACK.get(email)
        
        if supabase_client:
            result = supabase_client.table('users').select('*').eq('email', email).execute()
            if result.data:
                supabase_user = result.data[0]
        
        if supabase_user:
            user_data = {
                'id': supabase_user['id'],
                'email': supabase_user['email'],
                'name': supabase_user.get('name'),
                'password_hash': supabase_user.get('password_hash'),
                'auth_password_hash': supabase_user.get('auth_password_hash'),
                'auth_provider': supabase_user.get('auth_provider'),
                'role': _resolve_user_role(email, supabase_user=supabase_user, fallback_user=fallback_user),
                'source': 'supabase'
            }
            if not user_data['password_hash'] and fallback_user:
                user_data['password_hash'] = fallback_user.get('password_hash')
                user_data['role'] = _resolve_user_role(email, supabase_user=supabase_user, fallback_user=fallback_user)
            return user_data
        
        if fallback_user:
            fallback_user['source'] = 'fallback'
            return fallback_user
        return None
    except Exception as e:
        logger.error(f"Failed to get user from database: {e}")
        if email in USERS_DB_FALLBACK:
            USERS_DB_FALLBACK[email]['source'] = 'fallback'
            return USERS_DB_FALLBACK[email]
        return None

def db_cleanup_expired_sessions():
    """Clean up expired sessions from database"""
    try:
        now = datetime.now()
        for token in list(ACTIVE_SESSIONS.keys()):
            if now > ACTIVE_SESSIONS[token]['expires_at']:
                del ACTIVE_SESSIONS[token]
        
        if supabase_client:
            supabase_client.table('user_sessions').delete().lt('expires_at', now.isoformat()).execute()
    except Exception as e:
        logger.error(f"Failed to cleanup expired sessions: {e}")

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
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None, None
        
        token = auth_header.split(' ')[1]
        
        session = db_get_session(token)
        if not session:
            return None, None
        
        return session, token
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
            chatty_supabase_user_id = request.headers.get("X-Chatty-Supabase-User-Id")
            current_user = {"email": chatty_email}
            if _is_supabase_uuid(chatty_supabase_user_id):
                current_user["id"] = chatty_supabase_user_id.strip()
                current_user["supabase_user_id"] = chatty_supabase_user_id.strip()
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
    """Health check endpoint"""
    supabase_status = _get_supabase_status()
    return jsonify({
        "status": "healthy" if supabase_status["mode"] == "healthy" else "degraded",
        "timestamp": datetime.now().isoformat(),
        "service": "vvault-backend",
        "version": "1.0.0",
        "supabase": supabase_status,
        "oauth": {
            "configured": _google_oauth_ready(),
            "callback_url": f"{_get_backend_url()}/api/auth/google/callback",
            "frontend_url": _get_frontend_url(),
        },
    })

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
    if not supabase_client:
        logger.warning("Cannot create default folders: Supabase not configured")
        return False
    
    try:
        base_path = _get_user_base_path(user_id, user_email)
        
        # Get user's name for profile
        user_result = supabase_client.table('users').select('name').eq('id', user_id).execute()
        user_name = user_result.data[0].get('name', 'User') if user_result.data else 'User'
        
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
                supabase_client.table('vault_files').upsert(
                    folder, 
                    on_conflict='filename'
                ).execute()
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
    if user_id:
        result = (
            supabase_client.table('vault_files')
            .select(columns)
            .eq('user_id', user_id)
            .eq('filename', target['storage_path'])
            .execute()
        )
        if result.data:
            return result.data

        if target.get('is_hydro_project_thread'):
            storage_result = (
                supabase_client.table('vault_files')
                .select(columns)
                .eq('user_id', user_id)
                .eq('storage_path', target['storage_path'])
                .execute()
            )
            if storage_result.data:
                return storage_result.data

    fallback = (
        supabase_client.table('vault_files')
        .select(columns)
        .ilike('filename', f"%{target['filename']}%")
        .execute()
    )
    if not fallback.data:
        return []
    if target.get('is_hydro_project_thread'):
        suffix = f"/{target['folder']}/{target['filename']}"
        return [
            row for row in fallback.data
            if str(row.get('filename') or row.get('storage_path') or '').endswith(suffix)
        ]
    return fallback.data

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
    IDENTITY_FILES = {'prompt.txt', 'prompt.json', 'conditioning.txt', 'avatar.png', 'avatar.jpeg', 'avatar.jpg'}
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
    user_row = _ensure_vvault_user(email, display_name)
    role = user_row.get("role", "user")
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=90)
    db_create_session(email, role, session_token, expires_at, remember_me=True)
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
    """Get current user's vault info (display name, root path, etc.)"""
    try:
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        if not user_email:
            return jsonify({"success": False, "error": "Invalid session"}), 401
        user_role = current_user.get('role', 'user')
        
        if not supabase_client:
            display_name = user_email.split('@')[0].replace('.', ' ').title()
            return jsonify({
                "success": True,
                "display_name": display_name,
                "is_admin": user_role == 'admin',
                "root_label": display_name if user_role != 'admin' else "Vault (Admin)"
            })
        
        user_result = supabase_client.table('users').select('id, name, email').eq('email', user_email).execute()
        if user_result.data:
            user_data = user_result.data[0]
            display_name = user_data.get('name') or user_email.split('@')[0].replace('.', ' ').title()
            user_id = user_data.get('id')
        else:
            display_name = user_email.split('@')[0].replace('.', ' ').title()
            user_id = None
        
        return jsonify({
            "success": True,
            "display_name": display_name,
            "user_id": user_id,
            "is_admin": user_role == 'admin',
            "root_label": display_name if user_role != 'admin' else "Vault (Admin)"
        })
    except Exception as e:
        logger.error(f"Error getting user info: {e}")
        if _is_supabase_upstream_timeout(e):
            current_user = getattr(request, 'current_user', None) or {}
            user_email = current_user.get('email', '')
            user_role = current_user.get('role', 'user')
            display_name = user_email.split('@')[0].replace('.', ' ').title() if user_email else "Vault User"
            return _supabase_timeout_read_response(
                "/api/vault/user-info",
                extra={
                    "display_name": display_name,
                    "user_id": None,
                    "is_admin": user_role == 'admin',
                    "root_label": display_name if user_role != 'admin' else "Vault (Admin)",
                },
            )
        return jsonify({"success": False, "error": "Failed to load user info"}), 500

@app.route('/api/vault/files')
@require_auth
def get_vault_files():
    """Get vault files from Supabase (multi-tenant: users see only their files)"""
    try:
        if not supabase_client:
            return _supabase_unavailable_response(
                "Supabase is not configured for this backend. Vault files are temporarily unavailable."
            )
        
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        if not user_email:
            return jsonify({"success": False, "error": "Invalid session"}), 401
        user_role = current_user.get('role', 'user')
        is_admin = user_role == 'admin'
        
        user_result = supabase_client.table('users').select('id, name').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        user_name = user_result.data[0].get('name', user_email.split('@')[0]) if user_result.data else user_email.split('@')[0]
        
        if is_admin:
            logger.debug(f"Admin {user_email} fetching all vault files")
            rows = _fetch_all_rows(
                lambda: supabase_client.table('vault_files').select('id, user_id, is_system, filename, storage_path, construct_id, file_type, metadata, created_at')
            )
            files = _transform_files_for_display(rows, is_admin=True, user_id=None)
        else:
            if not user_id:
                return jsonify({
                    "success": True,
                    "files": [],
                    "count": 0,
                    "user_root": user_name,
                    "message": "No files yet - upload your first file to get started"
                })
            logger.debug(f"User {user_email} fetching their vault files (user_id={user_id})")
            rows = _fetch_all_rows(
                lambda: supabase_client.table('vault_files')
                .select('id, user_id, is_system, filename, storage_path, construct_id, file_type, metadata, created_at')
                .eq('user_id', user_id)
                .eq('is_system', False)
            )
            files = _transform_files_for_display(rows, is_admin=False, user_id=user_id)
        
        return jsonify({
            "success": True,
            "files": files,
            "count": len(files),
            "user_root": user_name if not is_admin else "Vault (Admin)"
        })
    except Exception as e:
        logger.error(f"Error fetching vault files: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_read_response(
                "/api/vault/files",
                include_files=True,
            )
        return jsonify({
            "success": False,
            "error": "Failed to load vault files"
        }), 500

@app.route('/api/vault/knowledge-files')
@require_chatty_auth
def get_knowledge_files():
    """Get knowledge files for a construct from Supabase vault_files.
    Used by GPTCreator to list construct documents stored in VVAULT.
    Query params: construct_id (required)
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400
        
        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        
        knowledge_folders = ['documents', 'identity', 'config', 'chatty']
        query = supabase_client.table('vault_files').select(
            'id, filename, file_type, metadata, created_at, construct_id, sha256'
        ).eq('construct_id', construct_id).eq('user_id', user_id)
        
        result = query.execute()
        
        knowledge_data = []
        for row in (result.data or []):
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
    into Supabase vault_files. Existing files with the same path are updated
    (upsert by filename + construct_id + user_id).

    Returns summary with created/updated/skipped/failed counts.
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        construct_id = (request.form.get('construct_id') or '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        callsign = _normalize_callsign(construct_id)
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

        existing_result = supabase_client.table('vault_files').select(
            'id, filename'
        ).eq('construct_id', callsign).eq('user_id', user_id).execute()
        existing_map = {}
        for row in (existing_result.data or []):
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
                    _vault_files_write_with_optional_updated_at('update', record, record_id=file_id)
                    updated += 1
                else:
                    record['created_at'] = now
                    _vault_files_write_with_optional_updated_at('insert', record)
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/knowledge-files/upload")
        return jsonify({"success": False, "error": "Knowledge upload failed"}), 500


@app.route('/api/vault/knowledge-files/<file_id>', methods=['DELETE'])
@require_chatty_auth
def delete_knowledge_file(file_id):
    """Delete a single knowledge file by ID (user-scoped)."""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        existing = supabase_client.table('vault_files').select('id, filename, construct_id').eq('id', file_id).eq('user_id', user_id).execute()
        if not existing.data:
            return jsonify({"success": False, "error": "File not found or access denied"}), 404

        row = existing.data[0]
        construct_id = (row.get('construct_id') or '').strip()
        if construct_id:
            enforce_pocketverse_authority(construct_id, _pocketverse_request_context())

        supabase_client.table('vault_files').delete().eq('id', file_id).eq('user_id', user_id).execute()
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/knowledge-files/<file_id>")
        return jsonify({"success": False, "error": "File delete failed"}), 500


@app.route('/api/vault/memup/sync', methods=['POST'])
@require_auth
def sync_memup():
    """Trigger memup sync for a construct — processes transcripts into capsule data."""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from memup_sync import sync_construct_memup

        result = sync_construct_memup(supabase_client, construct_id, user_id)
        status_code = 200 if result.get('success') else 404
        return jsonify(result), status_code

    except Exception as e:
        logger.error(f"MEMUP_SYNC_ERROR: {e}")
        import traceback
        traceback.print_exc()
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/memup/sync")
        return jsonify({"success": False, "error": "Memup sync failed"}), 500


@app.route('/api/vault/memup/status')
@require_auth
def memup_status():
    """Check memup sync status for a construct — returns capsule metadata if it exists."""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        capsule_path = f'instances/{construct_id}/memup/{construct_id}.capsule'
        result = supabase_client.table('vault_files').select(
            'id, filename, sha256, metadata, created_at, updated_at'
        ).eq('construct_id', construct_id).eq('user_id', user_id).eq('filename', capsule_path).execute()

        if result.data:
            row = result.data[0]
            meta = row.get('metadata')
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except: meta = {}
            if not isinstance(meta, dict): meta = {}

            return jsonify({
                "success": True,
                "construct_id": construct_id,
                "synced": True,
                "file_id": row['id'],
                "path": capsule_path,
                "sha256": row.get('sha256', ''),
                "last_synced_at": meta.get('last_synced_at', row.get('updated_at', row.get('created_at', ''))),
                "total_sessions": meta.get('total_sessions', 0),
                "capsule_version": meta.get('capsule_version', ''),
            })
        else:
            return jsonify({
                "success": True,
                "construct_id": construct_id,
                "synced": False,
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        construct_id = request.args.get('construct_id', '').strip()
        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        simdrive_path = f'instances/{construct_id}/simDrive/%'
        result = supabase_client.table('vault_files').select(
            'id, filename, file_type, sha256, metadata, created_at, updated_at'
        ).eq('construct_id', construct_id).eq('user_id', user_id).ilike('filename', simdrive_path).execute()

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simdrive_parser import SimDriveParser

        parser = SimDriveParser(construct_id)
        files = []
        for row in (result.data or []):
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

        manifest = parser.build_manifest(result.data or [])

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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        file_id = request.args.get('file_id', '').strip()
        construct_id = request.args.get('construct_id', '').strip()
        if not file_id or not construct_id:
            return jsonify({"success": False, "error": "file_id and construct_id are required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        result = supabase_client.table('vault_files').select(
            'id, filename, content, file_type, sha256, metadata, created_at, updated_at'
        ).eq('id', file_id).eq('construct_id', construct_id).eq('user_id', user_id).execute()

        if not result.data:
            return jsonify({"success": False, "error": "File not found"}), 404

        row = result.data[0]
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        filename = data.get('filename', '').strip()
        content = data.get('content', '')

        if not construct_id or not filename:
            return jsonify({"success": False, "error": "construct_id and filename are required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/simdrive/write")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/simdrive/inject', methods=['POST'])
@require_auth
def simdrive_inject():
    """Inject memup capsule data into a construct's SimDrive as a continuity injection file.

    Reads the construct's memup capsule, transforms it into SimDrive injection format,
    and writes it to instances/{construct}/simDrive/continuity_injection.json.
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = getattr(request, 'current_user', None)
        if not current_user:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        construct_id = data.get('construct_id', '').strip()
        max_sessions = data.get('max_sessions', 50)

        if not construct_id:
            return jsonify({"success": False, "error": "construct_id is required"}), 400

        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        capsule_path = f'instances/{construct_id}/memup/{construct_id}.capsule'
        capsule_result = supabase_client.table('vault_files').select('content').eq(
            'construct_id', construct_id
        ).eq('user_id', user_id).eq('filename', capsule_path).execute()

        if not capsule_result.data:
            return jsonify({
                "success": False,
                "error": "No memup capsule found. Run memup sync first."
            }), 404

        capsule_content = capsule_result.data[0].get('content', '')
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/simdrive/inject")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/vault/files/<file_id>')
@require_auth
def get_vault_file(file_id):
    """Get a single vault file by ID (multi-tenant: users can only access their files)"""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        current_user = request.current_user
        user_email = current_user.get('email')
        user_role = current_user.get('role', 'user')
        
        result = supabase_client.table('vault_files').select('*').eq('id', file_id).single().execute()
        
        if not result.data:
            return jsonify({"success": False, "error": "File not found"}), 404
        
        if user_role != 'admin':
            user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
            user_id = user_result.data[0]['id'] if user_result.data else None
            
            file_user_id = result.data.get('user_id')
            is_system = result.data.get('is_system', False)
            
            if file_user_id is None and not is_system:
                log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}", "denied", "unassigned_file")
                return jsonify({"success": False, "error": "Access denied"}), 403
            
            if file_user_id is not None and file_user_id != user_id:
                log_auth_decision("file_access", user_email, f"/api/vault/files/{file_id}", "denied", "not_owner")
                return jsonify({"success": False, "error": "Access denied"}), 403
        
        return jsonify({"success": True, "file": result.data})
    except Exception as e:
        logger.error(f"Error fetching vault file: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# SERVICE API ENDPOINTS (for FXShinobi/Chatty backend-to-backend integration)
# ============================================================================

@app.route('/api/vault/health')
def service_health():
    """Service health check - returns VVAULT availability status
    
    No auth required - allows services to check if VVAULT is up before auth
    """
    supabase_meta = _get_supabase_status()
    supabase_status = "connected" if supabase_client else "not_configured"
    service_api_status = "enabled" if VVAULT_SERVICE_TOKEN else "disabled"
    
    # Check Supabase connectivity
    store_status = "unknown"
    if supabase_client:
        try:
            supabase_client.table('users').select('id').limit(1).execute()
            store_status = "connected"
        except Exception as e:
            store_status = "error"
            logger.debug(f"Supabase connectivity check failed: {e}")
    else:
        store_status = "not_configured"
    
    overall_status = "ok"
    if store_status != "connected":
        overall_status = "degraded"
    if supabase_meta["mode"] != "healthy":
        overall_status = "degraded"
    if service_api_status == "disabled":
        overall_status = "degraded"
    
    return jsonify({
        "status": overall_status,
        "service": "vvault",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "components": {
            "supabase": supabase_status,
            "store": store_status,
            "service_api": service_api_status
        },
        "supabase_mode": supabase_meta["mode"],
        "supabase_config": supabase_meta,
        "message": "VVAULT service API" if service_api_status == "enabled" else "Service API disabled (VVAULT_SERVICE_TOKEN not set)"
    })

@app.route('/api/vault/configs/<service>')
@require_service_token
def get_service_configs(service):
    """Get strategy configs for a service (e.g., fxshinobi)
    
    Returns: symbols, risk limits, params, enabled flags
    Auth: Requires VVAULT_SERVICE_TOKEN
    """
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 503
        
        result = supabase_client.table('strategy_configs').select('*').eq('service', service).execute()
        
        if not result.data:
            # Return defaults if no configs found
            return jsonify({
                "success": True,
                "service": service,
                "configs": [],
                "message": "No configs found, using defaults"
            })
        
        configs = []
        for row in result.data:
            configs.append({
                "strategy_id": row.get('strategy_id'),
                "params": row.get('params', {}),
                "symbols": row.get('symbols', []),
                "risk_limits": row.get('risk_limits', {}),
                "enabled": row.get('enabled', True),
                "version": row.get('version', 1),
                "updated_at": row.get('updated_at')
            })
        
        logger.info(f"SERVICE_API: Configs retrieved for {service} ({len(configs)} strategies)")
        
        return jsonify({
            "success": True,
            "service": service,
            "configs": configs
        })
        
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching configs for {service}: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to retrieve configs"
        }), 500

@app.route('/api/vault/credentials/<key>')
@require_service_token
def get_service_credential(key):
    """Get a credential by key (decrypted)
    
    Auth: Requires VVAULT_SERVICE_TOKEN
    NEVER logs the actual credential value
    """
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 503
        
        result = supabase_client.table('service_credentials').select('*').eq('key', key).execute()
        
        if not result.data:
            logger.info(f"SERVICE_API: Credential not found: {key}")
            return jsonify({
                "success": False,
                "error": f"Credential '{key}' not found"
            }), 404
        
        row = result.data[0]
        
        try:
            decrypted_value = decrypt_credential(row['encrypted_value'])
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
            "service": row.get('service'),
            "value": decrypted_value,
            "metadata": row.get('metadata', {}),
            "updated_at": row.get('updated_at')
        })
        
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching credential {key}: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to retrieve credential"
        }), 500

@app.route('/api/vault/credentials', methods=['POST'])
@require_service_token
def store_service_credential():
    """Store or update a credential (encrypted at rest)
    
    Request body: { key, service, value, metadata? }
    Auth: Requires VVAULT_SERVICE_TOKEN
    NEVER logs the actual credential value
    """
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 503
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400
        
        key = data.get('key')
        service = data.get('service', 'default')
        value = data.get('value')
        metadata = data.get('metadata', {})
        
        if not key or not value:
            return jsonify({"success": False, "error": "key and value are required"}), 400
        
        # Encrypt the value
        encrypted_value = encrypt_credential(value)
        
        # Upsert: update if exists, insert if not
        existing = supabase_client.table('service_credentials').select('id').eq('key', key).eq('service', service).execute()
        
        if existing.data:
            # Update existing
            supabase_client.table('service_credentials').update({
                'encrypted_value': encrypted_value,
                'metadata': metadata,
                'updated_at': datetime.now().isoformat()
            }).eq('key', key).eq('service', service).execute()
            action = "updated"
        else:
            # Insert new
            supabase_client.table('service_credentials').insert({
                'key': key,
                'service': service,
                'encrypted_value': encrypted_value,
                'metadata': metadata,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }).execute()
            action = "created"
        
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
            "message": f"Credential {action} successfully"
        })

    except Exception as e:
        logger.error(f"SERVICE_API: Error storing credential: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/credentials")
        return jsonify({
            "success": False,
            "error": "Failed to store credential"
        }), 500

@app.route('/api/vault/configs/<service>', methods=['POST'])
@require_service_token
def store_service_config(service):
    """Store or update strategy config for a service
    
    Request body: { strategy_id, params, symbols, risk_limits, enabled }
    Auth: Requires VVAULT_SERVICE_TOKEN
    """
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 503
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400
        
        strategy_id = data.get('strategy_id', 'default')
        params = data.get('params', {})
        symbols = data.get('symbols', [])
        risk_limits = data.get('risk_limits', {})
        enabled = data.get('enabled', True)
        
        # Upsert: update if exists, insert if not
        existing = supabase_client.table('strategy_configs').select('id, version').eq('service', service).eq('strategy_id', strategy_id).execute()
        
        if existing.data:
            current_version = existing.data[0].get('version', 1)
            supabase_client.table('strategy_configs').update({
                'params': params,
                'symbols': symbols,
                'risk_limits': risk_limits,
                'enabled': enabled,
                'version': current_version + 1,
                'updated_at': datetime.now().isoformat()
            }).eq('service', service).eq('strategy_id', strategy_id).execute()
            action = "updated"
            new_version = current_version + 1
        else:
            supabase_client.table('strategy_configs').insert({
                'service': service,
                'strategy_id': strategy_id,
                'params': params,
                'symbols': symbols,
                'risk_limits': risk_limits,
                'enabled': enabled,
                'version': 1,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }).execute()
            action = "created"
            new_version = 1
        
        logger.info(f"SERVICE_API: Config {action} for {service}/{strategy_id} (v{new_version})")
        _log_privileged_event(
            "config_change",
            resource=f"config:{service}:{strategy_id}",
            action=action,
            result="success",
            description=f"Strategy config {action}",
            metadata={"service": service, "strategy_id": strategy_id, "version": new_version},
        )

        return jsonify({
            "success": True,
            "service": service,
            "strategy_id": strategy_id,
            "action": action,
            "version": new_version
        })

    except Exception as e:
        logger.error(f"SERVICE_API: Error storing config: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/configs/<service>")
        return jsonify({
            "success": False,
            "error": "Failed to store config"
        }), 500


@app.route('/api/vault/system-files', methods=['GET'])
@require_service_token
def get_system_file():
    """
    Retrieve a system file by storage_path (service-to-service).

    Query params:
      - storage_path (required)
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

        storage_path = (request.args.get("storage_path") or "").strip()
        if not storage_path:
            return jsonify({"success": False, "error": "storage_path is required"}), 400

        result = (
            supabase_client.table("vault_files")
            .select("*")
            .eq("is_system", True)
            .eq("storage_path", storage_path)
            .limit(1)
            .execute()
        )

        if not result.data:
            return jsonify({"success": False, "error": "File not found"}), 404

        return jsonify({"success": True, "file": result.data[0]})
    except Exception as e:
        logger.error(f"SERVICE_API: Error fetching system file: {e}")
        return jsonify({"success": False, "error": "Failed to fetch system file"}), 500


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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

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

        existing = (
            supabase_client.table("vault_files")
            .select("id, created_at")
            .eq("is_system", True)
            .eq("storage_path", storage_path)
            .limit(1)
            .execute()
        )

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

        action = "created"
        if existing.data:
            file_id = existing.data[0]["id"]
            created_at = existing.data[0].get("created_at") or now
            update_record = dict(record)
            update_record["created_at"] = created_at
            result = _vault_files_write_with_optional_updated_at('update', update_record, record_id=file_id)
            action = "updated"
        else:
            insert_record = dict(record)
            insert_record["created_at"] = now
            result = _vault_files_write_with_optional_updated_at('insert', insert_record)

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
                "file": (result.data[0] if result.data else None),
            }
        )
    except Exception as e:
        logger.error(f"SERVICE_API: Error upserting system file: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/system-files")
        return jsonify({"success": False, "error": "Failed to upsert system file"}), 500


@app.route('/api/vault/constructs/<construct_id>/identity-projection', methods=['GET'])
@require_service_token
def get_identity_projection(construct_id):
    """Read projected identity field state for a construct."""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/constructs/<construct_id>/identity-projection/project")
        return jsonify({"success": False, "error": "Failed to project identity fields"}), 500


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
        user_record = _ensure_vvault_user(email, supplied_name)
        role = user_record.get('role') or 'user'

        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        db_create_session(email, role, session_token, expires_at, remember_me=True)

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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/session/exchange")
        return jsonify({"success": False, "error": "Failed to exchange Chatty session"}), 500


@app.route('/api/vault/constructs', methods=['GET'])
@require_auth
def list_construct_editors():
    """List constructs for the authenticated user as VVAULT-native cards."""
    try:
        global _VAULT_FILES_HAS_UPDATED_AT
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured", "constructs": []}), 503

        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found", "constructs": []}), 403

        def _run(include_updated_at: bool):
            columns = _select_with_optional_updated_at(
                'construct_id, filename, storage_path, content, file_type, metadata, sha256, created_at, updated_at, user_id',
                include_updated_at,
            )
            return _fetch_all_rows(
                lambda: supabase_client.table('vault_files')
                .select(columns)
                .eq('user_id', user_id)
                .ilike('filename', 'instances/%/identity/%')
            )

        try:
            rows = _run(_VAULT_FILES_HAS_UPDATED_AT is not False)
            if _VAULT_FILES_HAS_UPDATED_AT is None:
                _VAULT_FILES_HAS_UPDATED_AT = True
        except Exception as err:
            if _is_missing_updated_at_error(err):
                _VAULT_FILES_HAS_UPDATED_AT = False
                rows = _run(False)
            else:
                raise

        grouped: Dict[str, List[Dict[str, Any]]] = {}
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 503

        callsign = _normalize_callsign(construct_id)
        user_id = _get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403

        payload = request.get_json(silent=True) or {}
        prompt_payload = {
            "name": (payload.get('displayName') or '').strip() or callsign,
            "description": (payload.get('description') or '').strip(),
            "instructions": payload.get('instructions') or '',
            "conversationStarters": payload.get('conversationStarters') or [],
            "source": "vvault_construct_editor",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        _upsert_construct_prompt_file(callsign, user_id, prompt_payload)

        _project_identity_fields(callsign, {
            "conditioning": payload.get('conditioning') or '',
            "definition": payload.get('definition') or '',
            "physicalFeatures": payload.get('physicalFeatures') or '',
            "voice": {
                "text": payload.get('voice') or '',
            },
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/vault/constructs/<construct_id>/editor")
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
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
                supabase_client, file_id, content,
                force=force, context=f"update_chatty_transcript:{construct_id}"
            )
            
            if not protection["allowed"]:
                return jsonify({
                    "success": False,
                    "error": protection["error"],
                    "existing_length": protection["existing_length"],
                    "new_length": len(content)
                }), 409
            
            supabase_client.table('vault_files').update({
                'content': content,
                'sha256': sha256
            }).eq('id', file_id).execute()
            
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/transcript/<construct_id>")
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
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
        
        supabase_client.table('vault_files').update({
            'content': updated_content,
            'sha256': sha256
        }).eq('id', file_id).execute()
        
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/transcript/<construct_id>/message")
        return jsonify({"success": False, "error": "Transcript append failed"}), 500

@app.route('/api/chatty/construct/<construct_id>/files')
@require_chatty_auth
def get_construct_files(construct_id):
    """List assets, documents, and identity files for a specific construct.

    Normalizes the incoming construct_id to callsign format and queries
    Supabase using BOTH the callsign (e.g. 'katana-001') and the bare
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = _get_authenticated_user_id()

        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 404

        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        folder_filter = request.args.get('folder')

        all_files = supabase_client.table('vault_files').select(
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

    Searches Supabase for identity files (prompt.txt, prompt.json,
    personality.json, CONTINUITY_GPT_PROMPT.md) using both the callsign
    and bare name construct_id values.

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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        enforce_pocketverse_authority(construct_id, _pocketverse_request_context())
        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        display_name = bare_name.capitalize()

        identity_files = ['prompt.txt', 'prompt.json', 'personality.json',
                          'CONTINUITY_GPT_PROMPT.md', 'conditioning.txt']

        result = supabase_client.table('vault_files').select(
            'filename, storage_path, content, file_type, created_at, updated_at'
        ).or_(
            f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
        ).not_.is_('content', 'null').execute()

        name = display_name
        description = ""
        instructions = ""
        system_prompt = ""
        personality = None
        conversation_starters = []
        conditioning = ""

        for f in _dedupe_vault_rows(result.data or []):
            fname = f.get('filename', '')
            basename = os.path.basename(fname)
            if basename not in identity_files:
                continue
            content = f.get('content', '') or ''

            if basename == 'prompt.txt':
                lines = content.strip().split('\n')
                for line in lines:
                    line_stripped = line.strip().strip('*')
                    if line_stripped.startswith('You Are '):
                        name = line_stripped.replace('You Are ', '').strip()
                    elif line_stripped.startswith('Helps ') or line_stripped.startswith('Description:'):
                        description = line_stripped.replace('Description:', '').strip()
                code_blocks = content.split('```')
                if len(code_blocks) >= 2:
                    instructions = code_blocks[1].strip()
                    if instructions.startswith('Instructions for'):
                        instructions = '\n'.join(instructions.split('\n')[1:]).strip()
                system_prompt = content.strip()

            elif basename == 'prompt.json':
                try:
                    data = json.loads(content)
                    name = data.get('name', name)
                    description = data.get('description', description)
                    instructions = data.get('instructions', instructions)
                    system_prompt = data.get('system_prompt', '') or data.get('prompt', '') or instructions or system_prompt
                    conversation_starters = data.get('conversation_starters', []) or data.get('conversationStarters', [])
                except json.JSONDecodeError:
                    pass

            elif basename == 'personality.json':
                try:
                    personality = json.loads(content)
                except json.JSONDecodeError:
                    pass

            elif basename == 'conditioning.txt':
                conditioning = content.strip()

            elif fname == 'CONTINUITY_GPT_PROMPT.md':
                if not system_prompt:
                    system_prompt = content.strip()

        enforcement = None
        enf_result = supabase_client.table('vault_files').select(
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
            "description": description or f"Helps you with your life problems.",
            "instructions": instructions,
            "system_prompt": system_prompt,
            "conversation_starters": conversation_starters,
            "conditioning": conditioning,
            "personality": personality,
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
    """Scaffold a full construct instance directory in Supabase vault_files.

    Accepts multipart/form-data OR JSON.
    Fields:
        callsign        (required, {name}-{NNN} format)
        name            (required, display name)
        description     (optional)
        instructions    (optional, system prompt body)
        conversationStarters (optional, JSON array)
        personality     (optional, JSON object)
        conditioning    (optional, text)
        color_hex       (optional, glyph color, default #722F37)
        center_image    (optional, file upload for glyph center)
        models          (optional, JSON array of model configs)
        orchestration_mode (optional, e.g. 'standard', 'autonomous')
        system_prompt   (optional, raw system prompt override)
        avatar_base64   (optional, base64-encoded avatar image)

    Scaffolds the full directory template per VSI spec.
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        if request.content_type and 'multipart/form-data' in request.content_type:
            callsign = (request.form.get('callsign') or '').strip().lower()
            name = (request.form.get('name') or '').strip()
            description = request.form.get('description', '')
            instructions = request.form.get('instructions', '')
            starters_raw = request.form.get('conversationStarters', '[]')
            try:
                conversation_starters = json.loads(starters_raw) if starters_raw else []
            except:
                conversation_starters = []
            personality_raw = request.form.get('personality', '{}')
            try:
                personality = json.loads(personality_raw) if personality_raw else {}
            except:
                personality = {}
            conditioning = request.form.get('conditioning', '')
            color_hex = request.form.get('color_hex', '#722F37')
            center_file = request.files.get('center_image')
            center_image_bytes = center_file.read() if center_file else None
            models_raw = request.form.get('models', '[]')
            try:
                models = json.loads(models_raw) if models_raw else []
            except:
                models = []
            orchestration_mode = request.form.get('orchestration_mode', 'standard')
            system_prompt_override = request.form.get('system_prompt', '')
            avatar_b64 = request.form.get('avatar_base64', '')
            prompt_json_raw = request.form.get('prompt_json', '')
            try:
                prompt_json_override = json.loads(prompt_json_raw) if prompt_json_raw else None
            except:
                prompt_json_override = None
        else:
            data = request.get_json(silent=True)
            if not data or not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid or missing body"}), 400
            callsign = data.get('callsign', '').strip().lower()
            name = data.get('name', '').strip()
            description = data.get('description', '')
            instructions = data.get('instructions', '')
            conversation_starters = data.get('conversationStarters', [])
            personality = data.get('personality', {})
            conditioning = data.get('conditioning', '')
            color_hex = data.get('color_hex', '#722F37')
            center_image_b64 = data.get('center_image_base64', '')
            center_image_bytes = None
            if center_image_b64:
                import base64 as b64mod
                center_image_bytes = b64mod.b64decode(center_image_b64)
            models = data.get('models', [])
            orchestration_mode = data.get('orchestration_mode', 'standard')
            system_prompt_override = data.get('system_prompt', '')
            avatar_b64 = data.get('avatar_base64', '')
            prompt_json_override = data.get('prompt_json', None)

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

        existing = supabase_client.table('vault_files').select('id').eq('construct_id', callsign).ilike('filename', '%prompt.json').execute()
        if existing.data:
            return jsonify({"success": False, "error": f"Construct {callsign} already exists (prompt.json found)"}), 409

        now = datetime.now().isoformat()

        if not isinstance(models, list):
            models = []
        if orchestration_mode not in ('standard', 'autonomous', 'hybrid', 'custom'):
            orchestration_mode = 'standard'

        if prompt_json_override and isinstance(prompt_json_override, dict):
            prompt_obj = prompt_json_override
            prompt_obj.setdefault('name', name)
            prompt_obj.setdefault('callsign', callsign)
            prompt_obj.setdefault('created_at', now)
        else:
            prompt_obj = {
                "name": name,
                "callsign": callsign,
                "description": description,
                "instructions": instructions,
                "conversation_starters": conversation_starters,
                "system_prompt": system_prompt_override or instructions,
                "created_at": now
            }

        if not personality:
            personality = {
                "construct_id": callsign,
                "instance_name": name,
                "traits": [],
                "rules": [],
                "metadata": {
                    "extractionTimestamp": now,
                    "mergedWithExisting": False
                }
            }
        elif 'construct_id' not in personality:
            personality['construct_id'] = callsign

        if not conditioning:
            conditioning = f"You are {name} ({callsign}). Maintain your identity at all times."

        metadata_obj = {
            "construct_id": callsign,
            "instance_name": name,
            "created_at": now,
            "version": "1.0.0",
            "capsule_updated": False,
            "color_hex": color_hex,
            "models": models if models else [{"id": "qwen2.5:0.5b", "provider": "ollama", "isDefault": True}],
            "orchestration_mode": orchestration_mode or "standard",
            "status": "active"
        }

        transcript_content = f"# Chat with {name}\n\nTranscript started {now}\n"

        log_files = [
            "capsule.log", "chat.log", "cns.log",
            "identity_guard.log", "independence.log", "ltm.log",
            "self_improvement_agent.log", "server.log", "stm.log",
            "watchdog.log"
        ]

        files_to_create = []

        files_to_create.append({
            'filename': 'prompt.json',
            'file_type': 'text',
            'content': json.dumps(prompt_obj, indent=2),
            'folder': 'identity',
        })
        files_to_create.append({
            'filename': 'conditioning.txt',
            'file_type': 'text',
            'content': conditioning,
            'folder': 'identity',
        })

        files_to_create.append({
            'filename': 'personality.json',
            'file_type': 'text',
            'content': json.dumps(personality, indent=2),
            'folder': 'config',
        })
        files_to_create.append({
            'filename': 'metadata.json',
            'file_type': 'text',
            'content': json.dumps(metadata_obj, indent=2),
            'folder': 'config',
        })

        files_to_create.append({
            'filename': f'chat_with_{callsign}.md',
            'file_type': 'transcript',
            'content': transcript_content,
            'folder': 'chatty',
        })

        for log_name in log_files:
            files_to_create.append({
                'filename': log_name,
                'file_type': 'text',
                'content': f"# {log_name.replace('.log', '').replace('_', ' ').title()} Log\n# Construct: {callsign}\n# Created: {now}\n",
                'folder': 'logs',
            })

        files_to_create.append({
            'filename': 'manifest.json',
            'file_type': 'simdrive',
            'content': json.dumps({
                'schema': 'simdrive_manifest',
                'version': '1.0.0',
                'construct_id': callsign,
                'generated_at': now,
                'total_files': 0,
                'type_distribution': {},
                'files': [],
            }, indent=2),
            'folder': 'simDrive',
        })

        files_to_create.append({
            'filename': 'README.md',
            'file_type': 'text',
            'content': f"# Frame Directory — {callsign}\nCognitive and emotional layer modules.\nCreated: {now}\n",
            'folder': 'frame',
        })

        avatar_created = False
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
                        'provider': 'vvault_scaffold',
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
        for file_def in files_to_create:
            ok, err = _validate_vault_filename(file_def['filename'])
            if not ok:
                return jsonify({"success": False, "error": err}), 400

            content_str = file_def['content']
            sha256 = hashlib.sha256(content_str.encode('utf-8')).hexdigest()
            folder = file_def.get('folder', '')
            vsi_path = f"instances/{callsign}/{folder}/{file_def['filename']}" if folder else f"instances/{callsign}/{file_def['filename']}"
            meta = {
                'construct_id': callsign,
                'provider': 'vvault_scaffold',
                'folder': folder,
            }
            record = {
                'filename': vsi_path,
                'file_type': file_def['file_type'],
                'content': content_str,
                'construct_id': callsign,
                'user_id': user_id,
                'is_system': False,
                'sha256': sha256,
                'metadata': json.dumps(meta),
                'storage_path': vsi_path,
                'created_at': now,
                'updated_at': now,
            }
            try:
                upsert_result = _upsert_vault_file_record(record, context='construct_scaffold')
                if upsert_result.get('id'):
                    created_files.append({
                        'id': upsert_result['id'],
                        'filename': vsi_path,
                        'file_type': file_def['file_type'],
                        'folder': folder,
                        'action': upsert_result['action'],
                    })
                else:
                    err_msg = f"No data returned for {vsi_path}"
                    logger.error(f"SCAFFOLD_INSERT_FAIL: {err_msg}")
                    failed_files.append({'filename': vsi_path, 'error': err_msg})
            except Exception as insert_err:
                err_msg = str(insert_err)
                logger.error(f"SCAFFOLD_INSERT_FAIL: {vsi_path} -> {err_msg}")
                failed_files.append({'filename': vsi_path, 'error': err_msg})

        import base64 as b64mod
        glyph_b64 = b64mod.b64encode(glyph_bytes).decode('utf-8')
        glyph_filename = f"{callsign}_glyph.png"
        glyph_meta = {
            'construct_id': callsign,
            'provider': 'vvault_scaffold',
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
                "identity": ["prompt.json", "conditioning.txt", glyph_filename] + (["avatar.png"] if avatar_created else []),
                "config": ["metadata.json", "personality.json"],
                "chatty": [f"chat_with_{callsign}.md"],
                "logs": log_files,
                "assets": [],
                "documents": [],
                "memup": [],
                "data": [],
            },
            "message": f"Construct {callsign} scaffolded with {len(created_files)} files"
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
        logger.error(f"Error creating construct: {e}")
        import traceback
        traceback.print_exc()
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/construct/create")
        return jsonify({"success": False, "error": "Construct creation failed"}), 500


@app.route('/api/chatty/constructs')
@require_chatty_auth
def get_chatty_constructs():
    """Get all available constructs with chat transcripts (user-scoped).

    Deduplicates bare-name vs callsign entries: if both 'katana' and
    'katana-001' transcripts exist, only 'katana-001' is returned.
    """
    try:
        if not supabase_client:
            return _supabase_unavailable_response(
                "Supabase is not configured for this backend. Construct transcripts are temporarily unavailable.",
                include_constructs=True,
            )

        current_user = request.current_user
        user_email = current_user.get('email')
        user_role = current_user.get('role', 'user')
        is_admin = user_role == 'admin'

        if is_admin:
            rows = _fetch_all_rows(
                lambda: supabase_client.table('vault_files').select('filename, metadata, created_at').ilike('filename', '%chat_with_%')
            )
        else:
            user_id = _get_authenticated_user_id()

            if not user_id:
                return jsonify({"success": True, "constructs": [], "count": 0})

            rows = _fetch_all_rows(
                lambda: supabase_client.table('vault_files')
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
            "constructs": constructs,
            "count": len(constructs)
        })
    except Exception as e:
        logger.error(f"Error fetching chatty constructs: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_read_response(
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
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
                    'model': 'qwen2.5:0.5b',
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
            existing = supabase_client.table('vault_files').select('id, content, filename, storage_path').or_(f'construct_id.eq.{callsign},construct_id.eq.{bare}').ilike('filename', f"%{target['filename']}%").execute()
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
        
        # Backup before updating transcript in Supabase
        _backup_before_write(file_id, actual_transcript_filename, current_content)
        
        # Update transcript in Supabase
        sha256 = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
        update_data = {
            'content': new_content,
            'sha256': sha256,
        }
        supabase_client.table('vault_files').update(update_data).eq('id', file_id).execute()
        
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/message")
        return jsonify({"success": False, "error": "Chatty message failed"}), 500


def _load_construct_identity(construct_id: str, construct_name: str) -> str:
    """Load the system prompt for a construct from its identity files.

    Searches both callsign and bare name in Supabase to handle the
    construct_id column inconsistency (some files use 'katana', others
    use 'katana-001').
    """
    try:
        prompt_path = os.path.join(PROJECT_DIR, 'instances', construct_id, 'identity', 'prompt.json')
        if os.path.exists(prompt_path):
            with open(prompt_path, 'r') as f:
                prompt_data = json.load(f)
                return prompt_data.get('system_prompt', '') or prompt_data.get('prompt', '')

        if supabase_client:
            callsign = _normalize_callsign(construct_id)
            bare_name = _bare_name_from_callsign(callsign)

            result = supabase_client.table('vault_files').select('content, filename, storage_path, created_at, updated_at').or_(
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
    return jsonify({
        "backend_port": 8000,
        "frontend_port": 7784,
        "project_dir": PROJECT_DIR,
        "capsules_dir": CAPSULES_DIR,
        "cors_origins": ["http://localhost:7784"]
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
        
        user_data = db_get_user(email)
        
        if not user_data:
            log_auth_decision("login_attempt", email, "/api/auth/login", "denied", "user_not_found", ip)
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        has_vvault_pw = bool(user_data.get('password_hash'))
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
        if user_data.get('password_hash'):
            import bcrypt
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
        
        db_create_session(email, role, session_token, expires_at, remember_me=remember_me)
        
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
    """User registration endpoint with bcrypt password hashing and Supabase storage"""
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
        
        existing_user = db_get_user(email)
        if existing_user and existing_user.get('source') != 'fallback':
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'user_exists', ip)
            return jsonify({"success": False, "error": "User already exists"}), 409
        
        if not verify_turnstile_token(turnstile_token, request.remote_addr):
            log_auth_decision('registration_failed', email, '/api/auth/register', 'denied', 'turnstile_failed', ip)
            return jsonify({"success": False, "error": "Human verification failed. Please try again."}), 400
        
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        user_stored = False
        new_user_id = None
        if supabase_client:
            try:
                insert_result = supabase_client.table('users').insert({
                    'email': email,
                    'password_hash': password_hash,
                    'name': name,
                    'role': 'user',
                    'created_at': datetime.now().isoformat()
                }).execute()
                if insert_result.data:
                    new_user_id = insert_result.data[0].get('id')
                logger.info(f"User registered in Supabase: {email}")
                user_stored = True
            except Exception as e:
                if 'password_hash' in str(e) or 'role' in str(e):
                    logger.warning(f"Supabase schema missing columns, using basic insert: {e}")
                    try:
                        supabase_client.table('users').insert({
                            'email': email,
                            'name': name,
                            'created_at': datetime.now().isoformat()
                        }).execute()
                        USERS_DB_FALLBACK[email] = {
                            'password_hash': password_hash,
                            'name': name,
                            'role': 'user'
                        }
                        logger.info(f"User registered in Supabase (basic) + local fallback: {email}")
                        user_stored = True
                    except Exception as e2:
                        if _is_supabase_upstream_timeout(e2):
                            return _supabase_timeout_write_response("/api/auth/register")
                        logger.error(f"Failed to register in Supabase: {e2}")
                else:
                    if _is_supabase_upstream_timeout(e):
                        return _supabase_timeout_write_response("/api/auth/register")
                    logger.error(f"Failed to register in Supabase: {e}")
        
        if not user_stored:
            USERS_DB_FALLBACK[email] = {
                'password_hash': password_hash,
                'name': name,
                'role': 'user'
            }
            logger.info(f"User registered in local fallback: {email}")
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        db_create_session(email, 'user', token, expires_at)
        
        # Create default folder structure for the new user
        if new_user_id:
            _create_default_user_folders(new_user_id, email)

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
            if supabase_client and new_user_id:
                glyph_record = {
                    'filename': glyph_filename,
                    'file_type': 'binary',
                    'content': glyph_b64,
                    'construct_id': None,
                    'user_id': new_user_id,
                    'is_system': False,
                    'sha256': glyph_sha,
                    'metadata': json.dumps(glyph_meta),
                    'created_at': datetime.now().isoformat(),
                }
                gr = supabase_client.table('vault_files').insert(glyph_record).execute()
                if gr.data:
                    logger.info(f"User glyph stored for {email}: {glyph_filename}")
            glyph_data = {
                'glyph_base64': glyph_b64,
                'number_rows': glyph_number_rows,
                'color_hex': glyph_color_hex,
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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/auth/register")
        log_auth_decision('registration_error', 'unknown', '/api/auth/register', 'denied', str(e), ip)
        return jsonify({"success": False, "error": "Registration failed"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """User logout endpoint (database-backed)"""
    try:
        auth_header = request.headers.get('Authorization')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            session = db_get_session(token)
            
            if session:
                user_email = session['email']
                db_delete_session(token)
                log_auth_decision("logout", user_email, "/api/auth/logout", "allowed", "session_terminated", ip)
                logger.info(f"User logged out: {user_email}")
        
        return jsonify({"success": True, "message": "Logged out successfully"})
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/auth/logout")
        return jsonify({"success": False, "error": "Logout failed"}), 500

@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    """Verify authentication token (database-backed)"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"success": False, "error": "No token provided"}), 401
        
        token = auth_header.split(' ')[1]
        
        session = db_get_session(token)
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
            "token": token
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
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
            ledger_result = supabase_client.table('vault_files').select(
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
    """Fetch transcript files from Supabase for a construct. Shared helper."""
    result = supabase_client.table('vault_files').select(
        'id, filename, storage_path, content, file_type, created_at, updated_at'
    ).or_(
        f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
    ).not_.is_('content', 'null').execute()

    transcript_keywords = ['transcript', 'character_ai', 'chatgpt', 'chat_with_', 'conversation', 'chat']
    candidates = []
    for f in _dedupe_vault_rows(result.data or []):
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
    continuity hooks. Stores the ledger in Supabase vault_files.
    
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
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        include_exchanges = request.args.get('include_exchanges', 'false').lower() == 'true'
        output_format = request.args.get('format', 'json')

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
            try:
                existing = supabase_client.table('vault_files').select('id').eq(
                    'filename', ledger_filename
                ).eq('construct_id', callsign).execute()
                ledger_record = {
                    'filename': ledger_filename,
                    'content': ledger_md,
                    'file_type': 'ledger',
                    'construct_id': callsign,
                    'metadata': json.dumps({
                        'type': 'continuity_ledger',
                        'format': 'markdown',
                        'total_sessions': len(entries),
                        'total_exchanges': total_exchanges,
                        'generated_at': datetime.utcnow().isoformat() + 'Z',
                    })
                }
                if existing.data:
                    supabase_client.table('vault_files').update(ledger_record).eq(
                        'id', existing.data[0]['id']
                    ).execute()
                else:
                    supabase_client.table('vault_files').insert(ledger_record).execute()
                logger.info(f"[Ledger] Stored markdown ledger for {callsign}: {len(entries)} sessions")
            except Exception as store_err:
                logger.warning(f"[Ledger] Failed to store markdown ledger: {store_err}")

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
        try:
            existing = supabase_client.table('vault_files').select('id').eq(
                'filename', ledger_filename
            ).eq('construct_id', callsign).execute()
            ledger_record = {
                'filename': ledger_filename,
                'content': json.dumps(ledger_json),
                'file_type': 'ledger',
                'construct_id': callsign,
                'metadata': json.dumps({
                    'type': 'continuity_ledger',
                    'format': 'json',
                    'total_sessions': len(entries),
                    'total_exchanges': total_exchanges,
                    'generated_at': datetime.utcnow().isoformat() + 'Z',
                })
            }
            if existing.data:
                supabase_client.table('vault_files').update(ledger_record).eq(
                    'id', existing.data[0]['id']
                ).execute()
            else:
                supabase_client.table('vault_files').insert(ledger_record).execute()
            logger.info(f"[Ledger] Stored JSON ledger for {callsign}: {len(entries)} sessions")
        except Exception as store_err:
            logger.warning(f"[Ledger] Failed to store JSON ledger: {store_err}")

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
        if _is_supabase_upstream_timeout(e):
            return _supabase_timeout_write_response("/api/chatty/construct/<construct_id>/ledger/generate")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chatty/construct/<construct_id>/ledger')
@require_chatty_auth
def get_construct_ledger(construct_id):
    """Retrieve a previously generated Continuity Ledger for a construct.
    
    Returns the stored ledger without re-processing transcripts.
    If no ledger exists, returns empty with a hint to generate one.
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        callsign = _normalize_callsign(construct_id)
        output_format = request.args.get('format', 'json')

        if output_format == 'markdown':
            ledger_filename = f'{callsign}_continuity_ledger.md'
        else:
            ledger_filename = f'{callsign}_continuity_ledger.json'

        result = supabase_client.table('vault_files').select(
            'content, metadata'
        ).eq('filename', ledger_filename).eq('construct_id', callsign).execute()

        if not result.data:
            return jsonify({
                "success": True,
                "construct_id": callsign,
                "ledger_exists": False,
                "message": f"No ledger found. POST to /api/chatty/construct/{callsign}/ledger/generate to create one.",
                "sessions": [],
            })

        content = result.data[0].get('content', '')
        metadata = result.data[0].get('metadata', '{}')
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
@app.route('/api/auth/google/health')
def google_oauth_health():
    """Check if Google OAuth is configured"""
    return jsonify({
        "oauth_configured": _google_oauth_ready(),
        "client_id_set": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_ID not in _OAUTH_PLACEHOLDER_VALUES),
        "client_secret_set": bool(GOOGLE_CLIENT_SECRET and GOOGLE_CLIENT_SECRET not in _OAUTH_PLACEHOLDER_VALUES),
        "provider": "google",
        "callback_url": f"{_get_backend_url()}/api/auth/google/callback",
        "frontend_url": _get_frontend_url(),
        "supabase_mode": _get_supabase_mode(),
        "error": None if _google_oauth_ready() else _google_oauth_config_error(),
    })


# Google OAuth Routes
@app.route('/api/auth/google')
@app.route('/api/auth/oauth/google')
def google_oauth_login():
    """Initiate Google OAuth login"""
    if _rate_limit_key("auth"):
        return jsonify({"success": False, "error": "rate_limit_exceeded"}), 429
    try:
        from flask import redirect

        if not google_client or not _google_oauth_ready():
            return jsonify({"success": False, "error": _google_oauth_config_error()}), 500
        
        # Get Google's OAuth endpoints
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]
        
        origin = request.headers.get('Origin', '')
        referer = request.headers.get('Referer', '')
        fwd_host = request.headers.get('X-Forwarded-Host', '')
        req_host = request.headers.get('Host', request.host)
        logger.info(f"OAuth login headers - Origin: {origin}, Referer: {referer}, X-Forwarded-Host: {fwd_host}, Host: {req_host}")
        
        is_replit = 'replit.dev' in origin or 'replit.dev' in referer or 'replit.dev' in fwd_host or 'replit.dev' in req_host
        is_localhost = 'localhost' in req_host or '127.0.0.1' in req_host
        
        # Use http for local development, https for production
        callback_scheme = "http" if is_localhost else "https"
        
        if is_replit and REPLIT_DEV_DOMAIN:
            callback_url = f"https://{REPLIT_DEV_DOMAIN}/api/auth/oauth/google/callback"
        elif OAUTH_BASE_URL or VVAULT_BACKEND_URL:
            callback_url = f"{_get_backend_url()}/api/auth/google/callback"
        else:
            callback_url = f"{callback_scheme}://{req_host}/api/auth/google/callback"

        frontend_origin = origin or ""
        if not frontend_origin and referer:
            frontend_origin = referer.split('/api/auth/google')[0].rstrip('/')
        if not frontend_origin:
            frontend_origin = _get_frontend_url()
        if not _allowed_redirect_base(frontend_origin):
            frontend_origin = _get_frontend_url()

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

@app.route('/api/auth/google/callback')
@app.route('/api/auth/oauth/google/callback')
def google_oauth_callback():
    """Handle Google OAuth callback"""
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
        
        # Get Google's OAuth endpoints
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        token_endpoint = google_provider_cfg["token_endpoint"]
        
        from flask import session as flask_session
        stored_callback = flask_session.pop('oauth_callback_url', None)
        stored_frontend = flask_session.pop('oauth_frontend_url', None)
        
        if stored_callback:
            callback_url = stored_callback
        elif '/api/auth/oauth/google/callback' in request.path and REPLIT_DEV_DOMAIN:
            callback_url = f"https://{REPLIT_DEV_DOMAIN}/api/auth/oauth/google/callback"
        elif OAUTH_BASE_URL or VVAULT_BACKEND_URL:
            callback_url = f"{_get_backend_url()}/api/auth/google/callback"
        else:
            host = request.headers.get('X-Forwarded-Host', request.headers.get('Host', request.host))
            is_localhost = 'localhost' in host or '127.0.0.1' in host
            callback_scheme = "http" if is_localhost else "https"
            callback_url = f"{callback_scheme}://{host}/api/auth/google/callback"
        
        from urllib.parse import urlparse
        parsed = urlparse(callback_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        authorization_response = f"{base}{request.full_path}"
        
        oauth_origin_base = base
        candidate_frontend = stored_frontend or oauth_origin_base
        frontend_url = _get_frontend_url(candidate_frontend) if _allowed_redirect_base(candidate_frontend) else _get_frontend_url()
        
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
        fallback_user = USERS_DB_FALLBACK.get(users_email)
        resolved_role = _resolve_user_role(users_email, fallback_user=fallback_user)

        user_id = None
        if supabase_client:
            try:
                existing = supabase_client.table('users').select('*').eq('email', users_email).execute()
                if existing.data:
                    user_id = existing.data[0]['id']
                    resolved_role = _resolve_user_role(users_email, supabase_user=existing.data[0], fallback_user=fallback_user)
                    logger.info(f"OAuth user exists in Supabase: {users_email} (id={user_id})")
                else:
                    from datetime import timezone as tz
                    ts = int(datetime.now(tz.utc).timestamp() * 1000)
                    safe_name = re.sub(r'[^a-z0-9_]', '_', users_name.lower().strip())
                    user_id = f"{safe_name}_{ts}"
                    _upsert_supabase_user_record(user_id, users_email, users_name, resolved_role)
                    logger.info(f"Created new OAuth user in Supabase: {users_email} (id={user_id})")
            except Exception as db_err:
                logger.warning(f"Supabase user upsert failed, using fallback: {db_err}")
        
        if users_email not in USERS_DB_FALLBACK:
            USERS_DB_FALLBACK[users_email] = {
                'password': None,
                'name': users_name,
                'role': resolved_role
            }
        else:
            USERS_DB_FALLBACK[users_email]['role'] = resolved_role
        
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        
        db_create_session(users_email, resolved_role, session_token, expires_at, remember_me=True)
        
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
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("VVAULT_BACKEND_HOST", "0.0.0.0")
    is_production = os.environ.get("REPL_DEPLOYMENT") == "1" or port == 5000

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

    supabase_status = _get_supabase_status()

    print("🌐 VVAULT Web Server")
    print("=" * 50)
    print(f"🔧 Project Directory: {PROJECT_DIR}")
    print(f"📦 Capsules Directory: {CAPSULES_DIR}")
    print(f"🌐 Server Port: {port}")
    print(f"🏭 Production Mode: {is_production}")
    print(f"🗄️ Supabase Mode: {supabase_status['mode']}")
    print("=" * 50)

    try:
        logger.info(
            "Supabase config: mode=%s service_role=%s anon_key=%s",
            supabase_status["mode"],
            supabase_status["using_service_role"],
            bool(SUPABASE_ANON_KEY),
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
