#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SELF="${ROOT}/scripts/bootstrap-wazuh-manager-deploy.sh"
SOURCE="${ROOT}/scripts/vvault-wazuh-manager-install-wrapper.sh"
TARGET="/usr/local/libexec/vvault-wazuh-manager-install"
SUDOERS="/etc/sudoers.d/vvault-wazuh-manager-install"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Wazuh deploy-boundary bootstrap requires root" >&2
  exit 1
fi
[[ -f "${SELF}" && ! -L "${SELF}" && "$(stat -c '%U:%G:%a' "${SELF}")" == "root:root:700" ]] || {
  echo "bootstrap must run from a root-owned private copy" >&2
  exit 1
}
[[ -f "${SOURCE}" && ! -L "${SOURCE}" ]] || {
  echo "reviewed Wazuh deploy wrapper is unavailable" >&2
  exit 1
}
[[ "$(stat -c '%U:%G:%a' "${SOURCE}")" == "root:root:700" ]] || {
  echo "reviewed Wazuh deploy wrapper must be a root-owned private copy" >&2
  exit 1
}
id deploy >/dev/null 2>&1 || {
  echo "VVAULT deploy account is unavailable" >&2
  exit 1
}
command -v visudo >/dev/null 2>&1 || {
  echo "visudo is required" >&2
  exit 1
}

install -d -o root -g root -m 0755 "$(dirname "${TARGET}")"
install -o root -g root -m 0755 "${SOURCE}" "${TARGET}"

temporary="$(mktemp "${SUDOERS}.XXXXXX")"
trap 'rm -f -- "${temporary}"' EXIT
printf '%s\n' \
  'Cmnd_Alias VVAULT_WAZUH_MANAGER_INSTALL = /usr/local/libexec/vvault-wazuh-manager-install' \
  'deploy ALL=(root) NOPASSWD: VVAULT_WAZUH_MANAGER_INSTALL' > "${temporary}"
chmod 0440 "${temporary}"
visudo -cf "${temporary}" >/dev/null
install -o root -g root -m 0440 "${temporary}" "${SUDOERS}"
visudo -cf "${SUDOERS}" >/dev/null

echo "bounded VVAULT Wazuh manager deployment boundary installed"
