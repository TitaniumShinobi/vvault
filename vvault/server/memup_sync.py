"""
Memup Sync — Transcript-to-Capsule Synchronization
====================================================

Reads canonical OVVAULTS transcripts through the VVAULT repository, runs ContinuityParser
to extract structured ledger entries, merges with existing memup capsule
data, and writes the result back to the construct's memup/ folder.

Usage:
    result = sync_construct_memup(vault_repository, construct_id, user_id)
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from uuid import NAMESPACE_URL, uuid5

from continuity_parser import ContinuityParser
from vvault.server.artifact_contract import CAPSULE_ARTIFACT_ID, storage_path

logger = logging.getLogger('vvault.memup_sync')


def _stable_entry_id(construct_id: str, filename: str, file_db_id: str = None) -> str:
    if file_db_id:
        raw = f'{construct_id}:{file_db_id}'
    else:
        raw = f'{construct_id}:{filename}'
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _materialized_capsule_path(construct_id: str) -> str:
    return f"instances/{construct_id}/memup/{construct_id}.materialized.capsule"


def _fetch_capsule_record(vault_repository, construct_id: str, user_id: str, capsule_path: str) -> dict[str, Any] | None:
    if not construct_id or not user_id or not capsule_path:
        return None

    row = None
    if hasattr(vault_repository, "find_by_path"):
        try:
            row = vault_repository.find_by_path(
                filename=capsule_path,
                storage_path=capsule_path,
                construct_id=construct_id,
                user_id=user_id,
                is_admin=False,
            )
        except TypeError:
            try:
                row = vault_repository.find_by_path(
                    filename=capsule_path,
                    storage_path=capsule_path,
                    construct_id=construct_id,
                    user_id=user_id,
                )
            except Exception:
                row = None
    if not row and hasattr(vault_repository, "get_canonical_capsule"):
        try:
            row = vault_repository.get_canonical_capsule(construct_id=construct_id, user_id=user_id)
        except Exception:
            row = None
    if not row:
        return None

    if row.get("filename") == capsule_path or row.get("storage_path") == capsule_path or row.get("path") == capsule_path:
        content = row.get("content")
        record = dict(row)
        record.setdefault("path", row.get("filename") or row.get("storage_path"))
        try:
            record["data"] = json.loads(content) if content else {}
        except (json.JSONDecodeError, TypeError):
            record["data"] = {}
        record.setdefault("raw_content", content)
        return record
    return None


def _load_transcript_text(vault_repository, row: dict[str, Any]) -> str:
    content = row.get("content")
    if isinstance(content, str) and content:
        return content

    row_id = row.get("id")
    if not row_id or not hasattr(vault_repository, "get_by_id"):
        return ""

    full_row = vault_repository.get_by_id(row_id) if row_id else None
    if not full_row:
        return ""
    return str(full_row.get("content") or "")


def _fetch_transcripts(vault_repository, construct_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Load transcript content exclusively from OVVAULTS transcripts."""
    rows = vault_repository.list_canonical_transcripts(
        construct_id=construct_id,
        user_id=user_id,
    )
    return [
        {
            'id': row.get('id'),
            'filename': row.get('filename', ''),
            'content': row.get('content', ''),
            'created_at': row.get('materialized_at') or row.get('created_at', ''),
        }
        for row in rows
        if isinstance(row.get('content'), str) and len(row['content']) >= 50
    ]


def _fetch_existing_capsule(vault_repository, construct_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = vault_repository.get_canonical_capsule(
        construct_id=construct_id,
        user_id=user_id,
    )
    if row:
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
    existing_body = existing_data.get('body') if isinstance(existing_data.get('body'), dict) else {}
    existing_sessions = existing_body.get('sessions', existing_data.get('sessions', []))

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

    summary = {
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
        }
    sync_stats = {
            'entries_added': added,
            'entries_existing': len(existing_sessions) - added,
            'synced_at': now,
        }
    existing_metadata = existing_data.get('metadata') if isinstance(existing_data.get('metadata'), dict) else {}
    lineage_uuid = str(
        existing_metadata.get('lineage_uuid')
        or uuid5(NAMESPACE_URL, f"life-vvault-lineage:{construct_id}")
    )
    capsule_uuid = str(uuid5(NAMESPACE_URL, f"life-vvault-capsule:{construct_id}:{now}"))
    fingerprint_hash = hashlib.sha256(
        json.dumps(
            {"construct_id": construct_id, "summary": summary, "sessions": existing_sessions},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    source_rows = [
        {
            "artifact_id": str(session.get("file_db_id") or session.get("entry_id") or ""),
            "source_name": str(session.get("filename") or session.get("source_file") or ""),
            "source_type": "transcript",
            "chronology_key": str(session.get("estimated_date") or ""),
            "source_date": str(session.get("estimated_date") or "") or None,
            "sha256": None,
            "supports": [
                hook.get("type")
                for hook in session.get("continuity_hooks", [])
                if isinstance(hook, dict) and hook.get("type")
            ],
        }
        for session in existing_sessions
    ]
    merged = {
        "metadata": {
            "construct_id": construct_id,
            "capsule_uuid": capsule_uuid,
            "lineage_uuid": lineage_uuid,
            "capsule_version": "2.1.0",
            "profile_kind": "custom",
            "generated_at": now,
            "generator": "memup_sync",
            "fingerprint_hash": fingerprint_hash,
            "tether_signature": existing_metadata.get("tether_signature"),
        },
        "quality_contract": {
            "accurate": True,
            "relevant": True,
            "non_redundant": True,
            "portable": True,
            "source_backed": True,
            "storage_topology_free": True,
        },
        "identity": {
            "construct_id": construct_id,
            "role": None,
            "core_definition": None,
            "do_not_flatten_into": [],
        },
        "memory": {
            "core_memories": [],
            "continuity_hooks": all_hooks[:15],
            "memory_index_refs": [
                str(session.get("file_db_id"))
                for session in existing_sessions
                if session.get("file_db_id")
            ],
        },
        "source_manifest": {"sources": source_rows},
        "retrieval_policy": {
            "primary": "memory_index_refs",
            "fallback": ["source_manifest"],
            "requires_source_hash": True,
        },
        "signatures": {
            "linguistic_sigil": {
                "signature_phrase": None,
                "common_phrases": [],
            },
            "visual_sigil": {
                "artifact_id": None,
                "glyph_hash": None,
                "number_band_hash": None,
                "render_profile": None,
                "generated_at": None,
            },
        },
        "body": {
            "summary": summary,
            "sessions": existing_sessions,
            "sync_stats": sync_stats,
        },
    }

    return merged


def _write_canonical_capsule(vault_repository, construct_id: str, user_id: str,
                             capsule_data: Dict[str, Any]) -> Dict[str, Any]:
    capsule_path = storage_path(CAPSULE_ARTIFACT_ID, construct_id)
    content_str = json.dumps(capsule_data, indent=2, default=str)
    sha256 = hashlib.sha256(content_str.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    meta = {
        'construct_id': construct_id,
        'provider': 'memup_sync',
        'folder': 'memup',
        'capsule_version': capsule_data.get('metadata', {}).get('capsule_version', '2.1.0'),
        'total_sessions': capsule_data.get('body', {}).get('summary', {}).get('total_sessions', 0),
        'last_synced_at': now,
    }

    result = vault_repository.upsert_canonical_capsule(
        construct_id=construct_id,
        user_id=user_id,
        content=content_str,
        metadata=meta,
    )
    return {
        'action': result.get('action'),
        'file_id': result.get('id'),
        'path': capsule_path,
        'sha256': sha256,
    }


def sync_construct_memup(vault_repository, construct_id: str, user_id: str) -> Dict[str, Any]:
    """
    Main sync function: fetches canonical transcripts, parses them, merges with
    an OVVAULTS capsule, and persists it through the VVAULT repository.

    Returns sync result with stats.
    """
    logger.info(f'MEMUP_SYNC: Starting sync for {construct_id}')

    transcripts = _fetch_transcripts(vault_repository, construct_id, user_id)
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

    existing = _fetch_existing_capsule(vault_repository, construct_id, user_id)
    existing_data = existing['data'] if existing else {}

    merged = _merge_capsule(existing_data, ledger_entries, construct_id)

    write_result = _write_canonical_capsule(
        vault_repository, construct_id, user_id, merged,
    )

    logger.info(
        f'MEMUP_SYNC: Complete for {construct_id} — '
        f'{merged["body"]["sync_stats"]["entries_added"]} new, '
        f'{merged["body"]["sync_stats"]["entries_existing"]} existing'
    )

    return {
        'success': True,
        'construct_id': construct_id,
        'transcripts_found': len(transcripts),
        'sessions_parsed': len(entries),
        'entries_added': merged['body']['sync_stats']['entries_added'],
        'entries_existing': merged['body']['sync_stats']['entries_existing'],
        'total_sessions': merged['body']['summary']['total_sessions'],
        'total_exchanges': merged['body']['summary']['total_exchanges'],
        'date_range': merged['body']['summary']['date_range'],
        'topics': merged['body']['summary']['topics'],
        'capsule_file': write_result,
        'storage_mode': 'vvault_body',
        'authority': 'ovvaults',
    }
