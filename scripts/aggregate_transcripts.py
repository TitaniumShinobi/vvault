#!/usr/bin/env python3
"""
VVAULT Transcript Aggregator

Finds all conversation files for constructs and aggregates them
into the canonical chat_with_<construct>.md transcript file.

Usage:
    python scripts/aggregate_transcripts.py [--dry-run] [--construct <id>]
"""

import os
import sys
import json
import re
from datetime import datetime

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

CONVERSATION_PATTERNS = [
    r'-K\d+\.md$',
    r'_\d{2}-\d{2}-\d{4}.*\.md$',
    r'test_\d.*\.md$',
]

EXCLUDE_FILES = [
    'CONTINUITY_GPT_PROMPT.md',
    'README.md',
]

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def is_conversation_file(filename: str) -> bool:
    if filename in EXCLUDE_FILES:
        return False
    if filename.startswith('chat_with_'):
        return False
    for pattern in CONVERSATION_PATTERNS:
        if re.search(pattern, filename):
            return True
    return False

def get_construct_id_from_path(original_path: str) -> str:
    match = re.search(r'instances/([^/]+)', original_path)
    if match:
        return match.group(1)
    return None

def aggregate_transcripts(supabase: Client, dry_run: bool = False, target_construct: str = None):
    print("=" * 70)
    print("VVAULT Transcript Aggregator")
    print("=" * 70)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'}")
    if target_construct:
        print(f"Target: {target_construct}")
    
    print("\n[1/4] Fetching all vault files...")
    result = supabase.table('vault_files').select('*').execute()
    files = result.data or []
    print(f"  Found {len(files)} total files")
    
    print("\n[2/4] Organizing files by construct...")
    constructs = {}
    
    for f in files:
        metadata = f.get('metadata', {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        
        original_path = metadata.get('original_path', '')
        construct_id = get_construct_id_from_path(original_path)
        
        if not construct_id:
            continue
        
        if target_construct and construct_id != target_construct:
            continue
        
        if construct_id not in constructs:
            constructs[construct_id] = {
                'canonical_transcript': None,
                'conversation_files': [],
                'user_id': f.get('user_id')
            }
        
        filename = f.get('filename', '')
        
        if filename.startswith('chat_with_') and filename.endswith('.md'):
            constructs[construct_id]['canonical_transcript'] = f
        elif 'chatty' in original_path.lower() and is_conversation_file(filename):
            constructs[construct_id]['conversation_files'].append(f)
        elif is_conversation_file(filename) and f.get('content'):
            constructs[construct_id]['conversation_files'].append(f)
    
    print(f"  Found {len(constructs)} constructs with chatty folders")
    
    print("\n[3/4] Analyzing transcript status...")
    needs_update = []
    
    for construct_id, data in constructs.items():
        canonical = data['canonical_transcript']
        convos = data['conversation_files']
        
        if not convos:
            continue
        
        current_content = canonical.get('content', '') if canonical else ''
        is_placeholder = 'placeholder' in current_content.lower() or len(current_content) < 500
        
        if is_placeholder and convos:
            total_chars = sum(len(c.get('content', '') or '') for c in convos)
            needs_update.append({
                'construct_id': construct_id,
                'canonical': canonical,
                'conversation_files': convos,
                'user_id': data['user_id'],
                'total_chars': total_chars
            })
            print(f"  {construct_id}: {len(convos)} conversations ({total_chars:,} chars total)")
    
    if not needs_update:
        print("\n  All transcripts are up to date!")
        return
    
    print(f"\n  {len(needs_update)} constructs need transcript updates")
    
    print("\n[4/4] Aggregating transcripts...")
    
    for item in needs_update:
        construct_id = item['construct_id']
        convos = item['conversation_files']
        canonical = item['canonical']
        user_id = item['user_id']
        
        print(f"\n  Processing {construct_id}...")
        
        sorted_convos = sorted(convos, key=lambda x: x.get('filename', ''))
        
        aggregated = f"# Chat Transcript: {construct_id}\n\n"
        aggregated += f"*Aggregated from {len(convos)} conversation files on {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n"
        aggregated += "---\n\n"
        
        for conv in sorted_convos:
            filename = conv.get('filename', 'Unknown')
            content = conv.get('content', '')
            
            if not content:
                continue
            
            title = filename.replace('.md', '').replace('-', ' ').replace('_', ' ')
            title = re.sub(r'-K\d+$', '', title)
            
            aggregated += f"## {title}\n\n"
            aggregated += content.strip() + "\n\n"
            aggregated += "---\n\n"
        
        if dry_run:
            print(f"    Would update transcript with {len(aggregated):,} chars")
            continue
        
        try:
            metadata = {
                'original_path': f"instances/{construct_id}/chatty/chat_with_{construct_id}.md",
                'aggregated_from': [c.get('filename') for c in convos],
                'aggregated_at': datetime.now().isoformat()
            }
            
            if canonical:
                supabase.table('vault_files').update({
                    'content': aggregated,
                    'metadata': json.dumps(metadata)
                }).eq('id', canonical['id']).execute()
                print(f"    Updated existing transcript ({len(aggregated):,} chars)")
            else:
                supabase.table('vault_files').insert({
                    'user_id': user_id,
                    'filename': f'chat_with_{construct_id}.md',
                    'content': aggregated,
                    'metadata': json.dumps(metadata),
                    'file_type': 'text/markdown'
                }).execute()
                print(f"    Created new transcript ({len(aggregated):,} chars)")
                
        except Exception as e:
            print(f"    Error: {e}")
    
    print("\n" + "=" * 70)
    print("AGGREGATION COMPLETE!")
    print("=" * 70)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Aggregate conversation files into transcripts')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--construct', type=str, help='Process only this construct ID')
    
    args = parser.parse_args()
    
    supabase = get_supabase_client()
    aggregate_transcripts(supabase, args.dry_run, args.construct)

if __name__ == "__main__":
    main()
