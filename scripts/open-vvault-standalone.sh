#!/usr/bin/env bash

set -euo pipefail

VVAULT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VVAULT_URL="${VVAULT_URL:-http://localhost:7784}"
VVAULT_PORT="${VVAULT_PORT:-7784}"
VVAULT_BACKEND_PORT="${VVAULT_BACKEND_PORT:-8000}"
VVAULT_BACKEND_HEALTH_URL="${VVAULT_BACKEND_HEALTH_URL:-http://127.0.0.1:${VVAULT_BACKEND_PORT}/api/ready}"
VVAULT_BACKEND_STATUS_URL="${VVAULT_BACKEND_STATUS_URL:-http://127.0.0.1:${VVAULT_BACKEND_PORT}/api/health}"
VVAULT_LOG="${VVAULT_LOG:-/tmp/vvault-devfull.log}"
VVAULT_RECEIPT="${VVAULT_RECEIPT:-/tmp/vvault-startup-receipt.json}"
VVAULT_FRONTEND_PID_FILE="${VVAULT_FRONTEND_PID_FILE:-/tmp/vvault-frontend.pid}"
VVAULT_WAIT_SECONDS="${VVAULT_WAIT_SECONDS:-30}"
VVAULT_OPEN_BROWSER="${VVAULT_OPEN_BROWSER:-1}"
VVAULT_FRONTEND_HOST="${VVAULT_FRONTEND_HOST:-127.0.0.1}"
VVAULT_BACKEND_HOST="${VVAULT_BACKEND_HOST:-127.0.0.1}"
VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS="${VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS:-8}"

backend_state="unknown"
frontend_state="unknown"
health_status="000"
health_body=""
ready_status="000"
ready_body=""
frontend_reachable=0
degraded_reason=""
last_error=""
frontend_started_pid=""

bootstrap_node_runtime() {
  local nvm_dir
  nvm_dir="${NVM_DIR:-$HOME/.nvm}"

  if [[ ! -f "$VVAULT_REPO/.nvmrc" ]]; then
    return 0
  fi

  if [[ -s "$nvm_dir/nvm.sh" ]]; then
    export NVM_DIR="$nvm_dir"
    # shellcheck source=/dev/null
    source "$NVM_DIR/nvm.sh"
    nvm use --silent >/dev/null 2>&1 || true
  fi
}

is_frontend_listening() {
  lsof -nP -iTCP:"$VVAULT_PORT" -sTCP:LISTEN >/dev/null 2>&1
}

is_frontend_reachable() {
  curl -fsS --max-time 2 "$VVAULT_URL" >/dev/null 2>&1
}

is_backend_ready() {
  curl -fsS --max-time 2 "$VVAULT_BACKEND_HEALTH_URL" >/dev/null 2>&1
}

is_backend_health_reachable() {
  curl -fsS --max-time 2 "$VVAULT_BACKEND_STATUS_URL" >/dev/null 2>&1
}

start_backend() {
  (
    cd "$VVAULT_REPO"
    bootstrap_node_runtime
    nohup env VVAULT_BACKEND_HOST="$VVAULT_BACKEND_HOST" VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS="$VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS" npm run backend >"$VVAULT_LOG" 2>&1 </dev/null &
    disown "$!" 2>/dev/null || true
  ) >/dev/null 2>&1
}

start_frontend() {
  (
    cd "$VVAULT_REPO"
    bootstrap_node_runtime
    nohup env VVAULT_FRONTEND_HOST="$VVAULT_FRONTEND_HOST" VVAULT_BACKEND_HOST="$VVAULT_BACKEND_HOST" ./node_modules/.bin/webpack-dev-server --mode development --no-watch-options-stdin >>"$VVAULT_LOG" 2>&1 </dev/null &
    printf '%s\n' "$!" >"$VVAULT_FRONTEND_PID_FILE"
    disown "$!" 2>/dev/null || true
  ) >/dev/null 2>&1
  frontend_started_pid="$(cat "$VVAULT_FRONTEND_PID_FILE" 2>/dev/null || true)"
}

start_devfull() {
  start_backend
  start_frontend
}

backend_listener_count() {
  lsof -nP -tiTCP:"$VVAULT_BACKEND_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | wc -l | tr -d ' ' || true
}

frontend_listener_count() {
  lsof -nP -tiTCP:"$VVAULT_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | wc -l | tr -d ' ' || true
}

port_pids() {
  local port="$1"
  lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//' || true
}

port_owner_table() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

process_args() {
  local pid="$1"
  ps -p "$pid" -o args= 2>/dev/null || true
}

process_cwd() {
  local pid="$1"
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true
}

is_expected_frontend_pid() {
  local pid="$1"
  local args
  local cwd

  if [[ -n "$frontend_started_pid" ]] && [[ "$pid" == "$frontend_started_pid" ]]; then
    return 0
  fi

  args="$(process_args "$pid")"
  cwd="$(process_cwd "$pid")"

  [[ "$args" == *"webpack-dev-server"* ]] && {
    [[ "$cwd" == "$VVAULT_REPO" ]] || [[ "$args" == *"$VVAULT_REPO/node_modules/.bin/webpack-dev-server"* ]]
  }
}

probe_url() {
  local url="$1"
  local body_file
  local status
  body_file="$(mktemp "${TMPDIR:-/tmp}/vvault-probe.XXXXXX")"
  status="$(curl -sS --max-time 2 -o "$body_file" -w "%{http_code}" "$url" 2>/dev/null || printf "000")"
  PROBE_STATUS="$status"
  PROBE_BODY="$(tr '\n' ' ' < "$body_file" | sed 's/[[:space:]]\+/ /g' | cut -c 1-4000)"
  rm -f "$body_file"
  [[ "$status" =~ ^2[0-9][0-9]$ ]]
}

refresh_backend_state() {
  local backend_count
  backend_count="$(backend_listener_count)"

  health_status="000"
  health_body=""
  ready_status="000"
  ready_body=""

  if [[ "$backend_count" -eq 0 ]]; then
    backend_state="dead"
    degraded_reason="backend has no listener on port $VVAULT_BACKEND_PORT"
    return 0
  fi

  if [[ "$backend_count" -gt 1 ]]; then
    backend_state="ambiguous"
    degraded_reason="multiple backend listeners on port $VVAULT_BACKEND_PORT"
    return 0
  fi

  if probe_url "$VVAULT_BACKEND_STATUS_URL"; then
    health_status="$PROBE_STATUS"
    health_body="$PROBE_BODY"
  else
    health_status="$PROBE_STATUS"
    health_body="$PROBE_BODY"
  fi

  if probe_url "$VVAULT_BACKEND_HEALTH_URL"; then
    ready_status="$PROBE_STATUS"
    ready_body="$PROBE_BODY"
    backend_state="ready"
    degraded_reason=""
    return 0
  fi

  ready_status="$PROBE_STATUS"
  ready_body="$PROBE_BODY"

  if [[ "$health_status" =~ ^2[0-9][0-9]$ ]]; then
    backend_state="degraded"
    degraded_reason="backend health is HTTP $health_status but readiness is HTTP $ready_status"
    return 0
  fi

  backend_state="unhealthy"
  degraded_reason="backend listener exists but health is HTTP $health_status and readiness is HTTP $ready_status"
}

refresh_frontend_state() {
  local frontend_count
  local frontend_pid
  frontend_count="$(frontend_listener_count)"
  frontend_reachable=0

  if [[ "$frontend_count" -eq 0 ]]; then
    frontend_state="dead"
    return 0
  fi

  if [[ "$frontend_count" -gt 1 ]]; then
    frontend_state="ambiguous"
    return 0
  fi

  frontend_pid="$(port_pids "$VVAULT_PORT")"
  if [[ -z "$frontend_pid" ]] || ! ps -p "$frontend_pid" >/dev/null 2>&1; then
    frontend_state="dead"
    return 0
  fi

  if ! is_expected_frontend_pid "$frontend_pid"; then
    frontend_state="unexpected"
    return 0
  fi

  if is_frontend_reachable; then
    frontend_reachable=1
    frontend_state="ready"
    return 0
  fi

  frontend_state="stale"
}

write_receipt() {
  local final_status="${1:-unknown}"
  local backend_pids
  local frontend_pids
  local backend_owner
  local frontend_owner

  backend_pids="$(port_pids "$VVAULT_BACKEND_PORT")"
  frontend_pids="$(port_pids "$VVAULT_PORT")"
  backend_owner="$(port_owner_table "$VVAULT_BACKEND_PORT")"
  frontend_owner="$(port_owner_table "$VVAULT_PORT")"

  RECEIPT_FINAL_STATUS="$final_status" \
  RECEIPT_FINAL_URL="$VVAULT_URL" \
  RECEIPT_BACKEND_STATE="$backend_state" \
  RECEIPT_FRONTEND_STATE="$frontend_state" \
  RECEIPT_BACKEND_PORT="$VVAULT_BACKEND_PORT" \
  RECEIPT_FRONTEND_PORT="$VVAULT_PORT" \
  RECEIPT_BACKEND_PIDS="$backend_pids" \
  RECEIPT_FRONTEND_PIDS="$frontend_pids" \
  RECEIPT_BACKEND_OWNER="$backend_owner" \
  RECEIPT_FRONTEND_OWNER="$frontend_owner" \
  RECEIPT_HEALTH_URL="$VVAULT_BACKEND_STATUS_URL" \
  RECEIPT_HEALTH_STATUS="$health_status" \
  RECEIPT_HEALTH_BODY="$health_body" \
  RECEIPT_READY_URL="$VVAULT_BACKEND_HEALTH_URL" \
  RECEIPT_READY_STATUS="$ready_status" \
  RECEIPT_READY_BODY="$ready_body" \
  RECEIPT_FRONTEND_REACHABLE="$frontend_reachable" \
  RECEIPT_DEGRADED_REASON="$degraded_reason" \
  RECEIPT_ERROR="$last_error" \
  python3 - "$VVAULT_RECEIPT" <<'PY' || true
import json
import os
import sys
from datetime import datetime, timezone

path = sys.argv[1]
receipt = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "status": os.environ.get("RECEIPT_FINAL_STATUS", "unknown"),
    "final_url": os.environ.get("RECEIPT_FINAL_URL", ""),
    "degraded_reason": os.environ.get("RECEIPT_DEGRADED_REASON", ""),
    "error": os.environ.get("RECEIPT_ERROR", ""),
    "backend": {
        "port": os.environ.get("RECEIPT_BACKEND_PORT", ""),
        "state": os.environ.get("RECEIPT_BACKEND_STATE", ""),
        "pids": os.environ.get("RECEIPT_BACKEND_PIDS", ""),
        "owner": os.environ.get("RECEIPT_BACKEND_OWNER", ""),
        "health": {
            "url": os.environ.get("RECEIPT_HEALTH_URL", ""),
            "status": os.environ.get("RECEIPT_HEALTH_STATUS", ""),
            "body": os.environ.get("RECEIPT_HEALTH_BODY", ""),
        },
        "ready": {
            "url": os.environ.get("RECEIPT_READY_URL", ""),
            "status": os.environ.get("RECEIPT_READY_STATUS", ""),
            "body": os.environ.get("RECEIPT_READY_BODY", ""),
        },
    },
    "frontend": {
        "port": os.environ.get("RECEIPT_FRONTEND_PORT", ""),
        "state": os.environ.get("RECEIPT_FRONTEND_STATE", ""),
        "pids": os.environ.get("RECEIPT_FRONTEND_PIDS", ""),
        "owner": os.environ.get("RECEIPT_FRONTEND_OWNER", ""),
        "reachable": os.environ.get("RECEIPT_FRONTEND_REACHABLE", "0") == "1",
    },
}
with open(path, "w", encoding="utf-8") as receipt_file:
    json.dump(receipt, receipt_file, indent=2, sort_keys=True)
    receipt_file.write("\n")
PY
}

fail_startup() {
  last_error="$1"
  refresh_backend_state
  refresh_frontend_state
  write_receipt "failed"
  echo "$last_error" >&2
  if [[ -n "$(port_owner_table "$VVAULT_BACKEND_PORT")" ]]; then
    echo "Backend port owner:" >&2
    port_owner_table "$VVAULT_BACKEND_PORT" >&2
  fi
  if [[ -n "$(port_owner_table "$VVAULT_PORT")" ]]; then
    echo "Frontend port owner:" >&2
    port_owner_table "$VVAULT_PORT" >&2
  fi
  echo "Startup receipt: $VVAULT_RECEIPT" >&2
  exit 1
}

open_browser() {
  if [[ "$VVAULT_OPEN_BROWSER" == "0" ]]; then
    return 0
  fi

  if command -v open >/dev/null 2>&1; then
    open "$VVAULT_URL" >/dev/null 2>&1 || true
    return 0
  fi

  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$VVAULT_URL" >/dev/null 2>&1 || true
  fi
}

if [[ ! -d "$VVAULT_REPO" ]]; then
  echo "VVAULT repo not found at $VVAULT_REPO" >&2
  exit 1
fi

bootstrap_node_runtime

refresh_backend_state
if [[ "$backend_state" == "ambiguous" ]]; then
  fail_startup "VVAULT backend has ambiguous duplicate listeners on port $VVAULT_BACKEND_PORT."
fi
if [[ "$backend_state" == "unhealthy" ]]; then
  fail_startup "VVAULT backend listener exists on port $VVAULT_BACKEND_PORT but neither /api/health nor /api/ready proved an alive backend."
fi
if [[ "$backend_state" == "dead" ]]; then
  start_backend
fi

refresh_frontend_state
if [[ "$frontend_state" == "ambiguous" ]]; then
  fail_startup "VVAULT frontend has ambiguous duplicate listeners on port $VVAULT_PORT."
fi
if [[ "$frontend_state" == "unexpected" ]]; then
  fail_startup "VVAULT frontend port $VVAULT_PORT is owned by an unexpected process; refusing to accept a stale listener."
fi
if [[ "$frontend_state" == "stale" ]]; then
  fail_startup "VVAULT frontend port $VVAULT_PORT is owned by the expected process but did not answer $VVAULT_URL."
fi
if [[ "$frontend_state" == "dead" ]]; then
  start_frontend
fi

ready=0
for ((i = 0; i < VVAULT_WAIT_SECONDS; i += 1)); do
  refresh_backend_state
  refresh_frontend_state

  if [[ "$backend_state" == "ambiguous" ]] || [[ "$frontend_state" == "ambiguous" ]] || [[ "$frontend_state" == "unexpected" ]] || [[ "$frontend_state" == "stale" ]]; then
    break
  fi

  if [[ "$backend_state" == "ready" || "$backend_state" == "degraded" ]] && [[ "$frontend_state" == "ready" ]]; then
    ready=1
    break
  fi

  sleep 1
done

if [[ "$ready" != "1" ]]; then
  fail_startup "VVAULT did not start successfully. Check $VVAULT_LOG"
fi

open_browser
if [[ "$backend_state" == "ready" ]]; then
  write_receipt "ready"
  echo "VVAULT is running at $VVAULT_URL"
else
  write_receipt "degraded"
  echo "VVAULT is running in degraded mode at $VVAULT_URL ($degraded_reason)."
  echo "Startup receipt: $VVAULT_RECEIPT"
fi
