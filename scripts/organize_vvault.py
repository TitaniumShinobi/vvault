#!/usr/bin/env python3
"""
VVAULT Organization Script
Reorganizes VVAULT according to VVAULT_FILE_STRUCTURE_SPEC.md

This script:
1. Generates new user ID using LIFE standard format
2. Creates proper shard structure
3. Moves real data to proper location
4. Cleans up test/mock users
5. Moves constructs from root to user's constructs folder
6. Moves capsules to user's capsules folder
7. Removes duplicate/incorrect folders
"""

import os
import shutil
import json
import hashlib
from pathlib import Path
from datetime import datetime

# VVAULT root path
VVAULT_ROOT = Path(__file__).parent.parent

# Import user ID generator
import sys
sys.path.insert(0, str(VVAULT_ROOT / "scripts"))
from create_user_profile import generate_user_id, get_user_shard, normalize_name_for_user_id

# Override shard function to use sequential (shard_0000) instead of hash-based
def get_user_shard_sequential(user_id):
    """Sequential sharding: start with shard_0000"""
    return "shard_0000"

# User info
USER_NAME = "Devon Woodson"
USER_EMAIL = "dwoodson92@gmail.com"

# Old user ID (MongoDB ObjectId format)
OLD_USER_ID = "690ec2d8c980c59365f284f5"

# Test/mock user IDs to remove
TEST_USER_IDS = ["anonymous_user_789", "test_user_123", "user_123"]

# Constructs to migrate (from root to user's constructs folder)
CONSTRUCTS_TO_MIGRATE = [
    "synth-001",
    "lin-001",
    "nova-001",
    "sera-001",
    "katana-001",
    "katana-002",
    "aurora-001",
    "monday-001",
    "frame-001",
    "frame-002",
]

# Folders to remove (duplicates/incorrect)
FOLDERS_TO_REMOVE = [
    "chatty",      # Not a construct, synth is
    "chatty-001",  # Duplicate/incorrect
    "lin",          # Should be lin-001
]

def generate_new_user_id():
    """Generate new user ID using LIFE standard"""
    return generate_user_id(USER_NAME)

def create_user_structure(new_user_id):
    """Create proper user directory structure with sharding"""
    shard = get_user_shard_sequential(new_user_id)  # Use sequential sharding
    user_dir = VVAULT_ROOT / "users" / shard / new_user_id
    
    # Create directory structure
    (user_dir / "identity").mkdir(parents=True, exist_ok=True)
    (user_dir / "constructs").mkdir(parents=True, exist_ok=True)
    (user_dir / "capsules").mkdir(parents=True, exist_ok=True)
    (user_dir / "sessions").mkdir(parents=True, exist_ok=True)
    
    print(f"✅ Created user structure: {user_dir}")
    print(f"   Shard: {shard}")
    
    return user_dir, shard

def create_user_profile(new_user_id, user_dir, constructs):
    """Create user profile.json"""
    profile = {
        "user_id": new_user_id,
        "user_name": USER_NAME,
        "email": USER_EMAIL,
        "created": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "constructs": constructs,
        "storage_quota": "unlimited",
        "features": ["blockchain_identity", "capsule_encryption", "multi_platform_memory"]
    }
    
    profile_file = user_dir / "identity" / "profile.json"
    with open(profile_file, 'w') as f:
        json.dump(profile, f, indent=2)
    
    print(f"✅ Created user profile: {profile_file}")
    return profile

def migrate_real_conversation(new_user_id, user_dir):
    """Migrate the real conversation from synth-001/Chatty/chat_with_synth-001.md"""
    source_file = VVAULT_ROOT / "synth-001" / "Chatty" / "chat_with_synth-001.md"
    
    if not source_file.exists():
        print(f"⚠️  Real conversation not found at {source_file}")
        return None
    
    # Create construct directory
    construct_dir = user_dir / "constructs" / "synth-001" / "chatty"
    construct_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy conversation file
    dest_file = construct_dir / "chat_with_synth-001.md"
    shutil.copy2(source_file, dest_file)
    
    print(f"✅ Migrated real conversation:")
    print(f"   From: {source_file}")
    print(f"   To: {dest_file}")
    
    return dest_file

def migrate_constructs(new_user_id, user_dir):
    """Migrate constructs from root to user's constructs folder"""
    migrated = []
    
    for construct_id in CONSTRUCTS_TO_MIGRATE:
        source_dir = VVAULT_ROOT / construct_id
        
        if not source_dir.exists():
            print(f"⚠️  Construct {construct_id} not found at {source_dir}")
            continue
        
        dest_dir = user_dir / "constructs" / construct_id
        
        if dest_dir.exists():
            print(f"⚠️  Construct {construct_id} already exists at {dest_dir}, skipping")
            continue
        
        # Move construct directory
        shutil.move(str(source_dir), str(dest_dir))
        migrated.append(construct_id)
        print(f"✅ Migrated construct: {construct_id}")
    
    return migrated

def migrate_capsules(new_user_id, user_dir):
    """Migrate capsules from root /capsules/ to user's capsules folder"""
    source_dir = VVAULT_ROOT / "capsules"
    
    if not source_dir.exists():
        print(f"⚠️  Capsules directory not found at {source_dir}")
        return []
    
    dest_dir = user_dir / "capsules"
    migrated = []
    
    # Move all .capsule files
    for capsule_file in source_dir.glob("*.capsule"):
        dest_file = dest_dir / capsule_file.name
        shutil.move(str(capsule_file), str(dest_file))
        migrated.append(capsule_file.name)
        print(f"✅ Migrated capsule: {capsule_file.name}")
    
    # Move archive folder if it exists
    archive_source = source_dir / "archive"
    if archive_source.exists():
        archive_dest = dest_dir / "archive"
        if archive_dest.exists():
            # Merge archives
            for item in archive_source.iterdir():
                shutil.move(str(item), str(archive_dest / item.name))
        else:
            shutil.move(str(archive_source), str(archive_dest))
        print(f"✅ Migrated capsule archive")
    
    # Move documentation files
    for doc_file in source_dir.glob("*.md"):
        dest_file = dest_dir / doc_file.name
        shutil.move(str(doc_file), str(dest_file))
        migrated.append(doc_file.name)
        print(f"✅ Migrated documentation: {doc_file.name}")
    
    return migrated

def cleanup_test_users():
    """Remove test/mock user directories"""
    removed = []
    
    for test_user_id in TEST_USER_IDS:
        test_user_dir = VVAULT_ROOT / "users" / test_user_id
        
        if test_user_dir.exists():
            shutil.rmtree(test_user_dir)
            removed.append(test_user_id)
            print(f"✅ Removed test user: {test_user_id}")
        else:
            print(f"⚠️  Test user {test_user_id} not found")
    
    return removed

def cleanup_old_user_data(old_user_id):
    """Remove old user data directory (if exists)"""
    old_user_dir = VVAULT_ROOT / "users" / old_user_id
    
    if old_user_dir.exists():
        # Check if it's just test data
        transcripts_dir = old_user_dir / "transcripts"
        if transcripts_dir.exists():
            # Count transcript folders
            transcript_folders = [d for d in transcripts_dir.iterdir() if d.is_dir()]
            # If all are "primary_" prefixed (test data), remove
            if all(d.name.startswith("primary_") for d in transcript_folders):
                shutil.rmtree(old_user_dir)
                print(f"✅ Removed old test user data: {old_user_id}")
                return True
    
    return False

def remove_duplicate_folders():
    """Remove duplicate/incorrect folders from root"""
    removed = []
    
    for folder_name in FOLDERS_TO_REMOVE:
        folder_path = VVAULT_ROOT / folder_name
        
        if folder_path.exists():
            # Check if it's a directory
            if folder_path.is_dir():
                # Check if it's empty or only contains test data
                if folder_name == "chatty":
                    # Check if it's just source code
                    src_dir = folder_path / "src"
                    if src_dir.exists():
                        print(f"⚠️  {folder_name} contains source code, skipping removal")
                        continue
                
                shutil.rmtree(folder_path)
                removed.append(folder_name)
                print(f"✅ Removed duplicate folder: {folder_name}")
            else:
                print(f"⚠️  {folder_name} is not a directory, skipping")
    
    return removed

def update_user_registry(new_user_id, constructs):
    """Update global user registry"""
    registry_file = VVAULT_ROOT / "users.json"
    
    if registry_file.exists():
        with open(registry_file, 'r') as f:
            registry = json.load(f)
    else:
        registry = {"users": {}}
    
    registry["users"][new_user_id] = {
        "id": new_user_id,
        "name": USER_NAME,
        "email": USER_EMAIL,
        "created": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "constructs": constructs,
        "storage_quota": "unlimited",
        "features": ["blockchain_identity", "capsule_encryption"]
    }
    
    with open(registry_file, 'w') as f:
        json.dump(registry, f, indent=2)
    
    print(f"✅ Updated user registry: {registry_file}")

def main():
    print("=" * 70)
    print("VVAULT Organization Script")
    print("=" * 70)
    print()
    
    # Step 1: Generate new user ID
    print("Step 1: Generating new user ID...")
    new_user_id = generate_new_user_id()
    print(f"   New User ID: {new_user_id}")
    print(f"   Old User ID: {OLD_USER_ID} (MongoDB ObjectId format)")
    print()
    
    # Step 2: Create user structure
    print("Step 2: Creating user directory structure...")
    user_dir, shard = create_user_structure(new_user_id)
    print()
    
    # Step 3: Migrate real conversation
    print("Step 3: Migrating real conversation...")
    migrated_conversation = migrate_real_conversation(new_user_id, user_dir)
    print()
    
    # Step 4: Migrate constructs
    print("Step 4: Migrating constructs...")
    migrated_constructs = migrate_constructs(new_user_id, user_dir)
    print()
    
    # Step 5: Migrate capsules
    print("Step 5: Migrating capsules...")
    migrated_capsules = migrate_capsules(new_user_id, user_dir)
    print()
    
    # Step 6: Cleanup test users
    print("Step 6: Cleaning up test users...")
    removed_test_users = cleanup_test_users()
    print()
    
    # Step 7: Cleanup old user data
    print("Step 7: Cleaning up old user data...")
    cleanup_old_user_data(OLD_USER_ID)
    print()
    
    # Step 8: Remove duplicate folders
    print("Step 8: Removing duplicate folders...")
    removed_folders = remove_duplicate_folders()
    print()
    
    # Step 9: Create user profile
    print("Step 9: Creating user profile...")
    all_constructs = migrated_constructs + ["synth-001"]  # synth-001 is already migrated
    profile = create_user_profile(new_user_id, user_dir, all_constructs)
    print()
    
    # Step 10: Update user registry
    print("Step 10: Updating user registry...")
    update_user_registry(new_user_id, all_constructs)
    print()
    
    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"✅ New User ID: {new_user_id}")
    print(f"✅ Shard: {shard}")
    print(f"✅ Migrated Constructs: {len(migrated_constructs)}")
    print(f"   {', '.join(migrated_constructs)}")
    print(f"✅ Migrated Capsules: {len(migrated_capsules)}")
    print(f"✅ Removed Test Users: {len(removed_test_users)}")
    print(f"✅ Removed Duplicate Folders: {len(removed_folders)}")
    print()
    print(f"📁 User Directory: {user_dir}")
    print(f"📄 Profile: {user_dir / 'identity' / 'profile.json'}")
    print()
    print("✅ VVAULT organization complete!")
    print("=" * 70)

if __name__ == "__main__":
    main()

