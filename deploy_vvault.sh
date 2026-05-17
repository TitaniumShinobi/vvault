#!/usr/bin/env bash
#
# deploy_vvault.sh
#
# Server-side deploy helper for the canonical VVAULT runtime.
# Intended target:
# - built frontend served by Flask from dist/
# - Gunicorn bound to 127.0.0.1:8000
# - Nginx as public ingress / TLS terminator
# - strict readiness gate at /api/ready
#
# Logging: each step ends with [OK] or [FAIL].
#
# Configuration (override via env vars):
# - VVAULT_REPO_DIR:          path to vvault git repo (default: script dir)
# - VVAULT_FRONTEND_DIR:      frontend dir (default: repo root if package.json exists)
# - VVAULT_BACKEND_DIR:       backend dir (default: repo root)
# - VVAULT_GIT_REMOTE:        git remote name (default: origin)
# - VVAULT_GIT_REF:           branch to deploy (default: current checked-out branch)
# - VVAULT_VENV_DIR:          venv path (default: <repo>/venv)
# - VVAULT_GUNICORN_SERVICE:  systemd unit name (default: autodetect common names)
# - VVAULT_NGINX_SERVICE:     nginx systemd unit name (default: nginx)
# - VVAULT_LOCAL_BASE_URL:    local backend probe URL (default: http://127.0.0.1:8000)
# - VVAULT_PUBLIC_BASE_URL:   optional public HTTPS URL to probe after nginx reload
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
GIT_REF="${VVAULT_GIT_REF:-}"
VENV_DIR="${VVAULT_VENV_DIR:-${REPO_DIR}/venv}"
NGINX_SERVICE="${VVAULT_NGINX_SERVICE:-nginx}"
LOCAL_BASE_URL="${VVAULT_LOCAL_BASE_URL:-http://127.0.0.1:8000}"
PUBLIC_BASE_URL="${VVAULT_PUBLIC_BASE_URL:-}"

FRONTEND_DIR="${VVAULT_FRONTEND_DIR:-${REPO_DIR}}"
BACKEND_DIR="${VVAULT_BACKEND_DIR:-${REPO_DIR}}"

require_cmd git
require_cmd npm
require_cmd python3
require_cmd sudo
require_cmd systemctl
require_cmd curl

run_step "cd repo (${REPO_DIR})" bash -lc "cd \"${REPO_DIR}\""

if [ ! -d "${REPO_DIR}/.git" ]; then
  fail "Not a git repo: ${REPO_DIR} (set VVAULT_REPO_DIR)"
fi

# Refuse to pull over local changes to avoid surprise conflicts.
cd "${REPO_DIR}" || fail "Cannot cd to repo: ${REPO_DIR}"
if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Working tree is dirty; commit/stash before deploy"
fi

resolve_git_ref() {
  if [ -n "${GIT_REF}" ]; then
    return 0
  fi
  GIT_REF="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  [ -n "${GIT_REF}" ]
}

resolve_git_ref || fail "Cannot infer deployment branch; set VVAULT_GIT_REF explicitly"

run_step "git fetch ${GIT_REMOTE}" git fetch "${GIT_REMOTE}"

if git show-ref --verify --quiet "refs/heads/${GIT_REF}"; then
  run_step "git checkout ${GIT_REF}" git checkout "${GIT_REF}"
else
  run_step "git checkout ${GIT_REF} from ${GIT_REMOTE}/${GIT_REF}" \
    git checkout -b "${GIT_REF}" --track "${GIT_REMOTE}/${GIT_REF}"
fi

run_step "git pull --ff-only ${GIT_REMOTE} ${GIT_REF}" git pull --ff-only "${GIT_REMOTE}" "${GIT_REF}"

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

if [ -f package-lock.json ]; then
  run_step "frontend npm ci" npm ci
else
  run_step "frontend npm install" npm install
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

if [ ! -d "${VENV_DIR}" ]; then
  run_step "create venv (${VENV_DIR})" python3 -m venv "${VENV_DIR}"
fi

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  fail "Python executable missing in ${VENV_DIR}; set VVAULT_VENV_DIR"
fi

run_step "backend pip install -r requirements.txt" \
  "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt"

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
run_step "nginx reload" sudo systemctl reload "${NGINX_SERVICE}"

check_url_status() {
  local url="$1"
  local expected="$2"
  local tmp_body
  local status
  tmp_body="$(mktemp)"
  status="$(curl -sS -o "${tmp_body}" -w '%{http_code}' "${url}")" || {
    rm -f "${tmp_body}"
    return 1
  }
  cat "${tmp_body}"
  rm -f "${tmp_body}"
  [ "${status}" = "${expected}" ]
}

run_step "local /api/status returns 200" \
  check_url_status "${LOCAL_BASE_URL}/api/status" 200
run_step "local /api/health returns 200" \
  check_url_status "${LOCAL_BASE_URL}/api/health" 200
run_step "local /api/ready returns 200" \
  check_url_status "${LOCAL_BASE_URL}/api/ready" 200

if [ -n "${PUBLIC_BASE_URL}" ]; then
  run_step "public /api/status returns 200" \
    check_url_status "${PUBLIC_BASE_URL}/api/status" 200
  run_step "public /api/health returns 200" \
    check_url_status "${PUBLIC_BASE_URL}/api/health" 200
  run_step "public /api/ready returns 200" \
    check_url_status "${PUBLIC_BASE_URL}/api/ready" 200
fi

ok "Deploy complete (${GIT_REF})"
