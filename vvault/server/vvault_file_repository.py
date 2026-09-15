"""VVAULT-native vault file persistence."""

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
from vvault.server.artifact_contract import CAPSULE_ARTIFACT_ID, storage_path as artifact_storage_path
from vvault.server.construct_taxonomy import canonical_category
from vvault.server.projection_classification import PROJECTABLE_METADATA_SQL

FILE_OWNER = "ovvaults.vault_files"
STORAGE_OWNER = "vvault_native_s3"
DEFAULT_BUCKET = "vvault-local"
SYSTEM_USER_EMAIL = "system@vvault.local"
FORGE_SOURCE_PATHS = {
    "identity_prompt": "identity/prompt.json",
    "config_metadata": "config/metadata.json",
    "identity_definition": "identity/definition.json",
    "identity_conditioning": "identity/conditioning.txt",
}


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
        try:
            from .relying_party_scope import current_relying_party_id
        except ImportError:  # direct script launcher compatibility
            from relying_party_scope import current_relying_party_id
        return f"users/{user_id}/{current_relying_party_id()}/{logical_path}".strip("/")
    return logical_path.strip("/")


def _codex_system_prefix_for_browser_path(normalized_path: str) -> str | None:
    parts = normalized_path.split("/")
    if len(parts) < 2 or parts[0] != "instances" or not parts[1]:
        return None
    if len(parts) == 2 or (len(parts) >= 3 and parts[2] == "codex"):
        return f"instances/{parts[1]}/codex/%"
    return None


class VVaultFileRepository:
    def __init__(self, *, auth_repository: vvault_auth_repository.VVaultAuthRepository | None = None) -> None:
        self.auth_repository = auth_repository or vvault_auth_repository.VVaultAuthRepository()

    def _connect(self):
        return chatty_body_service._connect()

    def _system_user_id(self) -> str:
        user = self.auth_repository.get_user_by_email(SYSTEM_USER_EMAIL)
        if not user or user.get("role") != "system":
            raise RuntimeError("pre-provisioned VVAULT system principal is required")
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

    def list_for_browser(
        self,
        *,
        user_id: str | None,
        is_admin: bool,
        requested_path: str = "",
        include_system: bool = False,
    ) -> list[dict[str, Any]]:
        # Ordinary vault browsing is owner-scoped even for application admins.
        # Administrative authority is intentionally limited to dedicated
        # security workflows and never widens access to user files.
        if not user_id:
            return []
        normalized_path = str(requested_path or "").strip().strip("/")
        # System material is never part of ordinary Drive.  The separate,
        # read-only System projection remains bound to the authenticated
        # account, so it cannot become a cross-account construct browser.
        system_value = "true" if include_system else "false"
        scope = f"AND user_id = %s AND coalesce(is_system, false) = {system_value}"
        params: list[Any] = [user_id]

        if normalized_path == "instances":
            return []

        if normalized_path.startswith("instances/"):
            parts = normalized_path.split("/")
            if len(parts) < 2 or not parts[1]:
                return []
            path_prefix = f"{normalized_path.rstrip('/')}/%"
            path_params: list[Any] = [parts[1], path_prefix, path_prefix]
            scoped_params = list(params)
            params = [*path_params, *scoped_params]
            return self._fetch(
                f"""
                SELECT {self._columns(include_content=False)}
                FROM vault_files
                WHERE (
                    construct_id = %s
                    OR filename ILIKE %s
                    OR storage_path ILIKE %s
                ) AND drive_trashed_at IS NULL AND {PROJECTABLE_METADATA_SQL} {scope}
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
                WHERE (filename ILIKE %s OR storage_path ILIKE %s)
                  AND drive_trashed_at IS NULL AND {PROJECTABLE_METADATA_SQL} {scope}
                ORDER BY coalesce(updated_at, created_at) DESC
                """,
                tuple(params),
            )

        return self._fetch(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE user_id = %s AND coalesce(is_system, false) = false
              AND drive_trashed_at IS NULL AND {PROJECTABLE_METADATA_SQL}
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (user_id,),
        )

    def list_system_files(self, *, path_prefix: str) -> list[dict[str, Any]]:
        """List system-owned configuration rows for service-token-only routes."""
        prefix = f"{str(path_prefix or '').strip().strip('/')}%"
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE coalesce(is_system, false) = true
              AND (filename ILIKE %s OR storage_path ILIKE %s)
              AND drive_trashed_at IS NULL
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (prefix, prefix),
        )

    def construct_is_projectable(self, *, user_id: str, callsign: str) -> bool:
        """Return true only when the owner has a live, projectable instance row."""
        row = self._one(
            f"""
            SELECT
              EXISTS (
                SELECT 1 FROM vault_files
                WHERE user_id = %s AND construct_id = %s
                  AND drive_trashed_at IS NULL
                  AND coalesce(is_system, false) = false
                  AND {PROJECTABLE_METADATA_SQL}
                LIMIT 1
              ) AS owner_projectable,
              EXISTS (
                SELECT 1 FROM vault_files
                WHERE user_id = %s AND construct_id = %s
                  AND drive_trashed_at IS NULL
                  AND is_system = true
                  AND {PROJECTABLE_METADATA_SQL}
                LIMIT 1
              ) AS system_projectable
            """,
            (user_id, callsign, user_id, callsign),
        )
        if not row:
            return False
        if row.get("owner_projectable"):
            return True
        return bool(
            row.get("system_projectable")
            and canonical_category(callsign) == "system"
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

    def get_canonical_owner_avatar(
        self, *, user_id: str, callsign: str
    ) -> dict[str, Any] | None:
        """Fetch only the exact owner-scoped canonical avatar row."""
        canonical_path = f"instances/{callsign}/identity/avatar.png"
        return self._one(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND construct_id = %s
              AND drive_trashed_at IS NULL
              AND (storage_path = %s OR filename = %s)
            ORDER BY
                CASE WHEN storage_path = %s AND filename = %s THEN 0 ELSE 1 END,
                coalesce(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (
                user_id, callsign, canonical_path, canonical_path,
                canonical_path, canonical_path,
            ),
        )

    def get_canonical_owner_avatar_descriptor(
        self, *, user_id: str, callsign: str
    ) -> dict[str, Any] | None:
        """Fetch canonical avatar metadata without transporting its body."""
        canonical_path = f"instances/{callsign}/identity/avatar.png"
        return self._one(
            """
            SELECT id::text AS id, user_id::text AS user_id, construct_id,
                   filename, storage_path, object_key, content_type, sha256,
                   size_bytes, created_at, updated_at,
                   (content IS NOT NULL AND content <> '') AS body_available
            FROM vault_files
            WHERE user_id = %s
              AND construct_id = %s
              AND drive_trashed_at IS NULL
              AND (storage_path = %s OR filename = %s)
            ORDER BY
                CASE WHEN storage_path = %s AND filename = %s THEN 0 ELSE 1 END,
                coalesce(updated_at, created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (
                user_id, callsign, canonical_path, canonical_path,
                canonical_path, canonical_path,
            ),
        )

    def get_active_store_avatar(
        self, *, metadata_file_id: str, include_content: bool = False
    ) -> dict[str, Any] | None:
        """Resolve an active public Store avatar without exposing its owner."""
        avatar_content = ", avatar.content AS avatar_content" if include_content else ""
        return self._one(
            f"""
            SELECT metadata_file.content AS metadata_content,
                   metadata_file.construct_id,
                   avatar.id::text AS avatar_id,
                   avatar.filename, avatar.content_type, avatar.sha256,
                   avatar.size_bytes
                   {avatar_content}
            FROM vault_files metadata_file
            JOIN LATERAL (
                SELECT avatar.*
                FROM vault_files avatar
                WHERE avatar.user_id = metadata_file.user_id
                  AND avatar.construct_id = metadata_file.construct_id
                  AND avatar.filename = 'instances/' || metadata_file.construct_id || '/identity/avatar.png'
                  AND avatar.drive_trashed_at IS NULL
                ORDER BY coalesce(avatar.updated_at, avatar.created_at) DESC,
                         avatar.id DESC
                LIMIT 1
            ) avatar ON true
            WHERE metadata_file.id = %s
              AND metadata_file.filename = 'instances/' || metadata_file.construct_id || '/config/metadata.json'
              AND coalesce(metadata_file.is_system, false) = false
              AND metadata_file.drive_trashed_at IS NULL
            LIMIT 1
            """,
            (metadata_file_id,),
        )

    def allocate_user_callsign(
        self,
        *,
        user_id: str,
        requested_callsign: str,
        display_name: str,
    ) -> dict[str, Any]:
        """Permanently reserve one collision-safe owner-scoped GPT callsign."""
        base, serial_text = requested_callsign.rsplit("-", 1)
        start_serial = int(serial_text)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"callsign-allocation:{user_id}:{base}",),
                )
                cur.execute(
                    """
                    SELECT id::text AS allocation_id, requested_callsign,
                           allocated_callsign, display_name,
                           construct_category, lifecycle_stage,
                           claimed_at, consumed_at
                    FROM ovvaults.construct_callsign_allocations
                    WHERE owner_user_id = %s
                      AND requested_callsign = %s
                      AND display_name = %s
                      AND consumed_at IS NULL
                      AND claimed_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (user_id, requested_callsign, display_name),
                )
                existing = cur.fetchone()
                if existing:
                    conn.commit()
                    return {**dict(existing), "reused": True}
                cur.execute(
                    """
                    SELECT construct_id AS callsign
                    FROM ovvaults.vault_files
                    WHERE user_id = %s AND construct_id LIKE %s
                    UNION
                    SELECT allocated_callsign AS callsign
                    FROM ovvaults.construct_callsign_allocations
                    WHERE owner_user_id = %s
                    """,
                    (user_id, f"{base}-%", user_id),
                )
                occupied = {
                    str(row["callsign"]).strip().lower()
                    for row in cur.fetchall()
                }
                serial = start_serial
                candidate = requested_callsign
                while candidate in occupied:
                    serial += 1
                    if serial > 999:
                        raise ValueError("No collision-safe callsign remains for this name")
                    candidate = f"{base}-{serial:03d}"
                cur.execute(
                    """
                    INSERT INTO ovvaults.construct_callsign_allocations (
                        owner_user_id, requested_callsign, allocated_callsign,
                        display_name, construct_category, lifecycle_stage
                    )
                    VALUES (%s, %s, %s, %s, 'user', 'gpt')
                    RETURNING id::text AS allocation_id, requested_callsign,
                              allocated_callsign, display_name,
                              construct_category, lifecycle_stage,
                              claimed_at, consumed_at
                    """,
                    (user_id, requested_callsign, candidate, display_name),
                )
                allocated = dict(cur.fetchone())
            conn.commit()
        return {**allocated, "reused": False}

    def claim_user_callsign_allocation(
        self, *, allocation_id: str, user_id: str, display_name: str
    ) -> dict[str, Any]:
        return self._one(
            """
            UPDATE ovvaults.construct_callsign_allocations
            SET claimed_at = now()
            WHERE id = %s AND owner_user_id = %s AND display_name = %s
              AND claimed_at IS NULL AND consumed_at IS NULL
            RETURNING id::text AS allocation_id, requested_callsign,
                      allocated_callsign, display_name,
                      construct_category, lifecycle_stage
            """,
            (allocation_id, user_id, display_name),
        )

    def release_user_callsign_allocation(
        self, *, allocation_id: str, user_id: str
    ) -> None:
        self._one(
            """
            UPDATE ovvaults.construct_callsign_allocations
            SET claimed_at = NULL
            WHERE id = %s AND owner_user_id = %s
              AND claimed_at IS NOT NULL AND consumed_at IS NULL
            RETURNING id::text AS allocation_id
            """,
            (allocation_id, user_id),
        )

    def cancel_user_callsign_allocation(
        self, *, allocation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Delete only an owner's unclaimed, unconsumed preflight reservation."""
        return self._one(
            """
            DELETE FROM ovvaults.construct_callsign_allocations
            WHERE id = %s AND owner_user_id = %s
              AND claimed_at IS NULL AND consumed_at IS NULL
            RETURNING id::text AS allocation_id, requested_callsign,
                      allocated_callsign
            """,
            (allocation_id, user_id),
        )

    def consume_user_callsign_allocation(
        self, *, allocation_id: str, user_id: str, callsign: str
    ) -> dict[str, Any] | None:
        return self._one(
            """
            UPDATE ovvaults.construct_callsign_allocations
            SET consumed_at = now()
            WHERE id = %s AND owner_user_id = %s
              AND allocated_callsign = %s
              AND claimed_at IS NOT NULL AND consumed_at IS NULL
            RETURNING id::text AS allocation_id, requested_callsign,
                      allocated_callsign, display_name,
                      construct_category, lifecycle_stage, consumed_at
            """,
            (allocation_id, user_id, callsign),
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
        if not user_id:
            return None
        paths = [path.strip() for path in {filename, storage_path} if isinstance(path, str) and path.strip()]
        if not paths:
            return None
        conditions = ["(filename = ANY(%s) OR storage_path = ANY(%s))"]
        params: list[Any] = [paths, paths]
        if construct_id:
            conditions.append("construct_id = %s")
            params.append(construct_id)
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
            WHERE construct_id = %s AND user_id = %s AND drive_trashed_at IS NULL
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (construct_id, user_id),
        )

    def list_simdrive_files(self, *, construct_id: str, user_id: str, include_content: bool = False) -> list[dict[str, Any]]:
        prefix = f"instances/{construct_id}/simDrive/%"
        rows = self._fetch(
            f"""
            SELECT {self._columns(include_content=include_content)}
            FROM vault_files
            WHERE construct_id = %s
              AND user_id = %s
              AND drive_trashed_at IS NULL
              AND filename ILIKE %s
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            (construct_id, user_id, prefix),
        )

    def get_user_file(self, *, file_id: str, construct_id: str | None = None, user_id: str | None = None) -> dict[str, Any] | None:
        if not user_id:
            return None
        conditions = ["id = %s"]
        params: list[Any] = [file_id]
        if construct_id:
            conditions.append("construct_id = %s")
            params.append(construct_id)
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

    def correct_transcript_wrapper_paths(
        self,
        *,
        user_id: str,
        callsign: str,
        expected_row_ids: list[str],
        operator: str,
    ) -> dict[str, Any]:
        """Atomically remove a legacy transcript(s) wrapper and append a receipt.

        Content, content hashes, filenames below the wrapper, and timestamps are
        preserved. Exact owner/callsign/row binding prevents cross-construct
        correction, and any collision or receipt failure rolls back every path.
        """
        old_prefix = f"instances/{callsign}/transcripts/"
        expected_ids = sorted({str(value) for value in expected_row_ids if value})
        if not expected_ids:
            raise ValueError("expected_row_ids is required")
        now = _utc_now_iso()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id::text AS id, filename, storage_path, object_key,
                           sha256, size_bytes, content_type, file_type, created_at
                    FROM vault_files
                    WHERE user_id = %s
                      AND construct_id = %s
                      AND file_type = 'transcript'
                      AND filename LIKE %s
                    ORDER BY id
                    FOR UPDATE
                    """,
                    (user_id, callsign, f"{old_prefix}%"),
                )
                rows = list(cur.fetchall())
                actual_ids = sorted(str(row["id"]) for row in rows)
                if actual_ids != expected_ids:
                    raise ValueError(
                        f"affected row set changed: expected={expected_ids} actual={actual_ids}"
                    )
                corrections: list[dict[str, Any]] = []
                for row in rows:
                    old_path = str(row["filename"])
                    new_path = f"instances/{callsign}/{old_path[len(old_prefix):]}"
                    if new_path == old_path or "/transcripts/" in new_path.lower():
                        raise ValueError(f"invalid corrected transcript path: {new_path}")
                    cur.execute(
                        """
                        SELECT id::text AS id
                        FROM vault_files
                        WHERE user_id = %s
                          AND id <> %s
                          AND (
                            filename = %s
                            OR storage_path = %s
                            OR object_key = %s
                          )
                        LIMIT 1
                        """,
                        (
                            user_id,
                            row["id"],
                            new_path,
                            new_path,
                            str(row.get("object_key") or "").replace(old_path, new_path),
                        ),
                    )
                    if cur.fetchone():
                        raise ValueError(f"corrected transcript path collides: {new_path}")
                    corrections.append({
                        "row_id": str(row["id"]),
                        "old_path": old_path,
                        "new_path": new_path,
                        "sha256": row.get("sha256"),
                        "size_bytes": row.get("size_bytes"),
                        "content_type": row.get("content_type"),
                        "file_type": row.get("file_type"),
                        "created_at": (
                            row["created_at"].isoformat()
                            if hasattr(row.get("created_at"), "isoformat")
                            else row.get("created_at")
                        ),
                    })
                receipt = {
                    "schema": "life.vvault.transcript-path-correction/1.0.0",
                    "owner_user_id": user_id,
                    "callsign": callsign,
                    "operator": operator,
                    "issued_at": now,
                    "reason": "remove_noncanonical_transcripts_wrapper",
                    "corrections": corrections,
                    "rollback": [
                        {
                            "row_id": item["row_id"],
                            "from": item["new_path"],
                            "to": item["old_path"],
                        }
                        for item in corrections
                    ],
                }
                receipt_content = json.dumps(
                    receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                receipt_hash = hashlib.sha256(receipt_content.encode("utf-8")).hexdigest()
                receipt_path = (
                    f"instances/{callsign}/config/path_corrections/{receipt_hash}.json"
                )
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type, size_bytes,
                        sha256, created_at, content, metadata, construct_id,
                        storage_path, file_type, is_system, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, 'application/json', %s, %s, %s, %s,
                        %s::jsonb, %s, %s, 'path_correction_receipt', false, %s
                    )
                    ON CONFLICT (bucket, object_key) DO NOTHING
                    RETURNING id::text AS id
                    """,
                    (
                        user_id,
                        DEFAULT_BUCKET,
                        f"users/{user_id}/{receipt_path}",
                        receipt_path,
                        len(receipt_content.encode("utf-8")),
                        receipt_hash,
                        now,
                        receipt_content,
                        json.dumps({
                            "artifact_id": "life.vvault.transcript-path-correction",
                            "receipt_hash": receipt_hash,
                            "append_only": True,
                            "affected_row_ids": expected_ids,
                        }),
                        callsign,
                        receipt_path,
                        now,
                    ),
                )
                receipt_row = cur.fetchone()
                if not receipt_row:
                    raise ValueError("transcript path correction receipt already exists")
                for item, row in zip(corrections, rows):
                    old_path = item["old_path"]
                    new_path = item["new_path"]
                    object_key = str(row.get("object_key") or "")
                    storage_path = str(row.get("storage_path") or old_path)
                    cur.execute(
                        """
                        UPDATE vault_files
                        SET filename = %s,
                            storage_path = %s,
                            object_key = %s,
                            updated_at = %s
                        WHERE id = %s
                          AND user_id = %s
                          AND construct_id = %s
                        RETURNING id::text AS id
                        """,
                        (
                            new_path,
                            storage_path.replace(old_path, new_path),
                            object_key.replace(old_path, new_path),
                            now,
                            row["id"],
                            user_id,
                            callsign,
                        ),
                    )
                    if not cur.fetchone():
                        raise ValueError(f"failed to correct row {row['id']}")
            conn.commit()
        return {
            "receipt_file_id": str(receipt_row["id"]),
            "receipt_path": receipt_path,
            "receipt_sha256": receipt_hash,
            "corrections": corrections,
        }

    def list_canonical_transcripts(self, *, construct_id: str, user_id: str) -> list[dict[str, Any]]:
        """Return OVVAULTS transcript rows only; never consult compatibility storage."""
        path_pattern = f"%instances/{construct_id}/%"
        filename_pattern = f"%chat_with_{construct_id}%"
        return self._fetch(
            """
            SELECT id::text AS id,
                   title AS filename,
                   content,
                   created_at,
                   materialized_at,
                   source_hash AS sha256
            FROM transcripts
            WHERE user_id = %s
              AND content IS NOT NULL
              AND content <> ''
              AND (title ILIKE %s OR title ILIKE %s)
            ORDER BY coalesce(materialized_at, created_at) ASC
            """,
            (user_id, path_pattern, filename_pattern),
        )

    def get_canonical_capsule(self, *, construct_id: str, user_id: str) -> dict[str, Any] | None:
        path = artifact_storage_path(CAPSULE_ARTIFACT_ID, construct_id)
        return self.find_by_path(
            construct_id=construct_id,
            user_id=user_id,
            filename=path,
        )

    def upsert_canonical_capsule(self, *, construct_id: str, user_id: str, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Persist a continuity capsule in canonical OVVAULTS vault_files."""
        path = artifact_storage_path(CAPSULE_ARTIFACT_ID, construct_id)
        return self.upsert(
            {
                "filename": path,
                "storage_path": path,
                "content": content,
                "metadata": metadata,
                "construct_id": construct_id,
                "user_id": user_id,
                "file_type": "capsule",
                "content_type": "application/json",
            }
        )

    def list_construct_identity_rows(self, *, callsign: str, bare_name: str, user_id: str | None) -> list[dict[str, Any]]:
        params: list[Any] = [
            callsign,
            bare_name,
            f"instances/{callsign}/identity/%",
            f"instances/{callsign}/config/metadata.json",
        ]
        user_clause = ""
        if user_id:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        rows = self._fetch(
            f"""
            SELECT {self._columns(include_content=False)},
                   CASE
                     WHEN lower(coalesce(storage_path, filename, ''))
                          ~ '/identity/avatar\\.(png|jpe?g|webp|gif|avif)$'
                     THEN NULL
                     ELSE content
                   END AS content
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND (filename ILIKE %s OR filename ILIKE %s)
              AND drive_trashed_at IS NULL
              {user_clause}
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            tuple(params),
        )
        return self._mark_integrity_repair_leaves(rows, user_id=user_id, callsign=callsign)

    def _mark_integrity_repair_leaves(
        self,
        rows: list[dict[str, Any]],
        *,
        user_id: str | None,
        callsign: str,
    ) -> list[dict[str, Any]]:
        # Migration 0005 may be staged in source before it is applied. Keep
        # reads available during that interval; once present, the immutable
        # ledger is the sole authority for which replacement is the leaf.
        try:
            leaves = self._fetch(
                """
                SELECT repair.replacement_row_id::text AS replacement_row_id
                FROM ovvaults.vault_file_integrity_repairs repair
                WHERE repair.owner_user_id = %s
                  AND repair.callsign = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ovvaults.vault_file_integrity_repairs next_repair
                      WHERE next_repair.prior_row_id = repair.replacement_row_id
                  )
                """,
                (user_id, callsign),
            ) if user_id else []
        except Exception:
            leaves = []
        leaf_ids = {str(row["replacement_row_id"]) for row in leaves}
        for row in rows:
            row["integrity_repair_leaf"] = str(row.get("id") or "") in leaf_ids
        return rows

    def list_user_identity_rows(self, *, user_id: str) -> list[dict[str, Any]]:
        return self._fetch(
            f"""
            SELECT {self._columns(include_content=True)}
            FROM vault_files
            WHERE user_id = %s
              AND filename ILIKE 'instances/%%/identity/%%'
              AND drive_trashed_at IS NULL
              AND coalesce(is_system,false)=false
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
        rows = self._fetch(
            f"""
            SELECT {self._columns(include_content=include_content)}
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND filename ILIKE %s
              AND drive_trashed_at IS NULL
              {user_clause}
            ORDER BY coalesce(updated_at, created_at) DESC
            """,
            tuple(params),
        )
        return self._mark_integrity_repair_leaves(rows, user_id=user_id, callsign=callsign)

    def construct_file_summary(self, *, callsign: str, bare_name: str, user_id: str | None) -> dict[str, Any]:
        params: list[Any] = [callsign, bare_name, f"instances/{callsign}/%"]
        user_clause = ""
        if user_id:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        row = self._one(
            f"""
            SELECT
              count(*)::bigint AS total_count,
              coalesce(sum(coalesce(size_bytes, 0)), 0)::bigint AS total_bytes,
              max(coalesce(updated_at, created_at)) AS updated_at
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND filename ILIKE %s
              AND drive_trashed_at IS NULL
              {user_clause}
            """,
            tuple(params),
        )
        return row or {"total_count": 0, "total_bytes": 0, "updated_at": None}

    def latest_construct_capsule_row(self, *, callsign: str, bare_name: str, user_id: str | None) -> dict[str, Any] | None:
        params: list[Any] = [callsign, bare_name, f"instances/{callsign}/%.capsule"]
        user_clause = ""
        if user_id:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        return self._one(
            f"""
            SELECT {self._columns(include_content=False)}
            FROM vault_files
            WHERE construct_id IN (%s, %s)
              AND filename ILIKE %s
              {user_clause}
            ORDER BY coalesce(updated_at, created_at) DESC
            LIMIT 1
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
              AND drive_trashed_at IS NULL
              AND coalesce(is_system,false)=false
            LIMIT 1
            """,
            (construct_id,),
        )
        return row.get("user_id") if row else None

    def first_transcript_owner_for_construct(self, construct_id: str) -> str | None:
        callsign = chatty_body_service.normalize_callsign(construct_id)
        row = self._one(
            """
            SELECT user_id::text AS user_id
            FROM transcripts
            WHERE lower(title) LIKE %s
              AND content IS NOT NULL
              AND content <> ''
            ORDER BY coalesce(materialized_at, created_at) DESC
            LIMIT 1
            """,
            (f"%instances/{callsign}/%",),
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

    def owner_has_construct_metadata(self, *, user_id: str, callsign: str) -> bool:
        metadata_path = f"instances/{callsign}/config/metadata.json"
        row = self._one(
            """
            SELECT id::text AS id
            FROM vault_files
            WHERE user_id = %s AND construct_id = %s AND filename = %s
            LIMIT 1
            """,
            (user_id, callsign, metadata_path),
        )
        return bool(row)

    def register_forge_success(
        self,
        *,
        user_id: str,
        callsign: str,
        forge_run_id: str,
        source_artifact_hashes: dict[str, str],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one owner-scoped Forge success artifact without promoting."""
        run_key = hashlib.sha256(forge_run_id.encode("utf-8")).hexdigest()
        path = f"instances/{callsign}/config/forge_success/{run_key}.json"
        now = _utc_now_iso()
        content = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        artifact_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text AS id
                    FROM vault_files
                    WHERE user_id = %s AND construct_id = %s AND filename = %s
                    LIMIT 1
                    FOR SHARE
                    """,
                    (user_id, callsign, f"instances/{callsign}/config/metadata.json"),
                )
                if not cur.fetchone():
                    raise ValueError("Canonical construct metadata was not found for owner")
                cur.execute(
                    """
                    SELECT filename, sha256 FROM vault_files
                    WHERE user_id = %s AND construct_id = %s AND filename = ANY(%s)
                    """,
                    (
                        user_id,
                        callsign,
                        [f"instances/{callsign}/{relative}" for relative in FORGE_SOURCE_PATHS.values()],
                    ),
                )
                canonical_by_path = {
                    str(row["filename"]): str(row["sha256"])
                    for row in cur.fetchall()
                }
                expected_by_path = {
                    f"instances/{callsign}/{relative}": source_artifact_hashes[key]
                    for key, relative in FORGE_SOURCE_PATHS.items()
                }
                if canonical_by_path != expected_by_path:
                    raise ValueError("Forge source hashes do not match the authoritative identity/config artifact set")
                metadata = json.dumps({
                    "artifact_id": "life.vvault.lifecycle.forge-success",
                    "forge_run_id": forge_run_id,
                    "append_only": True,
                })
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type, size_bytes,
                        sha256, created_at, content, metadata, construct_id,
                        storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'application/json', %s, %s, %s, %s, %s::jsonb,
                            %s, %s, 'forge_success', false, %s)
                    ON CONFLICT (bucket, object_key) DO NOTHING
                    RETURNING id::text AS id
                    """,
                    (
                        user_id, DEFAULT_BUCKET, f"users/{user_id}/{path}", path,
                        len(content.encode("utf-8")), artifact_sha, now, content,
                        metadata, callsign, path, now,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Forge run success artifact already exists")
            conn.commit()
        return {
            "file_id": str(row["id"]),
            "sha256": artifact_sha,
            "path": path,
            "forge_run_id": forge_run_id,
        }

    def promote_lifecycle(
        self,
        *,
        user_id: str,
        callsign: str,
        target_stage: str,
        receipt_hash: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically append Forge evidence and advance canonical lifecycle."""
        metadata_path = f"instances/{callsign}/config/metadata.json"
        evidence_path = f"instances/{callsign}/config/lifecycle_promotions/{receipt_hash}.json"
        now = _utc_now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, content, metadata
                    FROM vault_files
                    WHERE user_id = %s AND construct_id = %s AND filename = %s
                    ORDER BY coalesce(updated_at, created_at) DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (user_id, callsign, metadata_path),
                )
                metadata_row = cur.fetchone()
                if not metadata_row:
                    raise ValueError("Canonical construct metadata was not found for owner")
                document = json.loads(metadata_row["content"]) if isinstance(metadata_row["content"], str) else dict(metadata_row["content"] or {})
                current_stage = str(document.get("lifecycle_stage") or "gpt")
                transitions = {"gpt": "sim", "sim": "base", "base": "vsi"}
                if transitions.get(current_stage) != target_stage:
                    raise ValueError(f"Invalid lifecycle transition: {current_stage} -> {target_stage}")

                source_hashes = list(receipt["source_artifact_hashes"].values())
                cur.execute(
                    """
                    SELECT sha256 FROM vault_files
                    WHERE user_id = %s AND construct_id = %s AND sha256 = ANY(%s)
                    """,
                    (user_id, callsign, source_hashes),
                )
                found_hashes = {str(row["sha256"]) for row in cur.fetchall()}
                if found_hashes != set(source_hashes):
                    raise ValueError("One or more receipt source artifact hashes are not canonical for this owner/construct")

                forge_artifact = receipt["forge_success_artifact"]
                cur.execute(
                    """
                    SELECT 1 FROM vault_files
                    WHERE id = %s AND user_id = %s AND construct_id = %s AND sha256 = %s
                    """,
                    (forge_artifact["file_id"], user_id, callsign, forge_artifact["sha256"]),
                )
                if not cur.fetchone():
                    raise ValueError("Forge success artifact is missing or does not match owner/construct/hash")

                evidence_content = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                evidence_metadata = json.dumps({
                    "artifact_id": "life.vvault.lifecycle.promotion-receipt",
                    "receipt_hash": receipt_hash,
                    "forge_run_id": receipt["forge_run_id"],
                    "target_stage": target_stage,
                    "append_only": True,
                })
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type, size_bytes,
                        sha256, created_at, content, metadata, construct_id,
                        storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'application/json', %s, %s, %s, %s, %s::jsonb,
                            %s, %s, 'lifecycle_receipt', false, %s)
                    ON CONFLICT (bucket, object_key) DO NOTHING
                    RETURNING id::text AS id
                    """,
                    (
                        user_id, DEFAULT_BUCKET, f"users/{user_id}/{evidence_path}", evidence_path,
                        len(evidence_content.encode("utf-8")), receipt_hash, now, evidence_content,
                        evidence_metadata, callsign, evidence_path, now,
                    ),
                )
                evidence_row = cur.fetchone()
                if not evidence_row:
                    raise ValueError("Lifecycle promotion receipt was already consumed")

                document["lifecycle_stage"] = target_stage
                updated_content = json.dumps(document, indent=2, ensure_ascii=False)
                updated_sha = hashlib.sha256(updated_content.encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    UPDATE vault_files
                    SET content = %s, sha256 = %s, size_bytes = %s, updated_at = %s
                    WHERE id = %s
                    RETURNING id::text AS id
                    """,
                    (
                        updated_content, updated_sha, len(updated_content.encode("utf-8")),
                        now, metadata_row["id"],
                    ),
                )
                updated_row = cur.fetchone()
            conn.commit()
        return {
            "metadata_file_id": str(updated_row["id"]),
            "evidence_file_id": str(evidence_row["id"]),
            "receipt_hash": receipt_hash,
            "previous_stage": current_stage,
            "lifecycle_stage": target_stage,
            "evidence_path": evidence_path,
        }

    def apply_integrity_repair(
        self,
        *,
        user_id: str,
        callsign: str,
        canonical_path: str,
        prior_row_id: str,
        prior_sha256: str,
        replacement_bytes: bytes,
        replacement_sha256: str,
        receipt_hash: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically append a replacement row and immutable supersession receipt."""
        replacement_text = replacement_bytes.decode("utf-8", errors="strict")
        now = _utc_now_iso()
        metadata = {
            "artifact_id": "life.vvault.integrity-repair.replacement",
            "integrity_repair": {
                "receipt_hash": receipt_hash,
                "supersedes_row_id": prior_row_id,
                "current": True,
                "append_only": True,
            },
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"integrity-repair:{user_id}:{callsign}:{canonical_path}",),
                )
                cur.execute(
                    """
                    SELECT id::text AS id, sha256, filename, storage_path
                    FROM vault_files
                    WHERE id = %s AND user_id = %s AND construct_id = %s
                    FOR UPDATE
                    """,
                    (prior_row_id, user_id, callsign),
                )
                prior = cur.fetchone()
                if not prior:
                    raise ValueError("Prior canonical row was not found for owner/construct")
                prior_path = str(prior.get("storage_path") or prior.get("filename") or "")
                if prior_path != canonical_path or str(prior.get("sha256") or "") != prior_sha256:
                    raise ValueError("Prior canonical row path or digest changed")
                cur.execute(
                    """
                    SELECT repair.replacement_row_id::text AS replacement_row_id
                    FROM ovvaults.vault_file_integrity_repairs repair
                    WHERE repair.owner_user_id = %s
                      AND repair.callsign = %s
                      AND repair.canonical_path = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM ovvaults.vault_file_integrity_repairs next_repair
                          WHERE next_repair.prior_row_id = repair.replacement_row_id
                      )
                    """,
                    (user_id, callsign, canonical_path),
                )
                current_leaf = cur.fetchone()
                if current_leaf and str(current_leaf["replacement_row_id"]) != prior_row_id:
                    raise ValueError("Prior row is not the current integrity-repair leaf")
                cur.execute(
                    """
                    SELECT 1 FROM ovvaults.vault_file_integrity_repairs
                    WHERE receipt_hash = %s OR prior_row_id = %s
                    FOR SHARE
                    """,
                    (receipt_hash, prior_row_id),
                )
                if cur.fetchone():
                    raise ValueError("Integrity repair receipt or prior row was already consumed")
                object_key = f"users/{user_id}/{canonical_path}#integrity:{receipt_hash}"
                content_type = mimetypes.guess_type(canonical_path)[0] or "text/plain"
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type, size_bytes,
                        sha256, created_at, content, metadata, construct_id,
                        storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                            %s, %s, 'integrity_repair', false, %s)
                    RETURNING id::text AS id
                    """,
                    (
                        user_id, DEFAULT_BUCKET, object_key, canonical_path, content_type,
                        len(replacement_bytes), replacement_sha256, now, replacement_text,
                        json.dumps(metadata), callsign, canonical_path, now,
                    ),
                )
                replacement = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO ovvaults.vault_file_integrity_repairs (
                        receipt_hash, owner_user_id, callsign, canonical_path,
                        prior_row_id, prior_sha256, replacement_row_id,
                        replacement_sha256, byte_count, source_provenance,
                        operator, issued_at, receipt
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                            %s, %s, %s::jsonb)
                    RETURNING id::text AS id
                    """,
                    (
                        receipt_hash, user_id, callsign, canonical_path, prior_row_id,
                        prior_sha256, replacement["id"], replacement_sha256,
                        len(replacement_bytes), json.dumps(receipt["source_provenance"]),
                        receipt["operator"], receipt["issued_at"], json.dumps(receipt),
                    ),
                )
                evidence = cur.fetchone()
            conn.commit()
        return {
            "replacement_file_id": str(replacement["id"]),
            "integrity_repair_id": str(evidence["id"]),
            "receipt_hash": receipt_hash,
            "superseded_row_id": prior_row_id,
            "canonical_path": canonical_path,
            "sha256": replacement_sha256,
            "byte_count": len(replacement_bytes),
        }

    def apply_path_corrections(
        self,
        *,
        user_id: str,
        callsign: str,
        corrections: list[dict[str, Any]],
        operator: str,
        issued_at: str,
    ) -> dict[str, Any]:
        """Atomically correct paths in place and append immutable evidence."""
        if not corrections:
            raise ValueError("At least one path correction is required")
        canonical_items = sorted(
            (
                {
                    "row_id": str(item["row_id"]),
                    "before_path": str(item["before_path"]),
                    "after_path": str(item["after_path"]),
                    "sha256": str(item["sha256"]),
                    "size_bytes": int(item["size_bytes"]),
                    "created_at": str(item["created_at"]),
                    "updated_at": str(item["updated_at"]),
                }
                for item in corrections
            ),
            key=lambda item: item["row_id"],
        )
        receipt = {
            "schema_id": "life.vvault.path-correction.receipt",
            "schema_version": "1.0.0",
            "owner_uuid": user_id,
            "callsign": callsign,
            "operator": operator,
            "issued_at": issued_at,
            "corrections": canonical_items,
        }
        canonical_receipt = json.dumps(
            receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        receipt_sha = hashlib.sha256(canonical_receipt.encode("utf-8")).hexdigest()
        expected_prefix = f"instances/{callsign}/"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"path-correction:{user_id}:{callsign}",),
                )
                locked_rows: list[dict[str, Any]] = []
                for item in canonical_items:
                    if (
                        not item["after_path"].startswith(expected_prefix)
                        or "/transcript/" in item["after_path"]
                        or "/transcripts/" in item["after_path"]
                        or ".." in item["after_path"].split("/")
                    ):
                        raise ValueError("Corrected path violates the VSI instance contract")
                    cur.execute(
                        """
                        SELECT id::text AS id, user_id::text AS user_id,
                               construct_id, filename, storage_path, sha256,
                               size_bytes, created_at, updated_at, metadata
                        FROM ovvaults.vault_files
                        WHERE id = %s AND user_id = %s AND construct_id = %s
                        FOR UPDATE
                        """,
                        (item["row_id"], user_id, callsign),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError("Path correction row owner/construct binding failed")
                    current_path = str(row.get("storage_path") or row.get("filename") or "")
                    actual = {
                        "row_id": str(row["id"]),
                        "before_path": current_path,
                        "after_path": item["after_path"],
                        "sha256": str(row.get("sha256") or ""),
                        "size_bytes": int(row.get("size_bytes") or 0),
                        "created_at": row["created_at"].isoformat(),
                        "updated_at": row["updated_at"].isoformat(),
                    }
                    if actual != item:
                        raise ValueError("Path correction receipt no longer matches canonical row")
                    cur.execute(
                        """
                        SELECT 1
                        FROM ovvaults.vault_files
                        WHERE user_id = %s AND construct_id = %s
                          AND id <> %s
                          AND (filename = %s OR storage_path = %s)
                        FOR SHARE
                        """,
                        (
                            user_id, callsign, item["row_id"],
                            item["after_path"], item["after_path"],
                        ),
                    )
                    if cur.fetchone():
                        raise ValueError("Corrected path collides with an existing canonical row")
                    locked_rows.append(row)
                cur.execute(
                    """
                    INSERT INTO ovvaults.vault_file_path_corrections (
                        receipt_sha256, owner_user_id, callsign, operator,
                        issued_at, correction_count, receipt
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id::text AS id
                    """,
                    (
                        receipt_sha, user_id, callsign, operator, issued_at,
                        len(canonical_items), canonical_receipt,
                    ),
                )
                evidence = cur.fetchone()
                for item, row in zip(canonical_items, locked_rows):
                    metadata = row.get("metadata")
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    metadata = dict(metadata or {})
                    metadata["folder"] = item["after_path"].split("/")[2]
                    cur.execute(
                        """
                        UPDATE ovvaults.vault_files
                        SET filename = %s, storage_path = %s, metadata = %s::jsonb
                        WHERE id = %s
                        """,
                        (
                            item["after_path"], item["after_path"],
                            json.dumps(metadata), item["row_id"],
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO ovvaults.vault_file_path_correction_items (
                            correction_id, row_id, before_path, after_path,
                            sha256, size_bytes, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            evidence["id"], item["row_id"], item["before_path"],
                            item["after_path"], item["sha256"], item["size_bytes"],
                            item["created_at"], item["updated_at"],
                        ),
                    )
            conn.commit()
        return {
            "correction_id": str(evidence["id"]),
            "receipt_sha256": receipt_sha,
            "corrected_row_ids": [item["row_id"] for item in canonical_items],
            "correction_count": len(canonical_items),
        }

    def upsert(
        self,
        record: dict[str, Any],
        *,
        statement_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
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

        if statement_timeout_ms is not None:
            statement_timeout_ms = int(statement_timeout_ms)
            if statement_timeout_ms < 1 or statement_timeout_ms > 180_000:
                raise ValueError("Vault file statement timeout must be between 1 and 180000 ms")

        with self._connect() as conn:
            with conn.cursor() as cur:
                if statement_timeout_ms is not None:
                    # Transaction-local only. Large knowledge bodies must not broaden
                    # the timeout used by reads, identity writes, transcripts, or avatars.
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(statement_timeout_ms),),
                    )
                cur.execute(
                    """
                    INSERT INTO vault_files (
                        user_id, bucket, object_key, filename, content_type,
                        size_bytes, sha256, created_at, content, metadata,
                        construct_id, storage_path, file_type, is_system, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                    ON CONFLICT (bucket, object_key) DO UPDATE
                    SET filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size_bytes = EXCLUDED.size_bytes,
                        sha256 = EXCLUDED.sha256,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        construct_id = EXCLUDED.construct_id,
                        storage_path = EXCLUDED.storage_path,
                        file_type = EXCLUDED.file_type,
                        is_system = EXCLUDED.is_system,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id::text AS id, (xmax = 0) AS inserted
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
            conn.commit()

        action = "created" if row and row.get("inserted") else "updated"
        return {
            "action": action,
            "id": str(row["id"]) if row else None,
            "deduped": 0,
            "path": logical_path,
        }

    def append_cleanhouse_files_evidence_batch(
        self,
        *,
        user_id: str,
        callsign: str,
        batch_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append an idempotent CleanHouse evidence batch and receipt.

        Evidence remains in the existing ``ovvaults.vault_files`` authority.
        A repeated evidence ID is accepted only when its canonical content is
        byte-for-byte equivalent; collisions fail the entire transaction.
        """
        if len(batch_id) != 64 or any(character not in "0123456789abcdef" for character in batch_id):
            raise ValueError("CleanHouse batch ID is invalid")
        if not user_id or not callsign or not events:
            raise ValueError("CleanHouse evidence owner, instance, and events are required")

        now = _utc_now_iso()
        accepted_ids: list[str] = []
        receipt_id = f"cleanhouse-files:{batch_id}"
        prefix = f"instances/{callsign}/evidence/cleanhouse/files"
        with self._connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    evidence_id = str(event["evidence_id"])
                    content = str(event["content"])
                    content_sha = str(event["sha256"])
                    event_key = hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()
                    path = f"{prefix}/events/{event_key}.json"
                    metadata = {
                        "artifact_id": "life.cleanhouse.files.evidence",
                        "schema": "cleanhouse.files_evidence.event.v1",
                        "evidence_id": evidence_id,
                        "batch_id": batch_id,
                        "append_only": True,
                        "source_product": "cleanhouse",
                    }
                    cur.execute(
                        """
                        INSERT INTO vault_files (
                            user_id, bucket, object_key, filename, content_type,
                            size_bytes, sha256, created_at, content, metadata,
                            construct_id, storage_path, file_type, is_system, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, 'application/json', %s, %s, %s,
                            %s, %s::jsonb, %s, %s, 'cleanhouse_file_evidence', false, %s
                        )
                        ON CONFLICT (bucket, object_key) DO NOTHING
                        RETURNING id::text AS id, sha256
                        """,
                        (
                            user_id,
                            DEFAULT_BUCKET,
                            f"users/{user_id}/{path}",
                            path,
                            len(content.encode("utf-8")),
                            content_sha,
                            event.get("created_at") or now,
                            content,
                            json.dumps(metadata),
                            callsign,
                            path,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                    if not row:
                        cur.execute(
                            """
                            SELECT id::text AS id, sha256
                            FROM vault_files
                            WHERE bucket = %s AND object_key = %s
                            FOR SHARE
                            """,
                            (DEFAULT_BUCKET, f"users/{user_id}/{path}"),
                        )
                        row = cur.fetchone()
                    if not row or str(row.get("sha256") or "") != content_sha:
                        raise ValueError(f"CleanHouse evidence ID collision: {evidence_id}")
                    accepted_ids.append(evidence_id)

                receipt_path = f"{prefix}/receipts/{batch_id}.json"
                receipt_key = f"users/{user_id}/{receipt_path}"
                cur.execute(
                    """
                    SELECT id::text AS id, content, sha256
                    FROM vault_files
                    WHERE bucket = %s AND object_key = %s
                    FOR SHARE
                    """,
                    (DEFAULT_BUCKET, receipt_key),
                )
                existing_receipt = cur.fetchone()
                if existing_receipt:
                    receipt = json.loads(str(existing_receipt.get("content") or "{}"))
                    if (
                        receipt.get("batch_id") != batch_id
                        or receipt.get("accepted_evidence_ids") != accepted_ids
                    ):
                        raise ValueError("CleanHouse evidence receipt collision")
                else:
                    receipt = {
                        "schema": "ovvaults.cleanhouse.files_evidence.receipt.v1",
                        "receipt_id": receipt_id,
                        "batch_id": batch_id,
                        "accepted_evidence_ids": accepted_ids,
                        "owner_user_id": user_id,
                        "instance_id": callsign,
                        "committed_at": now,
                        "storage_owner": FILE_OWNER,
                    }
                    receipt_content = json.dumps(
                        receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    )
                    receipt_sha = hashlib.sha256(receipt_content.encode("utf-8")).hexdigest()
                    cur.execute(
                        """
                        INSERT INTO vault_files (
                            user_id, bucket, object_key, filename, content_type,
                            size_bytes, sha256, created_at, content, metadata,
                            construct_id, storage_path, file_type, is_system, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, 'application/json', %s, %s, %s,
                            %s, %s::jsonb, %s, %s, 'cleanhouse_file_evidence_receipt', false, %s
                        )
                        RETURNING id::text AS id
                        """,
                        (
                            user_id,
                            DEFAULT_BUCKET,
                            receipt_key,
                            receipt_path,
                            len(receipt_content.encode("utf-8")),
                            receipt_sha,
                            now,
                            receipt_content,
                            json.dumps(
                                {
                                    "artifact_id": "life.cleanhouse.files.evidence-receipt",
                                    "schema": receipt["schema"],
                                    "batch_id": batch_id,
                                    "append_only": True,
                                    "source_product": "cleanhouse",
                                }
                            ),
                            callsign,
                            receipt_path,
                            now,
                        ),
                    )
                    if not cur.fetchone():
                        raise ValueError("CleanHouse evidence receipt was not committed")
            conn.commit()
        return receipt

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
        storage_path = row.get("storage_path")
        if not bucket or not object_key:
            return None
        try:
            from packages.storage.client import StorageClient
        except Exception:  # Optional runtime dependency/config.
            return None
        # Migrated OVVAULTS rows can retain the original storage_path while
        # carrying a normalized object_key. Both are canonical references in
        # the same VVAULT object store, so try each bounded candidate rather
        # than treating a missing normalized key as missing user data.
        candidates = []
        metadata = _metadata(row.get("metadata"))
        canonical_contract = metadata.get("canonical_contract")
        if isinstance(canonical_contract, dict):
            source_object_key = canonical_contract.get("source_object_key")
            if source_object_key:
                candidates.append(str(source_object_key))
        # Legacy Supabase migrations commonly prefixed the database reference
        # with an owner UUID while retaining the actual bucket key in
        # metadata.original_path.  The prefixed reference is useful provenance,
        # but it is not necessarily a downloadable object key.  Prefer the
        # receipt-backed source above, then try the original bucket key before
        # the normalized migration aliases.
        for metadata_key in ("original_path", "source_object_key", "object_key"):
            metadata_object_key = metadata.get(metadata_key)
            if metadata_object_key:
                candidates.append(str(metadata_object_key))
        if storage_path and "#source:" in str(object_key):
            candidates.append(str(storage_path))
        candidates.append(str(object_key))
        if storage_path and str(storage_path) not in candidates:
            candidates.append(str(storage_path))
        candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
        try:
            client = StorageClient()
        except Exception:
            return None
        expected_sha256 = str(row.get("sha256") or "").strip().lower()
        verify_sha256 = len(expected_sha256) == 64 and all(
            character in "0123456789abcdef" for character in expected_sha256
        )
        for storage_key in candidates:
            try:
                stored = client.download_bytes(bucket=str(bucket), object_key=storage_key)
                if verify_sha256 and hashlib.sha256(stored.body).hexdigest() != expected_sha256:
                    continue
                return stored.body, stored.content_type
            except Exception:
                continue
        return None

    def binary_data_url(self, row: dict[str, Any] | None, mime: str) -> str | None:
        stored = self.load_bytes(row)
        if not stored:
            return None
        body, content_type = stored
        encoded = base64.b64encode(body).decode("utf-8")
        return f"data:{content_type or mime};base64,{encoded}"
