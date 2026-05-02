"""
Read-only CapsuleForge v3 dry-run helpers.

Builds a Nova-style v3 capsule proposal from transcript-like vault rows and an
optional legacy CapsuleForge transcript source without mutating Supabase.
"""

import copy
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

try:
    from continuity_parser import ContinuityParser
except ImportError:  # pragma: no cover - package import path fallback
    from vvault.server.continuity_parser import ContinuityParser  # type: ignore

try:
    import memup_sync as _memup_sync
except Exception:  # pragma: no cover - fallback keeps the dry-run helper bootable without write-path deps
    class _MemupSyncShim:
        @staticmethod
        def _original_capsule_path(construct_id: str) -> str:
            return f"instances/{construct_id}/memup/{construct_id}.capsule"

        @staticmethod
        def _materialized_capsule_path(construct_id: str) -> str:
            return f"instances/{construct_id}/memup/{construct_id}.materialized.capsule"

        @staticmethod
        def _load_transcript_text(supabase, row: Dict[str, Any]) -> str:
            content = row.get("content")
            if isinstance(content, str) and content:
                return content

            storage_path = row.get("storage_path") or row.get("filename")
            if not storage_path:
                return ""

            try:
                data = supabase.storage.from_("vault-files").download(storage_path)
                blob = data[0] if isinstance(data, tuple) else getattr(data, "data", None)
                error = data[1] if isinstance(data, tuple) and len(data) > 1 else getattr(data, "error", None)
                if error or not blob:
                    return ""
                raw = blob.read() if hasattr(blob, "read") else blob
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", errors="ignore")
                return str(raw)
            except Exception:
                return ""

        @staticmethod
        def _fetch_transcripts(
            supabase,
            construct_id: str,
            user_id: str = None,
            max_transcripts: Optional[int] = None,
            deadline: Optional[float] = None,
            allow_storage_download: bool = True,
        ) -> List[Dict[str, Any]]:
            del deadline  # unused in shim

            def _build_query(folder_pattern):
                query = supabase.table("vault_files").select(
                    "id, filename, storage_path, file_type, content, created_at, metadata"
                ).eq("construct_id", construct_id)
                if user_id:
                    query = query.eq("user_id", user_id)
                return query.ilike("filename", folder_pattern).execute()

            transcripts: List[Dict[str, Any]] = []
            seen_ids = set()
            for row in (_build_query("%chatty/%").data or []) + (_build_query("%chatgpt/%").data or []):
                row_id = row.get("id")
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)

                filename = row.get("filename") or row.get("storage_path") or ""
                lower_fn = filename.lower()
                if any(lower_fn.endswith(ext) for ext in (".capsule", ".png", ".jpg", ".jpeg", ".gif", ".pdf")):
                    continue
                if "memup/" in lower_fn:
                    continue

                content = row.get("content") if isinstance(row.get("content"), str) and row.get("content") else ""
                if not content and allow_storage_download:
                    content = _MemupSyncShim._load_transcript_text(supabase, row)
                if content and len(content) >= 50:
                    transcripts.append(
                        {
                            "id": row_id,
                            "filename": filename,
                            "content": content,
                            "created_at": row.get("created_at", ""),
                        }
                    )
                    if max_transcripts and len(transcripts) >= max_transcripts:
                        break
            return transcripts

        @staticmethod
        def _fetch_capsule_record(
            supabase,
            construct_id: str,
            user_id: str = None,
            capsule_path: str = None,
        ) -> Optional[Dict[str, Any]]:
            capsule_path = capsule_path or _MemupSyncShim._original_capsule_path(construct_id)
            query = supabase.table("vault_files").select("id, user_id, content, metadata, sha256, created_at")
            if user_id:
                query = query.eq("user_id", user_id)
            result = query.eq("construct_id", construct_id).eq("filename", capsule_path).execute()
            if not result.data:
                return None

            row = result.data[0]
            content = row.get("content", "")
            try:
                capsule_data = json.loads(content) if content else {}
            except (json.JSONDecodeError, TypeError):
                capsule_data = {}
            return {
                "id": row["id"],
                "path": capsule_path,
                "data": capsule_data,
                "sha256": row.get("sha256", ""),
                "created_at": row.get("created_at", ""),
                "metadata": row.get("metadata"),
                "raw_content": content if isinstance(content, str) else "",
                "user_id": row.get("user_id") or user_id,
            }

        @staticmethod
        def _fetch_existing_capsule(supabase, construct_id: str, user_id: str = None) -> Optional[Dict[str, Any]]:
            return _MemupSyncShim._fetch_capsule_record(
                supabase,
                construct_id,
                user_id=user_id,
                capsule_path=_MemupSyncShim._original_capsule_path(construct_id),
            )

        @staticmethod
        def _stable_entry_id(construct_id: str, filename: str, file_db_id: str = None) -> str:
            raw = f"{construct_id}:{file_db_id}" if file_db_id else f"{construct_id}:{filename}"
            return hashlib.sha256(raw.encode()).hexdigest()[:12]

        @staticmethod
        def _merge_capsule(existing_data: Dict[str, Any], new_entries: List[Dict[str, Any]], construct_id: str) -> Dict[str, Any]:
            existing_sessions = existing_data.get("sessions", [])

            for session in existing_sessions:
                if not session.get("entry_id"):
                    fn = session.get("filename", session.get("source_file", ""))
                    fid = session.get("file_db_id")
                    session["entry_id"] = _MemupSyncShim._stable_entry_id(construct_id, fn, fid)

            existing_ids = {session.get("entry_id") for session in existing_sessions if session.get("entry_id")}

            added = 0
            for entry in new_entries:
                entry_id = _MemupSyncShim._stable_entry_id(
                    construct_id,
                    entry.get("filename", ""),
                    entry.get("file_db_id"),
                )
                entry["entry_id"] = entry_id
                if entry_id not in existing_ids:
                    existing_sessions.append(entry)
                    existing_ids.add(entry_id)
                    added += 1

            existing_sessions.sort(key=lambda item: item.get("estimated_date", ""))

            all_topics = set()
            all_vibes: Dict[str, int] = {}
            all_hooks = []
            total_exchanges = 0
            sources = set()

            for session in existing_sessions:
                for topic in session.get("topics", []):
                    all_topics.add(topic)
                vibe = session.get("vibe", "neutral")
                all_vibes[vibe] = all_vibes.get(vibe, 0) + 1
                for hook in session.get("continuity_hooks", []):
                    if len(all_hooks) < 20:
                        all_hooks.append(hook)
                total_exchanges += session.get("exchange_count", 0)
                sources.add(session.get("source", "unknown"))

            now = datetime.now(timezone.utc).isoformat()
            return {
                "construct_id": construct_id,
                "capsule_version": "2.0.0",
                "generator": "memup_sync",
                "last_synced_at": now,
                "created_at": existing_data.get("created_at", now),
                "summary": {
                    "total_sessions": len(existing_sessions),
                    "total_exchanges": total_exchanges,
                    "date_range": {
                        "earliest": existing_sessions[0].get("estimated_date", "") if existing_sessions else "",
                        "latest": existing_sessions[-1].get("estimated_date", "") if existing_sessions else "",
                    },
                    "topics": sorted(all_topics),
                    "vibe_distribution": all_vibes,
                    "sources": sorted(sources),
                    "continuity_hooks": all_hooks[:15],
                },
                "sessions": existing_sessions,
                "sync_stats": {
                    "entries_added": added,
                    "entries_existing": len(existing_sessions) - added,
                    "synced_at": now,
                },
            }

    memup_sync = _MemupSyncShim()
else:  # pragma: no cover - exercised implicitly in venv-backed tests
    memup_sync = _memup_sync


logger = logging.getLogger("vvault.capsule_v3_dry_run")

_TRANSCRIPT_KEYWORDS = (
    "transcript",
    "character_ai",
    "chatgpt",
    "chatty",
    "chat_with_",
    "conversation",
    "chat",
)
_EXCLUDED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")
_STORAGE_TOPOLOGY_PATTERN = re.compile(
    r"(^/users/|^users/|^instances/|^vvault/|/memup/|/identity/|/config/|/assets/|/chatgpt/|/chatty/)",
    re.I,
)
_UTC = timezone.utc
_MAX_SOURCE_REFS_PER_CLAIM = 20
_DISCOVERY_PATTERNS = (
    "%chatty/%",
    "%chatgpt/%",
    "%chat_with_%",
    "%conversation%",
    "%transcript%",
    "%character_ai%",
)
_DISCOVERY_FILE_TYPES = ("transcript", "conversation")


def _normalize_callsign(raw_id: str) -> str:
    if re.match(r"^.+-\d{3}$", raw_id or ""):
        return raw_id
    return f"{raw_id}-001"


def _bare_name_from_callsign(callsign: str) -> str:
    match = re.match(r"^(.+)-\d{3}$", callsign or "")
    return match.group(1) if match else callsign


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _basename(value: str, fallback: str) -> str:
    if not value:
        return fallback
    return PurePosixPath(value).name or fallback


def _source_date_for(filename: str, created_at: str, parser: ContinuityParser) -> str:
    estimated_date, confidence = parser.estimate_date(filename)
    if confidence > 0.2 and estimated_date:
        return estimated_date
    if isinstance(created_at, str) and len(created_at) >= 10:
        return created_at[:10]
    return ""


def _chronology_key_for(source_date: str, source_name: str) -> str:
    date_part = source_date or "undated"
    return f"{date_part}:{source_name}"


def _compute_sha256(content: str) -> Optional[str]:
    if not isinstance(content, str) or not content:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _has_text_content(content: str) -> bool:
    return isinstance(content, str) and bool(content.strip())


def _load_row_text(supabase, row: Dict[str, Any], allow_storage_download: bool) -> str:
    content = row.get("content")
    if isinstance(content, str) and content:
        return content
    if not allow_storage_download:
        return ""
    return memup_sync._load_transcript_text(supabase, row)


def _run_paged_query(query_factory, page_size: int = 100, max_pages: int = 20) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in range(max_pages):
        start = page * page_size
        end = start + page_size - 1
        query = query_factory().range(start, end)
        result = query.execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def _read_query_rows(
    supabase,
    construct_id: str,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen_ids = set()
    callsign = _normalize_callsign(construct_id)
    bare_name = _bare_name_from_callsign(callsign)
    for construct_key in {callsign, bare_name}:
        if not construct_key:
            continue
        for pattern in _DISCOVERY_PATTERNS:
            for field_name in ("filename", "storage_path"):
                def _pattern_query(field_name=field_name, pattern=pattern, construct_key=construct_key):
                    query = supabase.table("vault_files").select(
                        "id, construct_id, user_id, filename, storage_path, file_type, created_at, sha256, metadata"
                    ).eq("construct_id", construct_key).ilike(field_name, pattern).order("created_at", desc=True)
                    if user_id:
                        query = query.eq("user_id", user_id)
                    return query

                for row in _run_paged_query(_pattern_query):
                    row_id = row.get("id")
                    if row_id in seen_ids:
                        continue
                    seen_ids.add(row_id)
                    rows.append(row)

        for file_type in _DISCOVERY_FILE_TYPES:
            def _file_type_query(file_type=file_type, construct_key=construct_key):
                query = supabase.table("vault_files").select(
                    "id, construct_id, user_id, filename, storage_path, file_type, created_at, sha256, metadata"
                ).eq("construct_id", construct_key).eq("file_type", file_type).order("created_at", desc=True)
                if user_id:
                    query = query.eq("user_id", user_id)
                return query

            for row in _run_paged_query(_file_type_query):
                row_id = row.get("id")
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                rows.append(row)
    return rows


def _classify_source_type(path: str, content: str) -> str:
    lower_path = path.lower()
    if _parse_legacy_capsule_payload(content):
        return "legacy_capsule_transcript"
    if "chatgpt" in lower_path:
        return "chatgpt_transcript"
    if "chatty" in lower_path:
        return "chatty_transcript"
    if "conversation" in lower_path or "chat_with_" in lower_path:
        return "conversation_text"
    return "transcript_text"


def _is_legacy_capsule_candidate(content: str) -> bool:
    return bool(_parse_legacy_capsule_payload(content))


def _parse_legacy_capsule_payload(content: str) -> Optional[Dict[str, Any]]:
    if not _has_text_content(content):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        return None
    legacy_markers = {"memory", "traits", "personality", "signatures", "environment", "additional_data"}
    if not any(key in parsed for key in legacy_markers):
        return None
    return parsed


def _looks_like_candidate_source(row: Dict[str, Any]) -> Tuple[bool, str]:
    filename = str(row.get("filename") or "").strip()
    storage_path = str(row.get("storage_path") or "").strip()
    path = storage_path or filename
    searchable_path = f"{filename}\n{storage_path}".strip().lower()
    file_type = str(row.get("file_type") or "").lower()

    if not path and "transcript" not in file_type and "conversation" not in file_type:
        return False, "missing_path_and_non_transcript_type"
    if "/identity/" in searchable_path or "/config/" in searchable_path:
        return False, "identity_or_config_excluded"
    if any(value.lower().endswith(ext) for value in (filename, storage_path) for ext in _EXCLUDED_SUFFIXES if value):
        return False, "binary_asset_excluded"
    if any(value.lower().endswith(".capsule") for value in (filename, storage_path) if value) and "/memup/" in searchable_path:
        return False, "memup_capsule_excluded"

    path_match = any(keyword in searchable_path for keyword in _TRANSCRIPT_KEYWORDS)
    type_match = "transcript" in file_type or "conversation" in file_type
    text_like = "markdown" in file_type or file_type == "text"

    if path_match or type_match or (text_like and path_match):
        return True, ""
    return False, "non_transcript_like"


def _supports_from_transcript(
    parser: ContinuityParser,
    filename: str,
    content: str,
    source_type: str,
) -> List[str]:
    supports = {"provenance"}
    if source_type == "legacy_capsule_transcript":
        legacy = _parse_legacy_capsule_payload(content) or {}
        if legacy.get("memory"):
            supports.add("memory")
        if legacy.get("signatures") or legacy.get("additional_data", {}).get("continuity"):
            supports.add("continuity")
        if legacy.get("metadata"):
            supports.add("identity")
        signatures = _json_object(legacy.get("signatures"))
        if signatures.get("linguistic_sigil") or signatures.get("visual_sigil"):
            supports.add("sigil")
        return sorted(supports)

    if not _has_text_content(content):
        return sorted(supports)

    entry = parser.process_transcript(filename, content, 0)
    if entry:
        supports.add("continuity")
        if entry.get("exchange_count", 0) > 0 or entry.get("continuity_hooks"):
            supports.add("memory")
        if any(hook.get("type") == "identity" for hook in entry.get("continuity_hooks", [])):
            supports.add("identity")
        if "Identity/Philosophy" in entry.get("topics", []):
            supports.add("identity")
    if re.search(r"\b(sigil|glyph|signature phrase|tether signature)\b", content, re.I):
        supports.add("sigil")
    return sorted(supports)


def _artifact_id_for(row: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    artifact_id = metadata.get("artifact_id")
    if isinstance(artifact_id, str) and artifact_id.strip():
        return artifact_id.strip()
    return f"vault-file:{row.get('id')}"


def collect_construct_source_inventory(
    supabase,
    construct_id: str,
    user_id: Optional[str] = None,
    include_legacy_capsule_transcript: bool = False,
    allow_storage_download: bool = True,
) -> Dict[str, Any]:
    callsign = _normalize_callsign(construct_id)
    parser = ContinuityParser(callsign)
    source_records: List[Dict[str, Any]] = []
    omitted_sources: List[Dict[str, Any]] = []
    total_content_bytes = 0
    metadata_rows = _read_query_rows(
        supabase,
        callsign,
        user_id=user_id,
    )
    hydrated_rows = _fetch_rows_by_ids(supabase, [row.get("id") for row in metadata_rows], user_id=user_id)

    for metadata_row in metadata_rows:
        row = hydrated_rows.get(str(metadata_row.get("id"))) or metadata_row
        included, reason = _looks_like_candidate_source(row)
        path = row.get("storage_path") or row.get("filename") or ""
        source_name = _basename(path, f"source-{row.get('id')}")
        if not included:
            omitted_sources.append(
                {
                    "row_id": row.get("id"),
                    "source_name": source_name,
                    "reason": reason,
                }
            )
            continue

        content = _load_row_text(supabase, row, allow_storage_download=allow_storage_download)
        source_type = _classify_source_type(path, content)
        if source_type == "legacy_capsule_transcript" and not include_legacy_capsule_transcript:
            omitted_sources.append(
                {
                    "row_id": row.get("id"),
                    "source_name": source_name,
                    "reason": "legacy_capsule_transcript_excluded_by_option",
                }
            )
            continue

        metadata = _json_object(row.get("metadata"))
        sha256 = row.get("sha256") or _compute_sha256(content)
        source_date = _source_date_for(path, row.get("created_at", ""), parser)
        artifact_id = _artifact_id_for(row, metadata)
        supports = _supports_from_transcript(parser, path, content, source_type)
        if _has_text_content(content):
            total_content_bytes += len(content.encode("utf-8"))

        source_records.append(
            {
                "row_id": row.get("id"),
                "artifact_id": artifact_id,
                "source_name": source_name,
                "source_type": source_type,
                "chronology_key": _chronology_key_for(source_date, source_name),
                "source_date": source_date,
                "sha256": sha256,
                "supports": supports,
                "content_available": bool(_has_text_content(content)),
            }
        )

    source_records.sort(key=lambda item: (item.get("source_date") or "", item.get("chronology_key") or "", item.get("artifact_id") or ""))
    omitted_sources.sort(key=lambda item: (item.get("reason") or "", item.get("source_name") or ""))
    return {
        "construct_id": callsign,
        "sources": source_records,
        "omitted_sources": omitted_sources,
        "stats": {
            "total_sources": len(source_records),
            "legacy_capsule_sources": sum(1 for item in source_records if item.get("source_type") == "legacy_capsule_transcript"),
            "content_available_sources": sum(1 for item in source_records if item.get("content_available")),
            "total_content_bytes": total_content_bytes,
        },
    }


def _fetch_rows_by_ids(
    supabase,
    row_ids: List[str],
    user_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    ids = [str(value).strip() for value in row_ids if str(value).strip()]
    if not ids:
        return {}

    rows_by_id: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        query = supabase.table("vault_files").select(
            "id, construct_id, user_id, filename, storage_path, file_type, content, created_at, sha256, metadata"
        ).in_("id", chunk)
        result = query.execute()
        for row in result.data or []:
            if user_id and row.get("user_id") not in (None, user_id):
                continue
            rows_by_id[str(row.get("id"))] = row
    return rows_by_id


def _select_legacy_capsule_source(legacy_sources: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not legacy_sources:
        return None

    def _score(source: Dict[str, Any]) -> Tuple[int, int]:
        payload = _parse_legacy_capsule_payload(source.get("_content", "")) or {}
        payload_score = sum(
            1
            for key in ("metadata", "memory", "traits", "personality", "signatures", "additional_data", "environment")
            if payload.get(key)
        )
        return payload_score, len(source.get("_content", ""))

    return sorted(legacy_sources, key=_score, reverse=True)[0]


def _materialized_or_original_capsule(
    supabase,
    construct_id: str,
    user_id: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    materialized = memup_sync._fetch_capsule_record(
        supabase,
        construct_id,
        user_id=user_id,
        capsule_path=memup_sync._materialized_capsule_path(construct_id),
    )
    original = memup_sync._fetch_existing_capsule(supabase, construct_id, user_id)
    if materialized:
        return materialized, original, materialized, "materialized"
    if original:
        return materialized, original, original, "original"
    return materialized, original, None, "none"


def _source_manifest_entry(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifact_id": source.get("artifact_id"),
        "source_name": source.get("source_name"),
        "source_type": source.get("source_type"),
        "chronology_key": source.get("chronology_key"),
        "source_date": source.get("source_date"),
        "sha256": source.get("sha256"),
        "supports": list(source.get("supports") or []),
    }


def _capsule_record_source_manifest_entry(
    capsule_record: Optional[Dict[str, Any]],
    *,
    source_type: str,
) -> Optional[Dict[str, Any]]:
    if not capsule_record:
        return None

    file_id = capsule_record.get("id")
    if not file_id:
        return None

    path = capsule_record.get("path") or ""
    source_name = _basename(path, f"{source_type}-{file_id}")
    created_at = _first_non_empty_string(
        capsule_record.get("created_at"),
        _json_object(capsule_record.get("data")).get("created_at"),
        _json_object(capsule_record.get("data")).get("last_synced_at"),
    )
    source_date = created_at[:10] if created_at else ""
    sha256 = _first_non_empty_string(
        capsule_record.get("sha256"),
        _compute_sha256(capsule_record.get("raw_content", "")),
    )

    return {
        "artifact_id": f"vault-file:{file_id}",
        "source_name": source_name,
        "source_type": source_type,
        "chronology_key": _chronology_key_for(source_date, source_name),
        "source_date": source_date,
        "sha256": sha256,
        "supports": ["continuity", "provenance"],
    }


def _dedupe_string_list(values: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _dedupe_hook_records(hooks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for hook in hooks:
        key = (hook.get("type"), hook.get("text"))
        if key in seen:
            continue
        seen.add(key)
        result.append(hook)
    return result


def _hook_memory_records(
    sessions: List[Dict[str, Any]],
    artifact_by_filename: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    core_memories: Dict[str, Dict[str, Any]] = {}
    continuity_hooks: Dict[str, Dict[str, Any]] = {}

    core_templates = {
        "identity": "Identity continuity was explicitly invoked across source-backed Nova transcripts.",
        "relationship": "Relationship continuity was explicitly invoked across source-backed Nova transcripts.",
        "promise": "Promise or commitment continuity anchors were explicitly invoked across source-backed Nova transcripts.",
        "memory_reference": "Prior conversation continuity was explicitly referenced across source-backed Nova transcripts.",
        "emotional_anchor": "Emotional continuity anchors were explicitly invoked across source-backed Nova transcripts.",
    }
    hook_templates = {
        "ongoing_project": "Ongoing project threads remained active across source-backed Nova transcripts.",
        "future_plan": "Future-plan or next-step threads remained active across source-backed Nova transcripts.",
    }

    def _add_ref(record: Dict[str, Any], source_ref: Optional[str]) -> None:
        if not source_ref:
            return
        record["source_ref_count"] = int(record.get("source_ref_count") or 0) + 1
        refs = record.setdefault("source_refs", [])
        if source_ref not in refs and len(refs) < _MAX_SOURCE_REFS_PER_CLAIM:
            refs.append(source_ref)

    for session in sessions:
        artifact_id = artifact_by_filename.get(session.get("filename", ""))
        for hook in session.get("continuity_hooks", []):
            hook_type = hook.get("type")
            if hook_type in core_templates:
                record = core_memories.setdefault(
                    hook_type,
                    {
                        "kind": hook_type,
                        "claim": core_templates[hook_type],
                        "source_refs": [],
                        "source_ref_count": 0,
                    },
                )
                _add_ref(record, artifact_id)
            elif hook_type in hook_templates:
                record = continuity_hooks.setdefault(
                    hook_type,
                    {
                        "type": hook_type,
                        "text": hook_templates[hook_type],
                        "source_refs": [],
                        "source_ref_count": 0,
                    },
                )
                _add_ref(record, artifact_id)

    return list(core_memories.values()), list(continuity_hooks.values())


def _first_non_empty_string(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_string_list([str(item) for item in value if isinstance(item, str)])


def _sanitize_storage_topology_free(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            sanitized = _sanitize_storage_topology_free(item)
            if sanitized in (None, "", [], {}):
                continue
            if isinstance(key, str) and "path" in key.lower():
                continue
            cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            sanitized = _sanitize_storage_topology_free(item)
            if sanitized in (None, "", [], {}):
                continue
            cleaned_list.append(sanitized)
        return cleaned_list
    if isinstance(value, str):
        if _STORAGE_TOPOLOGY_PATTERN.search(value):
            return None
        return value.strip()
    return value


def _contains_storage_topology(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and "path" in key.lower():
                return True
            if _contains_storage_topology(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_storage_topology(item) for item in value)
    if isinstance(value, str):
        return bool(_STORAGE_TOPOLOGY_PATTERN.search(value))
    return False


def _contains_key(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_contains_key(item, target_key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target_key) for item in value)
    return False


def _body_from_legacy_continuity(
    legacy_payload: Optional[Dict[str, Any]],
    source_artifact_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not legacy_payload:
        return None
    additional_data = _json_object(legacy_payload.get("additional_data"))
    continuity = additional_data.get("continuity")
    if continuity in (None, "", [], {}):
        return None
    cleaned = _sanitize_storage_topology_free(continuity)
    if cleaned in (None, "", [], {}):
        return None
    return {
        "kind": "nova.v3.continuity",
        "sections": {
            "legacy_continuity": {
                "data": cleaned,
                "source_refs": [source_artifact_id] if source_artifact_id else [],
            }
        },
    }



_CAPSULE_FILENAME_TIMESTAMP_PATTERN = re.compile(
    r"\bNova_(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{6})-00-00\.capsule\b"
)
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)\b"
)


def _normalize_iso_timestamp(value: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parseable = raw.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", parseable):
        parseable = f"{parseable[:-2]}:{parseable[-2:]}"
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC).isoformat()


def _timestamp_sort_key(value: str) -> datetime:
    normalized = _normalize_iso_timestamp(value)
    if not normalized:
        return datetime.min.replace(tzinfo=_UTC)
    return datetime.fromisoformat(normalized)


def _capsule_filename_timestamp(match: re.Match[str]) -> str:
    year, month, day, hour, minute, second, micros = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}.{micros}+00:00"


def _extract_capsule_origin_evidence(hydrated_sources: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Prefer explicit kept CapsuleForge lineage evidence over regenerated body timestamps."""
    candidates: List[Dict[str, str]] = []
    for source in hydrated_sources:
        artifact_id = _first_non_empty_string(source.get("artifact_id"))
        if not artifact_id:
            continue
        content = source.get("_content", "")
        if not isinstance(content, str) or not content:
            continue
        source_name = _first_non_empty_string(source.get("source_name"), _basename(source.get("_filename", ""), artifact_id)) or artifact_id

        for match in _CAPSULE_FILENAME_TIMESTAMP_PATTERN.finditer(content):
            timestamp = _normalize_iso_timestamp(_capsule_filename_timestamp(match))
            if timestamp:
                candidates.append(
                    {
                        "origin_timestamp": timestamp,
                        "source_artifact_id": artifact_id,
                        "source_name": source_name,
                        "evidence": match.group(0),
                        "kind": "capsule_filename",
                    }
                )

        for match in _ISO_TIMESTAMP_PATTERN.finditer(content):
            timestamp = _normalize_iso_timestamp(match.group(1))
            if timestamp and timestamp.startswith("2025-08-05T"):
                candidates.append(
                    {
                        "origin_timestamp": timestamp,
                        "source_artifact_id": artifact_id,
                        "source_name": source_name,
                        "evidence": match.group(1),
                        "kind": "explicit_timestamp",
                    }
                )

    if not candidates:
        return None

    def _score(candidate: Dict[str, str]) -> Tuple[int, datetime]:
        # File-name evidence identifies the kept CapsuleForge artifact; timestamp-only evidence is secondary.
        kind_score = 1 if candidate.get("kind") == "capsule_filename" else 0
        return kind_score, _timestamp_sort_key(candidate.get("origin_timestamp", ""))

    return sorted(candidates, key=_score, reverse=True)[0]


def _build_signatures(
    legacy_payload: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Optional[str], Optional[str], Optional[str], List[Dict[str, Any]]]:
    signatures_payload = _json_object((legacy_payload or {}).get("signatures"))
    linguistic = _json_object(signatures_payload.get("linguistic_sigil"))
    visual = _json_object(signatures_payload.get("visual_sigil"))
    output_metadata = _json_object(signatures_payload.get("output_metadata"))
    metadata = _json_object((legacy_payload or {}).get("metadata"))

    signature_phrase = _first_non_empty_string(
        linguistic.get("signature_phrase"),
        output_metadata.get("signature_phrase"),
    )
    tether_signature = _first_non_empty_string(
        metadata.get("tether_signature"),
        output_metadata.get("tether_signature"),
    )
    lineage_uuid = _first_non_empty_string(
        metadata.get("lineage_uuid"),
        metadata.get("capsule_uuid"),
        metadata.get("uuid"),
    )
    origin_timestamp = _first_non_empty_string(
        metadata.get("origin_timestamp"),
        metadata.get("timestamp"),
        metadata.get("created_at"),
    )

    verified_fields: List[Dict[str, Any]] = []
    if lineage_uuid:
        verified_fields.append({"field": "metadata.lineage_uuid"})
    if tether_signature:
        verified_fields.append({"field": "metadata.tether_signature"})
    if origin_timestamp:
        verified_fields.append({"field": "metadata.origin_timestamp"})
    if signature_phrase:
        verified_fields.append({"field": "signatures.linguistic_sigil.signature_phrase"})

    common_phrases = _first_list(linguistic.get("common_phrases"))
    visual_payload = {
        "artifact_id": _first_non_empty_string(visual.get("artifact_id")),
        "glyph_hash": _first_non_empty_string(visual.get("glyph_hash"), visual.get("source_hash")),
        "number_band_hash": _first_non_empty_string(visual.get("number_band_hash")),
        "render_profile": _first_non_empty_string(visual.get("render_profile")),
        "generated_at": _first_non_empty_string(visual.get("generated_at")),
    }
    if any(value for value in visual_payload.values()):
        verified_fields.append({"field": "signatures.visual_sigil"})

    return (
        {
            "linguistic_sigil": {
                "signature_phrase": signature_phrase,
                "common_phrases": common_phrases,
            },
            "visual_sigil": visual_payload,
        },
        lineage_uuid,
        origin_timestamp,
        tether_signature,
        verified_fields,
    )


def _finalize_fingerprint_hash(capsule: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    finalized = copy.deepcopy(capsule)
    finalized["metadata"]["fingerprint_hash"] = None
    canonical = json.dumps(finalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    finalized["metadata"]["fingerprint_hash"] = fingerprint
    return finalized, fingerprint


def _validation_summary(
    capsule: Dict[str, Any],
    source_manifest: List[Dict[str, Any]],
    source_corpus_bytes: int,
) -> Dict[str, Any]:
    body = capsule.get("body")
    memory = _json_object(capsule.get("memory"))
    memory_claims = memory.get("core_memories") or []
    continuity_hooks = memory.get("continuity_hooks") or []

    source_refs_are_clean = True
    for claim in list(memory_claims) + list(continuity_hooks):
        refs = claim.get("source_refs")
        if not isinstance(refs, list) or not all(isinstance(item, str) and item.strip() for item in refs):
            source_refs_are_clean = False
            break

    body_sections = _json_object(_json_object(body).get("sections")) if isinstance(body, dict) else {}
    for section in body_sections.values():
        refs = _json_object(section).get("source_refs")
        if refs is not None and (not isinstance(refs, list) or not all(isinstance(item, str) and item.strip() for item in refs)):
            source_refs_are_clean = False
            break

    capsule_json = json.dumps(capsule, indent=2, sort_keys=True, ensure_ascii=True)
    manifest_ids = [item.get("artifact_id") for item in source_manifest if item.get("artifact_id")]
    memory_index_refs = memory.get("memory_index_refs") or []
    unique_manifest = len(set(manifest_ids)) == len(manifest_ids)
    unique_memory_refs = len(set(memory_index_refs)) == len(memory_index_refs)
    claim_refs_backed = all(bool(item.get("source_refs")) for item in memory_claims)

    return {
        "no_storage_paths_in_capsule_body": not _contains_storage_topology(body),
        "no_identity_refs": not _contains_key(capsule, "identity_refs"),
        "no_glyph_path": not _contains_key(capsule, "glyph_path"),
        "no_duplicated_metadata": unique_manifest and unique_memory_refs and source_refs_are_clean,
        "memory_claims_are_source_backed": claim_refs_backed,
        "source_refs_are_storage_topology_free": source_refs_are_clean,
        "source_corpus_bytes": source_corpus_bytes,
        "capsule_bytes": len(capsule_json.encode("utf-8")),
        "capsule_smaller_than_source_corpus": len(capsule_json.encode("utf-8")) < source_corpus_bytes if source_corpus_bytes else True,
    }


def build_construct_v3_capsule_proposal(
    supabase,
    construct_id: str,
    source_inventory: Dict[str, Any],
    user_id: Optional[str] = None,
    allow_storage_download: bool = True,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    callsign = _normalize_callsign(construct_id)
    inventory_sources = list(source_inventory.get("sources") or [])
    rows_by_id = _fetch_rows_by_ids(supabase, [item.get("row_id") for item in inventory_sources], user_id=user_id)

    parser = ContinuityParser(callsign)
    hydrated_sources: List[Dict[str, Any]] = []
    synthesis_omissions: List[Dict[str, Any]] = []
    total_source_corpus_bytes = 0

    for source in inventory_sources:
        row = rows_by_id.get(str(source.get("row_id")))
        if not row:
            synthesis_omissions.append(
                {
                    "artifact_id": source.get("artifact_id"),
                    "reason": "source_row_not_found",
                }
            )
            continue
        content = _load_row_text(supabase, row, allow_storage_download=allow_storage_download)
        hydrated = dict(source)
        hydrated["_filename"] = row.get("storage_path") or row.get("filename") or ""
        hydrated["_created_at"] = row.get("created_at") or ""
        hydrated["_content"] = content
        hydrated["_legacy_payload"] = _parse_legacy_capsule_payload(content)
        hydrated_sources.append(hydrated)
        if _has_text_content(content):
            total_source_corpus_bytes += len(content.encode("utf-8"))

    legacy_sources = [item for item in hydrated_sources if item.get("source_type") == "legacy_capsule_transcript" and item.get("_legacy_payload")]
    transcript_sources = [
        item
        for item in hydrated_sources
        if item.get("source_type") != "legacy_capsule_transcript" and _has_text_content(item.get("_content", ""))
    ]
    unavailable_transcripts = [
        item
        for item in hydrated_sources
        if item.get("source_type") != "legacy_capsule_transcript" and not _has_text_content(item.get("_content", ""))
    ]
    for source in unavailable_transcripts:
        synthesis_omissions.append(
            {
                "artifact_id": source.get("artifact_id"),
                "reason": "content_unavailable_for_synthesis",
            }
        )
    for source in legacy_sources:
        synthesis_omissions.append(
            {
                "artifact_id": source.get("artifact_id"),
                "reason": "legacy_capsule_transcript_used_for_provenance_only",
            }
        )

    materialized_capsule, original_capsule, merge_base, merge_base_source = _materialized_or_original_capsule(
        supabase,
        callsign,
        user_id=user_id,
    )
    merge_base_data = merge_base.get("data") if merge_base else {}

    transcript_inputs = [
        {
            "id": source.get("row_id"),
            "filename": source.get("_filename", ""),
            "content": source.get("_content", ""),
            "created_at": source.get("_created_at", ""),
        }
        for source in transcript_sources
        if len(source.get("_content", "")) >= 50
    ]
    filename_to_artifact_id = {source.get("_filename", ""): source.get("artifact_id") for source in transcript_sources}

    merged_continuity: Optional[Dict[str, Any]] = None
    if transcript_inputs:
        entries = parser.process_all_transcripts(transcript_inputs)
        if entries:
            ledger_entries = parser.generate_ledger_json(entries, include_exchanges=False)
            for entry in ledger_entries:
                artifact_id = filename_to_artifact_id.get(entry.get("filename", ""))
                if artifact_id:
                    entry["artifact_id"] = artifact_id
            merged_continuity = memup_sync._merge_capsule(merge_base_data or {}, ledger_entries, callsign)
        else:
            synthesis_omissions.append(
                {
                    "artifact_id": None,
                    "reason": "continuity_parser_returned_no_entries",
                }
            )

    legacy_source = _select_legacy_capsule_source(legacy_sources)
    legacy_payload = legacy_source.get("_legacy_payload") if legacy_source else None
    signatures, lineage_uuid, origin_timestamp, tether_signature, signature_verified_fields = _build_signatures(legacy_payload)
    origin_evidence = _extract_capsule_origin_evidence(hydrated_sources)
    if origin_evidence and origin_evidence.get("origin_timestamp"):
        origin_timestamp = origin_evidence["origin_timestamp"]

    sessions = list((merged_continuity or {}).get("sessions") or [])
    core_memories, continuity_hooks = _hook_memory_records(sessions, filename_to_artifact_id)
    continuity_hooks = _dedupe_hook_records(continuity_hooks)
    memory_source_refs = []
    for record in list(core_memories) + list(continuity_hooks):
        memory_source_refs.extend(record.get("source_refs") or [])
    memory_index_refs = _dedupe_string_list(memory_source_refs)

    body = _body_from_legacy_continuity(legacy_payload, legacy_source.get("artifact_id") if legacy_source else None)

    capsule_evidence_sources = []
    v2_source = _capsule_record_source_manifest_entry(
        original_capsule,
        source_type="memup_sync_v2_capsule",
    )
    if v2_source:
        capsule_evidence_sources.append(v2_source)
        raw_content = original_capsule.get("raw_content", "") if original_capsule else ""
        if _has_text_content(raw_content):
            total_source_corpus_bytes += len(raw_content.encode("utf-8"))

    used_source_ids = set(memory_index_refs)
    if legacy_source and legacy_source.get("artifact_id"):
        used_source_ids.add(legacy_source.get("artifact_id"))
    if origin_evidence and origin_evidence.get("source_artifact_id"):
        used_source_ids.add(origin_evidence["source_artifact_id"])
    if body:
        for section in _json_object(body.get("sections")).values():
            for source_ref in _json_object(section).get("source_refs") or []:
                used_source_ids.add(source_ref)
    for source in capsule_evidence_sources:
        if source.get("artifact_id"):
            used_source_ids.add(source.get("artifact_id"))

    now = generated_at or datetime.now(_UTC).isoformat()
    source_manifest_sources = [
        _source_manifest_entry(source)
        for source in inventory_sources
        if source.get("artifact_id") in used_source_ids
    ] + capsule_evidence_sources
    capsule = {
        "metadata": {
            "construct_id": callsign,
            "capsule_uuid": None,
            "lineage_uuid": lineage_uuid,
            "origin_timestamp": origin_timestamp,
            "capsule_version": "3.0.0",
            "profile_kind": "custom",
            "generated_at": now,
            "generator": "CapsuleForge v3",
            "fingerprint_hash": None,
            "tether_signature": tether_signature,
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
            "construct_id": callsign,
            "role": None,
            "core_definition": None,
            "do_not_flatten_into": [],
        },
        "memory": {
            "core_memories": core_memories,
            "continuity_hooks": continuity_hooks,
            "memory_index_refs": memory_index_refs,
        },
        "source_manifest": {
            "sources": source_manifest_sources,
        },
        "retrieval_policy": {
            "primary": "memory_index_refs",
            "fallback": ["source_manifest"],
            "requires_source_hash": True,
        },
        "signatures": signatures,
        "body": body,
    }

    finalized_capsule, fingerprint = _finalize_fingerprint_hash(capsule)
    validation = _validation_summary(
        finalized_capsule,
        finalized_capsule["source_manifest"]["sources"],
        total_source_corpus_bytes,
    )

    verified_fields = [
        {
            "field": "memory.core_memories",
            "detail": f"{len(finalized_capsule['memory']['core_memories'])} core memory claim(s)",
        },
        {
            "field": "memory.continuity_hooks",
            "detail": f"{len(finalized_capsule['memory']['continuity_hooks'])} continuity hook(s)",
        },
        {
            "field": "memory.memory_index_refs",
            "detail": f"{len(finalized_capsule['memory']['memory_index_refs'])} transcript artifact id ref(s)",
        },
        {
            "field": "metadata.fingerprint_hash",
            "detail": fingerprint,
        },
    ]
    for item in signature_verified_fields:
        detail = legacy_source.get("artifact_id") if legacy_source else "source-backed"
        if item["field"] == "metadata.origin_timestamp" and origin_evidence:
            detail = origin_evidence.get("source_artifact_id") or detail
        verified_fields.append(
            {
                "field": item["field"],
                "detail": detail,
            }
        )
    if body:
        verified_fields.append(
            {
                "field": "body.sections.legacy_continuity",
                "detail": legacy_source.get("artifact_id") if legacy_source else "source-backed",
            }
        )
    if v2_source:
        verified_fields.append(
            {
                "field": "source_manifest.sources[memup_sync_v2_capsule]",
                "detail": v2_source.get("artifact_id"),
            }
        )

    kept_out = [
        "identity.role omitted because no explicit source-backed role was extracted from transcript/source rows.",
        "identity.core_definition omitted because no explicit source-backed definition was extracted from transcript/source rows.",
        "storage paths, glyph_path, and identity_refs were excluded by contract.",
        "legacy capsule transcript was not replayed into transcript sessions; it was used for provenance/signature evidence only.",
    ]
    if not body:
        kept_out.append("body remained null because no residual Nova-specific continuity payload needed a separate section.")

    return {
        "construct_id": callsign,
        "generated_at": now,
        "capsule": finalized_capsule,
        "source_inventory": source_inventory,
        "merge_base": {
            "source": merge_base_source,
            "path": merge_base.get("path") if merge_base else None,
            "file_id": merge_base.get("id") if merge_base else None,
            "sha256": merge_base.get("sha256") if merge_base else None,
        },
        "original_capsule": {
            "path": original_capsule.get("path") if original_capsule else None,
            "file_id": original_capsule.get("id") if original_capsule else None,
            "sha256": original_capsule.get("sha256") if original_capsule else None,
        },
        "materialized_capsule": {
            "path": materialized_capsule.get("path") if materialized_capsule else None,
            "file_id": materialized_capsule.get("id") if materialized_capsule else None,
            "sha256": materialized_capsule.get("sha256") if materialized_capsule else None,
        },
        "verified_fields": verified_fields,
        "kept_out_of_capsule": kept_out,
        "synthesis_omissions": synthesis_omissions,
        "validation": validation,
        "stats": {
            "total_inventory_sources": len(inventory_sources),
            "transcript_synthesis_sources": len(transcript_inputs),
            "legacy_capsule_sources": len(legacy_sources),
            "capsule_evidence_sources": len(capsule_evidence_sources),
            "total_source_corpus_bytes": total_source_corpus_bytes,
            "capsule_bytes": validation["capsule_bytes"],
        },
        "recommended_write_target": f"instances/{callsign}/memup/{callsign}.v3.capsule",
    }


def render_construct_v3_report(result: Dict[str, Any]) -> str:
    inventory = result.get("source_inventory") or {}
    capsule = result.get("capsule") or {}
    validation = result.get("validation") or {}
    merge_base = result.get("merge_base") or {}

    lines = [
        f"# CapsuleForge v3 Dry-Run Report - {result.get('construct_id')}",
        "",
        "## Verified Source Inventory Summary",
        f"- Sources inventoried: {inventory.get('stats', {}).get('total_sources', 0)}",
        f"- Content-available sources: {inventory.get('stats', {}).get('content_available_sources', 0)}",
        f"- Legacy capsule transcript sources: {inventory.get('stats', {}).get('legacy_capsule_sources', 0)}",
        f"- Merge base used: {merge_base.get('source') or 'none'}",
        "",
        "## Omitted or Rejected Sources",
    ]

    omitted = list(inventory.get("omitted_sources") or []) + list(result.get("synthesis_omissions") or [])
    if omitted:
        for item in omitted:
            label = item.get("source_name") or item.get("artifact_id") or "source"
            lines.append(f"- `{label}`: {item.get('reason')}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Verified Fields Placed Into The v3 Capsule",
        ]
    )
    for item in result.get("verified_fields") or []:
        detail = item.get("detail")
        if detail:
            lines.append(f"- `{item.get('field')}`: {detail}")
        else:
            lines.append(f"- `{item.get('field')}`")

    lines.extend(
        [
            "",
            "## Inference Or Observations Kept Out Of The Capsule",
        ]
    )
    for item in result.get("kept_out_of_capsule") or []:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Validation Checklist",
            f"- No storage paths in capsule body: {validation.get('no_storage_paths_in_capsule_body')}",
            f"- No `identity_refs`: {validation.get('no_identity_refs')}",
            f"- No `glyph_path`: {validation.get('no_glyph_path')}",
            f"- No duplicated metadata refs: {validation.get('no_duplicated_metadata')}",
            f"- Every memory claim source-backed: {validation.get('memory_claims_are_source_backed')}",
            f"- Source refs topology-free: {validation.get('source_refs_are_storage_topology_free')}",
            f"- Capsule bytes: {validation.get('capsule_bytes')}",
            f"- Source corpus bytes: {validation.get('source_corpus_bytes')}",
            f"- Capsule smaller than source corpus: {validation.get('capsule_smaller_than_source_corpus')}",
            "",
            "## Recommended Future Write Target",
            f"- `{result.get('recommended_write_target')}`",
        ]
    )

    if capsule:
        lines.extend(
            [
                "",
                "## Capsule Summary",
                f"- Core memories: {len(capsule.get('memory', {}).get('core_memories', []))}",
                f"- Continuity hooks: {len(capsule.get('memory', {}).get('continuity_hooks', []))}",
                f"- Memory index refs: {len(capsule.get('memory', {}).get('memory_index_refs', []))}",
                f"- Body present: {bool(capsule.get('body'))}",
            ]
        )

    return "\n".join(lines) + "\n"
