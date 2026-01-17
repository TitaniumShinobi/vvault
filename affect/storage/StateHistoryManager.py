"""
State History Manager

Manages append-only history tracking for affective state changes.
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..models.affective_state import StateHistoryEntry


class StateHistoryManager:
    """Manages append-only history log for affective states"""
    
    def __init__(self, vvault_root: str):
        """
        Initialize history manager
        
        Args:
            vvault_root: Root path to VVAULT filesystem
        """
        self.vvault_root = Path(vvault_root)
    
    def _get_history_path(self, user_id: str, construct_callsign: str) -> Path:
        """
        Get path to history file
        
        Args:
            user_id: VVAULT user ID (LIFE format)
            construct_callsign: Construct callsign (e.g., "synth-001")
        
        Returns:
            Path to history.jsonl file
        """
        # Find user shard (scan shard_* directories)
        shard = self._find_user_shard(user_id)
        if not shard:
            raise ValueError(f"User {user_id} not found in VVAULT")
        
        return (
            self.vvault_root / "users" / shard / user_id / 
            "instances" / construct_callsign / "affect" / "history.jsonl"
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
    
    def append_history_entry(self, user_id: str, construct_callsign: str, entry: StateHistoryEntry) -> bool:
        """
        Append entry to history log (append-only)
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            entry: StateHistoryEntry to append
        
        Returns:
            True if successful
        """
        history_path = self._get_history_path(user_id, construct_callsign)
        
        # Ensure directory exists
        history_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Append to file (append-only, WORM)
            with open(history_path, 'a', encoding='utf-8') as f:
                f.write(entry.to_jsonl() + '\n')
            return True
        except (IOError, OSError) as e:
            print(f"Error appending to history {history_path}: {e}")
            return False
    
    def load_history(self, user_id: str, construct_callsign: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Load history entries
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            limit: Maximum number of entries to return (None = all, returns most recent first)
        
        Returns:
            List of history entry dictionaries (most recent first)
        """
        history_path = self._get_history_path(user_id, construct_callsign)
        
        if not history_path.exists():
            return []
        
        entries = []
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
            
            # Reverse to get most recent first
            entries.reverse()
            
            if limit:
                entries = entries[:limit]
            
            return entries
        except (IOError, OSError) as e:
            print(f"Error loading history from {history_path}: {e}")
            return []
    
    def get_history_range(
        self, 
        user_id: str, 
        construct_callsign: str, 
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get history entries within a time range
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            start_timestamp: Start timestamp (ISO format, inclusive)
            end_timestamp: End timestamp (ISO format, inclusive)
        
        Returns:
            List of history entry dictionaries within range (most recent first)
        """
        all_entries = self.load_history(user_id, construct_callsign)
        
        if not start_timestamp and not end_timestamp:
            return all_entries
        
        filtered = []
        for entry in all_entries:
            entry_ts = entry.get("timestamp", "")
            if start_timestamp and entry_ts < start_timestamp:
                continue
            if end_timestamp and entry_ts > end_timestamp:
                continue
            filtered.append(entry)
        
        return filtered

