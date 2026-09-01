#!/usr/bin/env python3
"""Create private, verified recovery receipts for enrollment migrations.

The deploy host owns the credentials. This tool never prints them, backup data,
or object names; stdout contains only receipt paths and opaque receipt IDs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from urllib.request import Request, urlopen


def fail(message: str) -> None:
    raise SystemExit(f"[vvault-backup] {message}")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    pattern = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value[:1] in {"'", '"'}:
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                fail("runtime environment has an invalid quoted value")
        values[key] = value
    return values


def read_systemd_environment(service: str) -> dict[str, str]:
    """Read service Environment= entries without ever writing them to output."""
    result = subprocess.run(
        ["systemctl", "show", service, "-p", "Environment", "--value"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode:
        return {}
    values: dict[str, str] = {}
    try:
        entries = shlex.split(result.stdout)
    except ValueError:
        fail("service environment metadata is malformed")
    for entry in entries:
        key, separator, value = entry.partition("=")
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


def write_pg_service(database_url: str, destination: Path) -> None:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.fragment or not parsed.path.lstrip("/"):
        fail("runtime database configuration is not a PostgreSQL URL")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    fields = {
        "host": query.pop("host", "") or (parsed.hostname or ""),
        "port": query.pop("port", "") or (str(parsed.port) if parsed.port else ""),
        "user": unquote(parsed.username or query.pop("user", "")),
        "password": unquote(parsed.password or query.pop("password", "")),
        "dbname": unquote(parsed.path.lstrip("/")),
    }
    for key in ("sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout"):
        if key in query:
            fields[key] = query[key]
    if any("\n" in value or "\r" in value for value in fields.values()):
        fail("runtime database configuration is malformed")
    with destination.open("w", encoding="utf-8") as handle:
        handle.write("[vvault_backup]\n")
        for key, value in fields.items():
            if value:
                handle.write(f"{key}={value.replace(chr(92), chr(92) * 2)}\n")
    destination.chmod(0o600)


def run_checked(command: list[str], environment: dict[str, str], label: str) -> None:
    result = subprocess.run(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        detail = result.stderr.lower()
        if "server version" in detail:
            classification = "server/client version mismatch"
        elif "authentication failed" in detail or "password" in detail:
            classification = "database authentication failure"
        elif "permission denied" in detail or "certificate" in detail:
            classification = "database credential-file permission failure"
        elif "connection" in detail or "could not translate" in detail or "network" in detail:
            classification = "database connection failure"
        elif "shared libraries" in detail or "not found" in detail:
            classification = "PostgreSQL client runtime failure"
        else:
            classification = "unclassified PostgreSQL client failure"
        fail(f"backup verification command failed: {label} ({classification})")


def backup_database(database_url: str, destination: Path) -> None:
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        fail("pg_dump and pg_restore are required on the deploy host")
    with tempfile.TemporaryDirectory(prefix="vvault-pg-service.") as directory:
        service = Path(directory) / "service.conf"
        write_pg_service(database_url, service)
        environment = os.environ.copy()
        environment.update({"PGSERVICEFILE": str(service), "PGSERVICE": "vvault_backup"})
        server_version = subprocess.run(
            ["psql", "--no-psqlrc", "-X", "-At", "-c", "SHOW server_version_num"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        client_version = subprocess.run(
            ["pg_dump", "--version"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if server_version.returncode or client_version.returncode:
            fail("database version preflight failed")
        server_major = server_version.stdout.strip()[:2]
        client_match = re.search(r"(\d+)(?:\.\d+)?$", client_version.stdout.strip())
        client_major = client_match.group(1) if client_match else "unknown"
        if not server_major.isdigit() or client_major != server_major:
            fail(f"database PostgreSQL major mismatch: server={server_major or 'unknown'}, client={client_major}")
        run_checked(["pg_dump", "--format=custom", "--file", str(destination)], environment, "pg_dump")
        run_checked(["pg_restore", "--list", str(destination)], environment, "pg_restore")
    if not destination.is_file() or destination.stat().st_size == 0:
        fail("database backup is empty")


def safe_target(root: Path, key: str) -> Path:
    raw_parts = key.split("/")
    parts = [part for part in raw_parts if part]
    if not parts or any(part in {".", ".."} for part in parts):
        fail("object storage returned an unsafe object key")
    target = root.joinpath(*parts)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def copy_rest_objects(root: Path, base_url: str, service_key: str, bucket: str) -> int:
    base_url = base_url.rstrip("/")
    for suffix in ("/rest/v1", "/storage/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"}
    copied, pending, visited = 0, [""], set()
    while pending:
        prefix = pending.pop()
        if prefix in visited:
            continue
        visited.add(prefix)
        offset = 0
        while True:
            request = Request(
                f"{base_url}/storage/v1/object/list/{quote(bucket, safe='')}",
                data=json.dumps({"prefix": prefix, "limit": 1000, "offset": offset}).encode(), headers=headers, method="POST"
            )
            try:
                with urlopen(request, timeout=60) as response:
                    entries = json.load(response)
            except Exception:
                fail("object-storage listing failed")
            if not isinstance(entries, list):
                fail("object-storage listing returned an invalid response")
            for entry in entries:
                name = entry.get("name") if isinstance(entry, dict) else None
                if not isinstance(name, str) or not name:
                    fail("object-storage listing contains an invalid entry")
                key = f"{prefix}{name}" if prefix else name
                if entry.get("id") is None:
                    pending.append(f"{key.rstrip('/')}/")
                    continue
                target = safe_target(root, key)
                object_url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}/" + "/".join(quote(part, safe="") for part in key.split("/"))
                try:
                    with urlopen(Request(object_url, headers=headers), timeout=120) as response, target.open("wb") as handle:
                        shutil.copyfileobj(response, handle)
                except Exception:
                    fail("object-storage copy failed")
                target.chmod(0o600)
                copied += 1
            if len(entries) < 1000:
                break
            offset += len(entries)
    return copied


def copy_s3_objects(root: Path, endpoint: str, access_key: str, secret_key: str, bucket: str) -> int:
    try:
        import boto3
    except ImportError:
        fail("S3 backup requires boto3 on the deploy host")
    client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key, aws_secret_access_key=secret_key)
    copied, token = 0, None
    while True:
        response = client.list_objects_v2(Bucket=bucket, **({"ContinuationToken": token} if token else {}))
        for entry in response.get("Contents", []):
            key = entry.get("Key")
            if not isinstance(key, str):
                fail("S3 listing contains an invalid entry")
            target = safe_target(root, key)
            client.download_file(bucket, key, str(target))
            target.chmod(0o600)
            copied += 1
        if not response.get("IsTruncated"):
            return copied
        token = response.get("NextContinuationToken")
        if not token:
            fail("S3 listing was truncated without a continuation token")


def digest_tree(root: Path) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        count += 1
    return digest.hexdigest(), count


def write_receipt(path: Path, kind: str, backup_id: str, artifact: Path, digest: str, object_count: int | None = None) -> None:
    payload: dict[str, object] = {"kind": kind, "backup_id": backup_id, "verified": True, "created_at": datetime.now(timezone.utc).isoformat(), "sha256": digest, "artifact": artifact.name}
    if object_count is not None:
        payload["object_count"] = object_count
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/opt/vvault-public/.env")
    parser.add_argument("--backup-root", default="/opt/deploy/backups/vvault-enrollment")
    parser.add_argument("--receipt-dir", default="/opt/deploy/receipts")
    parser.add_argument("--systemd-service", default="vvault-backend.service")
    args = parser.parse_args()
    values = read_env(Path(args.env_file))
    # A hardened systemd unit may carry database/object-storage values directly
    # instead of in the writable deployment environment file.  Unit values win.
    values.update(read_systemd_environment(args.systemd_service))
    database_url = values.get("VVAULT_BODY_DATABASE_URL")
    if not database_url:
        fail("runtime database configuration is missing")
    bucket = values.get("S3_BUCKET") or values.get("VVAULT_STORAGE_BUCKET") or values.get("SUPABASE_STORAGE_BUCKET") or "vvault"
    backup_id = f"vvault-enrollment-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(8)}"
    root, receipts = Path(args.backup_root) / backup_id, Path(args.receipt_dir)
    root.mkdir(mode=0o700, parents=True)
    receipts.mkdir(mode=0o750, parents=True, exist_ok=True)
    if not receipts.is_dir():
        fail("protected receipt path is not a directory")
    database_dump, object_root = root / "database.dump", root / "objects"
    object_root.mkdir(mode=0o700)
    backup_database(database_url, database_dump)
    if all(values.get(key) for key in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")):
        copied = copy_s3_objects(object_root, values["S3_ENDPOINT_URL"], values["S3_ACCESS_KEY_ID"], values["S3_SECRET_ACCESS_KEY"], bucket)
    elif values.get("VVAULT_OBJECT_STORAGE_URL") and values.get("VVAULT_OBJECT_STORAGE_SERVICE_KEY"):
        copied = copy_rest_objects(object_root, values["VVAULT_OBJECT_STORAGE_URL"], values["VVAULT_OBJECT_STORAGE_SERVICE_KEY"], bucket)
    else:
        fail("runtime object-storage configuration is missing")
    database_digest, _ = digest_tree(root)
    object_digest, object_count = digest_tree(object_root)
    if copied != object_count:
        fail("object-storage copy count verification failed")
    database_receipt = receipts / f"{backup_id}-database.json"
    object_receipt = receipts / f"{backup_id}-object-storage.json"
    write_receipt(database_receipt, "database", f"{backup_id}-database", database_dump, database_digest)
    write_receipt(object_receipt, "object_storage", f"{backup_id}-object-storage", object_root, object_digest, object_count)
    print(f"VVAULT_DATABASE_BACKUP_RECEIPT_PATH={database_receipt}")
    print(f"VVAULT_DATABASE_BACKUP_RECEIPT_ID={backup_id}-database")
    print(f"VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH={object_receipt}")
    print(f"VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID={backup_id}-object-storage")


if __name__ == "__main__":
    main()
