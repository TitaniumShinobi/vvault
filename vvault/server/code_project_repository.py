"""VVAULT-owned Code project persistence.

Code project inventory is canonical in OVVAULTS vault_files. Local Code
workspaces are rebuildable materialization caches keyed by projectInstanceId.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

try:
    import chatty_body_service
except ImportError:
    from vvault.server import chatty_body_service


PROJECT_OWNER = "ovvaults.vault_files"
TRANSCRIPT_OWNER = "ovvaults.transcripts"
PROJECT_FILE_TYPE = "code-project"
PROJECT_FILE_ROOT = "code/projects"
CODE_SOURCE_PRODUCT = "code"
INTERNAL_FILE_NAMES = {"project.json", "state.json"}
INTERNAL_TOP_LEVEL_DIRS = {"runs", "recycle-bin", "node_modules", ".git", "dist"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row or {})
    for key in ("id", "user_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    for key in ("created_at", "updated_at", "materialized_at"):
        value = data.get(key)
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _project_instance_id(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip(".-_")


def _relative_path(value: Any) -> str:
    raw = _text(value).replace("\\", "/").lstrip("/")
    parts = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("relativePath may not escape project root")
        parts.append(part)
    return "/".join(parts)


def is_internal_code_project_path(relative_path: str) -> bool:
    normalized = _relative_path(relative_path)
    if not normalized:
        return True
    if normalized in INTERNAL_FILE_NAMES:
        return True
    top = normalized.split("/", 1)[0]
    return top in INTERNAL_TOP_LEVEL_DIRS


def _project_storage_path(project_instance_id: str) -> str:
    return f"{PROJECT_FILE_ROOT}/{project_instance_id}/project.json"


def _file_storage_path(project_instance_id: str, relative_path: str) -> str:
    return f"{PROJECT_FILE_ROOT}/{project_instance_id}/files/{relative_path}"


def _project_metadata(project_instance_id: str, project: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing_meta = _metadata(existing.get("metadata") if existing else None)
    transcript_ids = project.get("transcriptIds")
    if not isinstance(transcript_ids, list):
        transcript_ids = existing_meta.get("transcriptIds") if isinstance(existing_meta.get("transcriptIds"), list) else []
    return {
        **existing_meta,
        "sourceProduct": CODE_SOURCE_PRODUCT,
        "projectInstanceId": project_instance_id,
        "transcriptIds": [str(value) for value in transcript_ids if str(value).strip()],
        "fileRoot": f"{PROJECT_FILE_ROOT}/{project_instance_id}/files",
        "legacyHydroTranscriptSlug": project.get("legacyHydroTranscriptSlug"),
        "transcriptIdentityVersion": project.get("transcriptIdentityVersion") or 2,
    }


def _project_payload(project_instance_id: str, raw_project: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing_content = _json(existing.get("content") if existing else None)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    name = _text(raw_project.get("name")) or _text(existing_content.get("name")) or project_instance_id
    created_at = raw_project.get("createdAt") if isinstance(raw_project.get("createdAt"), (int, float)) else existing_content.get("createdAt")
    last_opened_at = raw_project.get("lastOpenedAt") if isinstance(raw_project.get("lastOpenedAt"), (int, float)) else existing_content.get("lastOpenedAt")
    project = {
        **existing_content,
        **raw_project,
        "id": project_instance_id,
        "projectId": project_instance_id,
        "projectInstanceId": project_instance_id,
        "rootPath": f".hydro/workspaces/{project_instance_id}",
        "name": name,
        "source": _text(raw_project.get("source")) or _text(existing_content.get("source")) or "unknown",
        "createdAt": int(created_at or now_ms),
        "lastOpenedAt": int(last_opened_at or now_ms),
    }
    project["transcriptIdentityVersion"] = project.get("transcriptIdentityVersion") or 2
    return project


class CodeProjectRepository:
    def _connect(self):
        return chatty_body_service._connect()

    def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    def _columns(self, *, include_content: bool = True) -> str:
        content = ", content" if include_content else ""
        return f"""
            id::text AS id,
            user_id::text AS user_id,
            bucket,
            object_key,
            filename,
            content_type,
            size_bytes,
            sha256,
            created_at,
            metadata,
            construct_id,
            storage_path,
            file_type,
            source_table,
            source_row_id,
            source_filename,
            source_storage_path,
            materialized_at,
            is_system,
            updated_at
            {content}
        """

    def list_projects(self, *, user_id: str) -> list[dict[str, Any]]:
        rows = self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND coalesce(is_system, false) = false
              AND file_type = %s
              AND storage_path ILIKE 'code/projects/%%/project.json'
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (user_id, PROJECT_FILE_TYPE),
        )
        projects_by_instance: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = _metadata(row.get("metadata"))
            content = _json(row.get("content"))
            project_instance_id = _project_instance_id(
                metadata.get("projectInstanceId") or content.get("projectInstanceId")
            )
            if not project_instance_id:
                continue
            project = _project_payload(project_instance_id, content, row)
            project["vvaultFileId"] = row.get("id")
            project["vvaultStoragePath"] = row.get("storage_path")
            current = projects_by_instance.get(project_instance_id)
            if not current or int(project.get("lastOpenedAt") or 0) >= int(current.get("lastOpenedAt") or 0):
                projects_by_instance[project_instance_id] = project
        return sorted(
            projects_by_instance.values(),
            key=lambda item: (-(int(item.get("lastOpenedAt") or 0)), str(item.get("name") or "")),
        )

    def get_project(self, *, user_id: str, project_instance_id: str) -> dict[str, Any] | None:
        project_instance_id = _project_instance_id(project_instance_id)
        if not project_instance_id:
            return None
        row = self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND coalesce(is_system, false) = false
              AND storage_path = %s
              AND file_type = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
            """,
            (user_id, _project_storage_path(project_instance_id), PROJECT_FILE_TYPE),
        )
        if not row:
            return None
        project = _project_payload(project_instance_id, _json(row.get("content")), row)
        project["vvaultFileId"] = row.get("id")
        project["vvaultStoragePath"] = row.get("storage_path")
        return project

    def upsert_project(self, *, user_id: str, project: dict[str, Any]) -> dict[str, Any]:
        project_instance_id = _project_instance_id(project.get("projectInstanceId") or project.get("projectId") or project.get("id"))
        if not project_instance_id:
            raise ValueError("projectInstanceId is required")
        storage_path = _project_storage_path(project_instance_id)
        existing = self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND storage_path = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
            """,
            (user_id, storage_path),
        )
        payload = _project_payload(project_instance_id, project, existing)
        metadata = _project_metadata(project_instance_id, payload, existing)
        content = json.dumps(payload, sort_keys=True)
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _utc_now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type,
                        size_bytes, sha256, created_at, content, metadata,
                        construct_id, storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, 'vvault-local', %s, %s, 'application/json',
                            %s, %s, %s, %s, %s::jsonb,
                            NULL, %s, %s, false, %s)
                    ON CONFLICT (bucket, object_key) DO UPDATE
                    SET filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size_bytes = EXCLUDED.size_bytes,
                        sha256 = EXCLUDED.sha256,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        storage_path = EXCLUDED.storage_path,
                        file_type = EXCLUDED.file_type,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id::text AS id
                    """,
                    (
                        user_id,
                        f"users/{user_id}/{storage_path}",
                        storage_path,
                        len(content.encode("utf-8")),
                        sha256,
                        now,
                        content,
                        json.dumps(metadata),
                        storage_path,
                        PROJECT_FILE_TYPE,
                        now,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        saved = self.get_project(user_id=user_id, project_instance_id=project_instance_id) or payload
        saved["vvaultFileId"] = str(row["id"]) if row else saved.get("vvaultFileId")
        return saved

    def list_files(self, *, user_id: str, project_instance_id: str) -> list[dict[str, Any]]:
        project_instance_id = _project_instance_id(project_instance_id)
        prefix = f"{PROJECT_FILE_ROOT}/{project_instance_id}/files/%"
        rows = self._fetch(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE user_id = %s
              AND coalesce(is_system, false) = false
              AND storage_path ILIKE %s
            ORDER BY storage_path ASC
            """,
            (user_id, prefix),
        )
        files = []
        marker = f"{PROJECT_FILE_ROOT}/{project_instance_id}/files/"
        for row in rows:
            storage_path = str(row.get("storage_path") or "")
            if not storage_path.startswith(marker):
                continue
            relative_path = storage_path[len(marker):]
            files.append({
                "id": row.get("id"),
                "relativePath": relative_path,
                "storagePath": storage_path,
                "contentType": row.get("content_type"),
                "sizeBytes": row.get("size_bytes"),
                "sha256": row.get("sha256"),
                "updatedAt": row.get("updated_at") or row.get("created_at"),
            })
        return files

    def read_file(self, *, user_id: str, project_instance_id: str, relative_path: str) -> dict[str, Any] | None:
        relative_path = _relative_path(relative_path)
        if is_internal_code_project_path(relative_path):
            raise ValueError("internal Code workspace files are not canonical project files")
        storage_path = _file_storage_path(_project_instance_id(project_instance_id), relative_path)
        row = self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND coalesce(is_system, false) = false
              AND storage_path = %s
            LIMIT 1
            """,
            (user_id, storage_path),
        )
        if not row:
            return None
        return {
            "id": row.get("id"),
            "relativePath": relative_path,
            "storagePath": storage_path,
            "content": row.get("content") or "",
            "contentType": row.get("content_type"),
            "sha256": row.get("sha256"),
            "updatedAt": row.get("updated_at") or row.get("created_at"),
        }

    def upsert_file(self, *, user_id: str, project_instance_id: str, relative_path: str, content: str, content_type: str = "text/plain") -> dict[str, Any]:
        project_instance_id = _project_instance_id(project_instance_id)
        relative_path = _relative_path(relative_path)
        if not project_instance_id:
            raise ValueError("projectInstanceId is required")
        if is_internal_code_project_path(relative_path):
            raise ValueError("internal Code workspace files are not canonical project files")
        storage_path = _file_storage_path(project_instance_id, relative_path)
        text = content if isinstance(content, str) else str(content or "")
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = _utc_now_iso()
        metadata = {
            "sourceProduct": CODE_SOURCE_PRODUCT,
            "projectInstanceId": project_instance_id,
            "relativePath": relative_path,
            "fileRoot": f"{PROJECT_FILE_ROOT}/{project_instance_id}/files",
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type,
                        size_bytes, sha256, created_at, content, metadata,
                        construct_id, storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, 'vvault-local', %s, %s, %s,
                            %s, %s, %s, %s, %s::jsonb,
                            NULL, %s, 'code-project-file', false, %s)
                    ON CONFLICT (bucket, object_key) DO UPDATE
                    SET filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size_bytes = EXCLUDED.size_bytes,
                        sha256 = EXCLUDED.sha256,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        storage_path = EXCLUDED.storage_path,
                        file_type = EXCLUDED.file_type,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id::text AS id
                    """,
                    (
                        user_id,
                        f"users/{user_id}/{storage_path}",
                        storage_path,
                        content_type or "text/plain",
                        len(text.encode("utf-8")),
                        sha256,
                        now,
                        text,
                        json.dumps(metadata),
                        storage_path,
                        now,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            "id": str(row["id"]) if row else None,
            "relativePath": relative_path,
            "storagePath": storage_path,
            "sha256": sha256,
            "updatedAt": now,
        }

    def delete_file(self, *, user_id: str, project_instance_id: str, relative_path: str) -> bool:
        relative_path = _relative_path(relative_path)
        storage_path = _file_storage_path(_project_instance_id(project_instance_id), relative_path)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM vault_files
                    WHERE user_id = %s
                      AND coalesce(is_system, false) = false
                      AND storage_path = %s
                    """,
                    (user_id, storage_path),
                )
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def list_transcript_links(self, *, project_instance_id: str) -> list[dict[str, Any]]:
        project_instance_id = _project_instance_id(project_instance_id)
        if not project_instance_id:
            return []
        return self._fetch(
            """
            SELECT id::text AS id, title, created_at, materialized_at, source_row_id, source_hash
            FROM transcripts
            WHERE lower(coalesce(title, '') || ' ' || coalesce(source_row_id::text, '') || ' ' || coalesce(source_hash, ''))
                  LIKE %s
            ORDER BY coalesce(materialized_at, created_at) DESC
            """,
            (f"%{project_instance_id.lower()}%",),
        )
