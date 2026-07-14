#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_FRONTEND_PORT="${VVAULT_LIVE_FRONTEND_PORT:-7784}"
DRILL_FRONTEND_PORT="${VVAULT_DRILL_FRONTEND_PORT:-17784}"
DRILL_BACKEND_PORT="${VVAULT_DRILL_BACKEND_PORT:-18000}"
DRILL_AUTH_PORT="${VVAULT_DRILL_AUTH_PORT:-1112}"
DRILL_HOST="${VVAULT_DRILL_HOST:-localhost}"
DRILL_BIND_HOST="${VVAULT_DRILL_BIND_HOST:-127.0.0.1}"
DRILL_EMAIL="${VVAULT_RESTORE_EMAIL:-dwoodson92@gmail.com}"
DRILL_NAME="${VVAULT_RESTORE_NAME:-Devon Woodson}"
OPEN_BROWSER=1
SOURCE_PYTHON="${VVAULT_PYTHON_BIN:-}"
if [ -z "${SOURCE_PYTHON}" ]; then
  if [ -x "${SOURCE_ROOT}/.venv/bin/python3" ]; then
    SOURCE_PYTHON="${SOURCE_ROOT}/.venv/bin/python3"
  elif [ -x "${SOURCE_ROOT}/.venv/bin/python" ]; then
    SOURCE_PYTHON="${SOURCE_ROOT}/.venv/bin/python"
  else
    SOURCE_PYTHON="python3"
  fi
fi

usage() {
  cat <<'USAGE'
Usage:
  scripts/vvault-restore-drill.sh [--no-open]
  scripts/vvault-restore-drill.sh --cleanup /private/tmp/vvault-restore-drill-...

Runs a VVAULT restore drill on 17784/18000/1112 without touching live ports.
USAGE
}

die() {
  echo "VVAULT restore drill failed: $*" >&2
  exit 1
}

is_listening() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${VVAULT_DRILL_WAIT_SECONDS:-60}"

  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  die "${label} did not answer at ${url}"
}

cleanup_drill() {
  local target="$1"
  case "${target}" in
    /private/tmp/vvault-restore-drill-*|/tmp/vvault-restore-drill-*) ;;
    *) die "refusing cleanup outside /private/tmp/vvault-restore-drill-*";;
  esac

  local state_dir="${target}/.vvault-restore-drill"
  if [ -d "${state_dir}" ]; then
    for pid_file in "${state_dir}"/*.pid; do
      [ -f "${pid_file}" ] || continue
      local pid
      pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [ -n "${pid}" ]; then
        kill "${pid}" >/dev/null 2>&1 || true
      fi
    done
  fi

  rm -rf "${target}"
  echo "Cleaned restore drill: ${target}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-open)
      OPEN_BROWSER=0
      shift
      ;;
    --open)
      OPEN_BROWSER=1
      shift
      ;;
    --cleanup)
      [ "$#" -ge 2 ] || die "--cleanup requires a drill path"
      cleanup_drill "$2"
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
done

for port in "${DRILL_FRONTEND_PORT}" "${DRILL_BACKEND_PORT}" "${DRILL_AUTH_PORT}"; do
  if is_listening "${port}"; then
    die "drill port ${port} is already in use"
  fi
done

if curl -fsS --max-time 3 "http://localhost:${LIVE_FRONTEND_PORT}/api/ready" >/dev/null 2>&1; then
  LIVE_READY_BEFORE="yes"
else
  LIVE_READY_BEFORE="no"
fi

DRILL_ROOT="$(mktemp -d /private/tmp/vvault-restore-drill-XXXXXXXX)"
STATE_DIR="${DRILL_ROOT}/.vvault-restore-drill"
mkdir -p "${STATE_DIR}"

rsync -a \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.vvault-restore-drill' \
  "${SOURCE_ROOT}/" "${DRILL_ROOT}/"

cd "${DRILL_ROOT}"

if [ -f ".env.local" ] || [ -f ".env" ]; then
  set -a
  [ ! -f ".env" ] || . ".env"
  [ ! -f ".env.local" ] || . ".env.local"
  set +a
fi

export AUTH_SESSION_SECRET="${AUTH_SESSION_SECRET:-dev-auth-session-secret-change-me}"
export AUTH_COOKIE_NAME="${AUTH_COOKIE_NAME:-auth_sid}"
export AUTH_PUBLIC_ORIGIN="${VVAULT_DRILL_AUTH_PUBLIC_ORIGIN:-http://${DRILL_HOST}:${DRILL_AUTH_PORT}}"
export DRILL_EMAIL
export DRILL_NAME
export VVAULT_PYTHON_BIN="${SOURCE_PYTHON}"

AUTH_DIR_DEFAULT="${SOURCE_ROOT}/../auth"
AUTH_DIR="${AUTH_DIR:-${AUTH_DIR_DEFAULT}}"

VVAULT_DETACH=1 \
VVAULT_OPEN_BROWSER=0 \
VVAULT_HOST="${DRILL_HOST}" \
VVAULT_FRONTEND_BIND_HOST="${DRILL_BIND_HOST}" \
VVAULT_FRONTEND_PORT="${DRILL_FRONTEND_PORT}" \
VVAULT_BACKEND_PORT="${DRILL_BACKEND_PORT}" \
AUTH_PORT="${DRILL_AUTH_PORT}" \
AUTH_DIR="${AUTH_DIR}" \
VVAULT_LOG_DIR="${STATE_DIR}" \
VVAULT_WAIT_SECONDS="${VVAULT_DRILL_WAIT_SECONDS:-60}" \
scripts/open-vvault-standalone.sh

DRILL_FRONTEND_URL="http://${DRILL_HOST}:${DRILL_FRONTEND_PORT}"
DRILL_READY_URL="${DRILL_FRONTEND_URL}/api/ready"
wait_for_url "${DRILL_READY_URL}" "drill frontend readiness"

SESSION_JSON="$("${VVAULT_PYTHON_BIN:-python3}" - <<'PY'
import base64
import hashlib
import hmac
import json
import os
import time

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

payload = {
    "email": os.environ["DRILL_EMAIL"],
    "name": os.environ["DRILL_NAME"],
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600,
}
encoded_payload = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
signature = hmac.new(
    os.environ["AUTH_SESSION_SECRET"].encode("utf-8"),
    encoded_payload.encode("utf-8"),
    hashlib.sha256,
).digest()
print(json.dumps({
    "cookie_name": os.environ.get("AUTH_COOKIE_NAME", "auth_sid"),
    "cookie_value": f"{encoded_payload}.{b64url(signature)}",
}))
PY
)"

COOKIE_HEADER="$("${VVAULT_PYTHON_BIN:-python3}" - <<'PY' "${SESSION_JSON}"
import json
import sys
session = json.loads(sys.argv[1])
print(f"{session['cookie_name']}={session['cookie_value']}")
PY
)"

BRIDGE_FILE="${STATE_DIR}/session-bridge.json"
READY_FILE="${STATE_DIR}/ready.json"
VAULT_FILE="${STATE_DIR}/vault-files.json"
CONSTRUCT_FILE="${STATE_DIR}/chatty-constructs.json"

curl -fsS --max-time 10 -X POST \
  -H "Cookie: ${COOKIE_HEADER}" \
  -o "${BRIDGE_FILE}" \
  "${DRILL_FRONTEND_URL}/api/vault/session-bridge"

BEARER_TOKEN="$("${VVAULT_PYTHON_BIN:-python3}" - <<'PY' "${BRIDGE_FILE}"
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
if not payload.get("success") or not payload.get("token"):
    raise SystemExit("session bridge did not return a VVAULT token")
print(payload["token"])
PY
)"

curl -fsS --max-time 10 -o "${READY_FILE}" "${DRILL_READY_URL}"
curl -fsS --max-time 20 -H "Authorization: Bearer ${BEARER_TOKEN}" -o "${VAULT_FILE}" "${DRILL_FRONTEND_URL}/api/vault/files"
curl -fsS --max-time 20 -H "Authorization: Bearer ${BEARER_TOKEN}" -o "${CONSTRUCT_FILE}" "${DRILL_FRONTEND_URL}/api/chatty/constructs"

"${VVAULT_PYTHON_BIN:-python3}" - <<'PY' "${READY_FILE}" "${VAULT_FILE}" "${CONSTRUCT_FILE}"
import json
import sys

def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)

ready = load(sys.argv[1])
vault = load(sys.argv[2])
constructs = load(sys.argv[3])
body_database = ready.get("body_database") or {}

if not ready.get("ready"):
    raise SystemExit("/api/ready did not report ready: true")
if (ready.get("storage_mode") or body_database.get("storage_mode")) != "vvault_body":
    raise SystemExit("/api/ready did not report storage_mode: vvault_body")
if (ready.get("canonical_schema") or body_database.get("canonical_schema") or body_database.get("schema")) != "ovvaults":
    raise SystemExit("/api/ready did not report canonical_schema: ovvaults")
if not vault.get("success"):
    raise SystemExit("/api/vault/files did not report success")
if int(vault.get("count") or 0) <= 0:
    raise SystemExit("/api/vault/files returned no rows")
if not constructs.get("success"):
    raise SystemExit("/api/chatty/constructs did not report success")
ids = {item.get("construct_id") for item in constructs.get("constructs") or []}
if "zen-001" not in ids:
    raise SystemExit("/api/chatty/constructs did not include zen-001")
PY

READY_SUMMARY="$("${VVAULT_PYTHON_BIN:-python3}" - <<'PY' "${READY_FILE}" "${VAULT_FILE}" "${CONSTRUCT_FILE}"
import json
import sys

def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)

ready = load(sys.argv[1])
vault = load(sys.argv[2])
constructs = load(sys.argv[3])
body_database = ready.get("body_database") or {}
print(json.dumps({
    "ready": ready.get("ready"),
    "storage_mode": ready.get("storage_mode") or body_database.get("storage_mode"),
    "canonical_schema": ready.get("canonical_schema") or body_database.get("canonical_schema") or body_database.get("schema"),
    "vault_file_count": vault.get("count"),
    "construct_count": constructs.get("count"),
    "has_zen_001": any(item.get("construct_id") == "zen-001" for item in constructs.get("constructs") or []),
}, indent=2))
PY
)"

cat > "${DRILL_ROOT}/RESTORE_RECEIPT.md" <<EOF
# VVAULT Restore Drill Receipt

- Drill root: \`${DRILL_ROOT}\`
- Drill frontend: \`${DRILL_FRONTEND_URL}\`
- Drill backend: \`http://${DRILL_HOST}:${DRILL_BACKEND_PORT}\`
- Drill auth: \`http://${DRILL_HOST}:${DRILL_AUTH_PORT}\`
- Live ready before drill: \`${LIVE_READY_BEFORE}\`
- Verified at: \`$(date -u +"%Y-%m-%dT%H:%M:%SZ")\`

\`\`\`json
${READY_SUMMARY}
\`\`\`

Cleanup:

\`\`\`bash
${SOURCE_ROOT}/scripts/vvault-restore-drill.sh --cleanup ${DRILL_ROOT}
\`\`\`
EOF

if [ "${OPEN_BROWSER}" = "1" ]; then
  open "${DRILL_FRONTEND_URL}" >/dev/null 2>&1 || true
fi

echo "VVAULT restore drill passed."
echo "Dashboard: ${DRILL_FRONTEND_URL}"
echo "Receipt: ${DRILL_ROOT}/RESTORE_RECEIPT.md"
echo "Cleanup: ${SOURCE_ROOT}/scripts/vvault-restore-drill.sh --cleanup ${DRILL_ROOT}"
