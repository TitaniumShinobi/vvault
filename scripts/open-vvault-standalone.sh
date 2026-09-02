#!/usr/bin/env bash
set -euo pipefail

# Before changing ports, auth routing, or readiness behavior, read
# docs/VVAULT_LOCAL_RESTORE.md and keep restore drills off live ports.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_PORT="${VVAULT_FRONTEND_PORT:-7784}"
BACKEND_PORT="${VVAULT_BACKEND_PORT:-8000}"
HOST="${VVAULT_HOST:-localhost}"
FRONTEND_BIND_HOST="${VVAULT_FRONTEND_BIND_HOST:-::}"
FRONTEND_URL="http://${HOST}:${FRONTEND_PORT}/"
BACKEND_HEALTH_URL="http://${HOST}:${BACKEND_PORT}/api/health"
LOG_DIR="${VVAULT_LOG_DIR:-/tmp}"
BACKEND_LOG="${LOG_DIR}/vvault-backend.log"
FRONTEND_LOG="${LOG_DIR}/vvault-frontend.log"
BACKEND_PID_FILE="${LOG_DIR}/vvault-backend.pid"
FRONTEND_PID_FILE="${LOG_DIR}/vvault-frontend.pid"
DATABASE_TUNNEL_LOG="${LOG_DIR}/vvault-database-tunnel.log"
DATABASE_TUNNEL_PID_FILE="${LOG_DIR}/vvault-database-tunnel.pid"
PYTHON_BIN="${VVAULT_PYTHON_BIN:-}"
KEYCHAIN_ACCOUNT="${VVAULT_KEYCHAIN_ACCOUNT:-${USER:-$(id -un)}}"
BODY_DATABASE_KEYCHAIN_SERVICE="${VVAULT_BODY_DATABASE_KEYCHAIN_SERVICE:-org.thewreck.vvault.body-database-url}"
OBJECT_STORAGE_URL_KEYCHAIN_SERVICE="${VVAULT_OBJECT_STORAGE_URL_KEYCHAIN_SERVICE:-org.thewreck.vvault.object-storage-url}"
OBJECT_STORAGE_KEY_KEYCHAIN_SERVICE="${VVAULT_OBJECT_STORAGE_KEY_KEYCHAIN_SERVICE:-org.thewreck.vvault.object-storage-service-key}"

keychain_secret() {
  local service="$1"
  if command -v security >/dev/null 2>&1; then
    security find-generic-password -a "${KEYCHAIN_ACCOUNT}" -s "${service}" -w 2>/dev/null || true
  fi
}

if [ -z "${VVAULT_BODY_DATABASE_URL:-}" ]; then
  VVAULT_BODY_DATABASE_URL="$(keychain_secret "${BODY_DATABASE_KEYCHAIN_SERVICE}")"
fi
if [ -z "${VVAULT_OBJECT_STORAGE_URL:-}" ]; then
  VVAULT_OBJECT_STORAGE_URL="$(keychain_secret "${OBJECT_STORAGE_URL_KEYCHAIN_SERVICE}")"
fi
if [ -z "${VVAULT_OBJECT_STORAGE_SERVICE_KEY:-}" ]; then
  VVAULT_OBJECT_STORAGE_SERVICE_KEY="$(keychain_secret "${OBJECT_STORAGE_KEY_KEYCHAIN_SERVICE}")"
fi
VVAULT_STORAGE_BUCKET="${VVAULT_STORAGE_BUCKET:-vault-files}"

if [ -z "${VVAULT_BODY_DATABASE_URL}" ]; then
  echo "VVAULT_BODY_DATABASE_URL is unavailable from the environment or macOS Keychain." >&2
  exit 1
fi
if [ -z "${VVAULT_OBJECT_STORAGE_URL}" ] || [ -z "${VVAULT_OBJECT_STORAGE_SERVICE_KEY}" ]; then
  echo "VVAULT object-storage authority is unavailable from the environment or macOS Keychain." >&2
  exit 1
fi

export VVAULT_BODY_DATABASE_URL
export VVAULT_OBJECT_STORAGE_URL
export VVAULT_OBJECT_STORAGE_SERVICE_KEY
export VVAULT_STORAGE_BUCKET

if [ -z "${PYTHON_BIN}" ]; then
  if [ -x "${ROOT_DIR}/.venv/bin/python3" ]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python3"
  elif [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

is_listening() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

DATABASE_TUNNEL_ENABLED="${VVAULT_DATABASE_TUNNEL_ENABLED:-1}"
DATABASE_TUNNEL_BIND_HOST="${VVAULT_DATABASE_TUNNEL_BIND_HOST:-127.0.0.1}"
DATABASE_TUNNEL_PORT="${VVAULT_DATABASE_TUNNEL_PORT:-25432}"
DATABASE_SSH_HOST="${VVAULT_DATABASE_SSH_HOST:-165.245.136.194}"
DATABASE_SSH_USER="${VVAULT_DATABASE_SSH_USER:-root}"
DATABASE_SSH_KEY="${VVAULT_DATABASE_SSH_KEY:-${HOME}/.ssh/digitalocean_vvault}"

if [ "${DATABASE_TUNNEL_ENABLED}" = "1" ]; then
  DATABASE_REMOTE_HOST="$(VVAULT_BODY_DATABASE_URL="${VVAULT_BODY_DATABASE_URL}" "${PYTHON_BIN}" - <<'PY'
import os
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["VVAULT_BODY_DATABASE_URL"])
if not parsed.hostname:
    raise SystemExit("VVAULT_BODY_DATABASE_URL has no database host")
print(parsed.hostname)
PY
)"
  DATABASE_REMOTE_PORT="$(VVAULT_BODY_DATABASE_URL="${VVAULT_BODY_DATABASE_URL}" "${PYTHON_BIN}" - <<'PY'
import os
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["VVAULT_BODY_DATABASE_URL"])
print(parsed.port or 5432)
PY
)"

  TUNNEL_RUNNING=0
  if [ -f "${DATABASE_TUNNEL_PID_FILE}" ]; then
    DATABASE_TUNNEL_PID="$(cat "${DATABASE_TUNNEL_PID_FILE}" 2>/dev/null || true)"
    if [ -n "${DATABASE_TUNNEL_PID}" ] && kill -0 "${DATABASE_TUNNEL_PID}" 2>/dev/null && is_listening "${DATABASE_TUNNEL_PORT}"; then
      TUNNEL_RUNNING=1
    fi
  fi

  if [ "${TUNNEL_RUNNING}" != "1" ]; then
    if is_listening "${DATABASE_TUNNEL_PORT}"; then
      echo "VVAULT database tunnel port ${DATABASE_TUNNEL_PORT} is occupied by another process." >&2
      exit 1
    fi
    if [ ! -f "${DATABASE_SSH_KEY}" ]; then
      echo "VVAULT database tunnel SSH key is missing: ${DATABASE_SSH_KEY}" >&2
      exit 1
    fi

    echo "Starting secure VVAULT database tunnel..."
    nohup ssh \
      -i "${DATABASE_SSH_KEY}" \
      -o BatchMode=yes \
      -o IdentitiesOnly=yes \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -N \
      -L "${DATABASE_TUNNEL_BIND_HOST}:${DATABASE_TUNNEL_PORT}:${DATABASE_REMOTE_HOST}:${DATABASE_REMOTE_PORT}" \
      "${DATABASE_SSH_USER}@${DATABASE_SSH_HOST}" \
      </dev/null >"${DATABASE_TUNNEL_LOG}" 2>&1 &
    DATABASE_TUNNEL_PID="$!"
    echo "${DATABASE_TUNNEL_PID}" >"${DATABASE_TUNNEL_PID_FILE}"

    for _ in $(seq 1 10); do
      if is_listening "${DATABASE_TUNNEL_PORT}"; then
        TUNNEL_RUNNING=1
        break
      fi
      if ! kill -0 "${DATABASE_TUNNEL_PID}" 2>/dev/null; then
        break
      fi
      sleep 1
    done

    if [ "${TUNNEL_RUNNING}" != "1" ]; then
      echo "VVAULT database tunnel could not be established; see ${DATABASE_TUNNEL_LOG}." >&2
      exit 1
    fi
  fi

  VVAULT_BODY_DATABASE_URL="$(
    VVAULT_BODY_DATABASE_URL="${VVAULT_BODY_DATABASE_URL}" \
    DATABASE_TUNNEL_BIND_HOST="${DATABASE_TUNNEL_BIND_HOST}" \
    DATABASE_TUNNEL_PORT="${DATABASE_TUNNEL_PORT}" \
    "${PYTHON_BIN}" - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit

parsed = urlsplit(os.environ["VVAULT_BODY_DATABASE_URL"])
userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
host = os.environ["DATABASE_TUNNEL_BIND_HOST"]
port = os.environ["DATABASE_TUNNEL_PORT"]
netloc = f"{userinfo}@{host}:{port}" if userinfo else f"{host}:{port}"
print(urlunsplit(parsed._replace(netloc=netloc)))
PY
  )"
  export VVAULT_BODY_DATABASE_URL
fi

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${VVAULT_WAIT_SECONDS:-30}"
  local request_timeout="${VVAULT_HEALTH_REQUEST_TIMEOUT_SECONDS:-10}"

  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time "${request_timeout}" "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "VVAULT ${label} did not answer at ${url}" >&2
  return 1
}

cd "${ROOT_DIR}"

if ! "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
import flask
PY
then
  echo "VVAULT backend dependencies are missing for ${PYTHON_BIN}." >&2
  echo "Run 'python3 -m pip install -r ${ROOT_DIR}/requirements.txt' or set VVAULT_PYTHON_BIN to a Python that has Flask installed." >&2
  exit 1
fi

if ! is_listening "${BACKEND_PORT}"; then
  echo "Starting VVAULT backend on ${BACKEND_PORT}..."
  PORT="${BACKEND_PORT}" VVAULT_FRONTEND_URL="${FRONTEND_URL%/}" \
    nohup "${PYTHON_BIN}" vvault/server/vvault_web_server.py </dev/null >"${BACKEND_LOG}" 2>&1 &
  echo "$!" >"${BACKEND_PID_FILE}"
else
  echo "VVAULT backend already listening on ${BACKEND_PORT}."
fi

wait_for_url "${BACKEND_HEALTH_URL}" "backend"

if ! is_listening "${FRONTEND_PORT}"; then
  if [ ! -x node_modules/.bin/webpack ]; then
    echo "Missing frontend dependencies. Run 'npm ci' in ${ROOT_DIR}, then retry." >&2
    exit 1
  fi

  if [ ! -f dist/index.html ] || find src public webpack.config.js -type f -newer dist/index.html | grep -q .; then
    echo "Building VVAULT frontend dist..."
    npm run build
  fi

    echo "Starting VVAULT frontend on ${FRONTEND_PORT}..."
    if [ "${VVAULT_DETACH:-0}" = "1" ]; then
      VVAULT_FRONTEND_URL="${FRONTEND_URL%/}" VVAULT_BACKEND_URL="http://${HOST}:${BACKEND_PORT}" \
      nohup "${PYTHON_BIN}" scripts/vvault_frontend_proxy.py \
      --host "${FRONTEND_BIND_HOST}" \
      --port "${FRONTEND_PORT}" \
      --backend-host "${HOST}" \
      --backend-port "${BACKEND_PORT}" \
      --public-origin "${FRONTEND_URL%/}" \
      --dist "${ROOT_DIR}/dist" \
      </dev/null \
      >"${FRONTEND_LOG}" 2>&1 &
    echo "$!" >"${FRONTEND_PID_FILE}"
    wait_for_url "${FRONTEND_URL}" "frontend"
  else
    if [ "${VVAULT_OPEN_BROWSER:-1}" != "0" ]; then
      (sleep 1; open "${FRONTEND_URL}") >/dev/null 2>&1 &
    fi
    echo "VVAULT ready: ${FRONTEND_URL}"
    echo "Press Ctrl-C to stop the VVAULT frontend."
    exec "${PYTHON_BIN}" scripts/vvault_frontend_proxy.py \
      --host "${FRONTEND_BIND_HOST}" \
      --port "${FRONTEND_PORT}" \
      --backend-host "${HOST}" \
      --backend-port "${BACKEND_PORT}" \
      --public-origin "${FRONTEND_URL%/}" \
      --dist "${ROOT_DIR}/dist"
  fi
else
  echo "VVAULT frontend already listening on ${FRONTEND_PORT}."
  wait_for_url "${FRONTEND_URL}" "frontend"
fi

if [ "${VVAULT_OPEN_BROWSER:-1}" != "0" ]; then
  open "${FRONTEND_URL}"
fi

echo "VVAULT ready: ${FRONTEND_URL}"
