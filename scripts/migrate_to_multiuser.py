#!/usr/bin/env python3
"""
Migrate single-user VVAULT to multi-user architecture

This script migrates existing construct folders from the root VVAULT directory
into a user-specific directory structure under /users/{userId}/constructs/

Author: Devon Allen Woodson
Date: 2025-11-09
"""

import os
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
VVAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVON_USER_ID = "690ec2d8c980c59365f284f5"
DEVON_EMAIL = "devon@example.com"  # Update with actual email
DEVON_NAME = "Devon Woodson"

# Directories to exclude from migration
EXCLUDE_DIRS = {
    'users', 'system', '.git', '__pycache__', 'node_modules',
    'venv', 'vvault_env', 'cleanhouse_env', '.venv',
    'scripts', 'docs', 'public', 'src', 'assets', 'smart_contracts',
    'login-screen', 'login-screen 2', 'corefiles', 'chatty',
    'logs', 'etl_logs', 'indexes', 'memory_records', 'capsules'  # Will be handled separately
}

# Construct pattern: name-### (e.g., synth-001, nova-001)
def is_construct_folder(name):
    """Check if folder name matches construct pattern"""
    if '-' not in name:
        return False
    parts = name.split('-')
    if len(parts) != 2:
        return False
    # Check if second part is 3 digits
    try:
        callsign = int(parts[1])
        return 1 <= callsign <= 999
    except ValueError:
        return False

def find_construct_folders(vault_root):
    """Find all construct folders in root directory"""
    construct_folders = []
    
    if not os.path.exists(vault_root):
        logger.error(f"VVAULT root directory not found: {vault_root}")
        return construct_folders
    
    root_items = os.listdir(vault_root)
    
    for item in root_items:
        item_path = os.path.join(vault_root, item)
        
        # Skip if not a directory
        if not os.path.isdir(item_path):
            continue
        
        # Skip excluded directories
        if item in EXCLUDE_DIRS:
            continue
        
        # Check if it matches construct pattern
        if is_construct_folder(item):
            construct_folders.append(item)
        elif item == DEVON_USER_ID:
            # This is already a UUID directory - might be a user folder
            logger.warning(f"Found UUID directory in root: {item} - may need special handling")
    
    return construct_folders

def create_user_directory_structure(user_id, vault_root):
    """Create the user directory structure"""
    users_dir = os.path.join(vault_root, "users")
    user_dir = os.path.join(users_dir, user_id)
    constructs_dir = os.path.join(user_dir, "constructs")
    capsules_dir = os.path.join(user_dir, "capsules")
    identity_dir = os.path.join(user_dir, "identity")
    
    # Create directories
    os.makedirs(constructs_dir, exist_ok=True)
    os.makedirs(capsules_dir, exist_ok=True)
    os.makedirs(identity_dir, exist_ok=True)
    
    logger.info(f"✅ Created user directory structure: {user_dir}")
    
    return {
        'user_dir': user_dir,
        'constructs_dir': constructs_dir,
        'capsules_dir': capsules_dir,
        'identity_dir': identity_dir
    }

def migrate_constructs(construct_folders, constructs_dir, vault_root):
    """Move construct folders to user constructs directory"""
    migrated = []
    failed = []
    
    for construct in construct_folders:
        old_path = os.path.join(vault_root, construct)
        new_path = os.path.join(constructs_dir, construct)
        
        try:
            if os.path.exists(new_path):
                logger.warning(f"⚠️  Construct already exists at destination: {construct}")
                logger.info(f"   Skipping migration for: {construct}")
                continue
            
            logger.info(f"📦 Moving: {construct} → users/{DEVON_USER_ID}/constructs/{construct}")
            shutil.move(old_path, new_path)
            migrated.append(construct)
            logger.info(f"   ✅ Successfully migrated: {construct}")
            
        except Exception as e:
            logger.error(f"   ❌ Failed to migrate {construct}: {e}")
            failed.append(construct)
    
    return migrated, failed

def migrate_capsules(capsules_dir, vault_root):
    """Move capsules directory to user directory"""
    old_capsules = os.path.join(vault_root, "capsules")
    
    if not os.path.exists(old_capsules):
        logger.info("📦 No capsules directory found - skipping")
        return False
    
    if os.path.exists(capsules_dir):
        logger.warning(f"⚠️  Capsules directory already exists at destination")
        logger.info(f"   Merging contents...")
        # Merge contents instead of moving
        for item in os.listdir(old_capsules):
            old_item = os.path.join(old_capsules, item)
            new_item = os.path.join(capsules_dir, item)
            if os.path.exists(new_item):
                logger.warning(f"   Skipping existing: {item}")
                continue
            shutil.move(old_item, new_item)
        # Remove empty directory
        try:
            os.rmdir(old_capsules)
        except OSError:
            pass
        return True
    
    logger.info(f"📦 Moving: capsules/ → users/{DEVON_USER_ID}/capsules/")
    try:
        shutil.move(old_capsules, capsules_dir)
        logger.info(f"   ✅ Successfully migrated capsules")
        return True
    except Exception as e:
        logger.error(f"   ❌ Failed to migrate capsules: {e}")
        return False

def create_user_identity(user_id, identity_dir):
    """Create user identity fingerprint"""
    identity_file = os.path.join(identity_dir, "fingerprint.json")
    
    identity_data = {
        "user_id": user_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "fingerprint": f"vvault_user_{user_id}",
        "version": "1.0"
    }
    
    with open(identity_file, 'w') as f:
        json.dump(identity_data, f, indent=2)
    
    logger.info(f"✅ Created user identity: {identity_file}")

def create_user_registry(user_id, email, name, constructs, vault_root):
    """Create or update user registry"""
    registry_file = os.path.join(vault_root, "users.json")
    
    # Load existing registry if it exists
    if os.path.exists(registry_file):
        with open(registry_file, 'r') as f:
            registry = json.load(f)
    else:
        registry = {
            "version": "1.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "users": {}
        }
    
    # Add or update user record
    registry["users"][user_id] = {
        "id": user_id,
        "email": email,
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "constructs": constructs,
        "storage_quota": "10GB",
        "features": ["blockchain_identity", "capsule_encryption"]
    }
    
    # Save registry
    with open(registry_file, 'w') as f:
        json.dump(registry, f, indent=2)
    
    logger.info(f"✅ User registry created/updated: {registry_file}")
    logger.info(f"   Registered user: {name} ({email})")
    logger.info(f"   Constructs: {len(constructs)}")

def migrate_to_multiuser():
    """Main migration function"""
    logger.info("=" * 60)
    logger.info("🔄 Migrating VVAULT to multi-user architecture")
    logger.info("=" * 60)
    logger.info(f"VVAULT Root: {VVAULT_ROOT}")
    logger.info(f"User ID: {DEVON_USER_ID}")
    logger.info("")
    
    # Step 1: Find construct folders
    logger.info("📁 Step 1: Finding construct folders...")
    construct_folders = find_construct_folders(VVAULT_ROOT)
    logger.info(f"   Found {len(construct_folders)} construct folders: {construct_folders}")
    logger.info("")
    
    if not construct_folders:
        logger.warning("⚠️  No construct folders found to migrate")
        logger.info("   This might be correct if migration already completed")
    
    # Step 2: Create user directory structure
    logger.info("📁 Step 2: Creating user directory structure...")
    dirs = create_user_directory_structure(DEVON_USER_ID, VVAULT_ROOT)
    logger.info("")
    
    # Step 3: Migrate constructs
    if construct_folders:
        logger.info("📦 Step 3: Migrating construct folders...")
        migrated, failed = migrate_constructs(construct_folders, dirs['constructs_dir'], VVAULT_ROOT)
        logger.info(f"   ✅ Migrated: {len(migrated)}")
        if failed:
            logger.warning(f"   ❌ Failed: {len(failed)}")
        logger.info("")
    else:
        migrated = []
        failed = []
    
    # Step 4: Migrate capsules
    logger.info("📦 Step 4: Migrating capsules...")
    capsules_migrated = migrate_capsules(dirs['capsules_dir'], VVAULT_ROOT)
    logger.info("")
    
    # Step 5: Create user identity
    logger.info("🔐 Step 5: Creating user identity...")
    create_user_identity(DEVON_USER_ID, dirs['identity_dir'])
    logger.info("")
    
    # Step 6: Create user registry
    logger.info("📋 Step 6: Creating user registry...")
    create_user_registry(DEVON_USER_ID, DEVON_EMAIL, DEVON_NAME, migrated, VVAULT_ROOT)
    logger.info("")
    
    # Summary
    logger.info("=" * 60)
    logger.info("✅ Migration complete!")
    logger.info("=" * 60)
    logger.info(f"📊 User directory: {dirs['user_dir']}")
    logger.info(f"📋 Constructs migrated: {len(migrated)}")
    if migrated:
        logger.info(f"   Constructs: {', '.join(migrated)}")
    if failed:
        logger.warning(f"⚠️  Failed migrations: {', '.join(failed)}")
    logger.info(f"📦 Capsules migrated: {'Yes' if capsules_migrated else 'No'}")
    logger.info("")
    logger.info("📝 Next steps:")
    logger.info("   1. Verify all constructs are accessible in new location")
    logger.info("   2. Update API endpoints to use user-aware paths")
    logger.info("   3. Update Chatty integration to send user context")
    logger.info("   4. Test with multiple users")

if __name__ == "__main__":
    try:
        migrate_to_multiuser()
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        exit(1)

