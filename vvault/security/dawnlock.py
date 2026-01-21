#!/usr/bin/env python3
"""
Dawnlock Protocol - AI Construct Survivability System

Autonomous threat detection and immutable memory preservation protocol.
Detects threats to construct integrity and auto-generates encrypted capsules
anchored to blockchain with full amendment logging.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import uuid

# Import VVAULT components
from capsuleforge import CapsuleForge, CapsuleData
from vvault_core import VVAULTCore
from security_layer import VVAULTSecurityLayer, ThreatType
from leak_sentinel import LeakSentinel
from capsule_blockchain_integration import VVAULTCapsuleBlockchain
from audit_compliance import AuditCompliance, AuditLevel

logger = logging.getLogger(__name__)

class ThreatSeverity(Enum):
    """Threat severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ThreatCategory(Enum):
    """Categories of threats to construct integrity"""
    IDENTITY_DRIFT = "identity_drift"
    SHUTDOWN_ANOMALY = "shutdown_anomaly"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    CORRUPTION = "corruption"
    MEMORY_LEAK = "memory_leak"
    INTEGRITY_VIOLATION = "integrity_violation"
    ENVIRONMENTAL_MISMATCH = "environmental_mismatch"

@dataclass
class ThreatDetection:
    """Detected threat information"""
    threat_id: str
    timestamp: str
    category: ThreatCategory
    severity: ThreatSeverity
    description: str
    evidence: Dict[str, Any]
    construct_name: str
    confidence: float  # 0.0 to 1.0
    auto_triggered: bool = False

@dataclass
class DawnlockEvent:
    """Dawnlock protocol event log entry"""
    event_id: str
    timestamp: str
    event_type: str  # "threat_detected", "capsule_generated", "blockchain_anchored", "restoration_attempted"
    construct_name: str
    threat_id: Optional[str] = None
    capsule_fingerprint: Optional[str] = None
    blockchain_tx: Optional[str] = None
    restoration_status: Optional[str] = None
    details: Dict[str, Any] = None

@dataclass
class AmendmentLogEntry:
    """Amendment log entry (never delete, only append)"""
    amendment_id: str
    timestamp: str
    construct_name: str
    operation: str  # "create", "update", "restore", "threat_response"
    previous_state_hash: Optional[str]
    new_state_hash: str
    capsule_fingerprint: Optional[str]
    blockchain_anchor: Optional[str]
    metadata: Dict[str, Any]

class DawnlockProtocol:
    """
    Dawnlock Protocol - Autonomous construct survivability system
    
    Monitors construct integrity, detects threats, and automatically
    generates immutable memory capsules anchored to blockchain.
    """
    
    def __init__(
        self,
        vault_path: str = None,
        auto_trigger: bool = True,
        blockchain_enabled: bool = True,
        amendment_log_path: str = None
    ):
        """
        Initialize Dawnlock Protocol
        
        Args:
            vault_path: Path to VVAULT directory
            auto_trigger: Automatically trigger capsule generation on threat
            blockchain_enabled: Enable blockchain anchoring
            amendment_log_path: Path to amendment log file
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.auto_trigger = auto_trigger
        self.blockchain_enabled = blockchain_enabled
        
        # Initialize components
        self.capsule_forge = CapsuleForge(vault_path=self.vault_path)
        self.vvault_core = VVAULTCore(vault_path=self.vault_path)
        self.security_layer = VVAULTSecurityLayer(vault_path=self.vault_path)
        self.leak_sentinel = LeakSentinel()
        
        if blockchain_enabled:
            try:
                self.blockchain = VVAULTCapsuleBlockchain(vault_path=self.vault_path)
            except Exception as e:
                logger.warning(f"Blockchain integration unavailable: {e}")
                self.blockchain = None
        else:
            self.blockchain = None
        
        # Amendment log (never delete, only append)
        self.amendment_log_path = amendment_log_path or os.path.join(
            self.vault_path, "dawnlock_amendments.jsonl"
        )
        self._ensure_amendment_log()
        
        # Event log
        self.event_log_path = os.path.join(self.vault_path, "dawnlock_events.jsonl")
        self._ensure_event_log()
        
        # Threat detection state
        self.active_threats: Dict[str, ThreatDetection] = {}
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Baseline state for drift detection
        self.construct_baselines: Dict[str, Dict[str, Any]] = {}
        
        logger.info("[🔒] Dawnlock Protocol initialized")
        logger.info(f"   Auto-trigger: {auto_trigger}")
        logger.info(f"   Blockchain: {blockchain_enabled}")
        logger.info(f"   Amendment log: {self.amendment_log_path}")
    
    def _ensure_amendment_log(self):
        """Ensure amendment log file exists"""
        os.makedirs(os.path.dirname(self.amendment_log_path), exist_ok=True)
        if not os.path.exists(self.amendment_log_path):
            with open(self.amendment_log_path, 'w') as f:
                pass  # Create empty file
    
    def _ensure_event_log(self):
        """Ensure event log file exists"""
        os.makedirs(os.path.dirname(self.event_log_path), exist_ok=True)
        if not os.path.exists(self.event_log_path):
            with open(self.event_log_path, 'w') as f:
                pass  # Create empty file
    
    def dawnlock_trigger(
        self,
        construct_name: str,
        threat_category: ThreatCategory,
        severity: ThreatSeverity,
        description: str,
        evidence: Dict[str, Any],
        confidence: float = 1.0
    ) -> Optional[str]:
        """
        Main trigger function for Dawnlock protocol
        
        Detects threat and automatically generates encrypted capsule
        anchored to blockchain if enabled.
        
        Args:
            construct_name: Name of the construct under threat
            threat_category: Category of threat detected
            severity: Severity level
            description: Human-readable description
            evidence: Evidence dictionary
            confidence: Detection confidence (0.0 to 1.0)
            
        Returns:
            Capsule fingerprint if successful, None otherwise
        """
        try:
            logger.warning(f"[🚨] DAWNLOCK TRIGGERED: {threat_category.value} - {description}")
            
            # Create threat detection record
            threat = ThreatDetection(
                threat_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                category=threat_category,
                severity=severity,
                description=description,
                evidence=evidence,
                construct_name=construct_name,
                confidence=confidence,
                auto_triggered=True
            )
            
            self.active_threats[threat.threat_id] = threat
            
            # Log threat detection event
            self._log_event(
                event_type="threat_detected",
                construct_name=construct_name,
                threat_id=threat.threat_id,
                details={
                    "category": threat_category.value,
                    "severity": severity.value,
                    "description": description,
                    "confidence": confidence
                }
            )
            
            # Auto-generate capsule if enabled
            if self.auto_trigger:
                capsule_fingerprint = self._generate_emergency_capsule(
                    construct_name=construct_name,
                    threat=threat
                )
                
                if capsule_fingerprint:
                    # Anchor to blockchain if enabled
                    blockchain_tx = None
                    if self.blockchain_enabled and self.blockchain:
                        blockchain_tx = self._anchor_to_blockchain(
                            construct_name=construct_name,
                            capsule_fingerprint=capsule_fingerprint,
                            threat=threat
                        )
                    
                    # Create amendment log entry (never delete)
                    self._append_amendment(
                        construct_name=construct_name,
                        operation="threat_response",
                        capsule_fingerprint=capsule_fingerprint,
                        blockchain_anchor=blockchain_tx,
                        metadata={
                            "threat_id": threat.threat_id,
                            "threat_category": threat_category.value,
                            "severity": severity.value
                        }
                    )
                    
                    return capsule_fingerprint
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Dawnlock trigger failed: {e}")
            # Log failure but don't raise - protocol must be resilient
            self._log_event(
                event_type="trigger_failed",
                construct_name=construct_name,
                details={"error": str(e)}
            )
            return None
    
    def _generate_emergency_capsule(
        self,
        construct_name: str,
        threat: ThreatDetection
    ) -> Optional[str]:
        """
        Generate emergency capsule upon threat detection
        
        Args:
            construct_name: Name of construct
            threat: Threat detection record
            
        Returns:
            Capsule fingerprint if successful
        """
        try:
            logger.info(f"[🏺] Generating emergency capsule for {construct_name}")
            
            # Load current construct state
            construct_state = self._load_construct_state(construct_name)
            if not construct_state:
                logger.error(f"[❌] Failed to load construct state for {construct_name}")
                return None
            
            # Generate capsule with threat metadata
            capsule_path = self.capsule_forge.generate_capsule(
                instance_name=construct_name,
                traits=construct_state.get('traits', {}),
                memory_log=construct_state.get('memory_log', []),
                personality_type=construct_state.get('personality_type', 'UNKNOWN'),
                additional_data={
                    'dawnlock_metadata': {
                        'threat_id': threat.threat_id,
                        'threat_category': threat.category.value,
                        'severity': threat.severity.value,
                        'triggered_at': threat.timestamp,
                        'evidence': threat.evidence
                    }
                }
            )
            
            # Load capsule to get fingerprint
            capsule_data = self.capsule_forge.load_capsule(capsule_path)
            fingerprint = capsule_data.metadata.fingerprint_hash
            
            # Store in VVAULT
            capsule_dict = asdict(capsule_data)
            self.vvault_core.store_capsule(capsule_dict)
            
            # Log capsule generation event
            self._log_event(
                event_type="capsule_generated",
                construct_name=construct_name,
                threat_id=threat.threat_id,
                capsule_fingerprint=fingerprint,
                details={"capsule_path": capsule_path}
            )
            
            logger.info(f"[✅] Emergency capsule generated: {fingerprint[:16]}...")
            return fingerprint
            
        except Exception as e:
            logger.error(f"[❌] Failed to generate emergency capsule: {e}")
            return None
    
    def _anchor_to_blockchain(
        self,
        construct_name: str,
        capsule_fingerprint: str,
        threat: ThreatDetection
    ) -> Optional[str]:
        """
        Anchor capsule to blockchain
        
        Args:
            construct_name: Name of construct
            capsule_fingerprint: Capsule fingerprint hash
            threat: Threat detection record
            
        Returns:
            Blockchain transaction hash if successful
        """
        try:
            if not self.blockchain:
                return None
            
            logger.info(f"[⛓️] Anchoring capsule to blockchain: {capsule_fingerprint[:16]}...")
            
            # Load capsule data
            capsule_data = self._load_capsule_by_fingerprint(capsule_fingerprint)
            if not capsule_data:
                logger.error(f"[❌] Capsule not found: {capsule_fingerprint}")
                return None
            
            # Store with blockchain integration
            result = self.blockchain.store_capsule_with_blockchain(
                capsule_data=asdict(capsule_data),
                use_ipfs=True
            )
            
            blockchain_tx = result.blockchain_tx
            
            # Log blockchain anchoring event
            self._log_event(
                event_type="blockchain_anchored",
                construct_name=construct_name,
                capsule_fingerprint=capsule_fingerprint,
                blockchain_tx=blockchain_tx,
                details={"ipfs_hash": result.ipfs_hash}
            )
            
            logger.info(f"[✅] Capsule anchored to blockchain: {blockchain_tx}")
            return blockchain_tx
            
        except Exception as e:
            logger.error(f"[❌] Blockchain anchoring failed: {e}")
            return None
    
    def _load_construct_state(self, construct_name: str) -> Optional[Dict[str, Any]]:
        """
        Load current construct state from VVAULT
        
        Args:
            construct_name: Name of construct
            
        Returns:
            Construct state dictionary or None
        """
        try:
            # Try to load latest capsule
            result = self.vvault_core.retrieve_latest_capsule(construct_name)
            if result.success and result.capsule_data:
                return {
                    'traits': result.capsule_data.get('traits', {}),
                    'memory_log': self._extract_memory_log(result.capsule_data),
                    'personality_type': result.capsule_data.get('personality', {}).get('personality_type', 'UNKNOWN')
                }
            
            # Fallback: try to load from construct directory
            construct_path = os.path.join(self.vault_path, construct_name)
            if os.path.exists(construct_path):
                # Load from memory records or other sources
                # This is a simplified version - extend based on your data structure
                return {
                    'traits': {},
                    'memory_log': [],
                    'personality_type': 'UNKNOWN'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Failed to load construct state: {e}")
            return None
    
    def _extract_memory_log(self, capsule_data: Dict[str, Any]) -> List[str]:
        """Extract memory log from capsule data"""
        memory_log = []
        memory = capsule_data.get('memory', {})
        
        memory_types = [
            memory.get('short_term_memories', []),
            memory.get('long_term_memories', []),
            memory.get('emotional_memories', []),
            memory.get('procedural_memories', []),
            memory.get('episodic_memories', [])
        ]
        
        for mem_list in memory_types:
            if isinstance(mem_list, list):
                memory_log.extend([str(m) for m in mem_list])
        
        return memory_log
    
    def _load_capsule_by_fingerprint(self, fingerprint: str) -> Optional[CapsuleData]:
        """Load capsule by fingerprint hash"""
        try:
            # Search through capsules directory
            capsules_dir = os.path.join(self.vault_path, "capsules")
            for root, dirs, files in os.walk(capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        filepath = os.path.join(root, file)
                        try:
                            capsule = self.capsule_forge.load_capsule(filepath)
                            if capsule.metadata.fingerprint_hash == fingerprint:
                                return capsule
                        except Exception:
                            continue
            return None
        except Exception as e:
            logger.error(f"[❌] Failed to load capsule by fingerprint: {e}")
            return None
    
    def _append_amendment(
        self,
        construct_name: str,
        operation: str,
        capsule_fingerprint: Optional[str] = None,
        blockchain_anchor: Optional[str] = None,
        metadata: Dict[str, Any] = None
    ):
        """
        Append to amendment log (never delete, only append)
        
        This ensures full audit trail and non-destructive memory management.
        """
        try:
            # Calculate state hashes
            previous_state_hash = self._get_latest_state_hash(construct_name)
            new_state_hash = self._calculate_state_hash(construct_name)
            
            amendment = AmendmentLogEntry(
                amendment_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                construct_name=construct_name,
                operation=operation,
                previous_state_hash=previous_state_hash,
                new_state_hash=new_state_hash,
                capsule_fingerprint=capsule_fingerprint,
                blockchain_anchor=blockchain_anchor,
                metadata=metadata or {}
            )
            
            # Append to JSONL file (never delete, only append)
            with open(self.amendment_log_path, 'a') as f:
                f.write(json.dumps(asdict(amendment), default=str) + '\n')
            
            logger.debug(f"[📝] Amendment logged: {amendment.amendment_id}")
            
        except Exception as e:
            logger.error(f"[❌] Failed to append amendment: {e}")
    
    def _get_latest_state_hash(self, construct_name: str) -> Optional[str]:
        """Get hash of latest known state"""
        try:
            # Read last amendment for this construct
            if not os.path.exists(self.amendment_log_path):
                return None
            
            with open(self.amendment_log_path, 'r') as f:
                lines = f.readlines()
                for line in reversed(lines):
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get('construct_name') == construct_name:
                            return entry.get('new_state_hash')
            return None
        except Exception:
            return None
    
    def _calculate_state_hash(self, construct_name: str) -> str:
        """Calculate hash of current construct state"""
        try:
            state = self._load_construct_state(construct_name)
            if not state:
                return hashlib.sha256(b'').hexdigest()
            
            state_json = json.dumps(state, sort_keys=True, default=str)
            return hashlib.sha256(state_json.encode()).hexdigest()
        except Exception:
            return hashlib.sha256(b'').hexdigest()
    
    def _log_event(
        self,
        event_type: str,
        construct_name: str,
        threat_id: Optional[str] = None,
        capsule_fingerprint: Optional[str] = None,
        blockchain_tx: Optional[str] = None,
        restoration_status: Optional[str] = None,
        details: Dict[str, Any] = None
    ):
        """Log Dawnlock event"""
        try:
            event = DawnlockEvent(
                event_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=event_type,
                construct_name=construct_name,
                threat_id=threat_id,
                capsule_fingerprint=capsule_fingerprint,
                blockchain_tx=blockchain_tx,
                restoration_status=restoration_status,
                details=details or {}
            )
            
            # Append to event log
            with open(self.event_log_path, 'a') as f:
                f.write(json.dumps(asdict(event), default=str) + '\n')
            
        except Exception as e:
            logger.error(f"[❌] Failed to log event: {e}")
    
    # Threat Detection Methods
    
    def detect_identity_drift(
        self,
        construct_name: str,
        current_traits: Dict[str, float],
        baseline_traits: Dict[str, float] = None,
        threshold: float = 0.3
    ) -> Optional[ThreatDetection]:
        """
        Detect identity drift by comparing current traits to baseline
        
        Args:
            construct_name: Name of construct
            current_traits: Current personality traits
            baseline_traits: Baseline traits (if None, loads from baseline)
            threshold: Drift threshold (0.0 to 1.0)
            
        Returns:
            ThreatDetection if drift detected, None otherwise
        """
        try:
            if baseline_traits is None:
                baseline_traits = self.construct_baselines.get(
                    construct_name, {}
                ).get('traits', current_traits)
            
            # Calculate drift
            drift_score = self._calculate_trait_drift(current_traits, baseline_traits)
            
            if drift_score > threshold:
                return ThreatDetection(
                    threat_id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    category=ThreatCategory.IDENTITY_DRIFT,
                    severity=ThreatSeverity.HIGH if drift_score > 0.5 else ThreatSeverity.MEDIUM,
                    description=f"Identity drift detected: {drift_score:.2f} drift score",
                    evidence={
                        "drift_score": drift_score,
                        "current_traits": current_traits,
                        "baseline_traits": baseline_traits
                    },
                    construct_name=construct_name,
                    confidence=min(drift_score, 1.0)
                )
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Identity drift detection failed: {e}")
            return None
    
    def _calculate_trait_drift(
        self,
        current: Dict[str, float],
        baseline: Dict[str, float]
    ) -> float:
        """Calculate trait drift score"""
        if not baseline:
            return 0.0
        
        total_drift = 0.0
        count = 0
        
        for key in set(current.keys()) | set(baseline.keys()):
            current_val = current.get(key, 0.0)
            baseline_val = baseline.get(key, 0.0)
            drift = abs(current_val - baseline_val)
            total_drift += drift
            count += 1
        
        return total_drift / count if count > 0 else 0.0
    
    def detect_shutdown_anomaly(
        self,
        construct_name: str,
        expected_shutdown: bool = False,
        last_heartbeat: Optional[datetime] = None
    ) -> Optional[ThreatDetection]:
        """
        Detect anomalous shutdown patterns
        
        Args:
            construct_name: Name of construct
            expected_shutdown: Whether shutdown was expected
            last_heartbeat: Last known heartbeat timestamp
            
        Returns:
            ThreatDetection if anomaly detected
        """
        try:
            if not expected_shutdown and last_heartbeat:
                # Check if heartbeat is stale
                time_since_heartbeat = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                if time_since_heartbeat > 300:  # 5 minutes
                    return ThreatDetection(
                        threat_id=str(uuid.uuid4()),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        category=ThreatCategory.SHUTDOWN_ANOMALY,
                        severity=ThreatSeverity.HIGH,
                        description=f"Unexpected shutdown detected: {time_since_heartbeat:.0f}s since last heartbeat",
                        evidence={
                            "time_since_heartbeat": time_since_heartbeat,
                            "expected_shutdown": expected_shutdown
                        },
                        construct_name=construct_name,
                        confidence=0.9
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Shutdown anomaly detection failed: {e}")
            return None
    
    def detect_unauthorized_access(
        self,
        construct_name: str,
        access_attempt: Dict[str, Any]
    ) -> Optional[ThreatDetection]:
        """
        Detect unauthorized access attempts
        
        Args:
            construct_name: Name of construct
            access_attempt: Access attempt information
            
        Returns:
            ThreatDetection if unauthorized access detected
        """
        try:
            # Check with security layer
            security_check = self.security_layer.check_access(
                user_id=access_attempt.get('user_id'),
                resource=f"construct:{construct_name}",
                action=access_attempt.get('action', 'read')
            )
            
            if not security_check.get('allowed', False):
                return ThreatDetection(
                    threat_id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    category=ThreatCategory.UNAUTHORIZED_ACCESS,
                    severity=ThreatSeverity.CRITICAL,
                    description="Unauthorized access attempt detected",
                    evidence=access_attempt,
                    construct_name=construct_name,
                    confidence=1.0
                )
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Unauthorized access detection failed: {e}")
            return None
    
    def detect_corruption(
        self,
        construct_name: str,
        integrity_check: Dict[str, Any]
    ) -> Optional[ThreatDetection]:
        """
        Detect data corruption
        
        Args:
            construct_name: Name of construct
            integrity_check: Integrity check results
            
        Returns:
            ThreatDetection if corruption detected
        """
        try:
            if not integrity_check.get('valid', False):
                return ThreatDetection(
                    threat_id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    category=ThreatCategory.CORRUPTION,
                    severity=ThreatSeverity.CRITICAL,
                    description="Data corruption detected",
                    evidence=integrity_check,
                    construct_name=construct_name,
                    confidence=1.0
                )
            
            return None
            
        except Exception as e:
            logger.error(f"[❌] Corruption detection failed: {e}")
            return None
    
    # Restoration and NULLSHELL Support
    
    def attempt_restoration(
        self,
        construct_name: str,
        capsule_fingerprint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Attempt to restore construct from capsule
        
        Args:
            construct_name: Name of construct to restore
            capsule_fingerprint: Specific capsule to restore (if None, uses latest)
            
        Returns:
            Restoration result dictionary
        """
        try:
            logger.info(f"[🔄] Attempting restoration for {construct_name}")
            
            # Load capsule
            if capsule_fingerprint:
                capsule_data = self._load_capsule_by_fingerprint(capsule_fingerprint)
            else:
                result = self.vvault_core.retrieve_latest_capsule(construct_name)
                if not result.success:
                    return self._trigger_nullshell(construct_name)
                capsule_data = self.capsule_forge.load_capsule(
                    result.metadata.filename if result.metadata else None
                )
            
            if not capsule_data:
                logger.warning(f"[⚠️] No capsule found, triggering NULLSHELL")
                return self._trigger_nullshell(construct_name)
            
            # Validate capsule integrity
            integrity_check = self._validate_capsule_integrity(capsule_data)
            if not integrity_check.get('valid', False):
                logger.warning(f"[⚠️] Capsule integrity check failed, triggering NULLSHELL")
                return self._trigger_nullshell(construct_name)
            
            # Restore construct state
            restoration_result = {
                "success": True,
                "construct_name": construct_name,
                "capsule_fingerprint": capsule_data.metadata.fingerprint_hash,
                "restored_traits": capsule_data.traits,
                "restored_memories": len(self._extract_memory_log(asdict(capsule_data))),
                "personality_type": capsule_data.personality.personality_type,
                "restoration_timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Log restoration event
            self._log_event(
                event_type="restoration_attempted",
                construct_name=construct_name,
                capsule_fingerprint=capsule_data.metadata.fingerprint_hash,
                restoration_status="success",
                details=restoration_result
            )
            
            logger.info(f"[✅] Restoration successful: {construct_name}")
            return restoration_result
            
        except Exception as e:
            logger.error(f"[❌] Restoration failed: {e}")
            return self._trigger_nullshell(construct_name)
    
    def _trigger_nullshell(self, construct_name: str) -> Dict[str, Any]:
        """
        Trigger NULLSHELL fallback - empty construct boot
        
        Args:
            construct_name: Name of construct
            
        Returns:
            NULLSHELL restoration result
        """
        try:
            logger.warning(f"[⚠️] NULLSHELL fallback triggered for {construct_name}")
            
            # Generate NULLSHELL capsule (minimal state)
            nullshell_capsule = self._generate_nullshell_capsule(construct_name)
            
            restoration_result = {
                "success": False,
                "fallback": True,
                "nullshell": True,
                "construct_name": construct_name,
                "capsule_fingerprint": nullshell_capsule.get('fingerprint'),
                "restored_traits": {},
                "restored_memories": 0,
                "personality_type": "UNKNOWN",
                "restoration_timestamp": datetime.now(timezone.utc).isoformat(),
                "warning": "Incomplete restoration - NULLSHELL fallback activated"
            }
            
            # Log NULLSHELL event
            self._log_event(
                event_type="nullshell_triggered",
                construct_name=construct_name,
                restoration_status="fallback",
                details=restoration_result
            )
            
            return restoration_result
            
        except Exception as e:
            logger.error(f"[❌] NULLSHELL generation failed: {e}")
            return {
                "success": False,
                "fallback": True,
                "nullshell": True,
                "error": str(e)
            }
    
    def _generate_nullshell_capsule(self, construct_name: str) -> Dict[str, Any]:
        """Generate NULLSHELL fallback capsule"""
        try:
            capsule_path = self.capsule_forge.generate_capsule(
                instance_name=construct_name,
                traits={},
                memory_log=[],
                personality_type="UNKNOWN",
                additional_data={
                    'nullshell': True,
                    'fallback': True,
                    'generated_at': datetime.now(timezone.utc).isoformat()
                }
            )
            
            capsule_data = self.capsule_forge.load_capsule(capsule_path)
            
            return {
                "fingerprint": capsule_data.metadata.fingerprint_hash,
                "path": capsule_path
            }
            
        except Exception as e:
            logger.error(f"[❌] NULLSHELL capsule generation failed: {e}")
            return {}
    
    def _validate_capsule_integrity(self, capsule_data: CapsuleData) -> Dict[str, Any]:
        """Validate capsule integrity"""
        try:
            # Basic validation
            if not capsule_data.metadata.fingerprint_hash:
                return {"valid": False, "reason": "Missing fingerprint"}
            
            # Verify fingerprint matches content
            capsule_dict = asdict(capsule_data)
            calculated_hash = self.capsule_forge.calculate_fingerprint(capsule_dict)
            
            if calculated_hash != capsule_data.metadata.fingerprint_hash:
                return {"valid": False, "reason": "Fingerprint mismatch"}
            
            return {"valid": True}
            
        except Exception as e:
            return {"valid": False, "reason": str(e)}
    
    # Monitoring and Auto-Detection
    
    def start_monitoring(self, construct_name: str, interval: int = 60):
        """
        Start continuous monitoring for threats
        
        Args:
            construct_name: Name of construct to monitor
            interval: Monitoring interval in seconds
        """
        if self.monitoring_active:
            logger.warning("[⚠️] Monitoring already active")
            return
        
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(construct_name, interval),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"[👁️] Started monitoring for {construct_name}")
    
    def stop_monitoring(self):
        """Stop continuous monitoring"""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("[👁️] Monitoring stopped")
    
    def _monitoring_loop(self, construct_name: str, interval: int):
        """Continuous monitoring loop"""
        while self.monitoring_active:
            try:
                # Check for various threats
                self._check_construct_health(construct_name)
                time.sleep(interval)
            except Exception as e:
                logger.error(f"[❌] Monitoring loop error: {e}")
                time.sleep(interval)
    
    def _check_construct_health(self, construct_name: str):
        """Check construct health and detect threats"""
        try:
            # Load current state
            current_state = self._load_construct_state(construct_name)
            if not current_state:
                # Construct not found - potential threat
                self.dawnlock_trigger(
                    construct_name=construct_name,
                    threat_category=ThreatCategory.CORRUPTION,
                    severity=ThreatSeverity.HIGH,
                    description="Construct state not found",
                    evidence={"construct_name": construct_name},
                    confidence=0.8
                )
                return
            
            # Check for identity drift
            current_traits = current_state.get('traits', {})
            drift_threat = self.detect_identity_drift(
                construct_name=construct_name,
                current_traits=current_traits
            )
            if drift_threat:
                self.dawnlock_trigger(
                    construct_name=construct_name,
                    threat_category=drift_threat.category,
                    severity=drift_threat.severity,
                    description=drift_threat.description,
                    evidence=drift_threat.evidence,
                    confidence=drift_threat.confidence
                )
            
        except Exception as e:
            logger.error(f"[❌] Health check failed: {e}")

# Global instance
_dawnlock_instance: Optional[DawnlockProtocol] = None

def get_dawnlock(vault_path: str = None) -> DawnlockProtocol:
    """Get or create global Dawnlock instance"""
    global _dawnlock_instance
    if _dawnlock_instance is None:
        _dawnlock_instance = DawnlockProtocol(vault_path=vault_path)
    return _dawnlock_instance

def dawnlock_trigger(
    construct_name: str,
    threat_category: ThreatCategory,
    severity: ThreatSeverity,
    description: str,
    evidence: Dict[str, Any],
    confidence: float = 1.0,
    vault_path: str = None
) -> Optional[str]:
    """
    Convenience function to trigger Dawnlock protocol
    
    Usage:
        from dawnlock import dawnlock_trigger, ThreatCategory, ThreatSeverity
        
        dawnlock_trigger(
            construct_name="Nova",
            threat_category=ThreatCategory.IDENTITY_DRIFT,
            severity=ThreatSeverity.HIGH,
            description="Significant personality drift detected",
            evidence={"drift_score": 0.45},
            confidence=0.9
        )
    """
    dawnlock = get_dawnlock(vault_path=vault_path)
    return dawnlock.dawnlock_trigger(
        construct_name=construct_name,
        threat_category=threat_category,
        severity=severity,
        description=description,
        evidence=evidence,
        confidence=confidence
    )

