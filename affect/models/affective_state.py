"""
Affective State Data Models

Defines the core data structures for the Affective Reciprocity Framework (ARF).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
import json


@dataclass
class AffectiveState:
    """Core affective state representation"""
    valence: float  # -1.0 to 1.0 (negative to positive)
    arousal: float  # -1.0 to 1.0 (calm to excited)
    dominant_emotion: str  # e.g., "neutral", "happy", "sad", "excited"
    last_update: str  # ISO format timestamp
    update_count: int = 0
    governance_status: Dict[str, Any] = field(default_factory=lambda: {
        "escalation_level": 0,
        "cooldown_until": None,
        "bounds_enforced": True
    })
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "dominantEmotion": self.dominant_emotion,
            "lastUpdate": self.last_update,
            "updateCount": self.update_count,
            "governanceStatus": self.governance_status
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AffectiveState':
        """Create from dictionary"""
        return cls(
            valence=data.get("valence", 0.0),
            arousal=data.get("arousal", 0.0),
            dominant_emotion=data.get("dominantEmotion", "neutral"),
            last_update=data.get("lastUpdate", datetime.utcnow().isoformat()),
            update_count=data.get("updateCount", 0),
            governance_status=data.get("governanceStatus", {
                "escalation_level": 0,
                "cooldown_until": None,
                "bounds_enforced": True
            })
        )
    
    @classmethod
    def default(cls) -> 'AffectiveState':
        """Create default neutral state"""
        return cls(
            valence=0.0,
            arousal=0.0,
            dominant_emotion="neutral",
            last_update=datetime.utcnow().isoformat(),
            update_count=0,
            governance_status={
                "escalation_level": 0,
                "cooldown_until": None,
                "bounds_enforced": True
            }
        )


@dataclass
class UserSignal:
    """User affective signal extracted from input"""
    valence: float  # -1.0 to 1.0
    arousal: float  # -1.0 to 1.0
    intent_category: str  # e.g., "question", "complaint", "compliment"
    confidence: float  # 0.0 to 1.0
    timestamp: str  # ISO format timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "intentCategory": self.intent_category,
            "confidence": self.confidence,
            "timestamp": self.timestamp
        }


@dataclass
class StateHistoryEntry:
    """Single entry in state history"""
    timestamp: str
    previous_state: Dict[str, Any]
    new_state: Dict[str, Any]
    user_signal: Optional[Dict[str, Any]]
    governance_decision: Dict[str, Any]
    actor: str  # e.g., "system", "user", "governor"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "timestamp": self.timestamp,
            "previousState": self.previous_state,
            "newState": self.new_state,
            "userSignal": self.user_signal,
            "governanceDecision": self.governance_decision,
            "actor": self.actor
        }
    
    def to_jsonl(self) -> str:
        """Convert to JSONL format for append-only log"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class AuditLogEntry:
    """Audit log entry for state changes"""
    timestamp: str
    event_type: str  # e.g., "state_update", "governance_decision", "influence_applied"
    previous_state: Optional[Dict[str, Any]]
    new_state: Optional[Dict[str, Any]]
    user_signal: Optional[Dict[str, Any]]
    governance_decision: Optional[Dict[str, Any]]
    actor: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "timestamp": self.timestamp,
            "eventType": self.event_type,
            "previousState": self.previous_state,
            "newState": self.new_state,
            "userSignal": self.user_signal,
            "governanceDecision": self.governance_decision,
            "actor": self.actor,
            "metadata": self.metadata
        }
    
    def to_jsonl(self) -> str:
        """Convert to JSONL format for append-only log"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

