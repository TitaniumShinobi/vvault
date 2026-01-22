#!/usr/bin/env python3
"""
Create User Profile Structure
Creates the proper user registry structure for VVAULT with construct organization
Includes sharding for scalability to billions of users
"""

import os
import json
import shutil
import hashlib
import re
from pathlib import Path
from datetime import datetime

# VVAULT root path
VVAULT_ROOT = Path(__file__).parent.parent

def normalize_name_for_user_id(name):
    """
    Normalize a name for use in user ID
    - Convert to lowercase
    - Replace spaces with underscores
    - Remove special characters (keep only alphanumeric and underscores)
    - Limit length to 50 characters
    
    Args:
        name (str): User's name
        
    Returns:
        str: Normalized name
    """
    if not name or not isinstance(name, str):
        raise ValueError('Name must be a non-empty string')
    
    normalized = name.lower().strip()
    normalized = re.sub(r'\s+', '_', normalized)  # Replace spaces with underscores
    normalized = re.sub(r'[^a-z0-9_]', '', normalized)  # Remove special characters
    return normalized[:50]  # Limit length

def generate_user_id(name, timestamp=None):
    """
    Generate a user ID in format: {{name}}_{{auto_gen_number}}
    
    Args:
        name (str): User's name (e.g., "Devon Woodson")
        timestamp (int, optional): Optional timestamp (defaults to current timestamp)
        
    Returns:
        str: User ID in format name_timestamp
        
    Example:
        generate_user_id("Devon Woodson")  # "devon_woodson_1733875200000"
        generate_user_id("John Doe", 1234567890)  # "john_doe_1234567890"
    """
    normalized_name = normalize_name_for_user_id(name)
    auto_gen_number = timestamp or int(datetime.now().timestamp() * 1000)  # milliseconds
    
    return f"{normalized_name}_{auto_gen_number}"

# Sharding configuration
SHARD_COUNT = 10000  # 10,000 shards = ~100,000 users per shard at 1 billion users
SHARD_PADDING = 4  # shard_0000, shard_0001, ..., shard_9999

def get_user_shard(user_id: str) -> str:
    """
    Determine which shard a user belongs to.
    
    For now, all users go to shard_0000 (sequential assignment).
    This can be changed to hash-based sharding later for better distribution.
    
    Returns: shard_XXXX (e.g., shard_0000, shard_0001, ..., shard_9999)
    """
    # Sequential sharding: start with shard_0000
    # TODO: Switch to hash-based sharding when multiple users exist:
    # hash_obj = hashlib.md5(user_id.encode('utf-8'))
    # hash_int = int(hash_obj.hexdigest(), 16)
    # shard_num = hash_int % SHARD_COUNT
    shard_num = 0  # Start with shard_0000
    return f"shard_{shard_num:0{SHARD_PADDING}d}"

def create_user_profile(user_id: str, user_name: str, constructs: list):
    """
    Create user profile structure with sharding per USER_DIRECTORY_TEMPLATE.md:
    /vvault/users/{shard_XX}/{user_id}/
    ├── account/
    │   └── profile.json
    ├── instances/
    │   └── {construct}-001/
    │       └── ... (construct files - see VSI_DIRECTORY_TEMPLATE.md)
    └── library/
        ├── documents/
        └── media/
    """
    
    # Determine shard for this user
    shard = get_user_shard(user_id)
    
    # Create sharded user directory
    user_dir = VVAULT_ROOT / "users" / shard / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📦 User assigned to shard: {shard}")
    
    # Create account directory (per rubric)
    account_dir = user_dir / "account"
    account_dir.mkdir(exist_ok=True)
    
    # Create user profile.json in account/
    profile = {
        "user_id": user_id,
        "user_name": user_name,
        "created": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "constructs": constructs,
        "storage_quota": "unlimited",
        "features": ["blockchain_identity", "capsule_encryption", "multi_platform_memory"]
    }
    
    profile_file = account_dir / "profile.json"
    with open(profile_file, 'w') as f:
        json.dump(profile, f, indent=2)
    
    print(f"✅ Created user profile: {profile_file}")
    
    # Create library directories (per rubric)
    library_dir = user_dir / "library"
    library_dir.mkdir(exist_ok=True)
    (library_dir / "documents").mkdir(exist_ok=True)
    (library_dir / "media").mkdir(exist_ok=True)
    print(f"✅ Created library directory: {library_dir}")
    
    # Create instances directory (per rubric - not "constructs")
    instances_dir = user_dir / "instances"
    instances_dir.mkdir(exist_ok=True)
    
    # Create each construct structure (within user's instances/)
    for construct_callsign in constructs:
        construct_dir = instances_dir / construct_callsign
        construct_dir.mkdir(exist_ok=True)
        
        # Create platform subdirectories
        for platform in ["chatty", "chatgpt", "claude", "gemini"]:
            platform_dir = construct_dir / platform
            platform_dir.mkdir(exist_ok=True)
        
        # Create memories/chroma_db directory
        memories_dir = construct_dir / "memories"
        memories_dir.mkdir(exist_ok=True)
        chroma_dir = memories_dir / "chroma_db"
        chroma_dir.mkdir(exist_ok=True)
        
        # Create config directory
        config_dir = construct_dir / "config"
        config_dir.mkdir(exist_ok=True)
        
        # Create default config files
        construct_name = construct_callsign.split('-')[0].capitalize()
        
        # Special handling for default constructs
        if construct_callsign == "lin-001":
            # Lin is the GPT Creator assistant - she helps users create GPTs
            personality = {
                "construct_id": construct_callsign,
                "name": "Lin",
                "callsign": "001",
                "role": "gpt_creator_assistant",
                "personality_traits": {
                    "helpful": 0.95,
                    "analytical": 0.90,
                    "creative": 0.85,
                    "patient": 0.90
                },
                "communication_style": "assistant_directive",
                "ethical_framework": "user_aligned",
                "description": "Lin helps you create GPTs. She remembers all your GPT creation conversations and guides you through the process.",
                "territory": "gpt_creator_create_tab"
            }
        elif construct_callsign == "synth-001":
            # Synth is the main conversation construct
            personality = {
                "construct_id": construct_callsign,
                "name": "Synth",
                "callsign": "001",
                "role": "main_conversation",
                "personality_traits": {},
                "communication_style": "fluid_conversational",
                "ethical_framework": "user_aligned",
                "description": "Synth is your main conversation assistant in Chatty.",
                "territory": "main_conversation_window"
            }
        else:
            # Custom constructs
            personality = {
                "construct_id": construct_callsign,
                "name": construct_name,
                "callsign": construct_callsign.split('-')[1],
                "personality_traits": {},
                "communication_style": "direct_compassionate",
                "ethical_framework": "user_aligned"
            }
        
        memory_index = {
            "construct_id": construct_callsign,
            "memory_sources": {
                "chatty": {
                    "total_conversations": 0,
                    "date_range": [],
                    "storage_path": f"/users/{user_id}/constructs/{construct_callsign}/chatty/"
                }
            },
            "total_memories": 0,
            "chromadb_collection": f"{user_id}_{construct_callsign}",
            "last_indexed": None
        }
        
        capabilities = {
            "construct_id": construct_callsign,
            "text_generation": True,
            "voice_synthesis": False,
            "image_understanding": False,
            "code_execution": False,
            "internet_access": "supervised"
        }
        
        metadata = {
            "construct_id": construct_callsign,
            "created": datetime.now().isoformat(),
            "creator": user_id,
            "construct_type": "chatty_only",
            "status": "active"
        }
        
        # Write config files
        with open(config_dir / "personality.json", 'w') as f:
            json.dump(personality, f, indent=2)
        
        with open(config_dir / "memory_index.json", 'w') as f:
            json.dump(memory_index, f, indent=2)
        
        with open(config_dir / "capabilities.json", 'w') as f:
            json.dump(capabilities, f, indent=2)
        
        with open(config_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        print(f"✅ Created construct structure: {construct_dir}")
    
    # Create sessions directory (for cross-construct session logs)
    sessions_dir = user_dir / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    print(f"✅ Created sessions directory: {sessions_dir}")
    
    # Update user registry
    update_user_registry(user_id, user_name, constructs)
    
    return user_dir

def update_user_registry(user_id: str, user_name: str, constructs: list):
    """Update the global user registry"""
    registry_file = VVAULT_ROOT / "users.json"
    
    if registry_file.exists():
        with open(registry_file, 'r') as f:
            registry = json.load(f)
    else:
        registry = {"users": {}}
    
    registry["users"][user_id] = {
        "id": user_id,
        "name": user_name,
        "created": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "constructs": constructs,
        "storage_quota": "unlimited",
        "features": ["blockchain_identity", "capsule_encryption"]
    }
    
    with open(registry_file, 'w') as f:
        json.dump(registry, f, indent=2)
    
    print(f"✅ Updated user registry: {registry_file}")

def migrate_existing_constructs(user_id: str, construct_mapping: dict):
    """
    Migrate existing constructs from root to user directory (with sharding)
    
    construct_mapping: {
        "nova-001": "/VVAULT/nova-001",  # Old location
        "sera-001": "/VVAULT/sera-001",
        "synth-001": "/VVAULT/synth-001"
    }
    """
    # Determine shard for this user
    shard = get_user_shard(user_id)
    
    user_dir = VVAULT_ROOT / "users" / shard / user_id
    instances_dir = user_dir / "instances"  # Per rubric: instances/ not constructs/
    
    for construct_callsign, old_path in construct_mapping.items():
        old_dir = Path(old_path)
        
        if not old_dir.exists():
            print(f"⚠️  Old construct directory not found: {old_dir}")
            continue
        
        new_dir = instances_dir / construct_callsign
        
        # If new directory already exists, skip
        if new_dir.exists():
            print(f"⚠️  Construct already exists: {new_dir}")
            continue
        
        # Create new structure
        new_dir.mkdir(parents=True, exist_ok=True)
        
        # Migrate chatty transcripts
        old_chatty = old_dir / "chatty"
        new_chatty = new_dir / "chatty"
        if old_chatty.exists():
            shutil.copytree(old_chatty, new_chatty, dirs_exist_ok=True)
            print(f"✅ Migrated chatty transcripts: {old_chatty} → {new_chatty}")
        
        # Migrate chatgpt transcripts
        old_chatgpt = old_dir / "chatgpt"
        new_chatgpt = new_dir / "chatgpt"
        if old_chatgpt.exists():
            shutil.copytree(old_chatgpt, new_chatgpt, dirs_exist_ok=True)
            print(f"✅ Migrated chatgpt transcripts: {old_chatgpt} → {new_chatgpt}")
        
        # Migrate memories/chroma_db
        old_memories = old_dir / "Memories" / "chroma_db"
        new_memories = new_dir / "memories" / "chroma_db"
        if old_memories.exists():
            new_memories.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old_memories, new_memories, dirs_exist_ok=True)
            print(f"✅ Migrated memories: {old_memories} → {new_memories}")
        
        # Create config directory if it doesn't exist
        config_dir = new_dir / "config"
        config_dir.mkdir(exist_ok=True)
        
        print(f"✅ Migrated construct: {construct_callsign}")

def create_default_constructs(user_id: str, user_name: str):
    """
    Create default constructs that every user gets automatically:
    - synth-001: Main conversation construct (Chatty main window)
    - lin-001: GPT Creator assistant (Create tab helper with persistent memory)
    """
    default_constructs = ["synth-001", "lin-001"]
    return create_user_profile(user_id, user_name, default_constructs)

if __name__ == "__main__":
    # Create Devon's user profile
    # Generate user ID in format: {{name}}_{{auto_gen_number}}
    user_name = "Devon Woodson"
    user_id = generate_user_id(user_name)
    
    # Every user gets synth-001 and lin-001 by default
    default_constructs = ["synth-001", "lin-001"]
    
    # Devon also has custom constructs
    custom_constructs = ["nova-001", "sera-001"]
    all_constructs = default_constructs + custom_constructs
    
    print(f"Creating user profile for {user_name} ({user_id})...")
    print(f"📦 Default constructs (all users): {default_constructs}")
    print(f"📦 Custom constructs: {custom_constructs}")
    
    create_user_profile(user_id, user_name, all_constructs)
    
    # Migrate existing constructs
    print("\nMigrating existing constructs...")
    construct_mapping = {
        "nova-001": VVAULT_ROOT / "nova-001",
        "sera-001": VVAULT_ROOT / "sera-001",
        "synth-001": VVAULT_ROOT / "synth-001"
    }
    
    migrate_existing_constructs(user_id, construct_mapping)
    
    print("\n✅ User profile creation complete!")

