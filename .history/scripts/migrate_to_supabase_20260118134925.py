#!/usr/bin/env python3
"""
VVAULT Migration Script - Migrate local users/ directory to Supabase

Usage:
    python scripts/migrate_to_supabase.py --source /Users/devonwoodson/Documents/GitHub/vvault/users/

This script will:
1. Scan the users/ directory for user data files
2. Upload user records to the Supabase 'users' table
3. Upload vault files (markdown, json) to Supabase storage and database
4. Generate SHA256 checksums for file integrity
"""

import os
import sys
import json
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

def get_supabase_client() -> Client:
    """Initialize Supabase client"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def compute_sha256(content: str) -> str:
    """Compute SHA256 hash of content"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def scan_users_directory(source_path: Path) -> dict:
    """Scan users directory and categorize files"""
    results = {
        'users': [],
        'vault_files': [],
        'other_files': []
    }
    
    if not source_path.exists():
        print(f"Error: Source path does not exist: {source_path}")
        return results
    
    for item in source_path.iterdir():
        if item.is_dir():
            user_email = item.name
            user_data = {
                'email': user_email,
                'name': user_email.split('@')[0],
                'files': []
            }
            
            for file in item.rglob('*'):
                if file.is_file():
                    user_data['files'].append({
                        'path': str(file),
                        'relative_path': str(file.relative_to(item)),
                        'filename': file.name,
                        'extension': file.suffix
                    })
            
            results['users'].append(user_data)
        elif item.is_file():
            if item.suffix in ['.json', '.md', '.yaml', '.yml']:
                results['vault_files'].append({
                    'path': str(item),
                    'filename': item.name,
                    'extension': item.suffix
                })
            else:
                results['other_files'].append(str(item))
    
    return results

def migrate_user(supabase: Client, user_data: dict) -> str:
    """Migrate a single user to Supabase"""
    try:
        result = supabase.table('users').upsert({
            'email': user_data['email'],
            'name': user_data['name'],
            'created_at': datetime.now().isoformat()
        }, on_conflict='email').execute()
        
        if result.data:
            print(f"  ✓ User migrated: {user_data['email']}")
            return result.data[0]['id']
        return None
    except Exception as e:
        print(f"  ✗ Failed to migrate user {user_data['email']}: {e}")
        return None

def migrate_vault_file(supabase: Client, user_id: str, file_info: dict) -> bool:
    """Migrate a vault file to Supabase"""
    try:
        file_path = Path(file_info['path'])
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        sha256 = compute_sha256(content)
        
        construct_id = None
        filename = file_info['filename'].lower()
        if 'zen' in filename:
            construct_id = 'zen'
        elif 'nova' in filename:
            construct_id = 'nova'
        elif 'echo' in filename:
            construct_id = 'echo'
        
        result = supabase.table('vault_files').insert({
            'user_id': user_id,
            'construct_id': construct_id,
            'filename': file_info['filename'],
            'content': content,
            'sha256': sha256,
            'metadata': json.dumps({
                'original_path': file_info.get('relative_path', file_info['path']),
                'extension': file_info['extension'],
                'migrated_at': datetime.now().isoformat()
            })
        }).execute()
        
        if result.data:
            print(f"    ✓ File migrated: {file_info['filename']}")
            return True
        return False
    except Exception as e:
        print(f"    ✗ Failed to migrate file {file_info['filename']}: {e}")
        return False

def run_migration(source_path: str, dry_run: bool = False):
    """Run the full migration"""
    print("=" * 60)
    print("VVAULT Migration to Supabase")
    print("=" * 60)
    
    source = Path(source_path)
    print(f"\nSource: {source}")
    print(f"Dry run: {dry_run}")
    
    print("\n[1/3] Scanning directory...")
    scan_results = scan_users_directory(source)
    
    print(f"\nFound:")
    print(f"  - {len(scan_results['users'])} users")
    total_files = sum(len(u['files']) for u in scan_results['users'])
    print(f"  - {total_files} user files")
    print(f"  - {len(scan_results['vault_files'])} root vault files")
    
    if dry_run:
        print("\n[DRY RUN] Would migrate the following:")
        for user in scan_results['users']:
            print(f"  User: {user['email']} ({len(user['files'])} files)")
        return
    
    print("\n[2/3] Connecting to Supabase...")
    supabase = get_supabase_client()
    print("  ✓ Connected")
    
    print("\n[3/3] Migrating data...")
    
    migrated_users = 0
    migrated_files = 0
    
    for user_data in scan_results['users']:
        print(f"\nMigrating user: {user_data['email']}")
        user_id = migrate_user(supabase, user_data)
        
        if user_id:
            migrated_users += 1
            
            for file_info in user_data['files']:
                if file_info['extension'] in ['.md', '.json', '.yaml', '.yml', '.txt']:
                    if migrate_vault_file(supabase, user_id, file_info):
                        migrated_files += 1
    
    print("\n" + "=" * 60)
    print("Migration Complete!")
    print(f"  Users migrated: {migrated_users}/{len(scan_results['users'])}")
    print(f"  Files migrated: {migrated_files}")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description='Migrate VVAULT users to Supabase')
    parser.add_argument('--source', '-s', required=True, help='Path to users/ directory')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Preview without migrating')
    
    args = parser.parse_args()
    run_migration(args.source, args.dry_run)

if __name__ == '__main__':
    main()