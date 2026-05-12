"""VVAULT-native vault file persistence.

This module owns runtime vault file rows for the Flask backend. It talks only
to the local/imported OVVAULTS Postgres body and optional VVAULT-native
S3-compatible object storage. Supabase table/storage clients are intentionally
out of scope here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from typing import Any

try:
    import chatty_body_service
    import vvault_auth_repository
except ImportError:  # Package import path used by pytest.
    from vvault.server import chatty_body_service, vvault_auth_repository

try:
    from packages.storage.client import StorageClient
except Exception:  # Optional runtime dependency/config.
    StorageClient = None  # type: ignore[assignment]


FILE_OWNER = "ovvaults.vault_files"
STORAGE_OWNER = "vvault_native_s3"
DEFAULT_BUCKET = "vvault-local"
SYSTEM_USER_EMAIL = "system@vvault.local"


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
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"raw": value}
    return {"value": value}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, indent=2, default=str)


def _content_type_for(record: dict[str, Any], filename: str) -> str:
    explicit = record.get("content_type") or record.get("file_type")
    if isinstance(explicit, str) and "/" in explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _file_type_for(record: dict[str, Any], content_type: str) -> str:
    explicit = record.get("file_type") or record.get("content_type")
    return str(explicit or content_type or "application/octet-stream")


def _object_key_for(record: dict[str, Any], logical_path: str, user_id: str | None, is_system: bool) -> str:
    explicit = str(record.get("object_key") or "").strip()
    if explicit:
        return explicit
    if is_system:
        return f"system/{logical_path}".strip("/")
    if user_id:
        return f"users/{user_id}/{logical_path}".strip("/")
    return logical_path.strip("/")


class VVaultFileRepository:
    def __init__(self, *, auth_repository: vvault_auth_repository.VVaultAuthRepository | None = None) -> None:
        self.auth_repository = auth_repository or vvault_auth_repository.VVaultAuthRepository()

    def _connect(self):
        return chatty_body_service._connect()

    def _system_user_id(self) -> str:
        user = self.auth_repository.ensure_external_user(
            email=SYSTEM_USER_EMAIL,
            name="VVAULT System",
            role="system",
        )
        return str(user["id"])

    def _columns(self, *, include_content: bool = True) -> str:
        content_column = ", content" if include_content else ""
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
            {content_column}
        """

    def healthcheck(self) -> dict[str, Any]:
        status = {
            "ready": False,
            "status": "unhealthy",
            "owner": FILE_OWNER,
            "checks": {"vault_files_readable": False, "runtime_columns": False},
            "source_database": chatty_body_service.source_database_name(),
        }
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, content, metadata, construct_id, storage_path,
                               file_type, is_system, updated_at
                        FROM vault_files
                        LIMIT 1
                        """
                    )
                    status["checks"]["vault_files_readable"] = True
                    status["checks"]["runtime_columns"] = True
            status["ready"] = True
            status["status"] = "healthy"
        except Exception as exc:
            status["error_code"] = type(exc).__name__
        return status

    def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    def list_for_browser(self, *, user_id: str | None, is_admin: bool, requested_path: str = "") -> list[dict[str, Any]]:
        normalized_path = str(requested_path or "").strip().strip("/")
        scope = ""
        params: list[Any] = []
        if not is_admin:
            scope = "AND user_id = %s AND coalesce(is_system, false) = false"
            params.append(user_id)

        if normalized_path == "instances":
            return []

        if normalized_path.startswith("instances/"):
            parts = normalized_path.split("/")
            if len(parts) < 2 or not parts[1]:
                return []
            params.insert(0, parts[1])
            return self._fetch(
                f"""
                SELECT {self._columns(include_content=False)}
                FROM vault_files
                WHERE construct_id = %s {scope}
                ORDER BY coalesce(updated_at, created_at) DESC
                """,
                tuple(params),
            )

        if normalized_path:
            top_level = normalized_path.split("/", 1)[0]
            if top_level not in {"library", "account", "system"}:
                return []
            prefix = f"{normalized_path}%"
            params = [prefix, prefix, *params]
            return self._fetch(
                f"""
                SELECT {self._columns(include_content=False)}
                FROM vault_files
                WHERE (filename ILIKE %s OR storage_path ILIKE %s) {scope}
                ORDER BY coalesce(updated_at, created_at) DESC
                """,
                tuple(params),
            )

        if is_admin:
            return self._fetch(
                f"""
                SELECT {self._columns(include_content=False)}
                FROM vault_files
                ORDER BY coalesce(updated_at, created_at) DESC
                """
            )
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE user_id = %s AND coalesce(is_system, false) = false
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (user_id,),
        )

    def get_by_id(self, file_id: str) -> dict[str, Any] | None:
        return self._one(
            f"SELECT {self._columns(include_content=True)} FROM vault_files WHERE id = %s",
            (file_id,),
        )

    def get_by_ids(self, file_ids: list[str]) -> list[dict[str, Any]]:
        ids = [str(file_id).strip() for file_id in file_ids if str(file_id).strip()]
        if not ids:
            return []
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE id = ANY(%s::uuid[])
            ORDER BY created_at DESC
            """,
            (ids,),
        )

    def find_exact(
        self,
        *,
        filename: str,
        storage_path: str,
        construct_id: str = "",
        user_id: str | None,
        is_admin: bool,
    ) -> dict[str, Any] | None:
        paths = [path.strip() for path in {filename, storage_path} if isinstance(path, str) and path.strip()]
        if not paths:
            return None
        conditions = ["(filename = ANY(%s) OR storage_path = ANY(%s))"]
        params: list[Any] = [paths, paths]
        if construct_id:
            conditions.append("construct_id = %s")
            params.append(construct_id)
        if not is_admin and user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        return self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE {' AND '.join(conditions)}
            ORDER BY coalesce(updated_at, created_at) DESC, length(coalesce(content, '')) DESC
            LIMIT 1
            """,
            tuple(params),
        )

    def list_knowledge_files(self, *, construct_id: str, user_id: str) -> list[dict[str, Any]]:
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE construct_id = %s AND user_id = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (construct_id, user_id),
        )

    def list_simdrive_files(self, *, construct_id: str, user_id: str, include_content: bool = False) -> list[dict[str, Any]]:
        prefix = f"instances/{construct_id}/simDrive/%"
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=include_content)}
            FROM vault_files
            WHERE construct_id = %s
              AND user_id = %s
              AND filename ILIKE %s
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (construct_id, user_id, prefix),
        )

    def get_user_file(self, *, file_id: str, construct_id: str | None = None, user_id: str | None = None) -> dict[str, Any] | None:
        conditions = ["id = %s"]
        params: list[Any] = [file_id]
        if construct_id:
            conditions.append("construct_id = %s")
            params.append(construct_id)
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        return self._one(
            f"SELECT {self._columns(include_content=True)} FROM vault_files WHERE {' AND '.join(conditions)}",
            tuple(params),
        )

    def find_by_path(self, *, construct_id: str, user_id: str, filename: str) -> dict[str, Any] | None:
        return self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE construct_id = %s AND user_id = %s AND filename = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
            """,
            (construct_id, user_id, filename),
        )

    def list_construct_identity_rows(self, *, callsign: str, bare_name: str, user_id: str | None) -> list[dict[str, Any]]:
        params: list[Any] = [callsign, bare_name, f"instances/{callsign}/identity/%"]
        user_clause = ""
        if user_id:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND filename ILIKE %s
              {user_clause}
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            tuple(params),
        )

    def list_user_identity_rows(self, *, user_id: str) -> list[dict[str, Any]]:
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND filename ILIKE 'instances/%%/identity/%%'
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (user_id,),
        )

    def list_construct_file_rows(self, *, callsign: str, bare_name: str, user_id: str | None, include_content: bool = False) -> list[dict[str, Any]]:
        params: list[Any] = [callsign, bare_name, f"instances/{callsign}/%"]
        user_clause = ""
        if user_id:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=include_content)}
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND filename ILIKE %s
              {user_clause}
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            tuple(params),
        )

    def query_transcript_rows_for_preview(self, *, callsign: str, bare_name: str, limit: int) -> list[dict[str, Any]]:
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE construct_id IN (%s, %s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (callsign, bare_name, max(limit * 2, 4)),
        )

    def first_owner_for_construct(self, construct_id: str) -> str | None:
        row = self._one(
            """
            SELECT user_id::text AS user_id
            FROM vault_files
            WHERE construct_id = %s
              AND user_id IS NOT NULL
            LIMIT 1
            """,
            (construct_id,),
        )
        return row.get("user_id") if row else None

    def get_system_file(self, storage_path: str) -> dict[str, Any] | None:
        return self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE coalesce(is_system, false) = true
              AND storage_path = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
            """,
            (storage_path,),
        )

    def delete_for_user(self, *, file_id: str, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM vault_files
                    WHERE id = %s AND user_id = %s
                    RETURNING {self._columns(include_content=False)}
                    """,
                    (file_id, user_id),
                )
                row = cur.fetchone()
            conn.commit()
        return _row_to_dict(row) if row else None

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        logical_path = str(record.get("storage_path") or record.get("filename") or "").strip()
        if not logical_path:
            raise ValueError("Vault file record is missing filename/storage_path")

        is_system = bool(record.get("is_system", False))
        user_id = str(record.get("user_id") or "").strip() or None
        if is_system and not user_id:
            user_id = self._system_user_id()
        if not user_id:
            raise ValueError("Vault file record is missing user_id")

        content = _text(record.get("content"))
        metadata = _metadata(record.get("metadata"))
        filename = logical_path
        storage_path = logical_path
        content_type = _content_type_for(record, filename)
        file_type = _file_type_for(record, content_type)
        sha256 = str(record.get("sha256") or hashlib.sha256(content.encode("utf-8")).hexdigest())
        now = str(record.get("updated_at") or _utc_now_iso())
        created_at = str(record.get("created_at") or now)
        bucket = str(record.get("bucket") or DEFAULT_BUCKET)
        object_key = _object_key_for(record, logical_path, user_id, is_system)
        size_bytes = int(record.get("size_bytes") or len(content.encode("utf-8")))

        existing = self._one(
            """
            SELECT id::text AS id, created_at
            FROM vault_files
            WHERE filename = %s
              AND construct_id IS NOT DISTINCT FROM %s
              AND user_id = %s
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
            """,
            (filename, record.get("construct_id"), user_id),
        )

        with self._connect() as conn:
            with conn.cursor() as cur:
                if existing:
                    cur.execute(
                        """
                        UPDATE vault_files
                        SET bucket = %s,
                            object_key = %s,
                            filename = %s,
                            content_type = %s,
                            size_bytes = %s,
                            sha256 = %s,
                            content = %s,
                            metadata = %s::jsonb,
                            construct_id = %s,
                            storage_path = %s,
                            file_type = %s,
                            is_system = %s,
                            updated_at = %s
                        WHERE id = %s
                        RETURNING id::text AS id
                        """,
                        (
                            bucket,
                            object_key,
                            filename,
                            content_type,
                            size_bytes,
                            sha256,
                            content,
                            json.dumps(metadata),
                            record.get("construct_id"),
                            storage_path,
                            file_type,
                            is_system,
                            now,
                            existing["id"],
                        ),
                    )
                    row = cur.fetchone()
                    action = "updated"
                else:
                    cur.execute(
                        """
                        INSERT INTO vault_files (
                            user_id, bucket, object_key, filename, content_type,
                            size_bytes, sha256, created_at, content, metadata,
                            construct_id, storage_path, file_type, is_system, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                        RETURNING id::text AS id
                        """,
                        (
                            user_id,
                            bucket,
                            object_key,
                            filename,
                            content_type,
                            size_bytes,
                            sha256,
                            created_at,
                            content,
                            json.dumps(metadata),
                            record.get("construct_id"),
                            storage_path,
                            file_type,
                            is_system,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                    action = "created"
            conn.commit()

        return {
            "action": action,
            "id": str(row["id"]) if row else None,
            "deduped": 0,
            "path": logical_path,
        }

    def load_text(self, row: dict[str, Any] | None) -> str:
        if not row:
            return ""
        content = row.get("content")
        if isinstance(content, str) and content:
            return content
        stored = self.load_bytes(row)
        if not stored:
            return ""
        try:
            return stored[0].decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def load_bytes(self, row: dict[str, Any] | None) -> tuple[bytes, str] | None:
        if not row:
            return None
        bucket = row.get("bucket")
        object_key = row.get("object_key")
        if not bucket or not object_key or StorageClient is None:
            return None
        try:
            stored = StorageClient().download_bytes(bucket=str(bucket), object_key=str(object_key))
            return stored.body, stored.content_type
        except Exception:
            return None

    def binary_data_url(self, row: dict[str, Any] | None, mime: str) -> str | None:
        stored = self.load_bytes(row)
        if not stored:
            return None
        body, content_type = stored
        encoded = base64.b64encode(body).decode("utf-8")
        return f"data:{content_type or mime};base64,{encoded}"
