#!/usr/bin/env bash
set -euo pipefail

VERSION="4.14.7-1"
PACKAGE="wazuh-manager_4.14.7-1_amd64.deb"
PACKAGE_URL="https://packages.wazuh.com/4.x/apt/pool/main/w/wazuh-manager/${PACKAGE}"
PACKAGE_SHA512="f54a48683683fea476b133646c6a2ad884c3d61d0f7d85bf8b0602e127e0e14a6976fc5cf5962cc47de2794fdd0e2abe2f195de1d7a7b9c69da4b79c09a970f7"
SERVICE_USER="${VVAULT_SERVICE_USER:-vvault}"
ENV_FILE="${VVAULT_WAZUH_ENV_FILE:-/etc/vvault/wazuh.env}"
ADMIN_ENV_FILE="${VVAULT_WAZUH_ADMIN_ENV_FILE:-/etc/vvault/wazuh-admin.env}"
AGENT_MANAGER="${VVAULT_WAZUH_AGENT_MANAGER:?VVAULT_WAZUH_AGENT_MANAGER is required}"
API_GUARD_INSTALLED=0
ADMIN_CREDENTIALS_ROTATED=0
stage=""

remove_api_guard() {
  if [[ "${API_GUARD_INSTALLED}" -eq 1 ]]; then
    iptables -D OUTPUT -p tcp -d 127.0.0.1 --dport 55000 \
      -m owner ! --uid-owner 0 -j REJECT >/dev/null 2>&1 || true
    API_GUARD_INSTALLED=0
  fi
}

cleanup() {
  local status="$?"
  if [[ "${ADMIN_CREDENTIALS_ROTATED}" -ne 1 ]] && \
     systemctl is-active --quiet wazuh-manager 2>/dev/null; then
    systemctl stop wazuh-manager >/dev/null 2>&1 || true
  fi
  remove_api_guard
  if [[ -n "${stage}" && "${stage}" == /var/tmp/vvault-wazuh-manager.* ]]; then
    rm -rf -- "${stage}"
  fi
  return "${status}"
}
trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "manager installation requires root" >&2
  exit 1
fi
if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  echo "VVAULT host architecture is not amd64" >&2
  exit 1
fi
for forbidden in wazuh-indexer wazuh-dashboard filebeat; do
  if dpkg-query -W -f='${Status}' "${forbidden}" 2>/dev/null | grep -q 'install ok installed'; then
    echo "forbidden component already installed: ${forbidden}" >&2
    exit 1
  fi
done
if [[ "$(df -Pk /var | awk 'NR==2 {print $4}')" -lt 5242880 ]]; then
  echo "less than 5 GiB is free under /var" >&2
  exit 1
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "VVAULT service user does not exist: ${SERVICE_USER}" >&2
  exit 1
fi
command -v iptables >/dev/null 2>&1 || {
  echo "iptables is required to protect Wazuh API bootstrap" >&2
  exit 1
}

stage="$(mktemp -d /var/tmp/vvault-wazuh-manager.XXXXXX)"
if ! iptables -C OUTPUT -p tcp -d 127.0.0.1 --dport 55000 \
  -m owner ! --uid-owner 0 -j REJECT >/dev/null 2>&1; then
  iptables -I OUTPUT 1 -p tcp -d 127.0.0.1 --dport 55000 \
    -m owner ! --uid-owner 0 -j REJECT
  API_GUARD_INSTALLED=1
fi
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --output "${stage}/${PACKAGE}" "${PACKAGE_URL}"
echo "${PACKAGE_SHA512}  ${stage}/${PACKAGE}" | sha512sum --check --strict
[[ "$(dpkg-deb -f "${stage}/${PACKAGE}" Package)" == "wazuh-manager" ]]
[[ "$(dpkg-deb -f "${stage}/${PACKAGE}" Version)" == "${VERSION}" ]]
[[ "$(dpkg-deb -f "${stage}/${PACKAGE}" Architecture)" == "amd64" ]]

if ! dpkg-query -W -f='${Version}' wazuh-manager 2>/dev/null | grep -qx "${VERSION}"; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${stage}/${PACKAGE}"
fi

python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

path = Path('/var/ossec/etc/ossec.conf')
root = ET.parse(path).getroot()
global_node = root.find('global')
if global_node is None:
    global_node = ET.SubElement(root, 'global')
for tag, value in (('jsonout_output', 'yes'), ('alerts_log', 'yes')):
    node = global_node.find(tag)
    if node is None:
        node = ET.SubElement(global_node, tag)
    node.text = value
auth_node = root.find('auth')
if auth_node is None:
    auth_node = ET.SubElement(root, 'auth')
for tag, value in (('disabled', 'yes'), ('remote_enrollment', 'no')):
    node = auth_node.find(tag)
    if node is None:
        node = ET.SubElement(auth_node, tag)
    node.text = value
ET.indent(root, space='  ')
temporary = path.with_suffix('.conf.vvault-new')
ET.ElementTree(root).write(temporary, encoding='utf-8', xml_declaration=True)
temporary.chmod(0o640)
temporary.replace(path)
PY

api_yaml="/var/ossec/api/configuration/api.yaml"
if grep -Eq '^[[:space:]]*host:' "${api_yaml}"; then
  sed -i -E "s/^[[:space:]]*host:.*/host: ['127.0.0.1']/" "${api_yaml}"
else
  printf "\nhost: ['127.0.0.1']\n" >> "${api_yaml}"
fi
systemctl enable --now wazuh-manager

install -d -m 0700 -o root -g root "$(dirname "${ADMIN_ENV_FILE}")"
if [[ ! -e "${ADMIN_ENV_FILE}" ]]; then
  umask 077
  admin_password="$(python3 -c 'import secrets; print("Wz1!" + secrets.token_urlsafe(32))')"
  wui_password="$(python3 -c 'import secrets; print("Wz1!" + secrets.token_urlsafe(32))')"
  {
    printf 'WAZUH_API_ADMIN_USER=wazuh\n'
    printf 'WAZUH_API_ADMIN_PASSWORD=%s\n' "${admin_password}"
    printf 'WAZUH_API_WUI_PASSWORD=%s\n' "${wui_password}"
  } > "${ADMIN_ENV_FILE}"
  chmod 0600 "${ADMIN_ENV_FILE}"
fi

read_secret() {
  local key="$1" value
  value="$(sed -n "s/^${key}=//p" "${ADMIN_ENV_FILE}" | tail -n 1)"
  [[ -n "${value}" ]] || {
    echo "root-owned Wazuh administrator state is incomplete" >&2
    return 1
  }
  printf '%s' "${value}"
}

admin_user="$(read_secret WAZUH_API_ADMIN_USER)"
admin_password="$(read_secret WAZUH_API_ADMIN_PASSWORD)"
wui_password="$(read_secret WAZUH_API_WUI_PASSWORD)"

authenticate() {
  local username="$1" password="$2" curl_config="$3"
  USERNAME="${username}" PASSWORD="${password}" python3 - "${curl_config}" <<'PY'
import base64
import os
import sys
from pathlib import Path

encoded = base64.b64encode(
    f'{os.environ["USERNAME"]}:{os.environ["PASSWORD"]}'.encode('utf-8')
).decode('ascii')
Path(sys.argv[1]).write_text(f'header = "Authorization: Basic {encoded}"\n', encoding='utf-8')
PY
  chmod 0600 "${curl_config}"
  curl --fail --silent --show-error --cacert /var/ossec/api/configuration/ssl/server.crt \
    --config "${curl_config}" \
    --request POST https://127.0.0.1:55000/security/user/authenticate | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["token"])'
}

bootstrap_curl_config="${stage}/bootstrap.curl"
if bootstrap_token="$(authenticate "${admin_user}" "${admin_password}" "${bootstrap_curl_config}")"; then
  ADMIN_CREDENTIALS_ROTATED=1
else
  bootstrap_token="$(authenticate wazuh wazuh "${bootstrap_curl_config}")" || {
    echo "Wazuh API administrator authentication failed" >&2
    exit 1
  }
fi
api_curl_config="${stage}/api.curl"
printf 'header = "Authorization: Bearer %s"\n' "${bootstrap_token}" > "${api_curl_config}"
chmod 0600 "${api_curl_config}"

api_call() {
  local method="$1" path="$2" body="${3:-}" body_file=""
  if [[ -n "${body}" ]]; then
    body_file="$(mktemp "${stage}/api-body.XXXXXX")"
    printf '%s' "${body}" > "${body_file}"
    chmod 0600 "${body_file}"
    curl --fail --silent --show-error --cacert /var/ossec/api/configuration/ssl/server.crt \
      --config "${api_curl_config}" -H 'Content-Type: application/json' \
      -X "${method}" --data-binary "@${body_file}" "https://127.0.0.1:55000${path}"
    rm -f -- "${body_file}"
  else
    curl --fail --silent --show-error --cacert /var/ossec/api/configuration/ssl/server.crt \
      --config "${api_curl_config}" -X "${method}" \
      "https://127.0.0.1:55000${path}"
  fi
}

readarray -t existing < <(api_call GET '/security/users?limit=500' | python3 -c \
  'import json,sys; print("\n".join(str(x["id"]) for x in json.load(sys.stdin)["data"]["affected_items"] if x.get("username")=="cleanhouse-ingest"))')
if [[ "${#existing[@]}" -eq 0 ]]; then
  ingest_password="$(python3 -c 'import secrets; print("Ch1!" + secrets.token_urlsafe(32))')"
  user_id="$(api_call POST '/security/users' "{\"username\":\"cleanhouse-ingest\",\"password\":\"${ingest_password}\"}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["affected_items"][0]["id"])')"
else
  user_id="${existing[0]}"
  if [[ ! -r "${ENV_FILE}" ]]; then
    echo "existing cleanhouse-ingest user has no recoverable root-owned VVAULT credential" >&2
    exit 1
  fi
  ingest_password="$(sed -n 's/^VVAULT_WAZUH_MANAGER_PASSWORD=//p' "${ENV_FILE}" | tail -n 1)"
  [[ -n "${ingest_password}" ]]
fi
policy_id="$(api_call GET '/security/policies?limit=500' | python3 -c \
  'import json,sys; print(next((str(x["id"]) for x in json.load(sys.stdin)["data"]["affected_items"] if x.get("name")=="cleanhouse-evidence"), ""))')"
if [[ -z "${policy_id}" ]]; then
  policy_id="$(api_call POST '/security/policies' '{"name":"cleanhouse-evidence","policy":{"actions":["agent:create","agent:read","syscheck:read","manager:read"],"resources":["*:*:*","agent:id:*","agent:group:*"],"effect":"allow"}}' | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["affected_items"][0]["id"])')"
fi
role_id="$(api_call GET '/security/roles?limit=500' | python3 -c \
  'import json,sys; print(next((str(x["id"]) for x in json.load(sys.stdin)["data"]["affected_items"] if x.get("name")=="cleanhouse-evidence"), ""))')"
if [[ -z "${role_id}" ]]; then
  role_id="$(api_call POST '/security/roles' '{"name":"cleanhouse-evidence"}' | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["affected_items"][0]["id"])')"
fi
role_has_policy="$(api_call GET "/security/roles?role_ids=${role_id}" | POLICY_ID="${policy_id}" python3 -c \
  'import json,sys,os; p=int(os.environ["POLICY_ID"]); items=json.load(sys.stdin)["data"]["affected_items"]; print("yes" if items and p in items[0].get("policies",[]) else "no")' \
  2>/dev/null || true)"
if [[ "${role_has_policy}" != "yes" ]]; then
  api_call POST "/security/roles/${role_id}/policies?policy_ids=${policy_id}" >/dev/null
fi
user_has_role="$(api_call GET "/security/users?user_ids=${user_id}" | ROLE_ID="${role_id}" python3 -c \
  'import json,sys,os; r=int(os.environ["ROLE_ID"]); items=json.load(sys.stdin)["data"]["affected_items"]; print("yes" if items and r in items[0].get("roles",[]) else "no")' \
  2>/dev/null || true)"
if [[ "${user_has_role}" != "yes" ]]; then
  api_call POST "/security/users/${user_id}/roles?role_ids=${role_id}" >/dev/null
fi

users_payload="$(api_call GET '/security/users?limit=500')"
wui_user_id="$(printf '%s' "${users_payload}" | python3 -c \
  'import json,sys; print(next((str(x["id"]) for x in json.load(sys.stdin)["data"]["affected_items"] if x.get("username")=="wazuh-wui"), ""))')"
admin_user_id="$(printf '%s' "${users_payload}" | ADMIN_USER="${admin_user}" python3 -c \
  'import json,os,sys; name=os.environ["ADMIN_USER"]; print(next((str(x["id"]) for x in json.load(sys.stdin)["data"]["affected_items"] if x.get("username")==name), ""))')"
[[ -n "${wui_user_id}" && -n "${admin_user_id}" ]] || {
  echo "Wazuh API default administrator identities are unavailable" >&2
  exit 1
}
api_call PUT "/security/users/${wui_user_id}" "{\"password\":\"${wui_password}\"}" >/dev/null
api_call PUT "/security/users/${admin_user_id}" "{\"password\":\"${admin_password}\"}" >/dev/null
ADMIN_CREDENTIALS_ROTATED=1
remove_api_guard

install -d -m 0750 -o root -g "${SERVICE_USER}" "$(dirname "${ENV_FILE}")"
umask 027
{
  printf 'VVAULT_WAZUH_MANAGER_API_URL=https://127.0.0.1:55000\n'
  printf 'VVAULT_WAZUH_MANAGER_CA_CERT=/var/ossec/api/configuration/ssl/server.crt\n'
  printf 'VVAULT_WAZUH_MANAGER_USERNAME=cleanhouse-ingest\n'
  printf 'VVAULT_WAZUH_MANAGER_PASSWORD=%s\n' "${ingest_password}"
  printf 'VVAULT_WAZUH_AGENT_MANAGER=%s\n' "${AGENT_MANAGER}"
  printf 'VVAULT_WAZUH_AGENT_NAME=zen-001\n'
  printf 'VVAULT_WAZUH_ALERTS_PATH=/var/ossec/logs/alerts/alerts.json\n'
} > "${ENV_FILE}"
chown root:"${SERVICE_USER}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"
usermod -a -G wazuh "${SERVICE_USER}"
install -d -m 0755 /etc/systemd/system/vvault-backend.service.d
printf '[Service]\nEnvironmentFile=%s\nSupplementaryGroups=wazuh\n' "${ENV_FILE}" \
  > /etc/systemd/system/vvault-backend.service.d/wazuh.conf
systemctl daemon-reload
systemctl restart vvault-backend.service

ss -ltn | grep -Eq '127\.0\.0\.1:55000'
ss -ltn | grep -Eq '(^|[[:space:]])[^[:space:]]*:1514([[:space:]]|$)'
if ss -ltn | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\[::\]):55000([[:space:]]|$)'; then
  echo "Wazuh API is exposed beyond loopback" >&2
  exit 1
fi
curl --fail --silent --show-error http://127.0.0.1:8000/api/ready >/dev/null
echo "manager-only Wazuh ${VERSION} installed and VVAULT restarted"
