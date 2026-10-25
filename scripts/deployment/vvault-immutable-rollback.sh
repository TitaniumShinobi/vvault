#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "usage: sudo $0 --expected-current <sha> --previous <sha>" >&2
  exit 64
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "root is required" >&2; exit 77; }
[[ "$#" -eq 4 ]] || usage
[[ "$1" == "--expected-current" && "$3" == "--previous" ]] || usage
expected_current="$2"
previous="$4"
[[ "$expected_current" =~ ^[0-9a-f]{40}$ && "$previous" =~ ^[0-9a-f]{40}$ ]] || usage

release_root="/opt/vvault-releases"
live_link="/opt/vvault-public"
service="vvault-backend.service"
ready_url="http://127.0.0.1:8000/api/ready"
current_path="$release_root/$expected_current"
previous_path="$release_root/$previous"

exec 9>/run/lock/vvault-deploy.lock
flock -n 9 || { echo "VVAULT deployment lock is busy" >&2; exit 75; }
[[ "$(readlink -f "$live_link")" == "$current_path" ]] || {
  echo "live release does not match --expected-current" >&2
  exit 65
}
[[ -d "$current_path" ]] || { echo "current release is absent" >&2; exit 65; }
[[ "$(git -c safe.directory="$current_path" -C "$current_path" rev-parse HEAD)" == "$expected_current" ]] || {
  echo "current release SHA mismatch" >&2
  exit 65
}
[[ -d "$previous_path" ]] || { echo "previous release is absent" >&2; exit 66; }
[[ "$(git -c safe.directory="$previous_path" -C "$previous_path" rev-parse HEAD)" == "$previous" ]] || {
  echo "previous release SHA mismatch" >&2
  exit 66
}

replacement="$release_root/.vvault-public.rollback.$$"
ln -s "$previous_path" "$replacement"
mv -Tf "$replacement" "$live_link"
systemctl restart "$service"
if ! curl --fail --silent --show-error --retry 12 --retry-all-errors \
  --retry-delay 2 "$ready_url" | python3 -c '
import json, sys
p=json.load(sys.stdin)
assert p.get("ready") is True
assert p.get("authority") == "vvault_body"
'; then
  recovery="$release_root/.vvault-public.recover.$$"
  ln -s "$current_path" "$recovery"
  mv -Tf "$recovery" "$live_link"
  systemctl restart "$service"
  echo "rollback readiness failed; restored expected-current release" >&2
  exit 1
fi

printf 'rollback=PASS restored=%s replaced=%s\n' "$previous" "$expected_current"
