"""
State Manager - Construct State and Continuity Tracking
Manages runtime state for a construct across sessions.
"""

import logging
import json
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages construct runtime state and continuity.
    Tracks session state, ingestion history, and continuity metrics.
    """
    
    def __init__(self, construct_id: str, vvault_root: Optional[str] = None):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        
        self._state = self._load_state()
        self._session_start = datetime.now()
    
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
    
    def _get_state_file(self) -> Optional[Path]:
        """Get the state file path."""
        construct_path = self._find_construct_path()
        if construct_path:
            return construct_path / ".construct_state.json"
        return None
    
    def _load_state(self) -> Dict[str, Any]:
        """Load state from disk."""
        state_file = self._get_state_file()
        
        default_state = {
            "construct_id": self.construct_id,
            "created_at": datetime.now().isoformat(),
            "last_active": None,
            "session_count": 0,
            "total_messages": 0,
            "total_responses": 0,
            "ingestion_history": [],
            "continuity_score": 1.0,
            "identity_locks": [],
            "preferences": {}
        }
        
        if state_file and state_file.exists():
            try:
                loaded = json.loads(state_file.read_text())
                default_state.update(loaded)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        
        return default_state
    
    def _save_state(self):
        """Save state to disk."""
        state_file = self._get_state_file()
        if state_file:
            try:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(json.dumps(self._state, indent=2))
            except Exception as e:
                logger.error(f"Failed to save state: {e}")
    
    def get_current_state(self) -> Dict[str, Any]:
        """Get the current construct state."""
        self._state["last_active"] = datetime.now().isoformat()
        return self._state.copy()
    
    def update_state(self, **updates) -> Dict[str, Any]:
        """Update state with new values."""
        for key, value in updates.items():
            if key in self._state:
                if isinstance(self._state[key], int) and isinstance(value, bool):
                    self._state[key] += 1 if value else 0
                else:
                    self._state[key] = value
        
        self._state["last_active"] = datetime.now().isoformat()
        self._save_state()
        return self._state
    
    def record_ingestion(self, result: Dict[str, Any]):
        """Record an ingestion cycle result."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "ingested": result.get("ingested", 0),
            "routed_stm": result.get("routed_stm", 0),
            "routed_ltm": result.get("routed_ltm", 0),
            "capsule_updated": result.get("capsule_updated", False)
        }
        
        self._state["ingestion_history"].append(entry)
        self._state["ingestion_history"] = self._state["ingestion_history"][-50:]
        
        self._state["total_messages"] += result.get("ingested", 0)
        self._save_state()
    
    def record_identity_lock(self, lock_info: Dict[str, Any]):
        """Record an identity lock event."""
        lock_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": lock_info.get("type", "context_lock"),
            "reason": lock_info.get("reason", ""),
            "duration": lock_info.get("duration")
        }
        
        self._state["identity_locks"].append(lock_entry)
        self._state["identity_locks"] = self._state["identity_locks"][-20:]
        self._save_state()
    
    def update_continuity_score(self, score: float):
        """Update the continuity score (0.0 - 1.0)."""
        self._state["continuity_score"] = max(0.0, min(1.0, score))
        self._save_state()
    
    def start_session(self) -> Dict[str, Any]:
        """Start a new session."""
        self._session_start = datetime.now()
        self._state["session_count"] += 1
        self._state["last_session_start"] = self._session_start.isoformat()
        self._save_state()
        
        return {
            "session_id": f"{self.construct_id}_{self._session_start.strftime('%Y%m%d_%H%M%S')}",
            "session_number": self._state["session_count"],
            "construct_id": self.construct_id
        }
    
    def end_session(self) -> Dict[str, Any]:
        """End the current session."""
        duration = (datetime.now() - self._session_start).total_seconds()
        self._state["last_session_end"] = datetime.now().isoformat()
        self._state["last_session_duration"] = duration
        self._save_state()
        
        return {
            "duration_seconds": duration,
            "total_sessions": self._state["session_count"]
        }
    
    def set_preference(self, key: str, value: Any):
        """Set a construct preference."""
        self._state["preferences"][key] = value
        self._save_state()
    
    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a construct preference."""
        return self._state["preferences"].get(key, default)
    
    def get_continuity_report(self) -> Dict[str, Any]:
        """Generate a continuity report for ContinuityGPT."""
        return {
            "construct_id": self.construct_id,
            "continuity_score": self._state["continuity_score"],
            "session_count": self._state["session_count"],
            "total_messages": self._state["total_messages"],
            "total_responses": self._state["total_responses"],
            "recent_ingestions": self._state["ingestion_history"][-5:],
            "identity_locks": self._state["identity_locks"][-5:],
            "last_active": self._state["last_active"],
            "created_at": self._state["created_at"]
        }
