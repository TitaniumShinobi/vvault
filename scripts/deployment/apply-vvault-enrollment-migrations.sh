#!/usr/bin/env bash
# Apply the forward-only VVAULT enrollment migrations only after an operator
# has supplied independently verified database and object-storage backup
# receipts. This script never creates, restores, or prints backups/secrets.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MIGRATION_DIR="${VVAULT_MIGRATION_DIR:-$REPO_ROOT/vvault/migrations}"
RECEIPT_DIR="${VVAULT_MIGRATION_RECEIPT_DIR:-/opt/deploy/receipts}"
DATABASE_URL="${VVAULT_BODY_DATABASE_URL:-}"
DATABASE_BACKUP_RECEIPT_PATH="${VVAULT_DATABASE_BACKUP_RECEIPT_PATH:-}"
DATABASE_BACKUP_RECEIPT_ID="${VVAULT_DATABASE_BACKUP_RECEIPT_ID:-}"
OBJECT_BACKUP_RECEIPT_PATH="${VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH:-}"
OBJECT_BACKUP_RECEIPT_ID="${VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID:-}"
DEPLOY_REF="${VVAULT_DEPLOY_REF:-unknown}"
DRY_RUN="${VVAULT_MIGRATION_DRY_RUN:-0}"

readonly -a VERSIONS=(0033 0034 0035)
readonly -a FILES=(
  0033_identity_directory.up.sql
  0034_enrollment_session_hardening.up.sql
  0035_chatty_pairing_intents.up.sql
)

log() { printf '[vvault-migrations] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

validate_receipt() {
  local kind="$1" path="$2" expected_id="$3"
  [[ "$path" = /* && -f "$path" && -r "$path" ]] || die "$kind backup receipt must be an absolute readable file"
  [[ "$expected_id" =~ ^[A-Za-z0-9._:-]{8,128}$ ]] || die "$kind backup receipt ID is invalid"
  python3 - "$kind" "$path" "$expected_id" <<'PY'
import json
import sys

kind, path, expected_id = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as handle:
        receipt = json.load(handle)
except (OSError, ValueError) as exc:
    raise SystemExit(f"{kind} backup receipt is not valid JSON") from exc

if receipt.get("kind") != kind or receipt.get("backup_id") != expected_id:
    raise SystemExit(f"{kind} backup receipt kind or ID does not match")
if receipt.get("verified") is not True:
    raise SystemExit(f"{kind} backup receipt is not verified")
PY
}

require_command python3
require_command sha256sum

validate_receipt database "$DATABASE_BACKUP_RECEIPT_PATH" "$DATABASE_BACKUP_RECEIPT_ID"
validate_receipt object_storage "$OBJECT_BACKUP_RECEIPT_PATH" "$OBJECT_BACKUP_RECEIPT_ID"

checksums=()
for index in "${!VERSIONS[@]}"; do
  file="$MIGRATION_DIR/${FILES[$index]}"
  [[ -s "$file" ]] || die "required migration is missing: ${FILES[$index]}"
  checksums+=("$(sha256sum "$file" | awk '{print $1}')")
done

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry run passed: verified backup receipts and checked migration files 0033-0035"
  exit 0
fi

require_command psql
[[ -n "$DATABASE_URL" ]] || die "VVAULT_BODY_DATABASE_URL is required"

# Create a restricted libpq service file so psql receives the full database
# URL without placing credentials in its command-line arguments.  PGDATABASE
# cannot carry a URI: libpq interprets it as a literal database name.
connection_service="$(mktemp "${TMPDIR:-/tmp}/vvault-pg-service.XXXXXX")"
chmod 0600 "$connection_service"
python3 - "$DATABASE_URL" "$connection_service" <<'PY'
import os
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

url, path = sys.argv[1:]
parsed = urlsplit(url)
if parsed.scheme not in {"postgres", "postgresql"} or parsed.fragment:
    raise SystemExit("VVAULT_BODY_DATABASE_URL must be a PostgreSQL URL")

query = dict(parse_qsl(parsed.query, keep_blank_values=True))
values = {
    "host": query.pop("host", "") or (parsed.hostname or ""),
    "port": query.pop("port", "") or (str(parsed.port) if parsed.port else ""),
    "user": unquote(parsed.username or query.pop("user", "")),
    "password": unquote(parsed.password or query.pop("password", "")),
    "dbname": unquote(parsed.path.lstrip("/")),
}
for key in ("sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"):
    if key in query:
        values[key] = query[key]
if not values["dbname"]:
    raise SystemExit("VVAULT_BODY_DATABASE_URL must include a database name")
if any("\n" in value or "\r" in value for value in values.values()):
    raise SystemExit("VVAULT_BODY_DATABASE_URL contains an invalid newline")

def escape(value):
    return value.replace("\\", "\\\\")

fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write("[vvault_enrollment_migrations]\n")
    for key, value in values.items():
        if value:
            handle.write(f"{key}={escape(value)}\n")
PY

# Ledger creation and validation happen in the same advisory-locked database
# transaction as application. Existing rows must match their checked-in bytes.
sql_file="$(mktemp "${TMPDIR:-/tmp}/vvault-migrations.XXXXXX.sql")"
trap 'rm -f "$sql_file" "$connection_service"' EXIT
{
  printf '%s\n' 'BEGIN;'
  printf '%s\n' 'SELECT pg_advisory_xact_lock(hashtext('"'"'vvault-enrollment-migrations-0033-0035'"'"'));'
  printf '%s\n' 'CREATE SCHEMA IF NOT EXISTS ovvaults;'
  printf '%s\n' 'CREATE TABLE IF NOT EXISTS ovvaults.schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());'
  for index in "${!VERSIONS[@]}"; do
    version="${VERSIONS[$index]}" checksum="${checksums[$index]}"
    printf "DO \$\$ BEGIN IF EXISTS (SELECT 1 FROM ovvaults.schema_migrations WHERE version = '%s' AND checksum <> '%s') THEN RAISE EXCEPTION 'migration %% checksum mismatch', '%s'; END IF; END \$\$;\n" "$version" "$checksum" "$version"
    printf "SELECT NOT EXISTS (SELECT 1 FROM ovvaults.schema_migrations WHERE version = '%s') AS apply_%s \\gset\n" "$version" "$version"
    printf "\\if :apply_%s\n" "$version"
    printf "\\i %s\n" "$MIGRATION_DIR/${FILES[$index]}"
    printf "INSERT INTO ovvaults.schema_migrations(version, checksum) VALUES ('%s', '%s');\n" "$version" "$checksum"
    printf '%s\n' '\endif'
  done
  printf '%s\n' 'COMMIT;'
} >"$sql_file"

log "applying forward-only enrollment migrations 0033-0035"
# Keep the connection string out of command-line arguments and all receipts.
PGSERVICEFILE="$connection_service" PGSERVICE="vvault_enrollment_migrations" \
  psql --no-psqlrc -X -v ON_ERROR_STOP=1 -f "$sql_file" >/dev/null

mkdir -p "$RECEIPT_DIR"
chmod 0750 "$RECEIPT_DIR"
receipt_path="$RECEIPT_DIR/vvault-enrollment-migrations-$(date -u +%Y%m%dT%H%M%SZ).json"
python3 - "$receipt_path" "$DEPLOY_REF" "$DATABASE_BACKUP_RECEIPT_ID" "$OBJECT_BACKUP_RECEIPT_ID" "${checksums[@]}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, deploy_ref, database_backup_id, object_backup_id, *checksums = sys.argv[1:]
payload = {
    "kind": "vvault_enrollment_migration_receipt",
    "applied_at": datetime.now(timezone.utc).isoformat(),
    "deploy_ref": deploy_ref,
    "migrations": [
        {"version": version, "sha256": checksum}
        for version, checksum in zip(("0033", "0034", "0035"), checksums)
    ],
    "backup_receipts": {
        "database_backup_id": database_backup_id,
        "object_storage_backup_id": object_backup_id,
    },
    "rollback": "forward_only_restore_verified_backup_required",
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\n")
PY

log "migration receipt recorded: $(basename "$receipt_path")"
