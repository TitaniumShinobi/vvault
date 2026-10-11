#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE="${ROOT}/scripts/vvault-wazuh-manager-install-wrapper.sh"
TARGET="/usr/local/libexec/vvault-wazuh-manager-install"
SUDOERS="/etc/sudoers.d/vvault-wazuh-manager-install"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Wazuh deploy-boundary bootstrap requires root" >&2
  exit 1
fi
[[ -f "${SOURCE}" && ! -L "${SOURCE}" ]] || {
  echo "reviewed Wazuh deploy wrapper is unavailable" >&2
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
