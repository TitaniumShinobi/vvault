#!/usr/bin/env bash
#
# deploy_vvault.sh
#
# Server-side deploy helper for VVAULT. Designed to be safe and noisy:
# - Pull latest code from `main`
# - Build frontend (`npm run build`)
# - Restart backend (Gunicorn via systemd)
# - Validate + reload nginx
#
# Logging: each step ends with `[OK]` or `[FAIL]`.
#
# Configuration (override via env vars):
# - VVAULT_REPO_DIR:    path to vvault git repo (default: script dir)
# - VVAULT_FRONTEND_DIR path to frontend dir (default: /frontend, then <repo>/frontend, then <repo>)
# - VVAULT_BACKEND_DIR  path to backend dir  (default: /backend, then <repo>/backend, then <repo>)
# - VVAULT_GUNICORN_SERVICE: systemd unit name (default: autodetect common names)
# - VVAULT_GIT_REMOTE:  git remote name (default: origin)
#

set -u

ok() {
  printf '[OK] %s\n' "$*"
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

run_step() {
  local desc="$1"
  shift
  if "$@"; then
    ok "$desc"
  else
    fail "$desc"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${VVAULT_REPO_DIR:-$SCRIPT_DIR}"
GIT_REMOTE="${VVAULT_GIT_REMOTE:-origin}"

FRONTEND_DIR="${VVAULT_FRONTEND_DIR:-/frontend}"
BACKEND_DIR="${VVAULT_BACKEND_DIR:-/backend}"

require_cmd git
require_cmd npm
require_cmd sudo
require_cmd systemctl

run_step "cd repo (${REPO_DIR})" bash -lc "cd \"${REPO_DIR}\""

if [ ! -d "${REPO_DIR}/.git" ]; then
  fail "Not a git repo: ${REPO_DIR} (set VVAULT_REPO_DIR)"
fi

# Refuse to pull over local changes to avoid surprise conflicts.
cd "${REPO_DIR}" || fail "Cannot cd to repo: ${REPO_DIR}"
if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Working tree is dirty; commit/stash before deploy"
fi

run_step "git fetch ${GIT_REMOTE}" git fetch "${GIT_REMOTE}"
run_step "git checkout main" git checkout main
run_step "git pull ${GIT_REMOTE} main" git pull --ff-only "${GIT_REMOTE}" main

resolve_frontend_dir() {
  if [ -f "${FRONTEND_DIR}/package.json" ]; then
    return 0
  fi
  if [ -f "${REPO_DIR}/frontend/package.json" ]; then
    FRONTEND_DIR="${REPO_DIR}/frontend"
    return 0
  fi
  if [ -f "${REPO_DIR}/package.json" ]; then
    FRONTEND_DIR="${REPO_DIR}"
    return 0
  fi
  return 1
}

if ! resolve_frontend_dir; then
  fail "Cannot locate frontend package.json (set VVAULT_FRONTEND_DIR)"
fi

cd "${FRONTEND_DIR}" || fail "Cannot cd to frontend: ${FRONTEND_DIR}"
if [ ! -d node_modules ]; then
  run_step "frontend npm ci" npm ci
fi
run_step "frontend npm run build" npm run build

resolve_backend_dir() {
  if [ -d "${BACKEND_DIR}" ]; then
    return 0
  fi
  if [ -d "${REPO_DIR}/backend" ]; then
    BACKEND_DIR="${REPO_DIR}/backend"
    return 0
  fi
  if [ -d "${REPO_DIR}" ]; then
    BACKEND_DIR="${REPO_DIR}"
    return 0
  fi
  return 1
}

if ! resolve_backend_dir; then
  fail "Cannot locate backend dir (set VVAULT_BACKEND_DIR)"
fi

pick_gunicorn_service() {
  if [ -n "${VVAULT_GUNICORN_SERVICE:-}" ]; then
    echo "${VVAULT_GUNICORN_SERVICE}"
    return 0
  fi

  # Try common unit names; only select units that exist.
  local svc
  for svc in vvault gunicorn vvault-backend vvault-gunicorn; do
    if systemctl list-unit-files --type=service --no-pager | awk '{print $1}' | grep -qx "${svc}.service"; then
      echo "${svc}"
      return 0
    fi
  done
  return 1
}

GUNICORN_SERVICE="$(pick_gunicorn_service)" || fail "Gunicorn service not found; set VVAULT_GUNICORN_SERVICE"
run_step "restart ${GUNICORN_SERVICE}.service" sudo systemctl restart "${GUNICORN_SERVICE}"

# Nginx reload with config validation.
require_cmd nginx
run_step "nginx config test" sudo nginx -t
run_step "nginx reload" sudo systemctl reload nginx

ok "Deploy complete"

