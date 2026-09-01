#!/usr/bin/env bash
# Restore a previously captured VVAULT static frontend snapshot. This is
# deliberately frontend-only: it neither changes the checkout nor touches the
# database, runtime environment, or backend service.
set -Eeuo pipefail

FRONTEND="${VVAULT_FRONTEND_ROOT:-/var/www/vvault}"
BACKUP_ROOT="${VVAULT_FRONTEND_BACKUP_ROOT:-/opt/deploy/backups}"
SNAPSHOT="${VVAULT_FRONTEND_SNAPSHOT:-}"
READY_URL="${VVAULT_BACKEND_READY_URL:-http://127.0.0.1:8000/api/ready}"
LOCK_FILE="${VVAULT_FRONTEND_RESTORE_LOCK:-/tmp/vvault-deploy.lock}"

log() { printf '[vvault-frontend-restore] %s\n' "$*"; }
die() { log "$*" >&2; exit 1; }

[[ "$SNAPSHOT" =~ ^/opt/deploy/backups/vvault-[0-9]{8}-[0-9]{6}$ ]] || \
  die "snapshot must be a named VVAULT frontend backup"
[[ "$SNAPSHOT" == "$BACKUP_ROOT"/* ]] || die "snapshot is outside the approved backup root"
[[ -d "$SNAPSHOT" && -f "$SNAPSHOT/index.html" && -s "$SNAPSHOT/index.html" ]] || \
  die "snapshot is missing a usable index.html"
[[ -d "$FRONTEND" ]] || die "frontend root is unavailable"

for command in curl flock find cp mkdir; do
  command -v "$command" >/dev/null 2>&1 || die "missing required command: $command"
done

exec 9>"$LOCK_FILE"
flock -n 9 || die "another VVAULT deployment or restore is already running"

# Confirm the backend remains healthy before and after the static-file change.
verify_backend() {
  curl --fail --silent --show-error --retry 3 --retry-all-errors --retry-delay 1 "$READY_URL" |
    python3 -c 'import json, sys; assert json.load(sys.stdin).get("ready") is True'
}

verify_backend
rollback="$(mktemp -d "$BACKUP_ROOT/vvault-frontend-restore-rollback.XXXXXX")"
cleanup() { rm -rf "$rollback"; }
trap cleanup EXIT

cp -a "$FRONTEND"/. "$rollback"/
restore_previous() {
  find "$FRONTEND" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  cp -a "$rollback"/. "$FRONTEND"/
}
trap 'restore_previous' ERR INT TERM

log "restoring verified frontend snapshot"
find "$FRONTEND" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$SNAPSHOT"/. "$FRONTEND"/
[[ -s "$FRONTEND/index.html" ]] || die "restored frontend is missing index.html"
verify_backend

trap - ERR INT TERM
log "frontend restore completed"
