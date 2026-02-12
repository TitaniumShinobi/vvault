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
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from uuid import uuid4

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import hashlib
import threading
import time
import secrets
import jwt
from datetime import datetime, timedelta
import requests  # For Turnstile verification
from oauthlib.oauth2 import WebApplicationClient

# Supabase client for vault files
try:
    from supabase import create_client
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
except Exception as e:
    supabase_client = None
    print(f"Supabase not configured: {e}")

_server_dir = os.path.dirname(os.path.abspath(__file__))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)
from vxrunner_baseline import convert_capsule_to_baseline
from continuity_parser import ContinuityParser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'dist')
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'assets')
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'public')

app = Flask(__name__, static_folder=DIST_DIR, static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'vvault-secret-key-change-in-production')
_cors_origins = ["http://localhost:7784", "http://localhost:5000", "https://vvault.thewreck.org"]
_replit_domain = os.environ.get("REPLIT_DEV_DOMAIN") or os.environ.get("REPL_SLUG")
if _replit_domain:
    _cors_origins.append(f"https://{_replit_domain}")
CORS(app, origins=_cors_origins)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

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
        
        user_result = supabase_client.table('users').select('role').eq('email', session_data['email']).execute()
        role = user_result.data[0]['role'] if user_result.data else 'user'
        
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
                'role': supabase_user.get('role', 'user'),
                'source': 'supabase'
            }
            if not user_data['password_hash'] and fallback_user:
                user_data['password_hash'] = fallback_user.get('password_hash')
                user_data['role'] = fallback_user.get('role', 'user')
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
            log_auth_decision(
                action="access_granted",
                user_id=chatty_email,
                resource=request.path,
                result="allowed",
                reason="chatty_api_key",
                ip=ip
            )
            request.current_user = {"email": chatty_email}
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
            "uptime_seconds": (datetime.now() - datetime.fromisoformat(self.status["server_started"])).total_seconds()
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
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "vvault-backend",
        "version": "1.0.0"
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
    for f in files:
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
        
        transformed.append(file_copy)
    return transformed

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
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/vault/files')
@require_auth
def get_vault_files():
    """Get vault files from Supabase (multi-tenant: users see only their files)"""
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 500
        
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
            result = supabase_client.table('vault_files').select('id, user_id, is_system, filename, storage_path, construct_id, content, file_type, metadata, created_at').execute()
            logger.debug(f"Admin {user_email} fetching all vault files")
            files = _transform_files_for_display(result.data or [], is_admin=True, user_id=None)
        else:
            if not user_id:
                return jsonify({
                    "success": True,
                    "files": [],
                    "count": 0,
                    "user_root": user_name,
                    "message": "No files yet - upload your first file to get started"
                })
            result = supabase_client.table('vault_files').select('id, user_id, is_system, filename, storage_path, construct_id, content, file_type, metadata, created_at').eq('user_id', user_id).eq('is_system', False).execute()
            logger.debug(f"User {user_email} fetching their vault files (user_id={user_id})")
            files = _transform_files_for_display(result.data or [], is_admin=False, user_id=user_id)
        
        return jsonify({
            "success": True,
            "files": files,
            "count": len(files),
            "user_root": user_name if not is_admin else "Vault (Admin)"
        })
    except Exception as e:
        logger.error(f"Error fetching vault files: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
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
        return jsonify({"success": False, "error": str(e)}), 500


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

        existing = supabase_client.table('vault_files').select('id').eq(
            'construct_id', construct_id
        ).eq('user_id', user_id).eq('filename', vsi_path).execute()

        if existing.data:
            supabase_client.table('vault_files').update({
                'content': content_str,
                'sha256': sha256,
                'metadata': json.dumps(meta),
                'updated_at': now,
            }).eq('id', existing.data[0]['id']).execute()
            action = 'updated'
            file_id = existing.data[0]['id']
        else:
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
            }
            ins_result = supabase_client.table('vault_files').insert(record).execute()
            action = 'created'
            file_id = ins_result.data[0]['id'] if ins_result.data else None

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

        existing = supabase_client.table('vault_files').select('id').eq(
            'construct_id', construct_id
        ).eq('user_id', user_id).eq('filename', vsi_path).execute()

        if existing.data:
            supabase_client.table('vault_files').update({
                'content': injection_str,
                'sha256': sha256,
                'metadata': json.dumps(meta),
                'updated_at': now,
            }).eq('id', existing.data[0]['id']).execute()
            action = 'updated'
            file_id = existing.data[0]['id']
        else:
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
            }
            ins_result = supabase_client.table('vault_files').insert(record).execute()
            action = 'created'
            file_id = ins_result.data[0]['id'] if ins_result.data else None

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
    supabase_status = "connected" if supabase_client else "not_configured"
    service_api_status = "enabled" if VVAULT_SERVICE_TOKEN else "disabled"
    
    # Check Supabase connectivity
    store_status = "unknown"
    if supabase_client:
        try:
            supabase_client.table('strategy_configs').select('id').limit(1).execute()
            store_status = "connected"
        except Exception as e:
            store_status = "error"
            logger.debug(f"Supabase connectivity check failed: {e}")
    else:
        store_status = "not_configured"
    
    overall_status = "ok"
    if store_status != "connected":
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
        
        return jsonify({
            "success": True,
            "key": key,
            "service": service,
            "action": action,
            "message": f"Credential {action} successfully"
        })
        
    except Exception as e:
        logger.error(f"SERVICE_API: Error storing credential: {e}")
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
        
        return jsonify({
            "success": True,
            "service": service,
            "strategy_id": strategy_id,
            "action": action,
            "version": new_version
        })
        
    except Exception as e:
        logger.error(f"SERVICE_API: Error storing config: {e}")
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
            result = supabase_client.table("vault_files").update(update_record).eq("id", file_id).execute()
            action = "updated"
        else:
            insert_record = dict(record)
            insert_record["created_at"] = now
            result = supabase_client.table("vault_files").insert(insert_record).execute()

        logger.info(f"SERVICE_API: System file upserted: {storage_path}")
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
        return jsonify({"success": False, "error": "Failed to upsert system file"}), 500

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
        
        search_filename = f"chat_with_{construct_id}.md"
        
        result = supabase_client.table('vault_files').select('*').ilike('filename', f'%{search_filename}%').execute()
        
        if result.data and len(result.data) > 0:
            file_data = result.data[0]
            return jsonify({
                "success": True,
                "construct_id": construct_id,
                "filename": file_data.get('filename'),
                "content": file_data.get('content'),
                "sha256": file_data.get('sha256'),
                "updated_at": file_data.get('updated_at') or file_data.get('created_at')
            })
        else:
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
        
        data = request.get_json()
        content = data.get('content', '')
        force = data.get('force', False)
        
        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400
        
        import hashlib
        sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
        search_filename = f"chat_with_{construct_id}.md"
        
        existing = supabase_client.table('vault_files').select('id, user_id').ilike('filename', f'%{search_filename}%').execute()
        
        if existing.data and len(existing.data) > 0:
            file_id = existing.data[0]['id']
            
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
                "construct_id": construct_id
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {construct_id}. Create it via migration first."
            }), 404
    except Exception as e:
        logger.error(f"Error updating chatty transcript: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
        
        current_user = request.current_user
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        
        data = request.get_json()
        role = data.get('role', 'user')
        content = data.get('content', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        attachments = data.get('attachments', [])
        
        if not content and not attachments:
            return jsonify({"success": False, "error": "Content or attachments required"}), 400
        
        if role not in ['user', 'assistant', 'system']:
            return jsonify({"success": False, "error": "Role must be 'user', 'assistant', or 'system'"}), 400
        
        search_filename = f"chat_with_{construct_id}.md"
        existing = supabase_client.table('vault_files').select('id, content, filename').eq('user_id', user_id).ilike('filename', f'%{search_filename}%').execute()
        
        if not existing.data or len(existing.data) == 0:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {construct_id}. Send a message first to create it."
            }), 404
        
        file_id = existing.data[0]['id']
        current_content = existing.data[0].get('content', '')
        actual_filename = existing.data[0].get('filename', f"chat_with_{construct_id}.md")
        
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
            "construct_id": construct_id,
            "role": role,
            "message_length": len(content),
            "attachment_count": attachment_count,
            "total_length": len(updated_content)
        })
        
    except Exception as e:
        logger.error(f"Error appending message to transcript: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None

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

        callsign = _normalize_callsign(construct_id)
        bare_name = _bare_name_from_callsign(callsign)
        display_name = bare_name.capitalize()

        identity_files = ['prompt.txt', 'prompt.json', 'personality.json',
                          'CONTINUITY_GPT_PROMPT.md', 'conditioning.txt']

        result = supabase_client.table('vault_files').select(
            'filename, content, file_type'
        ).or_(
            f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
        ).in_('filename', identity_files).not_.is_('content', 'null').execute()

        name = display_name
        description = ""
        instructions = ""
        system_prompt = ""
        personality = None
        conversation_starters = []
        conditioning = ""

        for f in (result.data or []):
            fname = f.get('filename', '')
            content = f.get('content', '') or ''

            if fname == 'prompt.txt':
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

            elif fname == 'prompt.json':
                try:
                    data = json.loads(content)
                    name = data.get('name', name)
                    description = data.get('description', description)
                    instructions = data.get('instructions', instructions)
                    system_prompt = data.get('system_prompt', '') or data.get('prompt', '') or instructions or system_prompt
                    conversation_starters = data.get('conversation_starters', [])
                except json.JSONDecodeError:
                    pass

            elif fname == 'personality.json':
                try:
                    personality = json.loads(content)
                except json.JSONDecodeError:
                    pass

            elif fname == 'conditioning.txt':
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
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
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
                    existing_avatar = supabase_client.table('vault_files').select('id').eq('construct_id', callsign).eq('filename', 'avatar.png').execute()
                    if existing_avatar.data:
                        supabase_client.table('vault_files').update({
                            'content': avatar_b64,
                            'sha256': avatar_sha,
                            'metadata': json.dumps(avatar_meta),
                            'updated_at': now,
                        }).eq('id', existing_avatar.data[0]['id']).execute()
                        avatar_created = True
                    else:
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
                        }
                        av_result = supabase_client.table('vault_files').insert(avatar_record).execute()
                        if av_result.data:
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
            }
            try:
                insert_result = supabase_client.table('vault_files').insert(record).execute()
                if insert_result.data:
                    created_files.append({
                        'id': insert_result.data[0]['id'],
                        'filename': vsi_path,
                        'file_type': file_def['file_type'],
                        'folder': folder,
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
        }
        glyph_result = supabase_client.table('vault_files').insert(glyph_record).execute()
        glyph_created = False
        if glyph_result.data:
            created_files.append({
                'id': glyph_result.data[0]['id'],
                'filename': glyph_filename,
                'file_type': 'binary',
                'folder': 'identity',
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
        
        return jsonify(response_data), 201

    except Exception as e:
        logger.error(f"Error creating construct: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chatty/constructs')
@require_chatty_auth
def get_chatty_constructs():
    """Get all available constructs with chat transcripts (user-scoped).

    Deduplicates bare-name vs callsign entries: if both 'katana' and
    'katana-001' transcripts exist, only 'katana-001' is returned.
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500

        current_user = request.current_user
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None

        if not user_id:
            return jsonify({"success": True, "constructs": [], "count": 0})

        result = supabase_client.table('vault_files').select('filename, metadata, created_at').eq('user_id', user_id).ilike('filename', '%chat_with_%').execute()

        special_roles = {
            'lin-001': {'role': 'undertone', 'context': 'gpt_creator_create_tab', 'is_system': True}
        }

        seen = {}
        for file in (result.data or []):
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

        return jsonify({
            "success": True,
            "constructs": constructs,
            "count": len(constructs)
        })
    except Exception as e:
        logger.error(f"Error fetching chatty constructs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        
        if not construct_id:
            return jsonify({"success": False, "error": "constructId is required"}), 400
        if not user_message:
            return jsonify({"success": False, "error": "message is required"}), 400
        
        current_user = request.current_user
        user_email = current_user.get('email')
        user_id = None
        try:
            user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
            user_id = user_result.data[0]['id'] if user_result.data else None
        except Exception:
            pass

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

        search_filename = f"chat_with_{construct_id}.md"
        file_id = None
        current_content = ''

        if user_id:
            user_chatty_path = _get_user_construct_path(user_id, user_email, construct_id, 'chatty')
            expected_filepath = f"{user_chatty_path}{search_filename}"
            existing = supabase_client.table('vault_files').select('id, content, filename').eq('user_id', user_id).ilike('filename', f'%{search_filename}%').execute()
        else:
            callsign = _normalize_callsign(construct_id)
            bare = _bare_name_from_callsign(callsign)
            expected_filepath = f"instances/{construct_id}/chatty/{search_filename}"
            existing = supabase_client.table('vault_files').select('id, content, filename').or_(f'construct_id.eq.{callsign},construct_id.eq.{bare}').ilike('filename', f'%{search_filename}%').execute()
            logger.info(f"[Message] Service call for {construct_id} (user {user_email} not in users table), querying by construct_id")

        if existing.data and len(existing.data) > 0:
            file_id = existing.data[0]['id']
            current_content = existing.data[0].get('content', '')
            actual_transcript_filename = existing.data[0].get('filename', search_filename)
        else:
            actual_transcript_filename = search_filename
            new_file_data = {
                'filename': expected_filepath,
                'file_type': 'text/markdown',
                'content': f"# Chat with {construct_name}\n\nTranscript started {datetime.now().isoformat()}\n",
                'is_system': False,
                'construct_id': construct_id,
                'metadata': json.dumps({'construct_id': construct_id, 'provider': 'chatty'})
            }
            if user_id:
                new_file_data['user_id'] = user_id
            insert_result = supabase_client.table('vault_files').insert(new_file_data).execute()
            if insert_result.data:
                file_id = insert_result.data[0]['id']
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
            "constructId": construct_id,
            "constructName": construct_name,
            "timestamp": iso_timestamp
        })
        
    except Exception as e:
        logger.error(f"Error in chatty message: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

            result = supabase_client.table('vault_files').select('content, filename').or_(
                f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
            ).in_('filename', ['prompt.json', 'prompt.txt', 'CONTINUITY_GPT_PROMPT.md']).not_.is_('content', 'null').execute()

            for f in (result.data or []):
                content = f.get('content', '') or ''
                fname = f.get('filename', '')
                if not content:
                    continue

                if fname == 'prompt.json':
                    try:
                        prompt_data = json.loads(content)
                        prompt = prompt_data.get('system_prompt', '') or prompt_data.get('instructions', '') or prompt_data.get('prompt', '')
                        if prompt:
                            return prompt
                    except json.JSONDecodeError:
                        pass

                elif fname in ('prompt.txt', 'CONTINUITY_GPT_PROMPT.md'):
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

# Authentication endpoints
@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login endpoint (database-backed)"""
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
                        logger.error(f"Failed to register in Supabase: {e2}")
                else:
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
        'id, filename, content, file_type'
    ).or_(
        f'construct_id.eq.{callsign},construct_id.eq.{bare_name}'
    ).not_.is_('content', 'null').execute()

    transcript_keywords = ['transcript', 'character_ai', 'chatgpt', 'chat_with_', 'conversation', 'chat']
    candidates = []
    for f in (result.data or []):
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


# Google OAuth Routes
@app.route('/api/auth/google')
@app.route('/api/auth/oauth/google')
def google_oauth_login():
    """Initiate Google OAuth login"""
    try:
        from flask import redirect
        
        if not google_client or not GOOGLE_CLIENT_ID:
            return jsonify({"success": False, "error": "Google OAuth not configured"}), 500
        
        # Get Google's OAuth endpoints
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]
        
        origin = request.headers.get('Origin', '')
        referer = request.headers.get('Referer', '')
        fwd_host = request.headers.get('X-Forwarded-Host', '')
        req_host = request.headers.get('Host', request.host)
        logger.info(f"OAuth login headers - Origin: {origin}, Referer: {referer}, X-Forwarded-Host: {fwd_host}, Host: {req_host}")
        
        is_replit = 'replit.dev' in origin or 'replit.dev' in referer or 'replit.dev' in fwd_host or 'replit.dev' in req_host
        
        if is_replit and REPLIT_DEV_DOMAIN:
            callback_url = f"https://{REPLIT_DEV_DOMAIN}/api/auth/oauth/google/callback"
        elif OAUTH_BASE_URL:
            callback_url = f"{OAUTH_BASE_URL}/api/auth/google/callback"
        else:
            callback_url = f"https://{req_host}/api/auth/google/callback"
        
        from flask import session as flask_session
        flask_session['oauth_callback_url'] = callback_url
        
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
    try:
        from flask import redirect
        
        if not google_client or not GOOGLE_CLIENT_ID:
            return jsonify({"success": False, "error": "Google OAuth not configured"}), 500
        
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
        
        if stored_callback:
            callback_url = stored_callback
        elif '/api/auth/oauth/google/callback' in request.path and REPLIT_DEV_DOMAIN:
            callback_url = f"https://{REPLIT_DEV_DOMAIN}/api/auth/oauth/google/callback"
        elif OAUTH_BASE_URL:
            callback_url = f"{OAUTH_BASE_URL}/api/auth/google/callback"
        else:
            host = request.headers.get('X-Forwarded-Host', request.headers.get('Host', request.host))
            callback_url = f"https://{host}/api/auth/google/callback"
        
        from urllib.parse import urlparse
        parsed = urlparse(callback_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        authorization_response = f"{base}{request.full_path}"
        
        oauth_origin_base = base
        
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
        
        # Parse the token response
        google_client.parse_request_body_response(json.dumps(token_response.json()))
        
        # Get user info from Google
        userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
        uri, headers, body = google_client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)
        userinfo = userinfo_response.json()
        
        # Verify email
        if not userinfo.get("email_verified"):
            return jsonify({"success": False, "error": "Email not verified by Google"}), 400
        
        users_email = userinfo["email"]
        users_name = userinfo.get("given_name", userinfo.get("name", "User"))
        
        user_id = None
        if supabase_client:
            try:
                existing = supabase_client.table('users').select('id, name').eq('email', users_email).execute()
                if existing.data:
                    user_id = existing.data[0]['id']
                    logger.info(f"OAuth user exists in Supabase: {users_email} (id={user_id})")
                else:
                    from datetime import timezone as tz
                    ts = int(datetime.now(tz.utc).timestamp() * 1000)
                    safe_name = re.sub(r'[^a-z0-9_]', '_', users_name.lower().strip())
                    user_id = f"{safe_name}_{ts}"
                    supabase_client.table('users').insert({
                        'id': user_id,
                        'email': users_email,
                        'name': users_name,
                        'role': 'user'
                    }).execute()
                    logger.info(f"Created new OAuth user in Supabase: {users_email} (id={user_id})")
            except Exception as db_err:
                logger.warning(f"Supabase user upsert failed, using fallback: {db_err}")
        
        if users_email not in USERS_DB_FALLBACK:
            USERS_DB_FALLBACK[users_email] = {
                'password': None,
                'name': users_name,
                'role': 'user'
            }
        
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        
        db_create_session(users_email, 'user', session_token, expires_at, remember_me=True)
        
        logger.info(f"Google OAuth login successful: {users_email}")
        
        from urllib.parse import quote
        frontend_url = oauth_origin_base
        encoded_email = quote(users_email, safe='')
        encoded_name = quote(users_name, safe='')
        redirect_url = f"{frontend_url}/?token={session_token}&email={encoded_email}&name={encoded_name}"
        logger.info(f"Redirecting to: {redirect_url}")
        return redirect(redirect_url)
        
    except Exception as e:
        logger.error(f"Google OAuth callback error: {e}")
        return jsonify({"success": False, "error": "OAuth callback failed"}), 500

# Error handlers
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
    is_production = os.environ.get("REPL_DEPLOYMENT") == "1" or port == 5000
    
    print("🌐 VVAULT Web Server")
    print("=" * 50)
    print(f"🔧 Project Directory: {PROJECT_DIR}")
    print(f"📦 Capsules Directory: {CAPSULES_DIR}")
    print(f"🌐 Server Port: {port}")
    print(f"🏭 Production Mode: {is_production}")
    print("=" * 50)
    
    try:
        logger.info(f"🚀 Starting VVAULT Web Server on port {port}...")
        app.run(
            host='0.0.0.0',
            port=port,
            debug=not is_production,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n🛑 VVAULT Web Server stopped by user")
    except Exception as e:
        print(f"❌ VVAULT Web Server error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()