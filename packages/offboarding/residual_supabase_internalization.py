from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol
from urllib import parse, request


RESIDUAL_TABLES = [
    "user_sessions",
    "service_credentials",
    "strategy_configs",
    "memory_embeddings",
    "fxshinobi_health",
    "fxshinobi_trades",
    "fxshinobi_snapshots",
]

BODY_DATABASE_NAME = "vvault_body_20260504t123219z"
BODY_DATABASE_HOST = "127.0.0.1"
BODY_DATABASE_PORT = "5432"
SYSTEM_USER_EMAIL = "system@vvault.local"
SECRETISH_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "encrypted_value",
    "key",
    "password",
    "refresh_token",
    "secret",
    "token",
    "value",
}
PRIVATE_COLUMNS = {
    "content",
    "embedding",
    "embedding_vector",
    "metadata",
    "params",
    "prompt",
    "risk_limits",
    "symbols",
    "transcript",
    "value",
    "vector",
}


@dataclass(frozen=True)
class TableAction:
    table: str
    mode: str
    target_owner: str
    target_store: str
    payload_class: str


TABLE_ACTIONS = {
    "user_sessions": TableAction(
        table="user_sessions",
        mode="discard",
        target_owner="none",
        target_store="discarded_legacy_sessions",
        payload_class="legacy_session",
    ),
    "service_credentials": TableAction(
        table="service_credentials",
        mode="vault_file",
        target_owner="ovvaults.vault_files",
        target_store="system/credentials/legacy-supabase",
        payload_class="credential",
    ),
    "strategy_configs": TableAction(
        table="strategy_configs",
        mode="vault_file",
        target_owner="ovvaults.vault_files",
        target_store="system/configs/legacy-supabase",
        payload_class="strategy_config",
    ),
    "memory_embeddings": TableAction(
        table="memory_embeddings",
        mode="archive",
        target_owner="ovvaults.supabase_residual_archives",
        target_store="supabase_residual_archives",
        payload_class="memory_embedding",
    ),
    "fxshinobi_health": TableAction(
        table="fxshinobi_health",
        mode="archive",
        target_owner="ovvaults.supabase_residual_archives",
        target_store="supabase_residual_archives",
        payload_class="fxshinobi",
    ),
    "fxshinobi_trades": TableAction(
        table="fxshinobi_trades",
        mode="archive",
        target_owner="ovvaults.supabase_residual_archives",
        target_store="supabase_residual_archives",
        payload_class="fxshinobi",
    ),
    "fxshinobi_snapshots": TableAction(
        table="fxshinobi_snapshots",
        mode="archive",
        target_owner="ovvaults.supabase_residual_archives",
        target_store="supabase_residual_archives",
        payload_class="fxshinobi",
    ),
}


class TargetRepository(Protocol):
    def upsert_archive_row(self, *, source_table: str, row: dict[str, Any], payload_class: str) -> None:
        ...

    def upsert_system_file(self, *, source_table: str, row: dict[str, Any], target_store: str) -> None:
        ...

    def record_manifest(self, manifest: dict[str, Any]) -> None:
        ...


@dataclass
class TableResult:
    source_table: str
    source_row_count: int = 0
    target_owner: str = ""
    target_store: str = ""
    action: str = "blocked"
    imported_count: int = 0
    archived_count: int = 0
    discarded_count: int = 0
    aggregate_checksum_sha256: str | None = None
    blocker: str | None = None
    schema: list[dict[str, Any]] = field(default_factory=list)

    def sanitized(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "source_row_count": self.source_row_count,
            "target_owner": self.target_owner,
            "target_store": self.target_store,
            "action": self.action,
            "imported_count": self.imported_count,
            "archived_count": self.archived_count,
            "discarded_count": self.discarded_count,
            "aggregate_checksum_sha256": self.aggregate_checksum_sha256,
            "blocker": self.blocker,
            "schema": self.schema,
        }


def env_gate() -> dict[str, Any]:
    return {
        "SUPABASE_URL_SET": bool(os.environ.get("SUPABASE_URL")),
        "SUPABASE_SERVICE_ROLE_KEY_SET": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
        "SUPABASE_SERVICE_KEY_SET": bool(os.environ.get("SUPABASE_SERVICE_KEY")),
        "SUPABASE_ANON_KEY_SET": bool(os.environ.get("SUPABASE_ANON_KEY")),
    }


def _supabase_env() -> tuple[str, str]:
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not supabase_url or not service_key:
        raise RuntimeError("required Supabase env vars unavailable")
    return supabase_url, service_key


def _local_body_database_url() -> str:
    explicit = os.environ.get("VVAULT_BODY_DATABASE_URL")
    if explicit:
        return explicit
    candidate = os.environ.get("DATABASE_URL")
    if candidate and BODY_DATABASE_NAME in candidate and "supabase.co" not in candidate:
        return candidate
    user = os.environ.get("PGUSER") or os.environ.get("USER") or os.environ.get("LOGNAME") or "devonwoodson"
    return f"postgresql://{user}@{BODY_DATABASE_HOST}:{BODY_DATABASE_PORT}/{BODY_DATABASE_NAME}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(row).encode("utf-8")).hexdigest()


def _source_row_id(row: dict[str, Any]) -> str:
    for key in ("id", "uuid", "source_row_id"):
        value = row.get(key)
        if value:
            return str(value)
    return _payload_hash(row)


def _aggregate_checksum(digests: Iterable[str]) -> str:
    joined = "\n".join(sorted(digests))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _safe_schema_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key, value in row.items():
            if key not in columns:
                columns[key] = {
                    "name": key,
                    "type": _type_name(value),
                    "nullable": value is None,
                    "default_present": False,
                }
            elif value is None:
                columns[key]["nullable"] = True
    return [columns[key] for key in sorted(columns)]


def _type_name(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _sanitize_schema(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = []
    for column in schema:
        sanitized.append(
            {
                "name": str(column.get("name") or ""),
                "type": str(column.get("type") or "unknown"),
                "nullable": bool(column.get("nullable", False)),
                "default_present": bool(column.get("default_present", False)),
            }
        )
    return sorted(sanitized, key=lambda item: item["name"])


def _safe_file_record(source_table: str, row: dict[str, Any], target_store: str) -> dict[str, Any]:
    source_id = _source_row_id(row)
    storage_path = f"{target_store}/{source_id}.json"
    content = _canonical_json(row)
    return {
        "filename": storage_path,
        "storage_path": storage_path,
        "content": content,
        "content_type": "application/json",
        "file_type": "application/json",
        "is_system": True,
        "metadata": {
            "source_system": "supabase",
            "source_table": source_table,
            "source_row_id": source_id,
            "payload_sha256": _payload_hash(row),
        },
        "source_table": source_table,
        "source_row_id": source_id,
        "sha256": _payload_hash(row),
    }


def _redact_for_error(message: str) -> str:
    for value in (os.environ.get("SUPABASE_SERVICE_ROLE_KEY"), os.environ.get("SUPABASE_SERVICE_KEY"), os.environ.get("SUPABASE_ANON_KEY")):
        if value:
            message = message.replace(value, "[REDACTED]")
    return message


class SupabaseRestClient:
    def __init__(self, supabase_url: str, service_key: str, *, page_size: int = 1000) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.service_key = service_key
        self.page_size = page_size

    def fetch_rows(self, table: str) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        total_count: int | None = None
        for page, parsed_count in self.iter_pages(table):
            rows.extend(item for item in page if isinstance(item, dict))
            if parsed_count is not None:
                total_count = parsed_count
        return rows, total_count if total_count is not None else len(rows)

    def iter_pages(self, table: str) -> Iterator[tuple[list[dict[str, Any]], int | None]]:
        fetched = 0
        total_count: int | None = None
        for page_index in range(100000):
            start = page_index * self.page_size
            end = start + self.page_size - 1
            response = self._get(
                f"/rest/v1/{parse.quote(table)}?select=*",
                headers={"Range": f"{start}-{end}", "Prefer": "count=exact"},
            )
            page = response["body"] if isinstance(response["body"], list) else []
            parsed_count = _parse_content_range(response["headers"])
            if parsed_count is not None:
                total_count = parsed_count
            clean_page = [item for item in page if isinstance(item, dict)]
            fetched += len(clean_page)
            yield clean_page, total_count
            if total_count is not None and fetched >= total_count:
                break
            if len(page) < self.page_size:
                break

    def _get(self, path: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json",
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
        }
        request_headers.update(headers or {})
        req = request.Request(
            f"{self.supabase_url}{path}",
            headers=request_headers,
            method="GET",
        )
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return {"status": response.status, "headers": dict(response.headers), "body": body}


def _parse_content_range(headers: dict[str, Any]) -> int | None:
    for key, value in headers.items():
        if key.lower() == "content-range" and "/" in str(value):
            total = str(value).rsplit("/", 1)[-1]
            return int(total) if total.isdigit() else None
    return None


class DbTargetRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or _local_body_database_url()
        self._column_cache: dict[str, set[str]] = {}

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.database_url, row_factory=dict_row, options="-c search_path=ovvaults,public")

    def _columns(self, table_name: str) -> set[str]:
        cached = self._column_cache.get(table_name)
        if cached is not None:
            return cached
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'ovvaults'
                      AND table_name = %s
                    """,
                    (table_name,),
                )
                columns = {str(row["column_name"]) for row in cur.fetchall()}
        self._column_cache[table_name] = columns
        return columns

    def _system_user_id(self) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, name, role, auth_provider, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (email) DO UPDATE
                    SET role = EXCLUDED.role,
                        updated_at = now()
                    RETURNING id::text AS id
                    """,
                    (SYSTEM_USER_EMAIL, "!vvault-system!", "VVAULT System", "system", "external"),
                )
                row = cur.fetchone()
            conn.commit()
        return str(row["id"])

    def upsert_archive_row(self, *, source_table: str, row: dict[str, Any], payload_class: str) -> None:
        source_id = _source_row_id(row)
        payload_sha = _payload_hash(row)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO supabase_residual_archives (
                        source_table, source_row_id, payload_jsonb, payload_sha256, payload_class
                    )
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (source_table, source_row_id) DO UPDATE
                    SET payload_jsonb = EXCLUDED.payload_jsonb,
                        payload_sha256 = EXCLUDED.payload_sha256,
                        payload_class = EXCLUDED.payload_class,
                        archived_at = now()
                    """,
                    (source_table, source_id, _canonical_json(row), payload_sha, payload_class),
                )
            conn.commit()

    def upsert_archive_rows(self, *, source_table: str, rows: list[dict[str, Any]], payload_class: str) -> None:
        if not rows:
            return
        batch_size = int(os.environ.get("VVAULT_RESIDUAL_IMPORT_BATCH_SIZE", "500"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(rows), batch_size):
                    batch = rows[start : start + batch_size]
                    cur.executemany(
                        """
                        INSERT INTO supabase_residual_archives (
                            source_table, source_row_id, payload_jsonb, payload_sha256, payload_class
                        )
                        VALUES (%s, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (source_table, source_row_id) DO UPDATE
                        SET payload_jsonb = EXCLUDED.payload_jsonb,
                            payload_sha256 = EXCLUDED.payload_sha256,
                            payload_class = EXCLUDED.payload_class,
                            archived_at = now()
                        """,
                        [
                            (source_table, _source_row_id(row), _canonical_json(row), _payload_hash(row), payload_class)
                            for row in batch
                        ],
                    )
                    conn.commit()
                    print(
                        f"RESIDUAL_INTERNALIZATION progress table={source_table} archived={min(start + len(batch), len(rows))}/{len(rows)}",
                        file=sys.stderr,
                        flush=True,
                    )

    def archived_source_ids(self, source_table: str) -> set[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source_row_id FROM supabase_residual_archives WHERE source_table = %s",
                    (source_table,),
                )
                return {str(row["source_row_id"]) for row in cur.fetchall()}

    def archived_count(self, source_table: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS count FROM supabase_residual_archives WHERE source_table = %s",
                    (source_table,),
                )
                row = cur.fetchone()
        return int(row["count"])

    def upsert_system_file(self, *, source_table: str, row: dict[str, Any], target_store: str) -> None:
        user_id = self._system_user_id()
        record = _safe_file_record(source_table, row, target_store)
        content = record["content"]
        columns = [
            "user_id",
            "bucket",
            "object_key",
            "filename",
            "content_type",
            "size_bytes",
            "sha256",
            "content",
            "metadata",
            "storage_path",
            "file_type",
            "source_table",
            "source_row_id",
        ]
        values: list[Any] = [
            user_id,
            "vvault-local",
            record["storage_path"],
            record["filename"],
            record["content_type"],
            len(content.encode("utf-8")),
            record["sha256"],
            content,
            json.dumps(record["metadata"], sort_keys=True),
            record["storage_path"],
            record["file_type"],
            source_table,
            record["source_row_id"],
        ]
        vault_file_columns = self._columns("vault_files")
        if "is_system" in vault_file_columns:
            columns.append("is_system")
            values.append(True)
        if "updated_at" in vault_file_columns:
            columns.append("updated_at")
            values.append("now()")

        placeholders = []
        final_values = []
        for column, value in zip(columns, values, strict=True):
            if column == "updated_at" and value == "now()":
                placeholders.append("now()")
            elif column == "metadata":
                placeholders.append("%s::jsonb")
                final_values.append(value)
            else:
                placeholders.append("%s")
                final_values.append(value)

        update_columns = ["content", "size_bytes", "sha256", "metadata", "source_table", "source_row_id"]
        if "updated_at" in vault_file_columns:
            update_columns.append("updated_at")
        update_clause = ",\n                        ".join(
            f"{column} = {'now()' if column == 'updated_at' else f'EXCLUDED.{column}'}" for column in update_columns
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO vault_files ({", ".join(columns)})
                    VALUES ({", ".join(placeholders)})
                    ON CONFLICT (bucket, object_key) DO UPDATE
                    SET {update_clause}
                    """,
                    tuple(final_values),
                )
            conn.commit()

    def record_manifest(self, manifest: dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO supabase_residual_retirement_manifests (
                        run_id, source_table, source_row_count, target_owner,
                        target_store, action, imported_count, archived_count,
                        discarded_count, aggregate_checksum_sha256, blocker
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, source_table) DO UPDATE
                    SET source_row_count = EXCLUDED.source_row_count,
                        target_owner = EXCLUDED.target_owner,
                        target_store = EXCLUDED.target_store,
                        action = EXCLUDED.action,
                        imported_count = EXCLUDED.imported_count,
                        archived_count = EXCLUDED.archived_count,
                        discarded_count = EXCLUDED.discarded_count,
                        aggregate_checksum_sha256 = EXCLUDED.aggregate_checksum_sha256,
                        blocker = EXCLUDED.blocker
                    """,
                    (
                        manifest["run_id"],
                        manifest["source_table"],
                        manifest["source_row_count"],
                        manifest["target_owner"],
                        manifest["target_store"],
                        manifest["action"],
                        manifest["imported_count"],
                        manifest["archived_count"],
                        manifest["discarded_count"],
                        manifest.get("aggregate_checksum_sha256"),
                        manifest.get("blocker"),
                    ),
                )
            conn.commit()


def process_table(
    *,
    table: str,
    rows: list[dict[str, Any]],
    source_row_count: int,
    target: TargetRepository,
    run_id: str,
) -> TableResult:
    action = TABLE_ACTIONS[table]
    result = TableResult(
        source_table=table,
        source_row_count=source_row_count,
        target_owner=action.target_owner,
        target_store=action.target_store,
        schema=_sanitize_schema(_safe_schema_from_rows(rows)),
    )
    digests = [_payload_hash(row) for row in rows]
    result.aggregate_checksum_sha256 = _aggregate_checksum(digests)
    try:
        if action.mode == "discard":
            result.action = "discarded"
            result.discarded_count = source_row_count
        elif action.mode == "vault_file":
            for row in rows:
                target.upsert_system_file(source_table=table, row=row, target_store=action.target_store)
            result.action = "imported"
            result.imported_count = len(rows)
        elif action.mode == "archive":
            batch_writer = getattr(target, "upsert_archive_rows", None)
            if callable(batch_writer):
                batch_writer(source_table=table, rows=rows, payload_class=action.payload_class)
            else:
                for index, row in enumerate(rows, start=1):
                    target.upsert_archive_row(source_table=table, row=row, payload_class=action.payload_class)
                    if index % 1000 == 0:
                        print(
                            f"RESIDUAL_INTERNALIZATION progress table={table} archived={index}/{len(rows)}",
                            file=sys.stderr,
                            flush=True,
                        )
            result.action = "archived"
            result.archived_count = len(rows)
        else:
            result.blocker = f"unsupported action mode: {action.mode}"
    except Exception as exc:
        result.action = "blocked"
        result.blocker = _redact_for_error(type(exc).__name__)

    target.record_manifest({"run_id": run_id, **result.sanitized()})
    return result


def process_archive_table_streaming(
    *,
    table: str,
    client: SupabaseRestClient,
    target: TargetRepository,
    run_id: str,
) -> TableResult:
    action = TABLE_ACTIONS[table]
    result = TableResult(
        source_table=table,
        target_owner=action.target_owner,
        target_store=action.target_store,
    )
    digests: list[str] = []
    schema_rows: list[dict[str, Any]] = []
    processed_count = 0
    skipped_count = 0
    source_row_count = 0
    existing_ids = set()
    existing_reader = getattr(target, "archived_source_ids", None)
    if callable(existing_reader):
        existing_ids = set(existing_reader(table))

    try:
        batch_writer = getattr(target, "upsert_archive_rows", None)
        if not callable(batch_writer):
            raise RuntimeError("target does not support batch archive writes")

        for page, total_count in client.iter_pages(table):
            if total_count is not None:
                source_row_count = total_count
            else:
                source_row_count += len(page)
            if len(schema_rows) < 25:
                schema_rows.extend(page[: 25 - len(schema_rows)])
            digests.extend(_payload_hash(row) for row in page)
            rows_to_write = [row for row in page if _source_row_id(row) not in existing_ids]
            skipped_count += len(page) - len(rows_to_write)
            if rows_to_write:
                batch_writer(source_table=table, rows=rows_to_write, payload_class=action.payload_class)
                existing_ids.update(_source_row_id(row) for row in rows_to_write)
            processed_count += len(page)
            denominator = source_row_count or processed_count
            print(
                f"RESIDUAL_INTERNALIZATION progress table={table} fetched={processed_count}/{denominator} skipped_existing={skipped_count}",
                file=sys.stderr,
                flush=True,
            )

        count_reader = getattr(target, "archived_count", None)
        final_count = int(count_reader(table)) if callable(count_reader) else len(existing_ids)
        result.action = "archived"
        result.source_row_count = source_row_count
        result.archived_count = final_count
        result.schema = _sanitize_schema(_safe_schema_from_rows(schema_rows))
        result.aggregate_checksum_sha256 = _aggregate_checksum(digests)
        if source_row_count and final_count < source_row_count:
            result.action = "blocked"
            result.blocker = f"archive_count_mismatch:{final_count}/{source_row_count}"
    except Exception as exc:
        result.action = "blocked"
        result.source_row_count = source_row_count
        result.archived_count = processed_count
        result.schema = _sanitize_schema(_safe_schema_from_rows(schema_rows))
        result.aggregate_checksum_sha256 = _aggregate_checksum(digests) if digests else None
        result.blocker = _redact_for_error(type(exc).__name__)

    target.record_manifest({"run_id": run_id, **result.sanitized()})
    return result


def run_internalization(
    *,
    client: SupabaseRestClient | None = None,
    target: TargetRepository | None = None,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    env = env_gate()
    if not (env["SUPABASE_URL_SET"] and (env["SUPABASE_SERVICE_ROLE_KEY_SET"] or env["SUPABASE_SERVICE_KEY_SET"])):
        return {
            "status": "blocked",
            "final_verdict": "residual supabase data internalization incomplete: required Supabase env vars unavailable",
            "supabase_env_gate": env,
            "table_actions": [],
            "provenance_manifest": None,
        }

    if client is None:
        supabase_url, service_key = _supabase_env()
        client = SupabaseRestClient(supabase_url, service_key)
    target = target or DbTargetRepository()

    results: list[TableResult] = []
    for table in RESIDUAL_TABLES:
        try:
            action = TABLE_ACTIONS[table]
            if action.mode == "archive" and isinstance(client, SupabaseRestClient):
                result = process_archive_table_streaming(table=table, client=client, target=target, run_id=run_id)
            else:
                rows, row_count = client.fetch_rows(table)
                result = process_table(table=table, rows=rows, source_row_count=row_count, target=target, run_id=run_id)
        except Exception as exc:
            action = TABLE_ACTIONS[table]
            result = TableResult(
                source_table=table,
                target_owner=action.target_owner,
                target_store=action.target_store,
                blocker=_redact_for_error(type(exc).__name__),
            )
            target.record_manifest({"run_id": run_id, **result.sanitized()})
        results.append(result)

    manifest = _sanitized_manifest(run_id, env, results)
    output_path = None
    if output_dir:
        output_path = output_dir / "retirement_manifest.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    blockers = [result.source_table for result in results if result.blocker or result.action == "blocked"]
    final = "residual supabase data internalized" if not blockers else f"residual supabase data internalization incomplete: {', '.join(blockers)}"
    return {
        "status": "complete" if not blockers else "blocked",
        "final_verdict": final,
        "supabase_env_gate": env,
        "table_actions": [result.sanitized() for result in results],
        "provenance_manifest": str(output_path) if output_path else None,
    }


def _sanitized_manifest(run_id: str, env: dict[str, Any], results: list[TableResult]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "supabase_env_gate": env,
        "tables": [result.sanitized() for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Internalize residual Supabase rows into VVAULT-local archival stores.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir) if args.output_dir else Path("/private/tmp/vvault-offboarding/residual-supabase") / (
        args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    result = run_internalization(output_dir=output_dir, run_id=args.run_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
