#!/usr/bin/env python3
"""
VVAULT Duplicate Cleanup Script

Removes duplicate entries from the vault_files table in Supabase,
keeping only the most recent version of each file.

Usage:
    python scripts/cleanup_duplicates.py [--dry-run]
"""

import os
import sys
import json
from collections import defaultdict

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def find_duplicates(supabase: Client) -> dict:
    """Find all duplicate files based on filename + user_id + original_path"""
    print("Fetching all vault files...")
    
    result = supabase.table('vault_files').select('*').execute()
    files = result.data or []
    
    print(f"Found {len(files)} total files")
    
    duplicates = defaultdict(list)
    
    for file in files:
        metadata = {}
        if file.get('metadata'):
            try:
                metadata = json.loads(file['metadata']) if isinstance(file['metadata'], str) else file['metadata']
            except:
                pass
        
        original_path = metadata.get('original_path', file.get('filename', ''))
        key = (file.get('user_id'), file.get('filename'), original_path)
        duplicates[key].append(file)
    
    duplicate_groups = {k: v for k, v in duplicates.items() if len(v) > 1}
    
    return duplicate_groups

def cleanup_duplicates(supabase: Client, dry_run: bool = False):
    """Remove duplicate files, keeping the most recent version"""
    print("=" * 60)
    print("VVAULT Duplicate Cleanup")
    print("=" * 60)
    print(f"Dry run: {dry_run}")
    
    duplicate_groups = find_duplicates(supabase)
    
    if not duplicate_groups:
        print("\nNo duplicates found!")
        return
    
    print(f"\nFound {len(duplicate_groups)} files with duplicates")
    
    total_to_delete = 0
    ids_to_delete = []
    
    for key, files in duplicate_groups.items():
        user_id, filename, original_path = key
        
        sorted_files = sorted(
            files,
            key=lambda f: f.get('created_at', '') or '',
            reverse=True
        )
        
        keep = sorted_files[0]
        remove = sorted_files[1:]
        
        print(f"\n  {filename}")
        print(f"    Original path: {original_path}")
        print(f"    Keeping: ID {keep['id']} (created {keep.get('created_at', 'unknown')})")
        
        for f in remove:
            print(f"    Removing: ID {f['id']} (created {f.get('created_at', 'unknown')})")
            ids_to_delete.append(f['id'])
            total_to_delete += 1
    
    print(f"\n{'=' * 60}")
    print(f"Total duplicates to remove: {total_to_delete}")
    
    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return
    
    if not ids_to_delete:
        print("\nNothing to delete.")
        return
    
    print("\nDeleting duplicates...")
    
    deleted = 0
    for file_id in ids_to_delete:
        try:
            supabase.table('vault_files').delete().eq('id', file_id).execute()
            deleted += 1
            print(f"  Deleted ID: {file_id}")
        except Exception as e:
            print(f"  Failed to delete ID {file_id}: {e}")
    
    print(f"\n{'=' * 60}")
    print(f"Cleanup complete! Deleted {deleted}/{total_to_delete} duplicates")
    print("=" * 60)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Clean up duplicate vault files')
    parser.add_argument('--dry-run', action='store_true', help='Preview without deleting')
    
    args = parser.parse_args()
    
    supabase = get_supabase_client()
    cleanup_duplicates(supabase, args.dry_run)

if __name__ == "__main__":
    main()
