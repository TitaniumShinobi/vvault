#!/usr/bin/env bash
set -Eeuo pipefail

mode="${1:-}"
case "$mode" in preflight|apply|rollback) ;; *) echo "usage: sudo $0 {preflight|apply|rollback}" >&2; exit 64 ;; esac
[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "root is required" >&2; exit 77; }

volume="/mnt/vvault_runtime_storage"
target_root="$volume/vvault-deploy"
release_source="/opt/vvault-releases"
backup_source="/opt/deploy/backups"
release_target="$target_root/releases"
backup_target="$target_root/backups"
release_original="$target_root/rootfs-original-vvault-releases"
backup_original="$target_root/rootfs-original-vvault-backups"
fstab="/etc/fstab"
marker="# vvault-deploy-storage-bind-v1"
ready_url="http://127.0.0.1:8000/api/ready"

require_mount() {
  [[ "$(findmnt -nro SOURCE "$volume")" == "/dev/sda" ]] || {
    echo "$volume is not the established /dev/sda volume" >&2
    exit 65
  }
  [[ "$(findmnt -nro FSTYPE "$volume")" == "ext4" ]] || {
    echo "$volume is not ext4" >&2
    exit 65
  }
}

capacity_gate() {
  local total available required
  read -r total available < <(df -B1 --output=size,avail / | tail -n 1)
  required=$((total * 15 / 100))
  (( required < 5368709120 )) && required=5368709120
  printf 'root_total=%s root_available=%s required=%s\n' "$total" "$available" "$required"
  (( available >= required ))
}

preflight() {
  require_mount
  for command in rsync flock findmnt mountpoint; do command -v "$command" >/dev/null; done
  [[ -d "$release_source" && -d "$backup_source" ]]
  [[ ! -e "$release_original" && ! -e "$backup_original" ]]
  local release_bytes backup_bytes volume_available required_copy
  release_bytes="$(du -sb "$release_source" | cut -f1)"
  backup_bytes="$(du -sb "$backup_source" | cut -f1)"
  volume_available="$(df -B1 --output=avail "$volume" | tail -n 1)"
  # Keep both the serving copy and a byte-preserving original on the volume so
  # relocation itself remains reversible without deleting a release or backup.
  required_copy=$((2 * (release_bytes + backup_bytes)))
  printf 'release_bytes=%s backup_bytes=%s volume_available=%s required_copy=%s\n' \
    "$release_bytes" "$backup_bytes" "$volume_available" "$required_copy"
  (( volume_available > required_copy + 1073741824 ))
  stat -c 'source=%n mode=%a owner=%u:%g' "$release_source" "$backup_source"
}

verify_copy() {
  local source="$1" target="$2" drift
  drift="$(rsync -aHAXnrc --delete --numeric-ids --one-file-system "$source"/ "$target"/)"
  [[ -z "$drift" ]] || { echo "copy verification failed for $source" >&2; exit 66; }
}

recover_failed_apply() {
  local status=$?
  trap - ERR
  set +e
  mountpoint -q "$backup_source" && umount "$backup_source"
  mountpoint -q "$release_source" && umount "$release_source"
  remove_fstab_entries
  if [[ -d "$release_original" ]]; then
    rmdir "$release_source" 2>/dev/null
    mv "$release_original" "$release_source"
  fi
  if [[ -d "$backup_original" ]]; then
    rmdir "$backup_source" 2>/dev/null
    mv "$backup_original" "$backup_source"
  fi
  systemctl daemon-reload
  systemctl start vvault-backend.service
  echo "capacity relocation failed; original rootfs paths restored" >&2
  exit "$status"
}

remove_fstab_entries() {
  local temporary
  temporary="$(mktemp)"
  awk -v marker="$marker" '$0 != marker && $0 !~ /\/mnt\/vvault_runtime_storage\/vvault-deploy\/(releases|backups) \/opt\/(vvault-releases|deploy\/backups) none bind/' "$fstab" > "$temporary"
  install -o root -g root -m 0644 "$temporary" "$fstab"
  rm -f "$temporary"
}

apply_relocation() {
  preflight
  mkdir -p "$release_target" "$backup_target"
  rsync -aHAX --numeric-ids --one-file-system "$release_source"/ "$release_target"/
  rsync -aHAX --numeric-ids --one-file-system "$backup_source"/ "$backup_target"/
  verify_copy "$release_source" "$release_target"
  verify_copy "$backup_source" "$backup_target"

  exec 9>/run/lock/vvault-deploy.lock
  flock -n 9 || { echo "VVAULT deployment lock is busy" >&2; exit 75; }
  trap recover_failed_apply ERR
  systemctl stop vvault-backend.service
  rsync -aHAX --numeric-ids --one-file-system "$release_source"/ "$release_target"/
  rsync -aHAX --numeric-ids --one-file-system "$backup_source"/ "$backup_target"/
  verify_copy "$release_source" "$release_target"
  verify_copy "$backup_source" "$backup_target"

  mv "$release_source" "$release_original"
  mv "$backup_source" "$backup_original"
  mkdir -p "$release_source" "$backup_source"
  chown root:root "$release_source"
  chmod 0755 "$release_source"
  chown deploy:deployers "$backup_source"
  chmod 0755 "$backup_source"
  mount --bind "$release_target" "$release_source"
  mount --bind "$backup_target" "$backup_source"
  {
    echo "$marker"
    echo "$release_target $release_source none bind,nofail,x-systemd.requires-mounts-for=$volume 0 0"
    echo "$backup_target $backup_source none bind,nofail,x-systemd.requires-mounts-for=$volume 0 0"
  } >> "$fstab"
  mountpoint -q "$release_source" && mountpoint -q "$backup_source"
  [[ "$(readlink -f /opt/vvault-public)" == "$release_source"/* ]]
  capacity_gate
  systemctl daemon-reload
  systemctl start vvault-backend.service
  curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 2 "$ready_url" >/dev/null
  trap - ERR
  echo "capacity_relocation=PASS"
}

rollback_relocation() {
  require_mount
  exec 9>/run/lock/vvault-deploy.lock
  flock -n 9 || { echo "VVAULT deployment lock is busy" >&2; exit 75; }
  [[ -d "$release_original" && -d "$backup_original" ]] || {
    echo "preserved rootfs originals are absent" >&2
    exit 66
  }
  systemctl stop vvault-backend.service
  mountpoint -q "$backup_source" && umount "$backup_source"
  mountpoint -q "$release_source" && umount "$release_source"
  rmdir "$release_source" "$backup_source"
  mv "$release_original" "$release_source"
  mv "$backup_original" "$backup_source"
  remove_fstab_entries
  systemctl daemon-reload
  systemctl start vvault-backend.service
  curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 2 "$ready_url" >/dev/null
  echo "capacity_relocation_rollback=PASS"
}

case "$mode" in
  preflight) preflight ;;
  apply) apply_relocation ;;
  rollback) rollback_relocation ;;
esac
