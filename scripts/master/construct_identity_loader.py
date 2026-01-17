#!/usr/bin/env python3
"""
Construct Identity Loader
Loads construct identity files dynamically from VVAULT directory structure.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional, Any
import glob


def load_if_exists(file_path: Path) -> Optional[str]:
    """Load text file if it exists."""
    if file_path.exists():
        try:
            return file_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Warning: Failed to load {file_path}: {e}")
            return None
    return None


def load_json_if_exists(file_path: Path) -> Optional[Dict[str, Any]]:
    """Load JSON file if it exists."""
    if file_path.exists():
        try:
            return json.loads(file_path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"Warning: Failed to load JSON {file_path}: {e}")
            return None
    return None


def search_workspace_for_identity_files(
    construct_callsign: str,
    vvault_root: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Search workspace for additional identity-related files.
    
    Args:
        construct_callsign: Construct callsign
        vvault_root: VVAULT root directory
        user_id: VVAULT user ID
    
    Returns:
        Dictionary of found identity files
    """
    base_path = Path(vvault_root) / "users" / "shard_0000" / user_id / "instances" / construct_callsign
    identity_files = {}
    
    # Search patterns for identity-related files
    patterns = [
        "**/*identity*.json",
        "**/*personality*.json",
        "**/*profile*.json",
        "**/*config*.json",
        "**/*.blueprint",
    ]
    
    for pattern in patterns:
        matches = glob.glob(str(base_path / pattern), recursive=True)
        for match in matches:
            rel_path = Path(match).relative_to(base_path)
            if rel_path.name not in identity_files:
                if match.endswith('.json'):
                    identity_files[rel_path.name] = load_json_if_exists(Path(match))
                else:
                    identity_files[rel_path.name] = load_if_exists(Path(match))
    
    return identity_files


def load_construct_identity(
    construct_callsign: str,
    vvault_root: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Load all identity files for a construct.
    
    Args:
        construct_callsign: Construct callsign (e.g., "nova-001")
        vvault_root: Root directory of VVAULT
        user_id: VVAULT user ID
    
    Returns:
        Dictionary containing all loaded identity data
    """
    base_path = Path(vvault_root) / "users" / "shard_0000" / user_id / "instances" / construct_callsign
    identity_dir = base_path / "identity"
    
    identity = {
        'construct_callsign': construct_callsign,
        'user_id': user_id,
        'base_path': str(base_path),
        'identity_dir': str(identity_dir),
        'prompt': load_if_exists(identity_dir / "prompt.txt"),
        'capsule': load_json_if_exists(identity_dir / f"{construct_callsign}.capsule"),
        'conditioning': load_if_exists(identity_dir / "conditioning.txt"),
        'personality': load_json_if_exists(identity_dir / "personality.json"),
        'config': load_json_if_exists(base_path / "config.json"),
        'additional_files': search_workspace_for_identity_files(construct_callsign, vvault_root, user_id)
    }
    
    return identity


def get_construct_config(
    construct_callsign: str,
    vvault_root: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Load construct configuration from config.json.
    
    Args:
        construct_callsign: Construct callsign
        vvault_root: VVAULT root directory
        user_id: VVAULT user ID
    
    Returns:
        Configuration dictionary with defaults
    """
    config_path = Path(vvault_root) / "users" / "shard_0000" / user_id / "instances" / construct_callsign / "config.json"
    
    default_config = {
        "persistence": {
            "enabled": True,
            "autonomy": True,
            "independence": True
        },
        "memory": {
            "stmEnabled": True,
            "ltmEnabled": True
        },
        "scripts": {}
    }
    
    if config_path.exists():
        try:
            config = load_json_if_exists(config_path)
            if config:
                # Merge with defaults
                merged = default_config.copy()
                merged.update(config)
                # Ensure nested structures are merged
                if 'persistence' in config:
                    merged['persistence'].update(config['persistence'])
                if 'memory' in config:
                    merged['memory'].update(config['memory'])
                if 'scripts' in config:
                    merged['scripts'].update(config['scripts'])
                return merged
        except Exception as e:
            print(f"Warning: Failed to load config.json: {e}")
    
    return default_config


def save_construct_config(
    construct_callsign: str,
    vvault_root: str,
    user_id: str,
    config: Dict[str, Any]
) -> bool:
    """
    Save construct configuration to config.json.
    
    Args:
        construct_callsign: Construct callsign
        vvault_root: VVAULT root directory
        user_id: VVAULT user ID
        config: Configuration dictionary to save
    
    Returns:
        True if successful, False otherwise
    """
    config_path = Path(vvault_root) / "users" / "shard_0000" / user_id / "instances" / construct_callsign / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error: Failed to save config.json: {e}")
        return False
