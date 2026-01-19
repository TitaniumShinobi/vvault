#!/usr/bin/env python3
"""
VVAULT Duplicate Cleanup Script v2

Removes ALL duplicate entries from the vault_files table in Supabase,
using original_path as the canonical unique key (not filename alone).

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

def get_original_path(file_record: dict) -> str:
    """Extract original_path from metadata - this is the canonical key"""
    metadata = file_record.get('metadata')
    if metadata:
        try:
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            return metadata.get('original_path', '')
        except:
            pass
    return file_record.get('filename', '')

def cleanup_duplicates(supabase: Client, dry_run: bool = False):
    """Remove duplicate files using original_path as the unique key"""
    print("=" * 70)
    print("VVAULT Duplicate Cleanup v2")
    print("=" * 70)
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE CLEANUP'}")
    
    print("\n[1/3] Fetching all vault files...")
    result = supabase.table('vault_files').select('*').execute()
    files = result.data or []
    print(f"  Found {len(files)} total records")
    
    print("\n[2/3] Identifying duplicates by original_path...")
    
    path_groups = defaultdict(list)
    for f in files:
        original_path = get_original_path(f)
        path_groups[original_path].append(f)
    
    duplicates_to_remove = []
    unique_count = 0
    
    for path, group in path_groups.items():
        if len(group) > 1:
            sorted_group = sorted(
                group, 
                key=lambda x: x.get('created_at') or '', 
                reverse=True
            )
            keep = sorted_group[0]
            remove = sorted_group[1:]
            
            for dup in remove:
                duplicates_to_remove.append({
                    'id': dup['id'],
                    'filename': dup.get('filename'),
                    'path': path,
                    'created_at': dup.get('created_at')
                })
            unique_count += 1
        else:
            unique_count += 1
    
    print(f"  Unique files: {unique_count}")
    print(f"  Duplicates to remove: {len(duplicates_to_remove)}")
    
    if not duplicates_to_remove:
        print("\n  No duplicates found!")
        return
    
    print(f"\n  Duplicate breakdown:")
    dup_by_file = defaultdict(int)
    for d in duplicates_to_remove:
        dup_by_file[d['filename']] += 1
    
    for filename, count in sorted(dup_by_file.items(), key=lambda x: -x[1])[:20]:
        print(f"    {filename}: {count} extra copies")
    if len(dup_by_file) > 20:
        print(f"    ... and {len(dup_by_file) - 20} more files with duplicates")
    
    if dry_run:
        print("\n[DRY RUN] No changes made.")
        print(f"Would remove {len(duplicates_to_remove)} duplicate records.")
        return
    
    print(f"\n[3/3] Removing {len(duplicates_to_remove)} duplicates...")
    
    removed = 0
    errors = 0
    
    for i, dup in enumerate(duplicates_to_remove):
        try:
            supabase.table('vault_files').delete().eq('id', dup['id']).execute()
            removed += 1
            if (i + 1) % 10 == 0 or i == len(duplicates_to_remove) - 1:
                print(f"  Progress: {i + 1}/{len(duplicates_to_remove)} ({removed} removed)")
        except Exception as e:
            errors += 1
            print(f"  Error removing {dup['id']}: {e}")
    
    print("\n" + "=" * 70)
    print("CLEANUP COMPLETE!")
    print(f"  Removed: {removed}")
    print(f"  Errors: {errors}")
    print(f"  Remaining unique files: {unique_count}")
    print("=" * 70)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Clean up duplicate vault files')
    parser.add_argument('--dry-run', action='store_true', help='Preview without deleting')
    
    args = parser.parse_args()
    
    supabase = get_supabase_client()
    cleanup_duplicates(supabase, args.dry_run)

if __name__ == "__main__":
    main()
