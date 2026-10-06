#!/usr/bin/env bash
set -euo pipefail

VERSION="4.14.7-1"
PACKAGE="wazuh-manager_4.14.7-1_amd64.deb"
PACKAGE_URL="https://packages.wazuh.com/4.x/apt/pool/main/w/wazuh-manager/${PACKAGE}"
PACKAGE_SHA512="f54a48683683fea476b133646c6a2ad884c3d61d0f7d85bf8b0602e127e0e14a6976fc5cf5962cc47de2794fdd0e2abe2f195de1d7a7b9c69da4b79c09a970f7"
SERVICE_USER="${VVAULT_SERVICE_USER:-vvault}"
ENV_FILE="${VVAULT_WAZUH_ENV_FILE:-/etc/vvault/wazuh.env}"
AGENT_MANAGER="${VVAULT_WAZUH_AGENT_MANAGER:?VVAULT_WAZUH_AGENT_MANAGER is required}"
BOOTSTRAP_USER="${WAZUH_API_BOOTSTRAP_USER:?WAZUH_API_BOOTSTRAP_USER is required}"
BOOTSTRAP_PASSWORD="${WAZUH_API_BOOTSTRAP_PASSWORD:?WAZUH_API_BOOTSTRAP_PASSWORD is required}"

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

stage="$(mktemp -d /var/tmp/vvault-wazuh-manager.XXXXXX)"
trap 'rm -rf "${stage}"' EXIT
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

bootstrap_curl_config="${stage}/bootstrap.curl"
BOOTSTRAP_USER="${BOOTSTRAP_USER}" BOOTSTRAP_PASSWORD="${BOOTSTRAP_PASSWORD}" \
  python3 - "${bootstrap_curl_config}" <<'PY'
import base64
import os
import sys
from pathlib import Path

encoded = base64.b64encode(
    f'{os.environ["BOOTSTRAP_USER"]}:{os.environ["BOOTSTRAP_PASSWORD"]}'.encode('utf-8')
).decode('ascii')
Path(sys.argv[1]).write_text(f'header = "Authorization: Basic {encoded}"\n', encoding='utf-8')
PY
chmod 0600 "${bootstrap_curl_config}"
bootstrap_token="$(curl --fail --silent --show-error --cacert /var/ossec/api/configuration/ssl/server.crt \
  --config "${bootstrap_curl_config}" \
  --request POST https://127.0.0.1:55000/security/user/authenticate | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["token"])')"
api_curl_config="${stage}/api.curl"
printf 'header = "Authorization: Bearer %s"\n' "${bootstrap_token}" > "${api_curl_config}"
chmod 0600 "${api_curl_config}"

api_call() {
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "${body}" ]]; then
    curl --fail --silent --show-error --cacert /var/ossec/api/configuration/ssl/server.crt \
      --config "${api_curl_config}" -H 'Content-Type: application/json' \
      -X "${method}" -d "${body}" "https://127.0.0.1:55000${path}"
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
