"""
Audit Logger

Manages append-only audit logging for all state changes, governance decisions, and influence applications.
"""

from pathlib import Path
from typing import Optional
from datetime import datetime

from ..models.affective_state import AuditLogEntry


class AuditLogger:
    """Manages append-only audit log for affective state operations"""
    
    def __init__(self, vvault_root: str):
        """
        Initialize audit logger
        
        Args:
            vvault_root: Root path to VVAULT filesystem
        """
        self.vvault_root = Path(vvault_root)
    
    def _get_audit_path(self, user_id: str, construct_callsign: str) -> Path:
        """
        Get path to audit log file
        
        Args:
            user_id: VVAULT user ID (LIFE format)
            construct_callsign: Construct callsign (e.g., "synth-001")
        
        Returns:
            Path to audit.jsonl file
        """
        # Find user shard (scan shard_* directories)
        shard = self._find_user_shard(user_id)
        if not shard:
            raise ValueError(f"User {user_id} not found in VVAULT")
        
        return (
            self.vvault_root / "users" / shard / user_id / 
            "instances" / construct_callsign / "affect" / "audit.jsonl"
        )
    
    def _find_user_shard(self, user_id: str) -> Optional[str]:
        """Find which shard contains the user"""
        users_dir = self.vvault_root / "users"
        if not users_dir.exists():
            return None
        
        for shard_dir in users_dir.iterdir():
            if shard_dir.is_dir() and shard_dir.name.startswith("shard_"):
                user_dir = shard_dir / user_id
                if user_dir.exists():
                    return shard_dir.name
        
        return None
    
    def log_state_change(
        self,
        user_id: str,
        construct_callsign: str,
        previous_state: Optional[dict],
        new_state: Optional[dict],
        user_signal: Optional[dict],
        governance_decision: Optional[dict],
        actor: str = "system"
    ) -> bool:
        """
        Log state change event
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            previous_state: Previous state dictionary
            new_state: New state dictionary
            user_signal: User signal dictionary (if applicable)
            governance_decision: Governance decision dictionary (if applicable)
            actor: Actor who triggered the change
        
        Returns:
            True if successful
        """
        entry = AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            event_type="state_update",
            previous_state=previous_state,
            new_state=new_state,
            user_signal=user_signal,
            governance_decision=governance_decision,
            actor=actor
        )
        return self._append_entry(user_id, construct_callsign, entry)
    
    def log_governance_decision(
        self,
        user_id: str,
        construct_callsign: str,
        governance_decision: dict,
        previous_state: Optional[dict],
        new_state: Optional[dict],
        actor: str = "governor"
    ) -> bool:
        """
        Log governance decision event
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            governance_decision: Governance decision dictionary
            previous_state: Previous state dictionary
            new_state: New state dictionary (if decision allowed update)
            actor: Actor who made the decision
        
        Returns:
            True if successful
        """
        entry = AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            event_type="governance_decision",
            previous_state=previous_state,
            new_state=new_state,
            user_signal=None,
            governance_decision=governance_decision,
            actor=actor
        )
        return self._append_entry(user_id, construct_callsign, entry)
    
    def log_influence_applied(
        self,
        user_id: str,
        construct_callsign: str,
        influence_params: dict,
        current_state: dict,
        actor: str = "system"
    ) -> bool:
        """
        Log influence application event
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            influence_params: Influence parameters dictionary
            current_state: Current state dictionary
            actor: Actor who applied influence
        
        Returns:
            True if successful
        """
        entry = AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            event_type="influence_applied",
            previous_state=None,
            new_state=None,
            user_signal=None,
            governance_decision=None,
            actor=actor,
            metadata={
                "influenceParams": influence_params,
                "currentState": current_state
            }
        )
        return self._append_entry(user_id, construct_callsign, entry)
    
    def _append_entry(self, user_id: str, construct_callsign: str, entry: AuditLogEntry) -> bool:
        """
        Append entry to audit log (append-only)
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            entry: AuditLogEntry to append
        
        Returns:
            True if successful
        """
        audit_path = self._get_audit_path(user_id, construct_callsign)
        
        # Ensure directory exists
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Append to file (append-only, WORM)
            with open(audit_path, 'a', encoding='utf-8') as f:
                f.write(entry.to_jsonl() + '\n')
            return True
        except (IOError, OSError) as e:
            print(f"Error appending to audit log {audit_path}: {e}")
            return False

