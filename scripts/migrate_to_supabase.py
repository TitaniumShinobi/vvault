#!/usr/bin/env python3
"""
VVAULT Migration Script - Migrate local users/ directory to Supabase

Usage:
    python scripts/migrate_to_supabase.py --source /path/to/users/

This script will:
1. Scan the users/ directory for all files (text + binary)
2. Upload user records to the Supabase 'users' table
3. Upload text files (markdown, json) to Supabase database
4. Upload binary files (PDFs, images, videos) to Supabase Storage
5. Generate SHA256 checksums for file integrity
"""

import os
import sys
import json
import hashlib
import argparse
import mimetypes
import base64
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

TEXT_EXTENSIONS = {
    '.md', '.json', '.yaml', '.yml', '.txt', '.csv', '.xml', '.html',
    '.py', '.js', '.ts', '.jsx', '.tsx', '.sh', '.bash', '.zsh',
    '.rb', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.hpp',
    '.css', '.scss', '.sass', '.less',
    '.sql', '.graphql', '.gql',
    '.lua', '.r', '.swift', '.kt', '.scala',
    '.php', '.pl', '.pm', '.ps1', '.bat', '.cmd',
    '.toml', '.ini', '.cfg', '.conf', '.env',
    '.vue', '.svelte', '.astro',
    '.dockerfile', '.makefile', '.cmake',
    '.gitignore', '.gitattributes', '.editorconfig'
}
BINARY_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.tiff',
    '.mp4', '.mp3', '.wav', '.ogg', '.webm', '.mov', '.avi', '.mkv', '.flac', '.m4a',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2',
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.exe', '.dll', '.so', '.dylib', '.bin', '.dat',
    '.sqlite', '.db', '.mdb'
}

STORAGE_BUCKET = 'vault-files'

def get_supabase_client() -> Client:
    """Initialize Supabase client"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def compute_sha256(content: bytes) -> str:
    """Compute SHA256 hash of content"""
    return hashlib.sha256(content).hexdigest()

def format_duration(seconds: Optional[float]) -> str:
    """Format seconds as human-readable H/M/S"""
    if seconds is None:
        return "calculating..."

    secs = int(max(0, seconds))
    hours, remainder = divmod(secs, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    return " ".join(parts)

def describe_progress(bytes_migrated: int, total_bytes: int, start_time: float) -> Tuple[str, float]:
    """Return ETA string and completion percent for the migration"""
    elapsed = time.monotonic() - start_time
    percent = 0.0
    if total_bytes > 0:
        percent = min((bytes_migrated / total_bytes) * 100, 100.0)
    elif bytes_migrated > 0:
        percent = 100.0

    remaining = max(total_bytes - bytes_migrated, 0)
    eta_seconds = None

    if remaining == 0:
        eta_seconds = 0
    elif bytes_migrated > 0 and elapsed > 0:
        rate = bytes_migrated / elapsed
        if rate > 0:
            eta_seconds = remaining / rate

    return format_duration(eta_seconds), percent

def get_content_type(filepath: Path) -> str:
    """Get MIME type for a file"""
    mime_type, _ = mimetypes.guess_type(str(filepath))
    return mime_type or 'application/octet-stream'

def ensure_storage_bucket(supabase: Client):
    """Create storage bucket if it doesn't exist"""
    try:
        buckets = supabase.storage.list_buckets()
        bucket_names = [b.name for b in buckets]
        
        if STORAGE_BUCKET not in bucket_names:
            supabase.storage.create_bucket(
                STORAGE_BUCKET,
                options={
                    "public": False,
                    "file_size_limit": 52428800  # 50 MB
                }
            )
            print(f"  ✓ Created storage bucket: {STORAGE_BUCKET}")
        else:
            print(f"  ✓ Storage bucket exists: {STORAGE_BUCKET}")
    except Exception as e:
        print(f"  ⚠ Bucket check/create warning: {e}")

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
                    ext = file.suffix.lower()
                    file_info = {
                        'path': str(file),
                        'relative_path': str(file.relative_to(item)),
                        'filename': file.name,
                        'extension': ext,
                        'size': file.stat().st_size,
                        'is_text': ext in TEXT_EXTENSIONS,
                        'is_binary': ext in BINARY_EXTENSIONS or ext not in TEXT_EXTENSIONS
                    }
                    user_data['files'].append(file_info)
            
            results['users'].append(user_data)
        elif item.is_file():
            ext = item.suffix.lower()
            if ext in TEXT_EXTENSIONS or ext in BINARY_EXTENSIONS:
                results['vault_files'].append({
                    'path': str(item),
                    'filename': item.name,
                    'extension': ext
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

def migrate_text_file(supabase: Client, user_id: str, file_info: dict) -> Tuple[bool, str]:
    """Migrate a text file to Supabase database"""
    try:
        file_path = Path(file_info['path'])
        original_path = file_info.get('relative_path', file_info['path'])
        
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        sha256 = compute_sha256(content.encode('utf-8'))
        
        existing = supabase.table('vault_files').select('id, sha256').eq('user_id', user_id).eq('filename', file_info['filename']).execute()
        
        if existing.data and existing.data[0].get('sha256') == sha256:
            return True, f"SKIP (unchanged): {file_info['filename']}"
        
        construct_id = None
        filename = file_info['filename'].lower()
        rel_path = file_info.get('relative_path', '').lower()
        if 'zen' in filename or '/zen' in rel_path:
            construct_id = 'zen'
        elif 'nova' in filename or '/nova' in rel_path:
            construct_id = 'nova'
        elif 'echo' in filename or '/echo' in rel_path:
            construct_id = 'echo'
        elif 'katana' in filename or '/katana' in rel_path:
            construct_id = 'katana'
        elif 'lin' in filename or '/lin' in rel_path:
            construct_id = 'lin'
        
        file_data = {
            'user_id': user_id,
            'construct_id': construct_id,
            'filename': file_info['filename'],
            'content': content,
            'sha256': sha256,
            'file_type': 'text',
            'metadata': json.dumps({
                'original_path': original_path,
                'extension': file_info['extension'],
                'size': file_info.get('size', len(content)),
                'migrated_at': datetime.now().isoformat()
            })
        }
        
        if existing.data:
            result = supabase.table('vault_files').update(file_data).eq('id', existing.data[0]['id']).execute()
            message = f"UPDATE: {file_info['filename']}"
        else:
            result = supabase.table('vault_files').insert(file_data).execute()
            message = f"NEW: {file_info['filename']}"
        
        if result.data:
            return True, message
        return False, f"No response for {message}"
    except Exception as e:
        return False, f"{file_info['filename']} - {e}"

def migrate_binary_file(supabase: Client, user_id: str, file_info: dict) -> Tuple[bool, str]:
    """Migrate a binary file to Supabase Storage"""
    try:
        file_path = Path(file_info['path'])
        
        with open(file_path, 'rb') as f:
            content = f.read()
        
        sha256 = compute_sha256(content)
        
        existing = supabase.table('vault_files').select('id, sha256').eq('user_id', user_id).eq('filename', file_info['filename']).execute()
        
        if existing.data and existing.data[0].get('sha256') == sha256:
            return True, f"SKIP (unchanged): {file_info['filename']}"
        
        content_type = get_content_type(file_path)
        storage_path = f"{user_id}/{file_info.get('relative_path', file_info['filename'])}"
        
        try:
            supabase.storage.from_(STORAGE_BUCKET).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": content_type}
            )
        except Exception as upload_error:
            if "Duplicate" in str(upload_error) or "already exists" in str(upload_error).lower():
                supabase.storage.from_(STORAGE_BUCKET).update(
                    path=storage_path,
                    file=content,
                    file_options={"content-type": content_type}
                )
            else:
                raise upload_error
        
        construct_id = None
        filename = file_info['filename'].lower()
        rel_path = file_info.get('relative_path', '').lower()
        if 'zen' in filename or '/zen' in rel_path:
            construct_id = 'zen'
        elif 'nova' in filename or '/nova' in rel_path:
            construct_id = 'nova'
        elif 'echo' in filename or '/echo' in rel_path:
            construct_id = 'echo'
        elif 'katana' in filename or '/katana' in rel_path:
            construct_id = 'katana'
        elif 'lin' in filename or '/lin' in rel_path:
            construct_id = 'lin'
        
        file_data = {
            'user_id': user_id,
            'construct_id': construct_id,
            'filename': file_info['filename'],
            'content': None,
            'sha256': sha256,
            'file_type': 'binary',
            'storage_path': storage_path,
            'metadata': json.dumps({
                'original_path': file_info.get('relative_path', file_info['path']),
                'extension': file_info['extension'],
                'size': file_info.get('size', len(content)),
                'content_type': content_type,
                'storage_bucket': STORAGE_BUCKET,
                'migrated_at': datetime.now().isoformat()
            })
        }
        
        if existing.data:
            result = supabase.table('vault_files').update(file_data).eq('id', existing.data[0]['id']).execute()
            message = f"UPDATE: {file_info['filename']} ({content_type})"
        else:
            result = supabase.table('vault_files').insert(file_data).execute()
            message = f"NEW: {file_info['filename']} ({content_type})"

        if result.data:
            return True, message
        return False, f"No response for {message}"
    except Exception as e:
        return False, f"{file_info['filename']} - {e}"

def run_migration(source_path: str, dry_run: bool = False):
    """Run the full migration"""
    print("=" * 60)
    print("VVAULT Migration to Supabase (Full Media Support)")
    print("=" * 60)
    
    source = Path(source_path)
    print(f"\nSource: {source}")
    print(f"Dry run: {dry_run}")
    
    print("\n[1/4] Scanning directory...")
    scan_results = scan_users_directory(source)
    
    text_files = 0
    binary_files = 0
    for user in scan_results['users']:
        for f in user['files']:
            if f['is_text']:
                text_files += 1
            else:
                binary_files += 1

    total_bytes = sum(f.get('size', 0) for user in scan_results['users'] for f in user['files'])
    
    print(f"\nFound:")
    print(f"  - {len(scan_results['users'])} users")
    print(f"  - {text_files} text files (.md, .json, .txt, etc)")
    print(f"  - {binary_files} binary files (.pdf, images, videos, etc)")
    print(f"  - {len(scan_results['vault_files'])} root vault files")
    
    if dry_run:
        print("\n[DRY RUN] Would migrate:")
        for user in scan_results['users']:
            print(f"\n  User: {user['email']}")
            for f in user['files'][:10]:
                file_type = "TEXT" if f['is_text'] else "BINARY"
                print(f"    [{file_type}] {f['relative_path']}")
            if len(user['files']) > 10:
                print(f"    ... and {len(user['files']) - 10} more files")
        return
    
    print("\n[2/4] Connecting to Supabase...")
    supabase = get_supabase_client()
    print("  ✓ Connected")
    
    print("\n[3/4] Ensuring storage bucket exists...")
    ensure_storage_bucket(supabase)
    
    print("\n[4/4] Migrating data...")
    
    migrated_users = 0
    migrated_text = 0
    migrated_binary = 0
    bytes_migrated = 0
    start_time = time.monotonic()
    
    for user_data in scan_results['users']:
        print(f"\nMigrating user: {user_data['email']}")
        user_id = migrate_user(supabase, user_data)
        
        if user_id:
            migrated_users += 1

            for file_info in user_data['files']:
                file_size = file_info.get('size', 0)
                is_text_file = file_info['is_text'] and file_info['extension'] in TEXT_EXTENSIONS
                if is_text_file:
                    success, message = migrate_text_file(supabase, user_id, file_info)
                else:
                    success, message = migrate_binary_file(supabase, user_id, file_info)

                if success:
                    bytes_migrated += file_size
                    eta_str, percent = describe_progress(bytes_migrated, total_bytes, start_time)
                    if is_text_file:
                        migrated_text += 1
                    else:
                        migrated_binary += 1
                    print(f"    ✓ {message} ({percent:.1f}% done, ETA {eta_str})")
                else:
                    print(f"    ✗ {message}")
    
    print("\n" + "=" * 60)
    print("Migration Complete!")
    print(f"  Users migrated: {migrated_users}/{len(scan_results['users'])}")
    print(f"  Text files: {migrated_text}")
    print(f"  Binary files: {migrated_binary}")
    print(f"  Total files: {migrated_text + migrated_binary}")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description='Migrate VVAULT users to Supabase')
    parser.add_argument('--source', required=True, help='Path to users/ directory')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    
    args = parser.parse_args()
    run_migration(args.source, args.dry_run)

if __name__ == "__main__":
    main()