#!/usr/bin/env bash

set -Eeuo pipefail

REPO="/opt/vvault-public"
FRONTEND="/var/www/vvault"
BACKUP_ROOT="/opt/deploy/backups"
BRANCH="recovery/vvault-preview-icons"
SERVICE="vvault-backend.service"
READY_URL="http://127.0.0.1:8000/api/ready"
LOCK_FILE="/tmp/vvault-deploy.lock"

OLD_REF=""
BACKUP=""
PUBLISHED=0
RESTART_ATTEMPTED=0

log() { printf '[vvault-deploy] %s\n' "$*"; }

rollback() {
  local status=$?
  trap - ERR INT TERM
  log "deployment failed; restoring the previous release"

  if (( PUBLISHED )) && [[ -n "$BACKUP" && -d "$BACKUP" ]]; then
    rm -rf "${FRONTEND:?}"/*
    cp -R "$BACKUP"/. "$FRONTEND"/
  fi

  if [[ -n "$OLD_REF" ]]; then
    git -C "$REPO" checkout --detach "$OLD_REF" >/dev/null 2>&1 || true
  fi

  if (( RESTART_ATTEMPTED )); then
    sudo systemctl restart "$SERVICE" || true
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
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || {
  log "repository is dirty; refusing deployment"
  exit 1
}

OLD_REF="$(git rev-parse HEAD)"
log "fetching $BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
NEW_REF="$(git rev-parse HEAD)"

log "installing locked frontend dependencies"
npm ci --ignore-scripts
log "building frontend"
./node_modules/.bin/webpack --mode production
[[ -s "$REPO/dist/index.html" ]] || { log "build is missing dist/index.html"; exit 1; }

BACKUP="$BACKUP_ROOT/vvault-$(date -u +%Y%m%d-%H%M%S)"
log "backing up the current frontend to $BACKUP"
mkdir -p "$BACKUP"
cp -a "$FRONTEND"/. "$BACKUP"/

log "publishing frontend"
rm -rf "${FRONTEND:?}"/*
cp -R "$REPO/dist"/. "$FRONTEND"/
PUBLISHED=1

log "restarting $SERVICE"
RESTART_ATTEMPTED=1
sudo systemctl restart "$SERVICE"

log "verifying canonical readiness"
READY_JSON="$(curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 2 "$READY_URL")"
python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert payload.get("ready") is True
assert payload.get("authority") == "vvault_body"
assert payload.get("storage_owner") == "ovvaults.vault_files"
assert payload.get("transcript_owner") == "ovvaults.transcripts"
' <<<"$READY_JSON"

trap - ERR INT TERM
log "deployment successful: $OLD_REF -> $NEW_REF"
