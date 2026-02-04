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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask app configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = 'vvault-secret-key-change-in-production'
CORS(app, origins=["http://localhost:7784"])  # Allow requests from frontend

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

# Service API Configuration (for FXShinobi/Chatty backend-to-backend calls)
VVAULT_SERVICE_TOKEN = os.environ.get("VVAULT_SERVICE_TOKEN")
VVAULT_ENCRYPTION_KEY = os.environ.get("VVAULT_ENCRYPTION_KEY", os.environ.get("SECRET_KEY", "default-encryption-key"))

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
                'oauth_provider': supabase_user.get('oauth_provider'),
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
    """Root endpoint - minimal status response"""
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

def _transform_files_for_display(files: list, is_admin: bool = False, user_id: str = None) -> list:
    """Transform file paths for user-friendly display, filtering out system files.
    
    For regular users: Use storage_path, strip bucket prefix and user folder for display
    Storage path format: {user_id}/{user_slug}/...
    Display path format: capsules/..., instances/..., etc.
    """
    import re
    UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/')
    USER_SLUG_PATTERN = re.compile(r'^[a-z_]+_\d+/')
    
    transformed = []
    for f in files:
        if f.get('is_system') and not is_admin:
            continue
        
        file_copy = dict(f)
        storage_path = f.get('storage_path') or f.get('filename') or ''
        
        if not is_admin and user_id:
            display_path = storage_path
            display_path = UUID_PATTERN.sub('', display_path)
            display_path = USER_SLUG_PATTERN.sub('', display_path)
            
            if not display_path:
                display_path = f.get('filename', 'unknown')
            
            file_copy['display_path'] = display_path
            file_copy['storage_path'] = storage_path
            file_copy['internal_path'] = storage_path
            
            metadata = file_copy.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}
            metadata['original_path'] = display_path
            file_copy['metadata'] = metadata
        else:
            file_copy['display_path'] = storage_path
            file_copy['storage_path'] = storage_path
            file_copy['internal_path'] = storage_path
        
        transformed.append(file_copy)
    return transformed

@app.route('/api/vault/user-info')
@require_auth
def get_vault_user_info():
    """Get current user's vault info (display name, root path, etc.)"""
    try:
        current_user = request.current_user
        user_email = current_user.get('email')
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
        
        current_user = request.current_user
        user_email = current_user.get('email')
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

# ============================================================================
# END SERVICE API ENDPOINTS
# ============================================================================

@app.route('/api/chatty/transcript/<construct_id>')
@require_auth
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
@require_auth
def update_chatty_transcript(construct_id):
    """Update or create chat transcript for a construct - used by Chatty integration
    
    POST body: { "content": "full markdown content" }
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        data = request.get_json()
        content = data.get('content', '')
        
        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400
        
        import hashlib
        sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
        search_filename = f"chat_with_{construct_id}.md"
        
        existing = supabase_client.table('vault_files').select('id, user_id').ilike('filename', f'%{search_filename}%').execute()
        
        if existing.data and len(existing.data) > 0:
            file_id = existing.data[0]['id']
            supabase_client.table('vault_files').update({
                'content': content,
                'sha256': sha256
            }).eq('id', file_id).execute()
            
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
@require_auth
def append_chatty_message(construct_id):
    """Append a single message to a construct's transcript
    
    POST body: {
        "role": "user" | "assistant" | "system",
        "content": "message text",
        "timestamp": "2026-01-20T12:00:00Z" (optional, defaults to now)
    }
    """
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        # Get current user for scoped queries
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
        
        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400
        
        if role not in ['user', 'assistant', 'system']:
            return jsonify({"success": False, "error": "Role must be 'user', 'assistant', or 'system'"}), 400
        
        # Query only the user's files
        search_filename = f"chat_with_{construct_id}.md"
        existing = supabase_client.table('vault_files').select('id, content').eq('user_id', user_id).ilike('filename', f'%{search_filename}%').execute()
        
        if not existing.data or len(existing.data) == 0:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {construct_id}. Send a message first to create it."
            }), 404
        
        file_id = existing.data[0]['id']
        current_content = existing.data[0].get('content', '')
        
        role_label = "**User**" if role == "user" else f"**{construct_id.split('-')[0].title()}**" if role == "assistant" else "**System**"
        formatted_message = f"\n\n---\n\n{role_label} ({timestamp}):\n\n{content}"
        
        updated_content = current_content + formatted_message
        
        import hashlib
        sha256 = hashlib.sha256(updated_content.encode('utf-8')).hexdigest()
        
        supabase_client.table('vault_files').update({
            'content': updated_content,
            'sha256': sha256
        }).eq('id', file_id).execute()
        
        logger.info(f"Appended {role} message to {construct_id} transcript")
        
        return jsonify({
            "success": True,
            "action": "appended",
            "construct_id": construct_id,
            "role": role,
            "message_length": len(content),
            "total_length": len(updated_content)
        })
        
    except Exception as e:
        logger.error(f"Error appending message to transcript: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/chatty/constructs')
@require_auth
def get_chatty_constructs():
    """Get all available constructs with chat transcripts (user-scoped)"""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        # Get current user for scoped query
        current_user = request.current_user
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        
        if not user_id:
            return jsonify({"success": True, "constructs": [], "count": 0})
        
        # Query only user's chat files
        result = supabase_client.table('vault_files').select('filename, metadata, created_at').eq('user_id', user_id).ilike('filename', '%chat_with_%').execute()
        
        constructs = []
        # Special construct roles - Lin has dual-mode: conversational + undertone stabilizer
        special_roles = {
            'lin-001': {'role': 'undertone', 'context': 'gpt_creator_create_tab', 'is_system': True}
        }
        
        for file in (result.data or []):
            filename = file.get('filename', '')
            # Extract construct_id from filename (chat_with_katana-001.md)
            basename = filename.split('/')[-1] if '/' in filename else filename
            if basename.startswith('chat_with_') and basename.endswith('.md'):
                construct_id = basename.replace('chat_with_', '').replace('.md', '')
                construct_data = {
                    "construct_id": construct_id,
                    "filename": basename,
                    "created_at": file.get('created_at')
                }
                # Add special role metadata if applicable
                if construct_id in special_roles:
                    construct_data.update(special_roles[construct_id])
                constructs.append(construct_data)
        
        return jsonify({
            "success": True,
            "constructs": constructs,
            "count": len(constructs)
        })
    except Exception as e:
        logger.error(f"Error fetching chatty constructs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chatty/message', methods=['POST'])
@require_auth
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
        
        # Get current user info for path construction
        current_user = request.current_user
        user_email = current_user.get('email')
        user_result = supabase_client.table('users').select('id').eq('email', user_email).execute()
        user_id = user_result.data[0]['id'] if user_result.data else None
        
        if not user_id:
            return jsonify({"success": False, "error": "User not found"}), 403
        
        # Get construct name from ID (e.g., zen-001 -> Zen)
        construct_name = construct_id.split('-')[0].title()
        
        # Load construct identity/system prompt
        system_prompt = _load_construct_identity(construct_id, construct_name)
        
        # Call Ollama for LLM inference
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
            
            # Check for non-200 responses
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
        
        # Format timestamp - use UTC for ISO, convert to user timezone for display
        from datetime import timezone as tz
        now_utc = datetime.now(tz.utc)
        iso_timestamp = now_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now_utc.microsecond // 1000:03d}Z'
        
        # Convert to EST for human-readable display (UTC-5)
        est_offset = timedelta(hours=-5)
        now_est = now_utc + est_offset
        human_time = now_est.strftime('%I:%M:%S %p').lstrip('0')
        date_header = now_est.strftime('%B %d, %Y')
        
        # Get current transcript - query user's files only
        search_filename = f"chat_with_{construct_id}.md"
        user_chatty_path = _get_user_construct_path(user_id, user_email, construct_id, 'chatty')
        expected_filepath = f"{user_chatty_path}{search_filename}"
        
        file_id = None
        current_content = ''
        
        # Query for user's transcript file (must belong to this user)
        existing = supabase_client.table('vault_files').select('id, content, filename').eq('user_id', user_id).ilike('filename', f'%{search_filename}%').execute()
        
        if existing.data and len(existing.data) > 0:
            file_id = existing.data[0]['id']
            current_content = existing.data[0].get('content', '')
        else:
            # Create new transcript file for user's construct
            new_file_data = {
                'filename': expected_filepath,
                'file_type': 'text/markdown',
                'content': f"# Chat with {construct_name}\n\nTranscript started {datetime.now().isoformat()}\n",
                'user_id': user_id,
                'is_system': False,
                'metadata': json.dumps({'construct_id': construct_id, 'provider': 'chatty'})
            }
            insert_result = supabase_client.table('vault_files').insert(new_file_data).execute()
            if insert_result.data:
                file_id = insert_result.data[0]['id']
                current_content = new_file_data['content']
                logger.info(f"Created new transcript at {expected_filepath} for user {user_id}")
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
        
        # Update transcript in Supabase
        sha256 = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
        supabase_client.table('vault_files').update({
            'content': new_content,
            'sha256': sha256,
            'updated_at': datetime.now().isoformat()
        }).eq('id', file_id).execute()
        
        logger.info(f"Message exchange with {construct_id}: user sent {len(user_message)} chars, got {len(assistant_response)} chars")
        
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
    """Load the system prompt for a construct from its identity files"""
    try:
        # Try to load from prompt.json in construct's identity folder
        prompt_path = os.path.join(PROJECT_DIR, 'instances', construct_id, 'identity', 'prompt.json')
        if os.path.exists(prompt_path):
            with open(prompt_path, 'r') as f:
                prompt_data = json.load(f)
                return prompt_data.get('system_prompt', '') or prompt_data.get('prompt', '')
        
        # Try to load from Supabase if not local
        if supabase_client:
            result = supabase_client.table('vault_files').select('content').eq('construct_id', construct_id).ilike('filename', '%prompt.json%').execute()
            if result.data and len(result.data) > 0:
                content = result.data[0].get('content', '{}')
                prompt_data = json.loads(content)
                return prompt_data.get('system_prompt', '') or prompt_data.get('prompt', '')
        
        # Fallback default prompt
        return f"You are {construct_name}, an AI assistant. Be helpful, concise, and friendly."
    except Exception as e:
        logger.warning(f"Could not load identity for {construct_id}: {e}")
        return f"You are {construct_name}, an AI assistant. Be helpful, concise, and friendly."


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

@app.route('/api/auth/register', methods=['POST'])
def register():
    """User registration endpoint with bcrypt password hashing and Supabase storage"""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    try:
        data = request.get_json()
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
        
        user_data = {'email': email, 'name': name, 'role': 'user'}
        log_auth_decision('registration_success', email, '/api/auth/register', 'allowed', 'user_created', ip)
        logger.info(f"New user registered: {email}")
        
        return jsonify({
            "success": True,
            "user": user_data,
            "token": token,
            "expires_at": expires_at.isoformat(),
            "message": "Registration successful"
        })
        
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

# Google OAuth Routes
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
        
        # Build callback URL - use REPLIT_DEV_DOMAIN for Replit environment
        if REPLIT_DEV_DOMAIN and 'replit.dev' in REPLIT_DEV_DOMAIN:
            host = REPLIT_DEV_DOMAIN
        else:
            host = request.headers.get('X-Forwarded-Host', request.headers.get('Host', request.host))
        callback_url = f"https://{host}/api/auth/oauth/google/callback"
        
        # Prepare the OAuth request
        request_uri = google_client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=callback_url,
            scope=["openid", "email", "profile"],
        )
        
        logger.info(f"Redirecting to Google OAuth with callback: {callback_url}")
        return redirect(request_uri)
        
    except Exception as e:
        logger.error(f"Google OAuth init error: {e}")
        return jsonify({"success": False, "error": "OAuth initialization failed"}), 500

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
        
        # Build callback URL (must match initial request)
        if REPLIT_DEV_DOMAIN and 'replit.dev' in REPLIT_DEV_DOMAIN:
            host = REPLIT_DEV_DOMAIN
        else:
            host = request.headers.get('X-Forwarded-Host', request.headers.get('Host', request.host))
        callback_url = f"https://{host}/api/auth/oauth/google/callback"
        authorization_response = f"https://{host}{request.full_path}"
        
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
        
        # Create or get user in fallback database
        if users_email not in USERS_DB_FALLBACK:
            USERS_DB_FALLBACK[users_email] = {
                'password': None,
                'name': users_name,
                'role': 'user',
                'oauth_provider': 'google'
            }
            logger.info(f"Created new OAuth user: {users_email}")
        
        # Create session token (30 days for OAuth logins)
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        
        ACTIVE_SESSIONS[session_token] = {
            'email': users_email,
            'user_id': users_email,
            'expires_at': expires_at,
            'created_at': datetime.now()
        }
        
        logger.info(f"Google OAuth login successful: {users_email}")
        
        # Redirect to frontend with token (URL encode email and name)
        from urllib.parse import quote
        frontend_url = f"https://{REPLIT_DEV_DOMAIN}"
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

def main():
    """Main entry point for VVAULT Web Server"""
    print("🌐 VVAULT Web Server")
    print("=" * 50)
    print(f"🔧 Project Directory: {PROJECT_DIR}")
    print(f"📦 Capsules Directory: {CAPSULES_DIR}")
    print(f"🌐 Backend Server: http://localhost:8000")
    print(f"🎨 Frontend Server: http://localhost:7784")
    print("=" * 50)
    
    try:
        logger.info("🚀 Starting VVAULT Web Server on port 8000...")
        app.run(
            host='0.0.0.0',
            port=8000,
            debug=True,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n🛑 VVAULT Web Server stopped by user")
    except Exception as e:
        print(f"❌ VVAULT Web Server error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()