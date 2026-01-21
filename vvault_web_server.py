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

# Mock user database (replace with real database in production)
USERS_DB = {
    'admin@vvault.com': {
        'password': 'admin123',
        'name': 'Admin User',
        'role': 'admin'
    },
    'user@vvault.com': {
        'password': 'user123', 
        'name': 'Regular User',
        'role': 'user'
    },
    'test@vvault.com': {
        'password': 'test123',
        'name': 'Test User', 
        'role': 'user'
    }
}

# Active sessions (replace with Redis/database in production)
ACTIVE_SESSIONS = {}

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

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "vvault-backend",
        "version": "1.0.0"
    })

@app.route('/api/vault/files')
def get_vault_files():
    """Get all vault files from Supabase"""
    try:
        if not supabase_client:
            return jsonify({
                "success": False,
                "error": "Supabase not configured"
            }), 500
        
        result = supabase_client.table('vault_files').select('*').execute()
        
        return jsonify({
            "success": True,
            "files": result.data or [],
            "count": len(result.data) if result.data else 0
        })
    except Exception as e:
        logger.error(f"Error fetching vault files: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/vault/files/<file_id>')
def get_vault_file(file_id):
    """Get a single vault file by ID"""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        result = supabase_client.table('vault_files').select('*').eq('id', file_id).single().execute()
        
        if result.data:
            return jsonify({"success": True, "file": result.data})
        else:
            return jsonify({"success": False, "error": "File not found"}), 404
    except Exception as e:
        logger.error(f"Error fetching vault file: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/chatty/transcript/<construct_id>')
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
        
        data = request.get_json()
        role = data.get('role', 'user')
        content = data.get('content', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        
        if not content:
            return jsonify({"success": False, "error": "Content is required"}), 400
        
        if role not in ['user', 'assistant', 'system']:
            return jsonify({"success": False, "error": "Role must be 'user', 'assistant', or 'system'"}), 400
        
        search_filename = f"chat_with_{construct_id}.md"
        existing = supabase_client.table('vault_files').select('id, content').ilike('filename', f'%{search_filename}%').execute()
        
        if not existing.data or len(existing.data) == 0:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {construct_id}"
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
def get_chatty_constructs():
    """Get all available constructs with chat transcripts"""
    try:
        if not supabase_client:
            return jsonify({"success": False, "error": "Supabase not configured"}), 500
        
        result = supabase_client.table('vault_files').select('filename, construct_id, created_at').ilike('filename', '%chat_with_%').execute()
        
        constructs = []
        # Special construct roles - Lin has dual-mode: conversational + undertone stabilizer
        special_roles = {
            'lin-001': {'role': 'undertone', 'context': 'gpt_creator_create_tab', 'is_system': True}
        }
        
        for file in (result.data or []):
            filename = file.get('filename', '')
            if filename.startswith('chat_with_') and filename.endswith('.md'):
                construct_id = filename.replace('chat_with_', '').replace('.md', '')
                construct_data = {
                    "construct_id": construct_id,
                    "filename": filename,
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
        
        # Get current transcript - check local first, then Supabase
        search_filename = f"chat_with_{construct_id}.md"
        local_transcript_path = os.path.join(PROJECT_DIR, 'instances', construct_id, 'chatty', search_filename)
        use_local = os.path.exists(local_transcript_path)
        file_id = None
        current_content = ''
        
        if use_local:
            with open(local_transcript_path, 'r') as f:
                current_content = f.read()
        elif supabase_client:
            existing = supabase_client.table('vault_files').select('id, content').ilike('filename', f'%{search_filename}%').execute()
            if existing.data and len(existing.data) > 0:
                file_id = existing.data[0]['id']
                current_content = existing.data[0].get('content', '')
            else:
                return jsonify({
                    "success": False,
                    "error": f"Transcript not found for {construct_id}. Create it via migration first."
                }), 404
        else:
            return jsonify({
                "success": False,
                "error": f"Transcript not found for {construct_id}. No local file or Supabase connection."
            }), 404
        
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
        
        # Update transcript - local file or Supabase
        if use_local:
            with open(local_transcript_path, 'w') as f:
                f.write(new_content)
        else:
            sha256 = hashlib.sha256(new_content.encode('utf-8')).hexdigest()
            supabase_client.table('vault_files').update({
                'content': new_content,
                'sha256': sha256
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
    """User login endpoint"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        # Validate input
        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400
        
        # Check if user exists
        if email not in USERS_DB:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401
        
        user_data = USERS_DB[email]
        
        # Verify password (in production, use proper password hashing)
        if user_data['password'] != password:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401
        
        # Create session token
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)
        
        # Store session
        ACTIVE_SESSIONS[session_token] = {
            'email': email,
            'user_id': email,
            'expires_at': expires_at,
            'created_at': datetime.now()
        }
        
        # Return user data and token
        user_info = {
            'email': email,
            'name': user_data['name'],
            'role': user_data['role']
        }
        
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
    """User registration endpoint"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        confirm_password = data.get('confirmPassword', '')
        name = data.get('name', '').strip()
        turnstile_token = data.get('turnstileToken', '')
        
        # Validate input
        if not email or not password or not confirm_password or not name:
            return jsonify({"success": False, "error": "All fields are required"}), 400
        
        # Validate email format
        if '@' not in email or '.' not in email.split('@')[1]:
            return jsonify({"success": False, "error": "Invalid email format"}), 400
        
        # Validate password confirmation
        if password != confirm_password:
            return jsonify({"success": False, "error": "Passwords do not match"}), 400
        
        # Validate password strength
        if len(password) < 6:
            return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400
        
        # Check if user already exists
        if email in USERS_DB:
            return jsonify({"success": False, "error": "User already exists"}), 409
        
        # Verify Turnstile token
        if not verify_turnstile_token(turnstile_token, request.remote_addr):
            return jsonify({"success": False, "error": "Human verification failed. Please try again."}), 400
        
        # Create new user (in production, hash the password)
        USERS_DB[email] = {
            'password': password,  # In production, use proper password hashing
            'name': name,
            'role': 'user',
            'created_at': datetime.now().isoformat()
        }
        
        # Generate session token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)
        
        ACTIVE_SESSIONS[token] = {
            'email': email,
            'expires_at': expires_at,
            'created_at': datetime.now()
        }
        
        user_data = {
            'email': email,
            'name': name,
            'role': 'user'
        }
        
        logger.info(f"New user registered: {email}")
        
        return jsonify({
            "success": True,
            "user": user_data,
            "token": token,
            "message": "Registration successful"
        })
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({"success": False, "error": "Registration failed"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """User logout endpoint"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            
            # Remove session
            if token in ACTIVE_SESSIONS:
                user_email = ACTIVE_SESSIONS[token]['email']
                del ACTIVE_SESSIONS[token]
                logger.info(f"User logged out: {user_email}")
        
        return jsonify({"success": True, "message": "Logged out successfully"})
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return jsonify({"success": False, "error": "Logout failed"}), 500

@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    """Verify authentication token"""
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"success": False, "error": "No token provided"}), 401
        
        token = auth_header.split(' ')[1]
        
        # Check if session exists and is valid
        if token not in ACTIVE_SESSIONS:
            return jsonify({"success": False, "error": "Invalid token"}), 401
        
        session = ACTIVE_SESSIONS[token]
        
        # Check if session expired
        if datetime.now() > session['expires_at']:
            del ACTIVE_SESSIONS[token]
            return jsonify({"success": False, "error": "Token expired"}), 401
        
        # Return user info
        email = session['email']
        user_data = USERS_DB[email]
        
        user_info = {
            'email': email,
            'name': user_data['name'],
            'role': user_data['role']
        }
        
        return jsonify({
            "success": True,
            "user": user_info,
            "token": token
        })
        
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        return jsonify({"success": False, "error": "Token verification failed"}), 500

def get_current_user():
    """Helper function to get current user from token"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        
        token = auth_header.split(' ')[1]
        
        if token not in ACTIVE_SESSIONS:
            return None
        
        session = ACTIVE_SESSIONS[token]
        
        # Check if session expired
        if datetime.now() > session['expires_at']:
            del ACTIVE_SESSIONS[token]
            return None
        
        return session['email']
        
    except Exception as e:
        logger.error(f"Get current user error: {e}")
        return None

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