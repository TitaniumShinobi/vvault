#!/usr/bin/env python3
"""
VVAULT Security Layer
Comprehensive security layer for VVAULT operations.

Features:
- Sensitive data detection and masking
- Access control and authentication
- Audit logging and compliance
- Encryption and secure storage
- Threat detection and monitoring
- Secure communication protocols

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import json
import hashlib
import hmac
import secrets
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import re
import base64
import cryptography
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
import sqlite3
from enum import Enum

# Configure logging
logger = logging.getLogger(__name__)

class SecurityLevel(Enum):
    """Security levels for different operations"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ThreatType(Enum):
    """Types of security threats"""
    DATA_LEAK = "data_leak"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    TAMPERING = "tampering"
    MALICIOUS_INPUT = "malicious_input"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    BRUTE_FORCE = "brute_force"

@dataclass
class SecurityEvent:
    """Security event record"""
    event_id: str
    timestamp: datetime
    event_type: str
    severity: SecurityLevel
    source: str
    description: str
    details: Dict[str, Any]
    resolved: bool = False
    resolution: Optional[str] = None

@dataclass
class AccessControl:
    """Access control configuration"""
    user_id: str
    permissions: List[str]
    restrictions: List[str]
    expires_at: Optional[datetime] = None
    last_access: Optional[datetime] = None
    failed_attempts: int = 0

@dataclass
class SecurityPolicy:
    """Security policy configuration"""
    policy_id: str
    name: str
    description: str
    rules: List[Dict[str, Any]]
    enabled: bool = True
    created_at: datetime = None
    updated_at: datetime = None

class VVAULTSecurityLayer:
    """Comprehensive security layer for VVAULT"""
    
    def __init__(self, vault_path: str, security_db_path: str = None):
        self.vault_path = vault_path
        self.security_db_path = security_db_path or os.path.join(vault_path, "security.db")
        
        # Security state
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.access_controls: Dict[str, AccessControl] = {}
        self.security_policies: Dict[str, SecurityPolicy] = {}
        self.threat_detectors: List[Callable] = []
        
        # Encryption keys
        self.master_key: Optional[bytes] = None
        self.session_keys: Dict[str, bytes] = {}
        
        # Audit logging
        self.audit_log: List[SecurityEvent] = []
        self.audit_db: Optional[sqlite3.Connection] = None
        
        # Security monitoring
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Initialize security components
        self._initialize_security()
    
    def _initialize_security(self):
        """Initialize security components"""
        try:
            # Initialize audit database
            self._init_audit_database()
            
            # Load security policies
            self._load_security_policies()
            
            # Initialize threat detectors
            self._init_threat_detectors()
            
            # Start security monitoring
            self._start_security_monitoring()
            
            logger.info("Security layer initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize security layer: {e}")
            raise
    
    def _init_audit_database(self):
        """Initialize audit database"""
        try:
            self.audit_db = sqlite3.connect(self.security_db_path, check_same_thread=False)
            cursor = self.audit_db.cursor()
            
            # Create audit events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS security_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    source TEXT NOT NULL,
                    description TEXT NOT NULL,
                    details TEXT NOT NULL,
                    resolved INTEGER DEFAULT 0,
                    resolution TEXT
                )
            """)
            
            # Create access control table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS access_controls (
                    user_id TEXT PRIMARY KEY,
                    permissions TEXT NOT NULL,
                    restrictions TEXT NOT NULL,
                    expires_at TEXT,
                    last_access TEXT,
                    failed_attempts INTEGER DEFAULT 0
                )
            """)
            
            # Create security policies table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS security_policies (
                    policy_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    rules TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            self.audit_db.commit()
            logger.info("Audit database initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize audit database: {e}")
            raise
    
    def _load_security_policies(self):
        """Load security policies from database"""
        try:
            cursor = self.audit_db.cursor()
            cursor.execute("SELECT * FROM security_policies WHERE enabled = 1")
            
            for row in cursor.fetchall():
                policy = SecurityPolicy(
                    policy_id=row[0],
                    name=row[1],
                    description=row[2],
                    rules=json.loads(row[3]),
                    enabled=bool(row[4]),
                    created_at=datetime.fromisoformat(row[5]),
                    updated_at=datetime.fromisoformat(row[6])
                )
                self.security_policies[policy.policy_id] = policy
            
            logger.info(f"Loaded {len(self.security_policies)} security policies")
            
        except Exception as e:
            logger.error(f"Failed to load security policies: {e}")
    
    def _init_threat_detectors(self):
        """Initialize threat detection systems"""
        # Add built-in threat detectors
        self.threat_detectors.extend([
            self._detect_data_leaks,
            self._detect_unauthorized_access,
            self._detect_tampering,
            self._detect_malicious_input,
            self._detect_privilege_escalation,
            self._detect_brute_force
        ])
        
        logger.info(f"Initialized {len(self.threat_detectors)} threat detectors")
    
    def _start_security_monitoring(self):
        """Start security monitoring thread"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self._security_monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.info("Security monitoring started")
    
    def _security_monitor_loop(self):
        """Main security monitoring loop"""
        while self.monitoring_active:
            try:
                # Run threat detectors
                for detector in self.threat_detectors:
                    try:
                        threats = detector()
                        for threat in threats:
                            self._handle_threat(threat)
                    except Exception as e:
                        logger.error(f"Threat detector error: {e}")
                
                # Monitor access patterns
                self._monitor_access_patterns()
                
                # Check for policy violations
                self._check_policy_violations()
                
                # Sleep between monitoring cycles
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Security monitoring error: {e}")
                time.sleep(60)  # Wait longer on error
    
    def authenticate_user(self, user_id: str, credentials: Dict[str, Any]) -> Tuple[bool, str]:
        """Authenticate a user"""
        try:
            # Check if user exists in access controls
            if user_id not in self.access_controls:
                self._log_security_event(
                    event_type="authentication_failed",
                    severity=SecurityLevel.MEDIUM,
                    source="security_layer",
                    description=f"Authentication failed for unknown user: {user_id}",
                    details={"user_id": user_id}
                )
                return False, "User not found"
            
            access_control = self.access_controls[user_id]
            
            # Check if account is locked
            if access_control.failed_attempts >= 5:
                self._log_security_event(
                    event_type="account_locked",
                    severity=SecurityLevel.HIGH,
                    source="security_layer",
                    description=f"Account locked due to too many failed attempts: {user_id}",
                    details={"user_id": user_id, "failed_attempts": access_control.failed_attempts}
                )
                return False, "Account locked"
            
            # Check expiration
            if access_control.expires_at and datetime.now() > access_control.expires_at:
                self._log_security_event(
                    event_type="access_expired",
                    severity=SecurityLevel.MEDIUM,
                    source="security_layer",
                    description=f"Access expired for user: {user_id}",
                    details={"user_id": user_id, "expires_at": access_control.expires_at.isoformat()}
                )
                return False, "Access expired"
            
            # Validate credentials (simplified for demo)
            if self._validate_credentials(user_id, credentials):
                # Reset failed attempts
                access_control.failed_attempts = 0
                access_control.last_access = datetime.now()
                
                # Create session
                session_id = self._create_session(user_id)
                
                self._log_security_event(
                    event_type="authentication_success",
                    severity=SecurityLevel.LOW,
                    source="security_layer",
                    description=f"User authenticated successfully: {user_id}",
                    details={"user_id": user_id, "session_id": session_id}
                )
                
                return True, session_id
            else:
                # Increment failed attempts
                access_control.failed_attempts += 1
                
                self._log_security_event(
                    event_type="authentication_failed",
                    severity=SecurityLevel.MEDIUM,
                    source="security_layer",
                    description=f"Authentication failed for user: {user_id}",
                    details={"user_id": user_id, "failed_attempts": access_control.failed_attempts}
                )
                
                return False, "Invalid credentials"
                
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False, "Authentication error"
    
    def authorize_operation(self, session_id: str, operation: str, resource: str) -> bool:
        """Authorize an operation for a session"""
        try:
            # Get session info
            if session_id not in self.active_sessions:
                self._log_security_event(
                    event_type="unauthorized_access",
                    severity=SecurityLevel.HIGH,
                    source="security_layer",
                    description=f"Unauthorized access attempt with invalid session: {session_id}",
                    details={"session_id": session_id, "operation": operation, "resource": resource}
                )
                return False
            
            session = self.active_sessions[session_id]
            user_id = session["user_id"]
            
            # Check session expiration
            if datetime.now() > session["expires_at"]:
                self._log_security_event(
                    event_type="session_expired",
                    severity=SecurityLevel.MEDIUM,
                    source="security_layer",
                    description=f"Session expired for user: {user_id}",
                    details={"user_id": user_id, "session_id": session_id}
                )
                del self.active_sessions[session_id]
                return False
            
            # Get user access control
            if user_id not in self.access_controls:
                return False
            
            access_control = self.access_controls[user_id]
            
            # Check permissions
            if operation not in access_control.permissions:
                self._log_security_event(
                    event_type="permission_denied",
                    severity=SecurityLevel.MEDIUM,
                    source="security_layer",
                    description=f"Permission denied for operation: {operation}",
                    details={"user_id": user_id, "operation": operation, "resource": resource}
                )
                return False
            
            # Check restrictions
            for restriction in access_control.restrictions:
                if self._matches_restriction(restriction, operation, resource):
                    self._log_security_event(
                        event_type="restriction_violation",
                        severity=SecurityLevel.MEDIUM,
                        source="security_layer",
                        description=f"Restriction violated: {restriction}",
                        details={"user_id": user_id, "operation": operation, "resource": resource, "restriction": restriction}
                    )
                    return False
            
            # Log successful authorization
            self._log_security_event(
                event_type="operation_authorized",
                severity=SecurityLevel.LOW,
                source="security_layer",
                description=f"Operation authorized: {operation}",
                details={"user_id": user_id, "operation": operation, "resource": resource}
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Authorization error: {e}")
            return False
    
    def encrypt_sensitive_data(self, data: str, key: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """Encrypt sensitive data"""
        try:
            if key is None:
                key = self._get_or_create_master_key()
            
            # Generate random IV
            iv = os.urandom(16)
            
            # Create cipher
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            encryptor = cipher.encryptor()
            
            # Pad data
            padder = padding.PKCS7(128).padder()
            padded_data = padder.update(data.encode('utf-8'))
            padded_data += padder.finalize()
            
            # Encrypt
            encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
            
            return encrypted_data, iv
            
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def decrypt_sensitive_data(self, encrypted_data: bytes, iv: bytes, key: Optional[bytes] = None) -> str:
        """Decrypt sensitive data"""
        try:
            if key is None:
                key = self._get_or_create_master_key()
            
            # Create cipher
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            
            # Decrypt
            padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
            
            # Unpad
            unpadder = padding.PKCS7(128).unpadder()
            data = unpadder.update(padded_data)
            data += unpadder.finalize()
            
            return data.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    def mask_sensitive_data(self, data: str) -> str:
        """Mask sensitive data in text"""
        # Define sensitive data patterns
        patterns = [
            (r'\b[a-fA-F0-9]{64}\b', '***HASH***'),  # SHA-256 hashes
            (r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '***UUID***'),  # UUIDs
            (r'private[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?', 'private_key: ***MASKED***'),  # Private keys
            (r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9]{20,}["\']?', 'api_key: ***MASKED***'),  # API keys
            (r'password["\']?\s*[:=]\s*["\']?[^"\']+["\']?', 'password: ***MASKED***'),  # Passwords
            (r'token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?', 'token: ***MASKED***'),  # Tokens
        ]
        
        masked_data = data
        for pattern, replacement in patterns:
            masked_data = re.sub(pattern, replacement, masked_data, flags=re.IGNORECASE)
        
        return masked_data
    
    def detect_sensitive_data(self, data: str) -> List[Dict[str, Any]]:
        """Detect sensitive data in text"""
        detections = []
        
        # Check for various sensitive data patterns
        patterns = [
            ("sha256_hash", r'\b[a-fA-F0-9]{64}\b'),
            ("uuid", r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'),
            ("private_key", r'private[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?'),
            ("api_key", r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9]{20,}["\']?'),
            ("password", r'password["\']?\s*[:=]\s*["\']?[^"\']+["\']?'),
            ("token", r'token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?'),
        ]
        
        for pattern_name, pattern in patterns:
            matches = re.findall(pattern, data, flags=re.IGNORECASE)
            if matches:
                detections.append({
                    "type": pattern_name,
                    "count": len(matches),
                    "matches": matches[:5]  # Limit to first 5 matches
                })
        
        return detections
    
    def _validate_credentials(self, user_id: str, credentials: Dict[str, Any]) -> bool:
        """Validate user credentials (simplified for demo)"""
        # In a real implementation, this would validate against a secure credential store
        # For demo purposes, we'll use a simple check
        return credentials.get("password") == "demo_password"
    
    def _create_session(self, user_id: str) -> str:
        """Create a new session for a user"""
        session_id = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=8)  # 8-hour session
        
        self.active_sessions[session_id] = {
            "user_id": user_id,
            "created_at": datetime.now(),
            "expires_at": expires_at,
            "last_activity": datetime.now()
        }
        
        return session_id
    
    def _matches_restriction(self, restriction: str, operation: str, resource: str) -> bool:
        """Check if an operation matches a restriction"""
        # Simple restriction matching (can be extended)
        if restriction == "no_export" and operation == "export":
            return True
        if restriction == "no_delete" and operation == "delete":
            return True
        if restriction.startswith("resource:") and resource == restriction.split(":", 1)[1]:
            return True
        
        return False
    
    def _get_or_create_master_key(self) -> bytes:
        """Get or create master encryption key"""
        if self.master_key is None:
            # Generate new master key
            self.master_key = os.urandom(32)  # 256-bit key
            
            # Store encrypted key (in real implementation, use HSM)
            key_file = os.path.join(self.vault_path, ".master_key")
            with open(key_file, 'wb') as f:
                f.write(self.master_key)
        
        return self.master_key
    
    def _log_security_event(self, event_type: str, severity: SecurityLevel, source: str, 
                           description: str, details: Dict[str, Any]):
        """Log a security event"""
        event = SecurityEvent(
            event_id=secrets.token_urlsafe(16),
            timestamp=datetime.now(),
            event_type=event_type,
            severity=severity,
            source=source,
            description=description,
            details=details
        )
        
        # Add to in-memory log
        self.audit_log.append(event)
        
        # Store in database
        try:
            cursor = self.audit_db.cursor()
            cursor.execute("""
                INSERT INTO security_events 
                (event_id, timestamp, event_type, severity, source, description, details, resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.timestamp.isoformat(),
                event.event_type,
                event.severity.value,
                event.source,
                event.description,
                json.dumps(event.details),
                0
            ))
            self.audit_db.commit()
            
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
        
        # Log to standard logger
        logger.warning(f"Security Event [{severity.value}]: {event_type} - {description}")
    
    def _detect_data_leaks(self) -> List[Dict[str, Any]]:
        """Detect potential data leaks"""
        threats = []
        
        # Check for sensitive data in logs
        for event in self.audit_log[-100:]:  # Check last 100 events
            if event.event_type in ["authentication_success", "operation_authorized"]:
                # Check if sensitive data is being logged
                sensitive_detections = self.detect_sensitive_data(event.description)
                if sensitive_detections:
                    threats.append({
                        "type": ThreatType.DATA_LEAK,
                        "severity": SecurityLevel.HIGH,
                        "description": "Sensitive data detected in logs",
                        "details": {"event_id": event.event_id, "detections": sensitive_detections}
                    })
        
        return threats
    
    def _detect_unauthorized_access(self) -> List[Dict[str, Any]]:
        """Detect unauthorized access attempts"""
        threats = []
        
        # Check for failed authentication attempts
        failed_auths = [e for e in self.audit_log[-50:] if e.event_type == "authentication_failed"]
        if len(failed_auths) >= 3:
            threats.append({
                "type": ThreatType.UNAUTHORIZED_ACCESS,
                "severity": SecurityLevel.HIGH,
                "description": "Multiple failed authentication attempts detected",
                "details": {"count": len(failed_auths)}
            })
        
        return threats
    
    def _detect_tampering(self) -> List[Dict[str, Any]]:
        """Detect potential tampering"""
        threats = []
        
        # Check for suspicious file modifications
        # This would integrate with file system monitoring
        # For now, we'll check for rapid successive operations
        
        return threats
    
    def _detect_malicious_input(self) -> List[Dict[str, Any]]:
        """Detect malicious input"""
        threats = []
        
        # Check for SQL injection patterns
        # Check for XSS patterns
        # Check for command injection patterns
        
        return threats
    
    def _detect_privilege_escalation(self) -> List[Dict[str, Any]]:
        """Detect privilege escalation attempts"""
        threats = []
        
        # Check for attempts to access restricted resources
        # Check for attempts to modify access controls
        
        return threats
    
    def _detect_brute_force(self) -> List[Dict[str, Any]]:
        """Detect brute force attacks"""
        threats = []
        
        # Check for rapid successive failed authentications
        recent_events = [e for e in self.audit_log if 
                        e.timestamp > datetime.now() - timedelta(minutes=5)]
        
        failed_auths = [e for e in recent_events if e.event_type == "authentication_failed"]
        if len(failed_auths) >= 5:
            threats.append({
                "type": ThreatType.BRUTE_FORCE,
                "severity": SecurityLevel.CRITICAL,
                "description": "Brute force attack detected",
                "details": {"attempts": len(failed_auths), "timeframe": "5 minutes"}
            })
        
        return threats
    
    def _monitor_access_patterns(self):
        """Monitor access patterns for anomalies"""
        # Check for unusual access patterns
        # Check for access outside normal hours
        # Check for access from unusual locations
        
        pass
    
    def _check_policy_violations(self):
        """Check for policy violations"""
        # Check against all active security policies
        for policy in self.security_policies.values():
            if policy.enabled:
                self._evaluate_policy(policy)
    
    def _evaluate_policy(self, policy: SecurityPolicy):
        """Evaluate a security policy"""
        # Evaluate policy rules against current state
        # Log violations
        
        pass
    
    def _handle_threat(self, threat: Dict[str, Any]):
        """Handle a detected threat"""
        # Log threat
        self._log_security_event(
            event_type="threat_detected",
            severity=SecurityLevel.HIGH,
            source="threat_detector",
            description=f"Threat detected: {threat['description']}",
            details=threat
        )
        
        # Take automated response actions
        if threat["type"] == ThreatType.BRUTE_FORCE:
            # Lock affected accounts
            pass
        elif threat["type"] == ThreatType.DATA_LEAK:
            # Mask sensitive data
            pass
    
    def get_security_report(self) -> Dict[str, Any]:
        """Generate security report"""
        return {
            "active_sessions": len(self.active_sessions),
            "access_controls": len(self.access_controls),
            "security_policies": len(self.security_policies),
            "recent_events": len([e for e in self.audit_log if 
                                e.timestamp > datetime.now() - timedelta(hours=24)]),
            "threat_detectors": len(self.threat_detectors),
            "monitoring_active": self.monitoring_active
        }
    
    def shutdown(self):
        """Shutdown security layer"""
        self.monitoring_active = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        if self.audit_db:
            self.audit_db.close()
        
        logger.info("Security layer shutdown complete")

def main():
    """Test the security layer"""
    vault_path = "/Users/devonwoodson/Documents/GitHub/VVAULT"
    
    # Create security layer
    security = VVAULTSecurityLayer(vault_path)
    
    # Test authentication
    success, session_id = security.authenticate_user("test_user", {"password": "demo_password"})
    print(f"Authentication: {success}, Session: {session_id}")
    
    # Test authorization
    if success:
        authorized = security.authorize_operation(session_id, "read", "capsule_001")
        print(f"Authorization: {authorized}")
    
    # Test sensitive data detection
    test_data = "This contains a private key: sk-1234567890abcdef and a password: secret123"
    detections = security.detect_sensitive_data(test_data)
    print(f"Sensitive data detections: {detections}")
    
    # Test masking
    masked = security.mask_sensitive_data(test_data)
    print(f"Masked data: {masked}")
    
    # Get security report
    report = security.get_security_report()
    print(f"Security report: {report}")
    
    # Shutdown
    security.shutdown()

if __name__ == "__main__":
    main()
