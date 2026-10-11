#!/usr/bin/env bash
set -Eeuo pipefail

STAGE_DIR="/var/tmp/vvault-wazuh-manager-deploy"
INSTALLER="${STAGE_DIR}/scripts/install-wazuh-manager.sh"
EXPECTED_SHA256="d45a60ce17e87cb570edf6c339de3a681f67fa950ec7d20a0257a42fd7cc890d"
LOCK_FILE="/run/lock/vvault-wazuh-manager-install.lock"
PRIVATE_INSTALLER=""

if [[ "${EUID}" -ne 0 ]]; then
  echo "the bounded Wazuh manager wrapper requires root" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${PRIVATE_INSTALLER}" && "${PRIVATE_INSTALLER}" == /var/tmp/vvault-wazuh-manager-root.* ]]; then
    rm -f -- "${PRIVATE_INSTALLER}"
  fi
  if [[ "${STAGE_DIR}" == /var/tmp/vvault-wazuh-manager-deploy && -d "${STAGE_DIR}" && ! -L "${STAGE_DIR}" ]]; then
    rm -rf -- "${STAGE_DIR}"
  fi
}
trap cleanup EXIT

exec 9>"${LOCK_FILE}"
flock -n 9 || {
  echo "another Wazuh manager installation is active" >&2
  exit 1
}

[[ -d "${STAGE_DIR}" && ! -L "${STAGE_DIR}" ]] || {
  echo "bounded Wazuh stage is unavailable" >&2
  exit 1
}
[[ -f "${INSTALLER}" && ! -L "${INSTALLER}" ]] || {
  echo "bounded Wazuh installer is unavailable" >&2
  exit 1
}
[[ "$(stat -c '%U:%G:%a' "${STAGE_DIR}")" == "deploy:deploy:700" ]] || {
  echo "bounded Wazuh stage ownership or mode is invalid" >&2
  exit 1
}
[[ "$(stat -c '%U:%G:%a' "${INSTALLER}")" == "deploy:deploy:600" ]] || {
  echo "bounded Wazuh installer ownership or mode is invalid" >&2
  exit 1
}
printf '%s  %s\n' "${EXPECTED_SHA256}" "${INSTALLER}" | sha256sum --check --strict

PRIVATE_INSTALLER="$(mktemp /var/tmp/vvault-wazuh-manager-root.XXXXXX)"
install -o root -g root -m 0700 "${INSTALLER}" "${PRIVATE_INSTALLER}"
printf '%s  %s\n' "${EXPECTED_SHA256}" "${PRIVATE_INSTALLER}" | sha256sum --check --strict

VVAULT_SERVICE_USER=vvault \
VVAULT_WAZUH_AGENT_MANAGER=vvault.thewreck.org \
  /bin/bash "${PRIVATE_INSTALLER}"
