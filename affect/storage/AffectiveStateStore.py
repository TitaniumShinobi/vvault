"""
Affective State Store

Persistent state storage in VVAULT filesystem.
Handles atomic writes with backup/rollback and thread-safe state access.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import threading

from ..models.affective_state import AffectiveState


class AffectiveStateStore:
    """Persistent state storage for affective states"""
    
    def __init__(self, vvault_root: str):
        """
        Initialize state store
        
        Args:
            vvault_root: Root path to VVAULT filesystem
        """
        self.vvault_root = Path(vvault_root)
        self._locks: Dict[str, threading.Lock] = {}
        self._lock_lock = threading.Lock()
    
    def _get_lock(self, key: str) -> threading.Lock:
        """Get or create lock for a given key"""
        with self._lock_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]
    
    def _get_state_path(self, user_id: str, construct_callsign: str) -> Path:
        """
        Get path to state file
        
        Args:
            user_id: VVAULT user ID (LIFE format)
            construct_callsign: Construct callsign (e.g., "synth-001")
        
        Returns:
            Path to state.json file
        """
        # Find user shard (scan shard_* directories)
        shard = self._find_user_shard(user_id)
        if not shard:
            raise ValueError(f"User {user_id} not found in VVAULT")
        
        return (
            self.vvault_root / "users" / shard / user_id / 
            "instances" / construct_callsign / "affect" / "state.json"
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
    
    def load_state(self, user_id: str, construct_callsign: str) -> Optional[AffectiveState]:
        """
        Load current affective state
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
        
        Returns:
            AffectiveState if found, None if not found (returns default state)
        """
        state_path = self._get_state_path(user_id, construct_callsign)
        
        if not state_path.exists():
            return None
        
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return AffectiveState.from_dict(data)
        except (json.JSONDecodeError, IOError) as e:
            # Log error but return None (will create default state)
            print(f"Error loading state from {state_path}: {e}")
            return None
    
    def save_state(self, user_id: str, construct_callsign: str, state: AffectiveState) -> bool:
        """
        Save affective state (atomic write with backup)
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            state: AffectiveState to save
        
        Returns:
            True if successful, False otherwise
        """
        state_path = self._get_state_path(user_id, construct_callsign)
        backup_path = state_path.parent / "state.json.backup"
        
        # Ensure directory exists
        state_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Get lock for this user/construct combination
        lock_key = f"{user_id}:{construct_callsign}"
        lock = self._get_lock(lock_key)
        
        with lock:
            try:
                # Create backup of existing state if it exists
                if state_path.exists():
                    shutil.copy2(state_path, backup_path)
                
                # Write new state atomically (write to temp, then rename)
                temp_path = state_path.parent / "state.json.tmp"
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
                
                # Atomic rename
                temp_path.replace(state_path)
                
                return True
            except (IOError, OSError) as e:
                # Restore backup if write failed
                if backup_path.exists() and state_path.exists():
                    try:
                        shutil.copy2(backup_path, state_path)
                    except:
                        pass
                print(f"Error saving state to {state_path}: {e}")
                return False
    
    def reset_state(self, user_id: str, construct_callsign: str) -> bool:
        """
        Reset state to default neutral state
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
        
        Returns:
            True if successful
        """
        default_state = AffectiveState.default()
        return self.save_state(user_id, construct_callsign, default_state)
    
    def state_exists(self, user_id: str, construct_callsign: str) -> bool:
        """
        Check if state file exists
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
        
        Returns:
            True if state file exists
        """
        state_path = self._get_state_path(user_id, construct_callsign)
        return state_path.exists()

