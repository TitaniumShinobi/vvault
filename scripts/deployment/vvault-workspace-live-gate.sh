#!/usr/bin/env bash
set -Eeuo pipefail

: "${VVAULT_BASE_URL:?Set VVAULT_BASE_URL, for example https://vvault.thewreck.org}"
: "${VVAULT_PRIMARY_COOKIE_JAR:?Set the authenticated primary-account cookie jar path}"
: "${VVAULT_SECONDARY_COOKIE_JAR:?Set the authenticated second-account cookie jar path}"
: "${VVAULT_CONSTRUCT_ID:?Set a canonical construct visible to the primary account}"
: "${VVAULT_FILE_ID:?Set a canonical file owned by the primary account}"

MAX_ROOT_SECONDS="${VVAULT_MAX_ROOT_SECONDS:-4.5}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

request() {
  local cookie_jar="$1" output="$2" url="$3"
  shift 3
  curl --fail-with-body --silent --show-error --max-time 15 \
    --cookie "$cookie_jar" --output "$output" "$@" "$url"
}

curl --fail --silent --show-error --max-time 10 \
  --output "$tmp_dir/ready.json" "$VVAULT_BASE_URL/api/ready"
python3 - "$tmp_dir/ready.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p.get("ready") is True
assert p.get("authority") == "vvault_body"
assert p.get("storage_owner") == "ovvaults.vault_files"
assert p.get("transcript_owner") == "ovvaults.transcripts"
PY

root_time="$(curl --fail-with-body --silent --show-error --max-time 15 \
  --cookie "$VVAULT_PRIMARY_COOKIE_JAR" \
  --output "$tmp_dir/root.json" --write-out '%{time_total}' \
  "$VVAULT_BASE_URL/api/vault/drive/workspace-root")"
workspace_ref="$(python3 - "$tmp_dir/root.json" "$VVAULT_CONSTRUCT_ID" "$MAX_ROOT_SECONDS" "$root_time" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
construct_id, limit, elapsed = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
assert p.get("success") is True and p.get("canonical") is True
assert p.get("ownerIdentifiersProjected") is False
assert elapsed <= limit, (elapsed, limit)
encoded = json.dumps(p, sort_keys=True)
assert "QueryCanceled" not in encoded
instances = next(node for node in p["children"] if node.get("name") == "instances")
match = next(node for node in instances.get("childrenPreview", []) if node.get("constructId") == construct_id)
print(match["workspaceRef"])
PY
)"

request "$VVAULT_PRIMARY_COOKIE_JAR" "$tmp_dir/children.json" \
  "$VVAULT_BASE_URL/api/vault/drive/children" --get \
  --data-urlencode "constructId=$VVAULT_CONSTRUCT_ID" \
  --data-urlencode "parentNodeId=root" \
  --data-urlencode "workspaceRef=$workspace_ref"
python3 - "$tmp_dir/children.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p.get("success") is True and p.get("canonical") is True
assert isinstance(p.get("children"), list)
PY

request "$VVAULT_PRIMARY_COOKIE_JAR" "$tmp_dir/file.json" \
  "$VVAULT_BASE_URL/api/vault/files/$VVAULT_FILE_ID"
python3 - "$tmp_dir/file.json" <<'PY'
import hashlib, json, sys
p = json.load(open(sys.argv[1]))
assert p.get("success") is True
payload = p.get("file") if isinstance(p.get("file"), dict) else p
assert payload.get("content") is not None
print("primary_file_receipt_sha256=" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
PY

request "$VVAULT_PRIMARY_COOKIE_JAR" "$tmp_dir/chatty.json" \
  "$VVAULT_BASE_URL/api/chatty/construct/$VVAULT_CONSTRUCT_ID/files"
request "$VVAULT_PRIMARY_COOKIE_JAR" "$tmp_dir/chatty-constructs.json" \
  "$VVAULT_BASE_URL/api/chatty/constructs"
python3 - "$tmp_dir/chatty.json" "$tmp_dir/chatty-constructs.json" "$VVAULT_CONSTRUCT_ID" <<'PY'
import json, sys
files = json.load(open(sys.argv[1]))
constructs = json.load(open(sys.argv[2]))
construct_id = sys.argv[3]
assert files.get("success") is not False
assert "QueryCanceled" not in json.dumps(files, sort_keys=True)
items = constructs.get("constructs") or constructs.get("data") or []
assert any((item.get("callsign") or item.get("construct_id")) == construct_id for item in items)
assert "QueryCanceled" not in json.dumps(constructs, sort_keys=True)
PY

request "$VVAULT_SECONDARY_COOKIE_JAR" "$tmp_dir/secondary-root.json" \
  "$VVAULT_BASE_URL/api/vault/drive/workspace-root"
python3 - "$tmp_dir/secondary-root.json" "$VVAULT_CONSTRUCT_ID" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p.get("success") is True and p.get("canonical") is True
construct_id = sys.argv[2]
instances = next(node for node in p["children"] if node.get("name") == "instances")
assert not any(node.get("constructId") == construct_id for node in instances.get("childrenPreview", []))
assert "QueryCanceled" not in json.dumps(p, sort_keys=True)
PY

secondary_status="$(curl --silent --show-error --max-time 15 \
  --cookie "$VVAULT_SECONDARY_COOKIE_JAR" \
  --output "$tmp_dir/secondary.json" --write-out '%{http_code}' \
  "$VVAULT_BASE_URL/api/vault/files/$VVAULT_FILE_ID")"
case "$secondary_status" in
  403|404) ;;
  *) echo "second-account isolation failed: HTTP $secondary_status" >&2; exit 1 ;;
esac

printf 'workspace_gate=PASS root_seconds=%s second_account_http=%s\n' \
  "$root_time" "$secondary_status"
