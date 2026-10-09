#!/usr/bin/env bash

set -Eeuo pipefail

REPO="$(readlink -f /opt/vvault-public)"
FRONTEND="/var/www/vvault"
BACKUP_ROOT="/opt/deploy/backups"
BRANCH="production"
SERVICE="vvault-backend.service"
READY_URL="http://127.0.0.1:8000/api/ready"
LOCK_FILE="/tmp/vvault-deploy.lock"
ENV_FILE="${VVAULT_RUNTIME_ENV_FILE:-/opt/vvault-public/.env}"
EXPECTED_SERVICE_USER="${VVAULT_SERVICE_USER:-vvault}"
EXPECTED_SERVICE_GROUP="${VVAULT_SERVICE_GROUP:-vvault}"
DEPLOY_MODE="${VVAULT_DEPLOY_MODE:-full}"

OLD_REF=""
BACKUP=""
PUBLISHED=0
RESTART_ATTEMPTED=0
MIGRATIONS_APPLIED=0
SOURCE_CHANGED=0
STAGING=""

auth_gate() {
  local phase="$1" ref="$2"
  [[ "${AUTH_RELEASE_GATE:-}" = /* && -f "$AUTH_RELEASE_GATE" && ! -L "$AUTH_RELEASE_GATE" ]] || { log "AUTH_RELEASE_GATE required"; return 1; }
  [[ "${AUTH_RELEASE_GATE_SHA256:-}" =~ ^[a-f0-9]{64}$ ]] || return 1
  [[ "$(sha256sum "$AUTH_RELEASE_GATE" | cut -d' ' -f1)" == "$AUTH_RELEASE_GATE_SHA256" ]] || return 1
  bash "$AUTH_RELEASE_GATE" "$phase" "$ref"
}

cleanup_stage() {
  if [[ -n "$STAGING" ]]; then
    [[ "$STAGING" == /tmp/vvault-auth-stage.* && -d "$STAGING" && ! -L "$STAGING" ]] || return 1
    rm -rf -- "$STAGING"
    STAGING=""
  fi
}
trap cleanup_stage EXIT

log() { printf '[vvault-deploy] %s\n' "$*"; }

git_repo() {
  git -c safe.directory="$REPO" -C "$REPO" "$@"
}

resolve_runtime_env_file() {
  local candidate service_files
  # The service unit is authoritative when its EnvironmentFile has moved.  Do
  # not print either file contents or values while locating it.
  for candidate in "$ENV_FILE"; do
    if [[ -r "$candidate" ]] && grep -q '^VVAULT_BODY_DATABASE_URL=.' "$candidate"; then
      ENV_FILE="$candidate"
      return 0
    fi
  done
  service_files="$(systemctl show "$SERVICE" -p EnvironmentFiles --value 2>/dev/null || true)"
  while IFS= read -r candidate; do
    [[ "$candidate" = /* ]] || continue
    if [[ -r "$candidate" ]] && grep -q '^VVAULT_BODY_DATABASE_URL=.' "$candidate"; then
      ENV_FILE="$candidate"
      return 0
    fi
  done < <(printf '%s\n' "$service_files" | grep -oE '/[^[:space:]()]+' || true)
  return 1
}

verify_runtime_contract() {
  local require_database_env="${1:-1}"
  local service_properties env_metadata
  service_properties="$(systemctl show "$SERVICE" -p LoadState -p User -p Group)"
  [[ "$service_properties" == *"LoadState=loaded"* ]] || { log "service unit is not loaded"; return 1; }
  [[ "$service_properties" == *"User=$EXPECTED_SERVICE_USER"* ]] || { log "service user contract mismatch"; return 1; }
  [[ "$service_properties" == *"Group=$EXPECTED_SERVICE_GROUP"* ]] || { log "service group contract mismatch"; return 1; }
  if [[ "$require_database_env" != "1" ]]; then
    return 0
  fi
  if ! resolve_runtime_env_file; then
    # Values supplied directly through systemd Environment= are intentionally
    # not printed; the backup helper reads them in-process when necessary.
    systemctl show "$SERVICE" -p Environment --value 2>/dev/null | grep 'VVAULT_BODY_DATABASE_URL=' >/dev/null || {
      log "runtime database configuration is missing from the service"
      return 1
    }
  fi
  [[ -f "$ENV_FILE" ]] || { log "runtime environment file is missing"; return 1; }
  env_metadata="$(stat -c '%U:%G:%a' "$ENV_FILE")"
  [[ "$env_metadata" == *":$EXPECTED_SERVICE_GROUP:640" ]] || { log "runtime environment ownership or mode mismatch"; return 1; }
}

verify_readiness() {
  local ready_json
  ready_json="$(curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 2 "$READY_URL")"
  python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert payload.get("ready") is True
assert payload.get("authority") == "vvault_body"
assert payload.get("storage_owner") == "ovvaults.vault_files"
assert payload.get("transcript_owner") == "ovvaults.transcripts"
' <<<"$ready_json"
}

prepare_enrollment_recovery_receipts() {
  local key value database_path="" database_id="" object_path="" object_id=""
  while IFS='=' read -r key value; do
    case "$key" in
      VVAULT_DATABASE_BACKUP_RECEIPT_PATH) database_path="$value" ;;
      VVAULT_DATABASE_BACKUP_RECEIPT_ID) database_id="$value" ;;
      VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH) object_path="$value" ;;
      VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID) object_id="$value" ;;
      *) log "backup receipt generator returned an invalid field"; return 1 ;;
    esac
  done < <(python3 "$REPO/scripts/deployment/create-vvault-enrollment-backup-receipts.py" --env-file "$ENV_FILE" --systemd-service "$SERVICE")
  [[ "$database_path" = /* && "$object_path" = /* && -n "$database_id" && -n "$object_id" ]] || {
    log "backup receipt generator returned incomplete data"
    return 1
  }
  export VVAULT_DATABASE_BACKUP_RECEIPT_PATH="$database_path"
  export VVAULT_DATABASE_BACKUP_RECEIPT_ID="$database_id"
  export VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH="$object_path"
  export VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID="$object_id"
}

ensure_backup_tools() {
  if command -v pg_dump >/dev/null 2>&1 && command -v pg_restore >/dev/null 2>&1; then
    return 0
  fi
  local tool_root="${VVAULT_BACKUP_TOOL_ROOT:-$BACKUP_ROOT/.tools/postgresql-client}"
  command -v apt-get >/dev/null 2>&1 && command -v dpkg-deb >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && command -v gpg >/dev/null 2>&1 || {
    log "PostgreSQL client tools are missing and no portable package bootstrap is available"
    return 1
  }
  log "bootstrapping a private PostgreSQL client for verified recovery copies"
  if [[ ! -x "$tool_root/usr/bin/pg_dump" || ! -x "$tool_root/usr/bin/pg_restore" ]]; then
    local temporary client_package client_major
    temporary="$(mktemp -d "${TMPDIR:-/tmp}/vvault-pg-client.XXXXXX")"
    client_major="${VVAULT_BACKUP_POSTGRES_MAJOR:-18}"
    [[ "$client_major" =~ ^[0-9]{2}$ ]] || {
      log "configured PostgreSQL backup client major is invalid"
      return 1
    }
    client_package="postgresql-client-$client_major"
    mkdir -p "$tool_root"
    if ! (
      cd "$temporary"
      if ! apt-get download "$client_package" libpq5 >/dev/null 2>&1; then
        codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
        [[ "$codename" =~ ^[a-z]+$ ]] || exit 1
        mkdir -p "$temporary/apt-state/lists/partial" "$temporary/apt-cache/archives/partial"
        curl --fail --silent --show-error --location --output "$temporary/postgresql.asc" \
          https://www.postgresql.org/media/keys/ACCC4CF8.asc
        gpg --dearmor --yes --output "$temporary/postgresql.gpg" "$temporary/postgresql.asc"
        printf 'deb [signed-by=%s] https://apt.postgresql.org/pub/repos/apt %s-pgdg main\n' \
          "$temporary/postgresql.gpg" "$codename" >"$temporary/postgresql.list"
        apt-get -o Dir::Etc::sourcelist="$temporary/postgresql.list" \
          -o Dir::Etc::sourceparts="-" -o Dir::Etc::trusted="$temporary/postgresql.gpg" \
          -o Dir::Etc::trustedparts="-" -o Dir::State="$temporary/apt-state" \
          -o Dir::Cache="$temporary/apt-cache" update -qq
        apt-get -o Dir::Etc::sourcelist="$temporary/postgresql.list" \
          -o Dir::Etc::sourceparts="-" -o Dir::Etc::trusted="$temporary/postgresql.gpg" \
          -o Dir::Etc::trustedparts="-" -o Dir::State="$temporary/apt-state" \
          -o Dir::Cache="$temporary/apt-cache" download "$client_package" libpq5 >/dev/null
      fi
      for package in ./*.deb; do
        dpkg-deb -x "$package" "$tool_root"
      done
    ); then
      rm -rf "$temporary"
      log "private PostgreSQL client bootstrap failed"
      return 1
    fi
    rm -rf "$temporary"
  fi
  local bin_dir
  for bin_dir in "$tool_root/usr/bin" "$tool_root"/usr/lib/postgresql/*/bin; do
    [[ -d "$bin_dir" ]] || continue
    PATH="$bin_dir:$PATH"
  done
  export PATH
  export LD_LIBRARY_PATH="$tool_root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  command -v pg_dump >/dev/null 2>&1 && command -v pg_restore >/dev/null 2>&1 || {
    log "private PostgreSQL client bootstrap did not provide pg_dump and pg_restore"
    return 1
  }
}

rollback() {
  local status=$?
  trap - ERR INT TERM
  if (( MIGRATIONS_APPLIED )); then
    log "deployment failed after forward-only database migrations; automatic code rollback is prohibited"
    log "restore requires the independently verified database/object-storage backup receipts and an operator-led recovery"
    exit "$status"
  fi

  log "deployment failed; restoring the previous release"

  if (( PUBLISHED )) && [[ -n "$BACKUP" && -d "$BACKUP" ]]; then
    rm -rf "${FRONTEND:?}"/*
    cp -R "$BACKUP"/. "$FRONTEND"/
  fi

  if (( SOURCE_CHANGED )) && [[ -n "$OLD_REF" ]]; then
    git_repo checkout --detach "$OLD_REF" >/dev/null 2>&1 || true
  fi

  if (( RESTART_ATTEMPTED )); then
    if ! sudo systemctl restart "$SERVICE" || ! verify_readiness; then
      log "rollback readiness verification failed"
      exit 70
    fi
  fi

  exit "$status"
}
trap rollback ERR INT TERM

for command in curl flock git npm python3; do
  command -v "$command" >/dev/null 2>&1 || { log "missing command: $command"; exit 1; }
done

exec 9>"$LOCK_FILE"
flock -n 9 || { log "another VVAULT deployment is already running"; exit 1; }

cd "$REPO"
[[ -z "$(git_repo status --porcelain --untracked-files=normal)" ]] || {
  log "repository is dirty; refusing deployment"
  exit 1
}

case "$DEPLOY_MODE" in
  full|backend-only) ;;
  *) log "unsupported deployment mode"; exit 1 ;;
esac

verify_runtime_contract 1

OLD_REF="$(git_repo rev-parse HEAD)"
log "fetching $BRANCH"
git_repo fetch origin "$BRANCH:refs/remotes/origin/$BRANCH"
NEW_REF="$(git_repo rev-parse "origin/$BRANCH")"
# Gate the immutable candidate tree before changing the serving checkout.
auth_gate candidate "$NEW_REF"
SOURCE_CHANGED=1
git_repo checkout -B "$BRANCH" "$NEW_REF"

if [[ "$DEPLOY_MODE" == "backend-only" ]]; then
  log "restarting backend from the tracked production checkout (frontend and database unchanged)"
  auth_gate candidate "$NEW_REF"
  RESTART_ATTEMPTED=1
  sudo systemctl restart "$SERVICE"
  log "verifying canonical readiness"
  verify_readiness
  auth_gate serving "$NEW_REF"
  trap - ERR INT TERM
  log "backend-only deployment successful: $OLD_REF -> $NEW_REF"
  exit 0
fi

log "creating verified database and object-storage recovery receipts"
ensure_backup_tools
prepare_enrollment_recovery_receipts
log "schema compatibility verified; migrations require a separate approved operation"

log "installing locked frontend dependencies"
npm ci --ignore-scripts
log "building frontend"
./node_modules/.bin/webpack --mode production
[[ -s "$REPO/dist/index.html" ]] || { log "build is missing dist/index.html"; exit 1; }

BACKUP="$BACKUP_ROOT/vvault-$(date -u +%Y%m%d-%H%M%S)"
log "backing up the current frontend to $BACKUP"
mkdir -p "$BACKUP"
cp -a "$FRONTEND"/. "$BACKUP"/

STAGING="$(mktemp -d /tmp/vvault-auth-stage.XXXXXXXX)"
cp -a "$REPO/dist"/. "$STAGING"/
auth_gate candidate "$NEW_REF"
log "publishing frontend"
PUBLISHED=1
rm -rf "${FRONTEND:?}"/*
cp -R "$STAGING"/. "$FRONTEND"/

auth_gate candidate "$NEW_REF"
log "restarting $SERVICE"
RESTART_ATTEMPTED=1
sudo systemctl restart "$SERVICE"

log "verifying canonical readiness"
verify_readiness
auth_gate serving "$NEW_REF"
cleanup_stage

trap - ERR INT TERM
log "deployment successful: $OLD_REF -> $NEW_REF (schema verified; no migrations applied)"
