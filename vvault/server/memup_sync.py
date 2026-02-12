"""
Memup Sync — Transcript-to-Capsule Synchronization
====================================================

Reads chat transcripts from Supabase vault_files, runs ContinuityParser
to extract structured ledger entries, merges with existing memup capsule
data, and writes the result back to the construct's memup/ folder.

Usage:
    result = sync_construct_memup(supabase_client, construct_id, user_id)
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from continuity_parser import ContinuityParser

logger = logging.getLogger('vvault.memup_sync')


def _stable_entry_id(construct_id: str, filename: str, file_db_id: str = None) -> str:
    if file_db_id:
        raw = f'{construct_id}:{file_db_id}'
    else:
        raw = f'{construct_id}:{filename}'
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _fetch_transcripts(supabase, construct_id: str, user_id: str = None) -> List[Dict[str, Any]]:
    EXCLUDED_SUFFIXES = ('.capsule', '.png', '.jpg', '.jpeg', '.gif', '.pdf')

    def _build_query(folder_pattern):
        q = supabase.table('vault_files').select(
            'id, filename, content, created_at, metadata'
        ).eq('construct_id', construct_id)
        if user_id:
            q = q.eq('user_id', user_id)
        return q.ilike('filename', folder_pattern).execute()

    chatty_result = _build_query(f'%chatty/%')
    chatgpt_result = _build_query(f'%chatgpt/%')

    transcripts = []
    seen_ids = set()
    for row in (chatty_result.data or []) + (chatgpt_result.data or []):
        row_id = row.get('id')
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)

        filename = row.get('filename', '')
        lower_fn = filename.lower()
        if any(lower_fn.endswith(ext) for ext in EXCLUDED_SUFFIXES):
            continue
        if 'memup/' in lower_fn:
            continue

        content = row.get('content', '')
        if content and len(content) >= 50:
            transcripts.append({
                'id': row_id,
                'filename': filename,
                'content': content,
                'created_at': row.get('created_at', ''),
            })

    return transcripts


def _fetch_existing_capsule(supabase, construct_id: str, user_id: str = None) -> Optional[Dict[str, Any]]:
    capsule_path = f'instances/{construct_id}/memup/{construct_id}.capsule'
    query = supabase.table('vault_files').select('id, content, metadata, sha256, created_at')

    if user_id:
        query = query.eq('user_id', user_id)

    result = query.eq('construct_id', construct_id).eq('filename', capsule_path).execute()

    if result.data:
        row = result.data[0]
        content = row.get('content', '')
        try:
            capsule_data = json.loads(content) if content else {}
        except (json.JSONDecodeError, TypeError):
            capsule_data = {}
        return {
            'id': row['id'],
            'data': capsule_data,
            'sha256': row.get('sha256', ''),
            'created_at': row.get('created_at', ''),
        }
    return None


def _merge_capsule(existing_data: Dict[str, Any], new_entries: List[Dict[str, Any]],
                   construct_id: str) -> Dict[str, Any]:
    existing_sessions = existing_data.get('sessions', [])

    for session in existing_sessions:
        if not session.get('entry_id'):
            fn = session.get('filename', session.get('source_file', ''))
            fid = session.get('file_db_id')
            session['entry_id'] = _stable_entry_id(construct_id, fn, fid)

    existing_ids = {s.get('entry_id') for s in existing_sessions if s.get('entry_id')}

    added = 0
    for entry in new_entries:
        entry_id = _stable_entry_id(construct_id, entry.get('filename', ''), entry.get('file_db_id'))
        entry['entry_id'] = entry_id
        if entry_id not in existing_ids:
            existing_sessions.append(entry)
            existing_ids.add(entry_id)
            added += 1

    existing_sessions.sort(key=lambda e: e.get('estimated_date', ''))

    all_topics = set()
    all_vibes = {}
    all_hooks = []
    total_exchanges = 0
    sources = set()

    for s in existing_sessions:
        for t in s.get('topics', []):
            all_topics.add(t)
        vibe = s.get('vibe', 'neutral')
        all_vibes[vibe] = all_vibes.get(vibe, 0) + 1
        for h in s.get('continuity_hooks', []):
            if len(all_hooks) < 20:
                all_hooks.append(h)
        total_exchanges += s.get('exchange_count', 0)
        sources.add(s.get('source', 'unknown'))

    now = datetime.now(timezone.utc).isoformat()

    merged = {
        'construct_id': construct_id,
        'capsule_version': '2.0.0',
        'generator': 'memup_sync',
        'last_synced_at': now,
        'created_at': existing_data.get('created_at', now),
        'summary': {
            'total_sessions': len(existing_sessions),
            'total_exchanges': total_exchanges,
            'date_range': {
                'earliest': existing_sessions[0].get('estimated_date', '') if existing_sessions else '',
                'latest': existing_sessions[-1].get('estimated_date', '') if existing_sessions else '',
            },
            'topics': sorted(all_topics),
            'vibe_distribution': all_vibes,
            'sources': sorted(sources),
            'continuity_hooks': all_hooks[:15],
        },
        'sessions': existing_sessions,
        'sync_stats': {
            'entries_added': added,
            'entries_existing': len(existing_sessions) - added,
            'synced_at': now,
        },
    }

    return merged


def _write_capsule_to_supabase(supabase, construct_id: str, user_id: str,
                                capsule_data: Dict[str, Any],
                                existing_id: str = None) -> Dict[str, Any]:
    capsule_path = f'instances/{construct_id}/memup/{construct_id}.capsule'
    content_str = json.dumps(capsule_data, indent=2, default=str)
    sha256 = hashlib.sha256(content_str.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    meta = {
        'construct_id': construct_id,
        'provider': 'memup_sync',
        'folder': 'memup',
        'capsule_version': capsule_data.get('capsule_version', '2.0.0'),
        'total_sessions': capsule_data.get('summary', {}).get('total_sessions', 0),
        'last_synced_at': now,
    }

    if existing_id:
        result = supabase.table('vault_files').update({
            'content': content_str,
            'sha256': sha256,
            'metadata': json.dumps(meta),
            'updated_at': now,
        }).eq('id', existing_id).execute()
        action = 'updated'
    else:
        record = {
            'filename': capsule_path,
            'file_type': 'capsule',
            'content': content_str,
            'construct_id': construct_id,
            'user_id': user_id,
            'is_system': False,
            'sha256': sha256,
            'metadata': json.dumps(meta),
            'storage_path': capsule_path,
            'created_at': now,
        }
        result = supabase.table('vault_files').insert(record).execute()
        action = 'created'

    file_id = result.data[0]['id'] if result.data else existing_id
    return {
        'action': action,
        'file_id': file_id,
        'path': capsule_path,
        'sha256': sha256,
    }


def sync_construct_memup(supabase, construct_id: str, user_id: str) -> Dict[str, Any]:
    """
    Main sync function: fetches transcripts, parses them, merges with
    existing capsule, writes back to Supabase.

    Returns sync result with stats.
    """
    logger.info(f'MEMUP_SYNC: Starting sync for {construct_id}')

    transcripts = _fetch_transcripts(supabase, construct_id, user_id)
    if not transcripts:
        return {
            'success': False,
            'construct_id': construct_id,
            'error': 'No transcripts found for this construct',
            'transcripts_found': 0,
        }

    logger.info(f'MEMUP_SYNC: Found {len(transcripts)} transcripts for {construct_id}')

    filename_to_db_id = {t['filename']: str(t['id']) for t in transcripts}

    parser = ContinuityParser(construct_id)
    entries = parser.process_all_transcripts(transcripts)

    if not entries:
        return {
            'success': False,
            'construct_id': construct_id,
            'error': 'No parseable sessions found in transcripts',
            'transcripts_found': len(transcripts),
            'sessions_parsed': 0,
        }

    logger.info(f'MEMUP_SYNC: Parsed {len(entries)} sessions from {len(transcripts)} transcripts')

    ledger_entries = parser.generate_ledger_json(entries, include_exchanges=False)

    for entry in ledger_entries:
        fn = entry.get('filename', entry.get('source_file', ''))
        db_id = filename_to_db_id.get(fn)
        if db_id:
            entry['file_db_id'] = db_id

    existing = _fetch_existing_capsule(supabase, construct_id, user_id)
    existing_data = existing['data'] if existing else {}

    merged = _merge_capsule(existing_data, ledger_entries, construct_id)

    write_result = _write_capsule_to_supabase(
        supabase, construct_id, user_id, merged,
        existing_id=existing['id'] if existing else None
    )

    logger.info(
        f'MEMUP_SYNC: Complete for {construct_id} — '
        f'{merged["sync_stats"]["entries_added"]} new, '
        f'{merged["sync_stats"]["entries_existing"]} existing'
    )

    return {
        'success': True,
        'construct_id': construct_id,
        'transcripts_found': len(transcripts),
        'sessions_parsed': len(entries),
        'entries_added': merged['sync_stats']['entries_added'],
        'entries_existing': merged['sync_stats']['entries_existing'],
        'total_sessions': merged['summary']['total_sessions'],
        'total_exchanges': merged['summary']['total_exchanges'],
        'date_range': merged['summary']['date_range'],
        'topics': merged['summary']['topics'],
        'capsule_file': write_result,
    }
