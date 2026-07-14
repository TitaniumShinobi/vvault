"""VVAULT-native Chatty API body helpers."""

from __future__ import annotations

import os
import re
import json
import hashlib
import base64
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Iterable

CONTENT_MISSING_FIELDS = [
    "vault_files.content",
    "vault_files.construct_id",
    "vault_files.metadata",
    "vault_files.storage_path",
    "vault_files.file_type",
    "transcripts.content(real)",
    "identities/anatomies(materialized)",
]

BODY_SCHEMA = "ovvaults"
PLACEHOLDER_TRANSCRIPT_CONTENT = "not_exported_in_phase_1_3"
@dataclass(frozen=True)
class BodyResult:
    status: str
    route: str
    source_database: str | None
    payload: dict[str, Any]
    http_status: int = 200

    def to_response(self) -> tuple[dict[str, Any], int]:
        body = {
            "success": self.status == "body_native",
            "status": self.status,
            "route": self.route,
            "storage_mode": "vvault_body",
            "canonical": self.status == "body_native",
            "source_database": self.source_database,
            **self.payload,
        }
        return body, self.http_status


def database_url() -> str | None:
    return os.environ.get("VVAULT_BODY_DATABASE_URL") or None


def source_database_name(url: str | None = None) -> str | None:
    raw = url if url is not None else database_url()
    if not raw:
        return None
    without_query = raw.split("?", 1)[0].rstrip("/")
    return without_query.rsplit("/", 1)[-1] or None


def _connect():
    url = database_url()
    if not url:
        raise RuntimeError("VVAULT_BODY_DATABASE_URL is required; local database fallback is disabled")
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(url, row_factory=dict_row, options=f"-c search_path={BODY_SCHEMA},public")


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def _one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def normalize_callsign(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    if re.search(r"-\d{3}$", raw):
        return raw
    return f"{raw}-001"


def bare_name(callsign: str) -> str:
    return re.sub(r"-\d{3}$", "", callsign or "")


def display_name(callsign: str) -> str:
    return bare_name(callsign).replace("-", " ").title()


def _construct_from_path(path: str) -> str | None:
    lowered = (path or "").lower()
    match = re.search(r"(?:^|/)instances/([^/]+)/", lowered)
    if match:
        return normalize_callsign(match.group(1))
    match = re.search(r"chat_with_([a-z0-9_-]+)\.md$", lowered)
    if match:
        return normalize_callsign(match.group(1))
    return None


def _entry_from_file(row: dict[str, Any]) -> dict[str, Any]:
    path = row.get("storage_path") or row.get("object_key") or row.get("filename") or ""
    filename = row.get("filename") or str(path).split("/")[-1] or path
    content = row.get("content")
    return {
        "id": str(row.get("id")),
        "filename": str(filename).split("/")[-1],
        "path": path,
        "storage_path": row.get("storage_path") or path,
        "construct_id": row.get("construct_id"),
        "file_type": row.get("file_type") or row.get("content_type"),
        "content_type": row.get("content_type"),
        "created_at": row.get("created_at").isoformat() if hasattr(row.get("created_at"), "isoformat") else row.get("created_at"),
        "sha256": row.get("sha256"),
        "has_materialized_content": isinstance(content, str) and bool(content),
        "content_length": len(content) if isinstance(content, str) else 0,
        "body_source": "ovvaults.vault_files",
    }


def _jsonish(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list, bool)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    parsed = _jsonish(row.get("metadata"), {}) or {}
    return parsed if isinstance(parsed, dict) else {}


def _construct_from_file(row: dict[str, Any]) -> str | None:
    direct = normalize_callsign(str(row.get("construct_id") or ""))
    if direct:
        return direct

    metadata = _metadata(row)
    for key in ("construct_id", "callsign", "construct"):
        candidate = normalize_callsign(str(metadata.get(key) or ""))
        if candidate:
            return candidate

    for key in ("storage_path", "object_key", "filename"):
        candidate = _construct_from_path(str(row.get(key) or ""))
        if candidate:
            return candidate
    return None


def _source_file_entry(row: dict[str, Any]) -> dict[str, Any]:
    entry = _entry_from_file(row)
    content = row.get("content")
    if isinstance(content, str):
        entry["content"] = content
    entry["metadata"] = _metadata(row)
    return entry


def _is_png_base64_content(content: Any) -> bool:
    if not isinstance(content, str) or not content:
        return False
    raw = content
    match = re.match(r"^data:image/[^;]+;base64,(.+)$", raw, re.IGNORECASE | re.DOTALL)
    if match:
        raw = match.group(1)
    try:
        return base64.b64decode(raw, validate=True).startswith(b"\x89PNG\r\n\x1a\n")
    except Exception:
        return False


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _basename(row: dict[str, Any]) -> str:
    path = row.get("filename") or row.get("object_key") or row.get("storage_path") or ""
    return str(path).rstrip("/").rsplit("/", 1)[-1]


def _first_text(values: Iterable[Any], default: str = "") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _content_bearing_file_rows(callsign: str, *, terms: Iterable[str] | None = None) -> list[dict[str, Any]]:
    search_terms = [term.lower() for term in (terms or []) if term]
    like_prefix = f"%instances/{callsign}/%"
    rows = _rows(
        """
        SELECT id, filename, object_key, storage_path, content_type, file_type, created_at,
               sha256, content, metadata, construct_id
        FROM vault_files
        WHERE (
            construct_id = %s
            OR lower(coalesce(filename, '') || ' ' || coalesce(object_key, '') || ' ' || coalesce(storage_path, '')) LIKE %s
        )
          AND content IS NOT NULL
          AND content <> ''
        ORDER BY created_at ASC
        """,
        (callsign, like_prefix),
    )
    if not search_terms:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(str(row.get(key) or "") for key in ("filename", "object_key", "storage_path", "file_type", "content_type")).lower()
        if any(term in haystack for term in search_terms):
            filtered.append(row)
    return filtered


def _transcript_rows(callsign: str) -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, title, content, created_at, materialized_at, source_row_id, source_hash
        FROM transcripts
        WHERE content IS NOT NULL
          AND content <> ''
          AND content <> %s
          AND lower(title) LIKE %s
        ORDER BY created_at ASC
        """,
        (PLACEHOLDER_TRANSCRIPT_CONTENT, f"%{callsign}%"),
    )


def _row_sort_text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _select_transcript_read_row(callsign: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    target_title = str(_transcript_target(callsign)["storage_path"]).lower()
    exact_rows = [row for row in rows if str(row.get("title") or "").lower() == target_title]
    candidates = exact_rows or rows
    return max(candidates, key=lambda row: (_row_sort_text(row, "materialized_at"), _row_sort_text(row, "created_at")))


def _transcript_file_rows(callsign: str) -> list[dict[str, Any]]:
    return _content_bearing_file_rows(
        callsign,
        terms=("chat_with_", "transcript", "chatgpt", "chatty", "conversation", "character.ai", "character_ai"),
    )


def _identity_file_rows(callsign: str) -> list[dict[str, Any]]:
    supported = {
        "prompt.txt",
        "prompt.json",
        "personality.json",
        "metadata.json",
        "continuity_gpt_prompt.md",
        "definition.json",
        "definition.txt",
        "voice.json",
        "voice.md",
        "memory.json",
        "avatar.png",
        "avatar.jpg",
        "avatar.jpeg",
        "avatar.webp",
        "avatar.gif",
        "avatar.avif",
    }
    rows = _content_bearing_file_rows(callsign, terms=("identity", "config", "memup"))
    return [row for row in rows if _basename(row).lower() in supported]


def _pick_latest_by_basename(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = _basename(row).lower()
        if not name:
            continue
        current = selected.get(name)
        if not current:
            selected[name] = row
            continue
        current_created = str(current.get("created_at") or "")
        row_created = str(row.get("created_at") or "")
        if row_created >= current_created:
            selected[name] = row
    return selected


def _row_content(rows_by_name: dict[str, dict[str, Any]], name: str) -> str:
    return _text(rows_by_name.get(name, {}).get("content"))


def _parse_markdown_pairs(content: str, callsign: str) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    user_text: str | None = None
    construct_text: str | None = None
    construct_names = {callsign.lower(), bare_name(callsign).lower(), display_name(callsign).lower()}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(user|human|devon|assistant|ai|bot|construct|[^:]{1,40}):\s*(.+)$", line, re.I)
        if not match:
            continue
        speaker = match.group(1).strip().lower()
        text = match.group(2).strip()
        if speaker in {"user", "human", "devon"}:
            if user_text and construct_text:
                pairs.append({"user": user_text, "construct": construct_text})
                construct_text = None
            user_text = text
        elif speaker in {"assistant", "ai", "bot", "construct"} or speaker in construct_names:
            construct_text = text
            if user_text:
                pairs.append({"user": user_text, "construct": construct_text})
                user_text = None
                construct_text = None
    return pairs


def _blocked(route: str, *, reason: str, missing_fields: Iterable[str] | None = None, missing_tables: Iterable[str] | None = None) -> BodyResult:
    return BodyResult(
        status="body_missing",
        route=route,
        source_database=source_database_name(),
        http_status=503,
        payload={
            "error_code": "VVAULT_BODY_MISSING",
            "reason": reason,
            "missing_fields": list(missing_fields or CONTENT_MISSING_FIELDS),
            "missing_tables": list(missing_tables or []),
            "body_native_available": False,
        },
    )


def body_missing(route: str, *, reason: str | None = None) -> BodyResult:
    return _blocked(
        route,
        reason=reason or "Imported VVAULT body does not yet materialize the content fields required by this Chatty API route.",
    )


def list_constructs() -> BodyResult:
    route = "/api/chatty/constructs"
    try:
        rows = _rows(
            """
            SELECT id, filename, object_key, storage_path, construct_id, metadata,
                   content_type, created_at, sha256
            FROM vault_files
            WHERE nullif(btrim(coalesce(construct_id, '')), '') IS NOT NULL
               OR lower(
                    coalesce(storage_path, '') || ' ' ||
                    coalesce(object_key, '') || ' ' ||
                    coalesce(filename, '')
                  ) LIKE %s
            ORDER BY created_at ASC
            """,
            ("%instances/%",),
        )
    except Exception as exc:
        return _blocked(
            route,
            reason=f"VVAULT body database is unavailable or unreadable: {type(exc).__name__}",
            missing_fields=[],
            missing_tables=["ovvaults.vault_files"],
        )
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        callsign = _construct_from_file(row)
        if not callsign:
            continue
        created = row.get("created_at")
        created_text = created.isoformat() if hasattr(created, "isoformat") else created
        current = seen.get(callsign)
        if current and (current.get("created_at") or "") >= (created_text or ""):
            continue
        seen[callsign] = {
            "construct_id": callsign,
            "name": display_name(callsign),
            "filename": f"chat_with_{callsign}.md",
            "created_at": created_text,
            "body_source": "ovvaults.vault_files",
        }
    constructs = sorted(seen.values(), key=lambda item: item["construct_id"])
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "degraded": False,
            "storage_mode": "vvault_body",
            "constructs": constructs,
            "count": len(constructs),
            "body_native_available": True,
        },
    )


def construct_files(construct_id: str, *, folder: str | None = None) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/construct/{callsign}/files"
    like_prefix = f"%instances/{callsign}/%"
    try:
        rows = _rows(
            """
            SELECT id, filename, object_key, storage_path, content_type, file_type,
                   created_at, sha256, construct_id, metadata, content
            FROM vault_files
            WHERE (
                construct_id = %s
                OR lower(coalesce(filename, '') || ' ' || coalesce(object_key, '') || ' ' || coalesce(storage_path, '')) LIKE %s
            )
            ORDER BY created_at ASC
            """,
            (callsign, like_prefix),
        )
    except Exception as exc:
        return _blocked(
            route,
            reason=f"VVAULT body database is unavailable or unreadable: {type(exc).__name__}",
            missing_fields=[],
            missing_tables=["ovvaults.vault_files"],
        )
    assets: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    identity: list[dict[str, Any]] = []
    for row in rows:
        path = (row.get("storage_path") or row.get("object_key") or row.get("filename") or "").lower()
        entry = _entry_from_file(row)
        if "/identity/" in path or path.endswith(".capsule") or "identity" in (row.get("content_type") or "").lower():
            identity.append(entry)
        elif "/assets/" in path or path.endswith((".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".avif")):
            assets.append(entry)
        else:
            documents.append(entry)
    payload: dict[str, Any] = {
        "construct_id": callsign,
        "counts": {"assets": len(assets), "documents": len(documents), "identity": len(identity)},
        "body_native_available": True,
    }
    if not folder or folder == "assets":
        payload["assets"] = assets
    if not folder or folder == "documents":
        payload["documents"] = documents
    if not folder or folder == "identity":
        payload["identity"] = identity
    return BodyResult(status="body_native", route=route, source_database=source_database_name(), payload=payload)


def transcript_body(construct_id: str) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/transcript/{callsign}"
    try:
        rows = _transcript_rows(callsign)
        if not rows:
            file_rows = _transcript_file_rows(callsign)
            rows = [
                {
                    "id": row.get("id"),
                    "title": row.get("filename") or row.get("object_key") or row.get("storage_path"),
                    "content": row.get("content"),
                    "created_at": row.get("created_at"),
                    "source_row_id": row.get("source_row_id"),
                    "source_hash": row.get("sha256"),
                }
                for row in file_rows
            ]
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body database is unavailable or unreadable: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.transcripts", "ovvaults.vault_files"])
    if not rows:
        return _blocked(route, reason="No materialized transcript content exists for this construct in the VVAULT body.", missing_fields=["transcripts.content(real)", "vault_files.content"], missing_tables=[])
    row = _select_transcript_read_row(callsign, rows)
    title = row.get("title") or f"chat_with_{callsign}.md"
    updated = row.get("materialized_at") or row.get("created_at")
    updated_text = updated.isoformat() if hasattr(updated, "isoformat") else updated
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "construct_id": callsign,
            "filename": str(title).rsplit("/", 1)[-1],
            "storage_path": title,
            "content": row.get("content") or "",
            "sha256": row.get("source_hash"),
            "updated_at": updated_text,
            "thread_id": f"{callsign}_chat_with_{callsign}",
            "title": display_name(callsign),
            "body_source": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def identity(construct_id: str) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/construct/{callsign}/identity"
    try:
        rows = _identity_file_rows(callsign)
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body database is unavailable or unreadable: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.vault_files"])
    if not rows:
        return _blocked(route, reason="No materialized identity content exists for this construct in the VVAULT body.", missing_fields=["vault_files.content", "vault_files.construct_id", "vault_files.metadata"], missing_tables=[])
    by_name = _pick_latest_by_basename(rows)
    prompt_text = _row_content(by_name, "prompt.txt")
    prompt_json = _jsonish(_row_content(by_name, "prompt.json"), {}) or {}
    metadata_json = _jsonish(_row_content(by_name, "metadata.json"), {}) or {}
    personality = _jsonish(_row_content(by_name, "personality.json"), None)
    definition_json = _jsonish(_row_content(by_name, "definition.json"), {}) or {}
    definition_text = _row_content(by_name, "definition.txt")
    voice_json = _jsonish(_row_content(by_name, "voice.json"), {}) or {}
    voice_md = _row_content(by_name, "voice.md")
    name = _first_text(
        [
            prompt_json.get("displayName") if isinstance(prompt_json, dict) else None,
            prompt_json.get("display_name") if isinstance(prompt_json, dict) else None,
            prompt_json.get("name") if isinstance(prompt_json, dict) else None,
            metadata_json.get("display_name") if isinstance(metadata_json, dict) else None,
            display_name(callsign),
        ],
        default=display_name(callsign),
    )
    description = _first_text([
        prompt_json.get("description") if isinstance(prompt_json, dict) else None,
        metadata_json.get("description") if isinstance(metadata_json, dict) else None,
    ], default=f"Helps you with your life problems.")
    instructions = _first_text([
        prompt_json.get("instructions") if isinstance(prompt_json, dict) else None,
        prompt_json.get("prompt") if isinstance(prompt_json, dict) else None,
    ])
    system_prompt = _first_text([
        prompt_json.get("system_prompt") if isinstance(prompt_json, dict) else None,
        prompt_json.get("prompt") if isinstance(prompt_json, dict) else None,
        prompt_text,
    ])
    definition = _first_text([
        definition_json.get("instructions") if isinstance(definition_json, dict) else None,
        definition_json.get("prompt") if isinstance(definition_json, dict) else None,
        definition_text,
    ])
    voice = _first_text([
        voice_md,
        voice_json.get("text") if isinstance(voice_json, dict) else None,
    ])
    starters = prompt_json.get("conversationStarters") if isinstance(prompt_json, dict) else []
    if not isinstance(starters, list):
        starters = []
    avatar_row = by_name.get("avatar.png")
    avatar_descriptor = None
    if avatar_row and _is_png_base64_content(avatar_row.get("content")):
        avatar_metadata = _metadata(avatar_row)
        avatar_path = avatar_row.get("storage_path") or avatar_row.get("object_key") or avatar_row.get("filename")
        avatar_descriptor = {
            "status": "present",
            "filename": avatar_row.get("filename") or avatar_path,
            "storagePath": avatar_path,
            "contentType": avatar_metadata.get("contentType") or avatar_metadata.get("mimeType") or "image/png",
            "mimeType": avatar_metadata.get("mimeType") or avatar_metadata.get("contentType") or "image/png",
            "sha256": avatar_row.get("sha256"),
            "content": avatar_row.get("content") if isinstance(avatar_row.get("content"), str) else "",
            "pngMagicOk": True,
            "body_source": "ovvaults.vault_files",
        }
    source_files = [
        _source_file_entry(row)
        for _name, row in sorted(by_name.items())
    ]
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "construct_id": callsign,
            "name": name,
            "displayName": name,
            "fullName": _first_text([prompt_json.get("fullName") if isinstance(prompt_json, dict) else None, name], default=name),
            "description": description,
            "instructions": instructions,
            "system_prompt": system_prompt,
            "conversation_starters": starters,
            "conversationStarters": starters,
            "conditioning": _row_content(by_name, "conditioning.txt"),
            "definition": definition,
            "voice": voice,
            "personality": personality,
            "avatar_descriptor": avatar_descriptor,
            "avatarDescriptor": avatar_descriptor,
            "source_files": source_files,
            "body_source": "ovvaults.vault_files",
            "body_native_available": True,
        },
    )


def memories(construct_id: str) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/construct/{callsign}/memories"
    try:
        transcript_rows = _transcript_rows(callsign)
        if not transcript_rows:
            transcript_rows = _transcript_file_rows(callsign)
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body database is unavailable or unreadable: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.transcripts", "ovvaults.vault_files"])
    memories_payload: list[dict[str, Any]] = []
    total_pairs = 0
    for file_index, row in enumerate(transcript_rows):
        pairs = _parse_markdown_pairs(_text(row.get("content")), callsign)
        total_pairs += len(pairs)
        if pairs:
            first = {**pairs[0], "tag": "first_exchange", "score": 100.0, "index": 0, "source": row.get("title") or row.get("filename") or "Transcript"}
            memories_payload.append(first)
            if len(pairs) > 1:
                last = {**pairs[-1], "tag": "last_exchange", "score": 99.0, "index": len(pairs) - 1, "source": first["source"]}
                memories_payload.append(last)
        elif row.get("content"):
            memories_payload.append({
                "user": "",
                "construct": _text(row.get("content"))[:1200],
                "tag": "transcript_excerpt",
                "score": 1.0,
                "index": file_index,
                "source": row.get("title") or row.get("filename") or "Transcript",
            })
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "construct_id": callsign,
            "memories": memories_payload[:10],
            "total_pairs": total_pairs,
            "transcript_files": len(transcript_rows),
            "chronological": True,
            "query_terms": [],
            "ledger_available": False,
            "body_source": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def _slugify_hydro_project_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "project"


def _infer_project_name_from_root_path(root_path: str | None) -> str | None:
    trimmed = str(root_path or "").strip().rstrip("/")
    if not trimmed:
        return None
    name = trimmed.rsplit("/", 1)[-1]
    return name if name not in {"", "."} else None


def _transcript_target(construct_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    callsign = normalize_callsign(construct_id)
    data = payload or {}
    project_name = data.get("projectName") or data.get("project_name") or _infer_project_name_from_root_path(data.get("rootPath") or data.get("root_path"))
    if callsign == "hydro-001" and project_name:
        project_slug = _slugify_hydro_project_name(str(project_name))
        filename = f"{project_slug}_hydro_chat.md"
        storage_path = f"instances/{callsign}/code/{filename}"
        title = f"Hydro Ask - {project_name}"
        thread_id = f"{callsign}_{project_slug}_hydro_chat"
    else:
        filename = f"chat_with_{callsign}.md"
        storage_path = f"instances/{callsign}/chatty/{filename}"
        title = f"Chat with {display_name(callsign)}"
        thread_id = f"{callsign}_chat_with_{callsign}"
    return {
        "construct_id": callsign,
        "filename": filename,
        "storage_path": storage_path,
        "title": title,
        "thread_id": thread_id,
        "project_name": project_name,
    }


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _invalid(route: str, reason: str, *, error_code: str = "VVAULT_BODY_INVALID_REQUEST") -> BodyResult:
    return BodyResult(
        status="body_invalid",
        route=route,
        source_database=source_database_name(),
        http_status=400,
        payload={
            "error_code": error_code,
            "reason": reason,
            "body_native_available": True,
        },
    )


def _select_writable_transcript(cur: Any, callsign: str, target: dict[str, Any]) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, user_id, title, content, source_hash, created_at
        FROM transcripts
        WHERE lower(title) = lower(%s)
          AND content IS NOT NULL
          AND content <> ''
          AND content <> %s
        ORDER BY created_at DESC
        LIMIT 1
        FOR UPDATE
        """,
        (target["storage_path"], PLACEHOLDER_TRANSCRIPT_CONTENT),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    cur.execute(
        """
        SELECT id, user_id, title, content, source_hash, created_at
        FROM transcripts
        WHERE lower(title) LIKE %s
          AND content IS NOT NULL
          AND content <> ''
          AND content <> %s
        ORDER BY created_at DESC
        LIMIT 1
        FOR UPDATE
        """,
        (f"%instances/{callsign}/%chat_with_{callsign}%", PLACEHOLDER_TRANSCRIPT_CONTENT),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _commit_transcript_content(construct_id: str, content_builder: Any, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    callsign = normalize_callsign(construct_id)
    target = _transcript_target(callsign, payload)
    with _connect() as conn:
        with conn.cursor() as cur:
            row = _select_writable_transcript(cur, callsign, target)
            if not row:
                conn.rollback()
                return None, "No writable materialized transcript row exists for this construct in ovvaults.transcripts."
            current_content = row.get("content") or ""
            new_content = content_builder(current_content, row, target)
            if not isinstance(new_content, str) or not new_content:
                conn.rollback()
                return None, "Transcript write produced empty content; refusing to persist."
            new_hash = _sha256_text(new_content)
            cur.execute(
                """
                UPDATE transcripts
                SET content = %s,
                    source_hash = %s,
                    materialized_at = now()
                WHERE id = %s
                RETURNING id, user_id, title, content, source_hash, materialized_at, created_at
                """,
                (new_content, new_hash, row["id"]),
            )
            updated = dict(cur.fetchone())
        conn.commit()
    return updated, None


def update_transcript_body(construct_id: str, payload: dict[str, Any] | None = None) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/transcript/{callsign}"
    data = payload or {}
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        return _invalid(route, "content is required for body-native transcript replacement")
    try:
        updated, blocker = _commit_transcript_content(callsign, lambda _current, _row, _target: content, data)
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body transcript replacement failed: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.transcripts"])
    if not updated:
        return _blocked(route, reason=blocker or "No writable transcript row exists.", missing_fields=["transcripts.content(real)"], missing_tables=[])
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "action": "updated",
            "construct_id": callsign,
            "filename": str(updated.get("title") or "").rsplit("/", 1)[-1],
            "storage_path": updated.get("title"),
            "thread_id": _transcript_target(callsign, data)["thread_id"],
            "sha256": updated.get("source_hash"),
            "content_length": len(updated.get("content") or ""),
            "persistence_owner": "ovvaults.transcripts",
            "body_source": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def _format_transcript_message(callsign: str, role: str, content: str, timestamp: str, attachments: list[dict[str, Any]] | None = None) -> str:
    if role == "user":
        role_label = "**User**"
    elif role == "assistant":
        role_label = f"**{display_name(callsign)}**"
    else:
        role_label = "**System**"
    attachment_block = ""
    if attachments:
        lines: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            filename = attachment.get("filename") or "unknown"
            mime = attachment.get("mime") or attachment.get("content_type") or "application/octet-stream"
            sha = attachment.get("sha256") or ""
            lines.append(f"- {filename} ({mime})")
            if sha:
                lines.append(f"  - sha256: {sha}")
        if lines:
            attachment_block = "Attachments:\n" + "\n".join(lines) + "\n\n"
    return f"\n\n---\n\n{role_label} ({timestamp}):\n\n{attachment_block}{content}"


def append_transcript_message(construct_id: str, payload: dict[str, Any] | None = None) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = f"/api/chatty/transcript/{callsign}/message"
    data = payload or {}
    role = str(data.get("role") or "user").strip().lower()
    content = data.get("content")
    attachments = data.get("attachments") or []
    if role not in {"user", "assistant", "system"}:
        return _invalid(route, "role must be 'user', 'assistant', or 'system'")
    if not isinstance(content, str):
        content = ""
    if not content.strip() and not attachments:
        return _invalid(route, "content or attachments are required for body-native transcript append")
    timestamp = str(data.get("timestamp") or datetime.now(timezone.utc).isoformat())
    formatted = _format_transcript_message(callsign, role, content, timestamp, attachments if isinstance(attachments, list) else [])
    try:
        updated, blocker = _commit_transcript_content(callsign, lambda current, _row, _target: current + formatted, data)
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body transcript append failed: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.transcripts"])
    if not updated:
        return _blocked(route, reason=blocker or "No writable transcript row exists.", missing_fields=["transcripts.content(real)"], missing_tables=[])
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "action": "appended",
            "construct_id": callsign,
            "filename": str(updated.get("title") or "").rsplit("/", 1)[-1],
            "storage_path": updated.get("title"),
            "thread_id": _transcript_target(callsign, data)["thread_id"],
            "role": role,
            "message_length": len(content),
            "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
            "total_length": len(updated.get("content") or ""),
            "sha256": updated.get("source_hash"),
            "persistence_owner": "ovvaults.transcripts",
            "body_source": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def append_transcript_exchange(construct_id: str, user_content: str, assistant_content: str, payload: dict[str, Any] | None = None) -> BodyResult:
    callsign = normalize_callsign(construct_id)
    route = "/api/chatty/message"
    data = payload or {}
    if not isinstance(user_content, str) or not user_content.strip():
        return _invalid(route, "message is required")
    if not isinstance(assistant_content, str) or not assistant_content.strip():
        return _generation_blocked(route, "local generation returned empty response")
    user_timestamp = str(data.get("timestamp") or datetime.now(timezone.utc).isoformat())
    assistant_timestamp = datetime.now(timezone.utc).isoformat()
    user_block = _format_transcript_message(callsign, "user", user_content, user_timestamp)
    assistant_block = _format_transcript_message(callsign, "assistant", assistant_content, assistant_timestamp)
    try:
        updated, blocker = _commit_transcript_content(callsign, lambda current, _row, _target: current + user_block + assistant_block, data)
    except Exception as exc:
        return _blocked(route, reason=f"VVAULT body message persistence failed: {type(exc).__name__}", missing_fields=[], missing_tables=["ovvaults.transcripts"])
    if not updated:
        return _blocked(route, reason=blocker or "No writable transcript row exists.", missing_fields=["transcripts.content(real)"], missing_tables=[])
    return BodyResult(
        status="body_native",
        route=route,
        source_database=source_database_name(),
        payload={
            "response": assistant_content,
            "constructId": callsign,
            "construct_id": callsign,
            "constructName": display_name(callsign),
            "timestamp": user_timestamp,
            "thread_id": _transcript_target(callsign, data)["thread_id"],
            "filename": str(updated.get("title") or "").rsplit("/", 1)[-1],
            "storage_path": updated.get("title"),
            "sha256": updated.get("source_hash"),
            "total_length": len(updated.get("content") or ""),
            "persistence_owner": "ovvaults.transcripts",
            "body_source": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def _generation_blocked(route: str, reason: str) -> BodyResult:
    return BodyResult(
        status="generation_blocked",
        route=route,
        source_database=source_database_name(),
        http_status=503,
        payload={
            "error_code": "VVAULT_GENERATION_BLOCKED",
            "reason": reason,
            "persistence_owner": "ovvaults.transcripts",
            "body_native_available": True,
        },
    )


def _generate_assistant_response(construct_id: str, user_message: str) -> str:
    callsign = normalize_callsign(construct_id)
    identity_result = identity(callsign)
    if identity_result.status != "body_native":
        raise RuntimeError(identity_result.payload.get("reason") or "body-native identity is unavailable")
    system_prompt = _first_text([
        identity_result.payload.get("system_prompt"),
        identity_result.payload.get("instructions"),
        identity_result.payload.get("definition"),
        f"You are {identity_result.payload.get('name') or display_name(callsign)}.",
    ])
    endpoint = os.environ.get("VVAULT_CHATTY_OLLAMA_URL", "http://localhost:11434/api/generate")
    model = os.environ.get("VVAULT_CHATTY_MESSAGE_MODEL", "phi3:latest")
    import requests

    response = requests.post(
        endpoint,
        json={"model": model, "prompt": user_message, "system": system_prompt, "stream": False},
        timeout=float(os.environ.get("VVAULT_CHATTY_GENERATION_TIMEOUT", "60")),
    )
    if not response.ok:
        raise RuntimeError(f"local generation returned HTTP {response.status_code}")
    data = response.json()
    assistant_response = data.get("response")
    if not isinstance(assistant_response, str) or not assistant_response.strip():
        raise RuntimeError("local generation returned empty response")
    return assistant_response.strip()


def message(construct_id: str | None = None, payload: dict[str, Any] | None = None) -> BodyResult:
    route = "/api/chatty/message"
    data = payload or {}
    callsign = normalize_callsign(construct_id or data.get("constructId") or "")
    user_message = data.get("message")
    if not callsign:
        return _invalid(route, "constructId is required")
    if not isinstance(user_message, str) or not user_message.strip():
        return _invalid(route, "message is required")
    try:
        assistant_response = _generate_assistant_response(callsign, user_message)
    except Exception as exc:
        return _generation_blocked(
            route,
            reason=f"Chatty message generation is blocked before persistence: {type(exc).__name__}: {exc}",
        )
    return append_transcript_exchange(callsign, user_message, assistant_response, data)
