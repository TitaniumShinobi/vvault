#!/usr/bin/env python3
"""
VVAULT Migration Script v2 - Smart Sync to Supabase

Workflow:
1. Scan Supabase for existing files (database + storage)
2. Scan local directory for files
3. Calculate diff and estimate time BEFORE starting
4. Remove duplicates from Supabase
5. Sync new/changed files
6. Verify all changes
7. Generate summary report

Usage:
    python scripts/migrate_to_supabase.py --source /path/to/users/
"""

import os
import sys
import json
import hashlib
import argparse
import mimetypes
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict, List, Set
from collections import defaultdict

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

TEXT_EXTENSIONS = {
    '.md', '.json', '.yaml', '.yml', '.txt', '.csv', '.xml', '.html', '.py',
    '.js', '.ts', '.jsx', '.tsx', '.sh', '.bash', '.zsh', '.rb', '.go', '.rs',
    '.java', '.c', '.cpp', '.h', '.hpp', '.css', '.scss', '.sass', '.less',
    '.sql', '.graphql', '.gql', '.lua', '.r', '.swift', '.kt', '.scala',
    '.php', '.pl', '.pm', '.ps1', '.bat', '.cmd', '.toml', '.ini', '.cfg',
    '.conf', '.env', '.vue', '.svelte', '.astro', '.dockerfile', '.makefile',
    '.cmake', '.gitignore', '.gitattributes', '.editorconfig'
}
BINARY_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp',
    '.tiff', '.mp4', '.mp3', '.wav', '.ogg', '.webm', '.mov', '.avi', '.mkv',
    '.flac', '.m4a', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip',
    '.tar', '.gz', '.rar', '.7z', '.bz2', '.ttf', '.otf', '.woff', '.woff2',
    '.eot', '.exe', '.dll', '.so', '.dylib', '.bin', '.dat', '.sqlite', '.db',
    '.mdb', '.capsule'
}

STORAGE_BUCKET = 'vault-files'
AVG_UPLOAD_SPEED_BYTES_PER_SEC = 500_000  # ~500 KB/s estimate


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins, secs = divmod(int(seconds), 60)
        return f"{mins}m {secs}s"
    else:
        hours, remainder = divmod(int(seconds), 3600)
        mins, secs = divmod(remainder, 60)
        return f"{hours}h {mins}m"


def format_size(bytes_size: int) -> str:
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


def get_content_type(filepath: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(filepath))
    return mime_type or 'application/octet-stream'


def get_construct_id(filename: str, rel_path: str) -> Optional[str]:
    filename_lower = filename.lower()
    path_lower = rel_path.lower()
    for construct in ['zen', 'nova', 'echo', 'katana', 'lin', 'aurora']:
        if construct in filename_lower or f'/{construct}' in path_lower or f'{construct}-' in path_lower:
            return construct
    return None


class MigrationEngine:

    def __init__(self, source_path: str, dry_run: bool = False):
        self.source = Path(source_path)
        self.dry_run = dry_run
        self.supabase: Optional[Client] = None

        self.local_files: Dict[str, dict] = {}
        self.remote_files: Dict[str, dict] = {}

        self.to_add: List[dict] = []
        self.to_update: List[dict] = []
        self.to_skip: List[dict] = []
        self.duplicates: List[dict] = []

        self.changes_made: List[str] = []
        self.errors: List[str] = []

    def run(self):
        print("=" * 70)
        print("VVAULT Smart Sync to Supabase")
        print("=" * 70)
        print(f"\nSource: {self.source}")
        print(
            f"Mode: {'DRY RUN (no changes)' if self.dry_run else 'LIVE SYNC'}")

        print("\n" + "─" * 70)
        print("STEP 1: Reading Supabase (existing files)")
        print("─" * 70)
        self.supabase = get_supabase_client()
        self._fetch_remote_state()

        print("\n" + "─" * 70)
        print("STEP 2: Scanning local directory")
        print("─" * 70)
        self._scan_local_files()

        print("\n" + "─" * 70)
        print("STEP 3: Calculating sync plan")
        print("─" * 70)
        self._calculate_diff()
        self._show_sync_plan()

        if self.dry_run:
            print("\n[DRY RUN] No changes will be made.")
            return

        print("\n" + "─" * 70)
        print("STEP 4: Removing duplicates")
        print("─" * 70)
        self._remove_duplicates()

        print("\n" + "─" * 70)
        print("STEP 5: Syncing files to Supabase")
        print("─" * 70)
        self._sync_files()

        print("\n" + "─" * 70)
        print("STEP 6: Verification")
        print("─" * 70)
        self._verify_sync()

        print("\n" + "─" * 70)
        print("STEP 7: Summary Report")
        print("─" * 70)
        self._show_report()

    def _fetch_remote_state(self):
        print("  Fetching vault_files table...")
        result = self.supabase.table('vault_files').select('*').execute()
        files = result.data or []

        file_groups = defaultdict(list)
        for f in files:
            metadata = {}
            if f.get('metadata'):
                try:
                    metadata = json.loads(f['metadata']) if isinstance(
                        f['metadata'], str) else f['metadata']
                except:
                    pass

            original_path = metadata.get('original_path',
                                         f.get('filename', ''))
            key = original_path
            file_groups[key].append(f)

        for key, group in file_groups.items():
            if len(group) > 1:
                sorted_group = sorted(group,
                                      key=lambda x: x.get('created_at', ''),
                                      reverse=True)
                self.remote_files[key] = sorted_group[0]
                for dup in sorted_group[1:]:
                    self.duplicates.append(dup)
            else:
                self.remote_files[key] = group[0]

        print(f"  ✓ Found {len(files)} total records in database")
        print(f"  ✓ Unique files: {len(self.remote_files)}")
        print(f"  ✓ Duplicates to remove: {len(self.duplicates)}")

    def _scan_local_files(self):
        if not self.source.exists():
            print(f"  ✗ Source path does not exist: {self.source}")
            return

        total_size = 0
        text_count = 0
        binary_count = 0

        for user_dir in self.source.iterdir():
            if not user_dir.is_dir():
                continue

            user_email = user_dir.name

            for file_path in user_dir.rglob('*'):
                if not file_path.is_file():
                    continue

                ext = file_path.suffix.lower()
                rel_path = str(file_path.relative_to(user_dir))
                size = file_path.stat().st_size
                is_text = ext in TEXT_EXTENSIONS

                key = rel_path
                self.local_files[key] = {
                    'path': str(file_path),
                    'relative_path': rel_path,
                    'filename': file_path.name,
                    'extension': ext,
                    'size': size,
                    'is_text': is_text,
                    'user_email': user_email
                }

                total_size += size
                if is_text:
                    text_count += 1
                else:
                    binary_count += 1

        print(f"  ✓ Found {len(self.local_files)} local files")
        print(f"    - Text files: {text_count}")
        print(f"    - Binary files: {binary_count}")
        print(f"    - Total size: {format_size(total_size)}")

    def _calculate_diff(self):
        for key, local_file in self.local_files.items():
            if key in self.remote_files:
                remote = self.remote_files[key]

                with open(local_file['path'], 'rb') as f:
                    content = f.read()
                local_sha = compute_sha256(content)

                if remote.get('sha256') == local_sha:
                    self.to_skip.append(local_file)
                else:
                    local_file['remote_id'] = remote['id']
                    self.to_update.append(local_file)
            else:
                self.to_add.append(local_file)

    def _show_sync_plan(self):
        add_size = sum(f['size'] for f in self.to_add)
        update_size = sum(f['size'] for f in self.to_update)
        upload_size = add_size + update_size

        est_seconds = upload_size / AVG_UPLOAD_SPEED_BYTES_PER_SEC if upload_size > 0 else 0
        est_seconds += len(self.duplicates) * 0.5

        print(f"\n  SYNC PLAN:")
        print(f"  ┌─────────────────────────────────────────────────────────┐")
        print(
            f"  │  Files to ADD:        {len(self.to_add):>5}  ({format_size(add_size):>10})     │"
        )
        print(
            f"  │  Files to UPDATE:     {len(self.to_update):>5}  ({format_size(update_size):>10})     │"
        )
        print(
            f"  │  Files to SKIP:       {len(self.to_skip):>5}  (unchanged)         │"
        )
        print(
            f"  │  Duplicates to REMOVE:{len(self.duplicates):>5}                       │"
        )
        print(f"  ├─────────────────────────────────────────────────────────┤")
        print(
            f"  │  Estimated time: {format_time(est_seconds):>10}                        │"
        )
        print(f"  └─────────────────────────────────────────────────────────┘")

        if self.to_add:
            print(f"\n  NEW files to add:")
            for f in self.to_add[:10]:
                print(f"    + {f['relative_path']} ({format_size(f['size'])})")
            if len(self.to_add) > 10:
                print(f"    ... and {len(self.to_add) - 10} more")

        if self.to_update:
            print(f"\n  CHANGED files to update:")
            for f in self.to_update[:10]:
                print(f"    ~ {f['relative_path']} ({format_size(f['size'])})")
            if len(self.to_update) > 10:
                print(f"    ... and {len(self.to_update) - 10} more")

    def _remove_duplicates(self):
        if not self.duplicates:
            print("  ✓ No duplicates to remove")
            return

        removed = 0
        for dup in self.duplicates:
            try:
                self.supabase.table('vault_files').delete().eq(
                    'id', dup['id']).execute()
                removed += 1
                self.changes_made.append(
                    f"REMOVED duplicate: {dup.get('filename')} (id: {dup['id']})"
                )
                print(
                    f"    ✓ Removed: {dup.get('filename')} (id: {dup['id'][:8]}...)"
                )
            except Exception as e:
                self.errors.append(f"Failed to remove {dup['id']}: {e}")
                print(f"    ✗ Failed: {dup.get('filename')} - {e}")

        print(f"  ✓ Removed {removed}/{len(self.duplicates)} duplicates")

    def _sync_files(self):
        all_files = self.to_add + self.to_update
        if not all_files:
            print("  ✓ Nothing to sync - all files are up to date!")
            return

        total = len(all_files)
        completed = 0

        for file_info in all_files:
            completed += 1
            is_new = file_info not in self.to_update

            try:
                success, msg = self._upload_file(file_info, is_new)
                if success:
                    action = "ADDED" if is_new else "UPDATED"
                    self.changes_made.append(
                        f"{action}: {file_info['relative_path']}")
                    print(f"    [{completed}/{total}] ✓ {msg}")
                else:
                    self.errors.append(msg)
                    print(f"    [{completed}/{total}] ✗ {msg}")
            except Exception as e:
                self.errors.append(f"{file_info['filename']}: {e}")
                print(
                    f"    [{completed}/{total}] ✗ {file_info['filename']}: {e}"
                )

        print(f"\n  ✓ Synced {completed - len(self.errors)}/{total} files")

    def _upload_file(self, file_info: dict, is_new: bool) -> Tuple[bool, str]:
        file_path = Path(file_info['path'])
        user_email = file_info['user_email']

        result = self.supabase.table('users').upsert(
            {
                'email': user_email,
                'name':
                user_email.split('@')[0] if '@' in user_email else user_email,
                'created_at': datetime.now().isoformat()
            },
            on_conflict='email').execute()
        user_id = result.data[0]['id'] if result.data else None

        if not user_id:
            return False, f"Could not get user_id for {user_email}"

        with open(file_path, 'rb') as f:
            content = f.read()

        sha256 = compute_sha256(content)
        construct_id = get_construct_id(file_info['filename'],
                                        file_info['relative_path'])

        if file_info['is_text']:
            text_content = content.decode('utf-8', errors='replace')
            file_data = {
                'user_id':
                user_id,
                'construct_id':
                construct_id,
                'filename':
                file_info['filename'],
                'content':
                text_content,
                'sha256':
                sha256,
                'file_type':
                'text',
                'metadata':
                json.dumps({
                    'original_path': file_info['relative_path'],
                    'extension': file_info['extension'],
                    'size': file_info['size'],
                    'migrated_at': datetime.now().isoformat()
                })
            }
        else:
            storage_path = f"{user_id}/{file_info['relative_path']}"
            content_type = get_content_type(file_path)

            try:
                self.supabase.storage.from_(STORAGE_BUCKET).upload(
                    path=storage_path,
                    file=content,
                    file_options={"content-type": content_type})
            except Exception as e:
                if "Duplicate" in str(e) or "already exists" in str(e).lower():
                    self.supabase.storage.from_(STORAGE_BUCKET).update(
                        path=storage_path,
                        file=content,
                        file_options={"content-type": content_type})
                else:
                    raise e

            file_data = {
                'user_id':
                user_id,
                'construct_id':
                construct_id,
                'filename':
                file_info['filename'],
                'content':
                None,
                'sha256':
                sha256,
                'file_type':
                'binary',
                'storage_path':
                storage_path,
                'metadata':
                json.dumps({
                    'original_path': file_info['relative_path'],
                    'extension': file_info['extension'],
                    'size': file_info['size'],
                    'content_type': content_type,
                    'storage_bucket': STORAGE_BUCKET,
                    'migrated_at': datetime.now().isoformat()
                })
            }

        if is_new:
            self.supabase.table('vault_files').insert(file_data).execute()
            return True, f"ADDED: {file_info['relative_path']}"
        else:
            self.supabase.table('vault_files').update(file_data).eq(
                'id', file_info['remote_id']).execute()
            return True, f"UPDATED: {file_info['relative_path']}"

    def _verify_sync(self):
        result = self.supabase.table('vault_files').select(
            'id, filename, sha256').execute()
        remote_count = len(result.data) if result.data else 0

        expected = len(self.local_files)

        if remote_count == expected:
            print(
                f"  ✓ Verification PASSED: {remote_count} files in Supabase match local count"
            )
        else:
            diff = remote_count - expected
            status = "extra" if diff > 0 else "missing"
            print(
                f"  ⚠ Verification WARNING: {remote_count} remote vs {expected} local ({abs(diff)} {status})"
            )

    def _show_report(self):
        print(f"\n  CHANGES MADE ({len(self.changes_made)} total):")
        if self.changes_made:
            for change in self.changes_made[:20]:
                print(f"    • {change}")
            if len(self.changes_made) > 20:
                print(f"    ... and {len(self.changes_made) - 20} more")
        else:
            print("    (no changes)")

        if self.errors:
            print(f"\n  ERRORS ({len(self.errors)}):")
            for err in self.errors[:10]:
                print(f"    ✗ {err}")
            if len(self.errors) > 10:
                print(f"    ... and {len(self.errors) - 10} more")

        print("\n" + "=" * 70)
        print("SYNC COMPLETE!")
        print(
            f"  Added: {len([c for c in self.changes_made if c.startswith('ADDED')])}"
        )
        print(
            f"  Updated: {len([c for c in self.changes_made if c.startswith('UPDATED')])}"
        )
        print(
            f"  Removed duplicates: {len([c for c in self.changes_made if c.startswith('REMOVED')])}"
        )
        print(f"  Skipped (unchanged): {len(self.to_skip)}")
        print(f"  Errors: {len(self.errors)}")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description='VVAULT Smart Sync to Supabase')
    parser.add_argument('--source',
                        required=True,
                        help='Path to users/ directory')
    parser.add_argument('--dry-run',
                        action='store_true',
                        help='Preview without making changes')

    args = parser.parse_args()

    engine = MigrationEngine(args.source, args.dry_run)
    engine.run()


if __name__ == "__main__":
    main()
