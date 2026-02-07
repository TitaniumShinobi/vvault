#!/usr/bin/env bash
set -euo pipefail

SSH_HOST="${SSH_HOST:-vvault-server}"
SSH_USER="${SSH_USER:-vvault}"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"

# Nginx docroot for vvault.thewreck.org on the droplet.
REMOTE_ROOT="${REMOTE_ROOT:-/var/www/vvault}"

# Stage first (no sudo required), then promote with sudo.
REMOTE_STAGE="${REMOTE_STAGE:-/tmp/vvault-deploy}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

command -v rsync >/dev/null || die 'rsync is required'
command -v ssh >/dev/null || die 'ssh is required'
command -v npm >/dev/null || die 'npm is required'

log "Building frontend..."
npm run build

log "Ensuring remote stage directory exists..."
ssh "${SSH_TARGET}" "mkdir -p '${REMOTE_STAGE}'"

log "Syncing dist/ to stage: ${SSH_TARGET}:${REMOTE_STAGE}/dist/ ..."
rsync -az --delete \
  --exclude='.DS_Store' \
  dist/ \
  "${SSH_TARGET}:${REMOTE_STAGE}/dist/"

if [ -d "assets" ]; then
  log "Syncing assets/ to stage: ${SSH_TARGET}:${REMOTE_STAGE}/assets/ ..."
  rsync -az --delete \
    --exclude='.DS_Store' \
    assets/ \
    "${SSH_TARGET}:${REMOTE_STAGE}/assets/"
else
  log "No assets/ directory found locally; skipping."
fi

log "Promoting staged files to ${REMOTE_ROOT} (sudo)..."
ssh "${SSH_TARGET}" bash -s -- "${REMOTE_ROOT}" "${REMOTE_STAGE}" <<'REMOTE'
set -euo pipefail

REMOTE_ROOT="$1"
REMOTE_STAGE="$2"

sudo mkdir -p "${REMOTE_ROOT}" "${REMOTE_ROOT}/assets"
sudo rsync -a --delete "${REMOTE_STAGE}/dist/" "${REMOTE_ROOT}/"
if [ -d "${REMOTE_STAGE}/assets" ]; then
  sudo rsync -a --delete "${REMOTE_STAGE}/assets/" "${REMOTE_ROOT}/assets/"
  # Nginx runs as www-data; make sure assets stay world-readable even if local perms are restrictive.
  sudo find "${REMOTE_ROOT}/assets" -type d -exec chmod 755 {} +
  sudo find "${REMOTE_ROOT}/assets" -type f -exec chmod 644 {} +
fi

rm -rf "${REMOTE_STAGE}"
REMOTE

log "Done. Verify: https://vvault.thewreck.org"
