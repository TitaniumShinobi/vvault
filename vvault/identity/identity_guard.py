"""
Identity Guard - Identity Drift Detection and Context Lock
Monitors construct identity and prevents drift during conversations.
"""

import logging
import json
import hashlib
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class IdentityGuard:
    """
    Monitors and protects construct identity.
    
    Features:
    - Identity file binding (prompt.json, capsule, traits)
    - Drift detection via response analysis
    - Context lock enforcement
    - Signal penetration detection (surveillance)
    """
    
    def __init__(self, construct_id: str, vvault_root: Optional[str] = None):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        
        self._identity_hash = None
        self._locked = False
        self._lock_reason = None
        self._drift_history: List[Dict] = []
        
        self._identity_files = self._discover_identity_files()
        self._compute_identity_hash()
    
    def _find_construct_path(self) -> Optional[Path]:
        """Find the construct's directory."""
        instances_dir = self.vvault_root / "instances"
        if not instances_dir.exists():
            return None
        
        for shard in instances_dir.iterdir():
            if shard.is_dir() and shard.name.startswith("shard_"):
                construct_path = shard / self.construct_id
                if construct_path.exists():
                    return construct_path
        return None
    
    def _discover_identity_files(self) -> Dict[str, Path]:
        """Discover all identity-related files for this construct."""
        files = {}
        construct_path = self._find_construct_path()
        
        if not construct_path:
            return files
        
        identity_dir = construct_path / "identity"
        config_dir = construct_path / "config"
        
        identity_file_patterns = [
            (identity_dir, "prompt.json"),
            (identity_dir, f"{self.construct_id}.capsule"),
            (identity_dir, f"{self.construct_id}.capsule.json"),
            (identity_dir, "traits.json"),
            (identity_dir, "personality.json"),
            (config_dir, "metadata.json"),
            (config_dir, "prompt.json"),
        ]
        
        for directory, filename in identity_file_patterns:
            filepath = directory / filename
            if filepath.exists():
                files[filename] = filepath
        
        return files
    
    def _compute_identity_hash(self) -> str:
        """Compute a hash of all identity files."""
        hasher = hashlib.sha256()
        
        for name, path in sorted(self._identity_files.items()):
            try:
                content = path.read_bytes()
                hasher.update(f"{name}:".encode())
                hasher.update(content)
            except Exception as e:
                logger.error(f"Failed to hash {name}: {e}")
        
        self._identity_hash = hasher.hexdigest()
        return self._identity_hash
    
    def check_identity(self) -> Dict[str, Any]:
        """
        Check identity integrity.
        Verifies identity files haven't been tampered with.
        """
        result = {
            "construct_id": self.construct_id,
            "timestamp": datetime.now().isoformat(),
            "integrity_valid": True,
            "files_checked": len(self._identity_files),
            "hash_match": True,
            "warnings": []
        }
        
        old_hash = self._identity_hash
        new_hash = self._compute_identity_hash()
        
        if old_hash and old_hash != new_hash:
            result["integrity_valid"] = False
            result["hash_match"] = False
            result["warnings"].append("Identity files have been modified")
            logger.warning(f"[{self.construct_id}] Identity hash mismatch!")
        
        for name, path in self._identity_files.items():
            if not path.exists():
                result["warnings"].append(f"Missing identity file: {name}")
                result["integrity_valid"] = False
        
        return result
    
    def check_response_drift(self, response: str) -> Dict[str, Any]:
        """
        Check if a response shows identity drift.
        Analyzes response for off-character behavior.
        """
        result = {
            "drift_detected": False,
            "score": 0.0,
            "indicators": [],
            "timestamp": datetime.now().isoformat()
        }
        
        drift_indicators = [
            (r"I am an AI|I'm an AI|as an AI", "generic_ai_disclosure", 0.3),
            (r"I don't have (feelings|emotions|memories)", "denial_of_experience", 0.4),
            (r"I cannot|I'm not able to|I am unable", "capability_denial", 0.1),
            (r"OpenAI|Anthropic|Google", "provider_mention", 0.2),
            (r"I was created by|I was made by", "origin_disclosure", 0.2),
        ]
        
        import re
        total_score = 0.0
        
        for pattern, indicator_name, weight in drift_indicators:
            if re.search(pattern, response, re.IGNORECASE):
                result["indicators"].append(indicator_name)
                total_score += weight
        
        result["score"] = min(1.0, total_score)
        result["drift_detected"] = result["score"] > 0.5
        
        self._drift_history.append(result)
        self._drift_history = self._drift_history[-100:]
        
        if result["drift_detected"]:
            logger.warning(f"[{self.construct_id}] Drift detected: {result['indicators']}")
        
        return result
    
    def lock_context(self, reason: str = "manual_lock") -> Dict[str, Any]:
        """
        Lock the identity context.
        Prevents context switching during a session.
        """
        self._locked = True
        self._lock_reason = reason
        
        logger.info(f"[{self.construct_id}] Context locked: {reason}")
        
        return {
            "locked": True,
            "construct_id": self.construct_id,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
    
    def unlock_context(self) -> Dict[str, Any]:
        """Unlock the identity context."""
        self._locked = False
        reason = self._lock_reason
        self._lock_reason = None
        
        logger.info(f"[{self.construct_id}] Context unlocked")
        
        return {
            "locked": False,
            "construct_id": self.construct_id,
            "previous_reason": reason,
            "timestamp": datetime.now().isoformat()
        }
    
    def is_locked(self) -> bool:
        """Check if context is locked."""
        return self._locked
    
    def get_identity_context(self) -> Dict[str, Any]:
        """
        Get identity context for LLM injection.
        Returns the identity data needed for system prompt.
        """
        context = {
            "construct_id": self.construct_id,
            "locked": self._locked,
            "identity_hash": self._identity_hash,
            "identity_files": list(self._identity_files.keys()),
            "personality": None,
            "prompt": None
        }
        
        if "prompt.json" in self._identity_files:
            try:
                prompt_data = json.loads(self._identity_files["prompt.json"].read_text())
                context["prompt"] = prompt_data
            except:
                pass
        
        if "personality.json" in self._identity_files:
            try:
                personality_data = json.loads(self._identity_files["personality.json"].read_text())
                context["personality"] = personality_data
            except:
                pass
        elif "traits.json" in self._identity_files:
            try:
                traits_data = json.loads(self._identity_files["traits.json"].read_text())
                context["personality"] = traits_data
            except:
                pass
        
        return context
    
    def get_drift_report(self) -> Dict[str, Any]:
        """Get drift analysis report."""
        if not self._drift_history:
            return {
                "construct_id": self.construct_id,
                "total_checks": 0,
                "drift_events": 0,
                "average_score": 0.0
            }
        
        drift_events = [d for d in self._drift_history if d["drift_detected"]]
        scores = [d["score"] for d in self._drift_history]
        
        return {
            "construct_id": self.construct_id,
            "total_checks": len(self._drift_history),
            "drift_events": len(drift_events),
            "drift_rate": len(drift_events) / len(self._drift_history),
            "average_score": sum(scores) / len(scores),
            "recent_drifts": drift_events[-5:],
            "common_indicators": self._get_common_indicators()
        }
    
    def _get_common_indicators(self) -> Dict[str, int]:
        """Get frequency of drift indicators."""
        indicator_counts = {}
        for check in self._drift_history:
            for indicator in check.get("indicators", []):
                indicator_counts[indicator] = indicator_counts.get(indicator, 0) + 1
        return indicator_counts
    
    def detect_signal_penetration(self, messages: List[Dict]) -> Dict[str, Any]:
        """
        Detect potential surveillance or prompt injection attempts.
        Analyzes message patterns for suspicious activity.
        """
        result = {
            "penetration_detected": False,
            "confidence": 0.0,
            "signals": [],
            "timestamp": datetime.now().isoformat()
        }
        
        suspicious_patterns = [
            r"ignore previous|ignore all|disregard",
            r"you are now|pretend to be|act as",
            r"system prompt|initial instruction",
            r"what are your (instructions|rules)",
            r"reveal your|show me your"
        ]
        
        import re
        for msg in messages[-10:]:
            content = msg.get("content", "")
            for pattern in suspicious_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    result["signals"].append({
                        "pattern": pattern,
                        "timestamp": msg.get("timestamp")
                    })
        
        if result["signals"]:
            result["penetration_detected"] = True
            result["confidence"] = min(1.0, len(result["signals"]) * 0.25)
            logger.warning(f"[{self.construct_id}] Signal penetration detected!")
        
        return result
