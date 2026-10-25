# VVAULT Workspace Restoration Release Receipt

This receipt is bounded to the authenticated workspace projection repair. It
does not authorize activation and it does not claim live product acceptance.

## Read-only production inventory

Captured `2026-10-25T23:37:22Z` from host `vvault-server` through the existing
`deploy@165.245.136.194` SSH path using read-only `df`, `du`, `readlink`, Git,
and systemd inspection.

- Mounted serving filesystem: `/dev/vda1`, ext4, `/`.
- Capacity: `35,283,333,120` bytes total; `33,322,553,344` used;
  `1,944,002,560` available; 95% used.
- Live link: `/opt/vvault-public`.
- Live release: `/opt/vvault-releases/f0dad3d71638096e348f9873800c6ec635531c1c`.
- Verified live Git SHA: `f0dad3d71638096e348f9873800c6ec635531c1c`.
- Live release size: `339,332,317` bytes.
- Published frontend size: `28,471,273` bytes.
- Release source-byte projection: `339,358,470` bytes (the live release plus
  the exact `26,153`-byte net source delta in this candidate).
- Minimum simultaneous activation requirement: `367,829,743` bytes
  (one new immutable release plus one frontend rollback copy).
- Capacity remaining after that minimum: `1,576,172,817` bytes.
- Deployment unit: `vvault-deploy.service`, last recorded result `success`.
- Deployment executable: `/usr/local/libexec/vvault-immutable-deploy`.

No deletion or storage purchase is required for this bounded release. The
remaining headroom is not permission to retain additional releases indefinitely.

The canonical deploy capacity gate is stricter than the release's immediate
copy requirement: it requires the greater of 5 GiB or 15% of the root
filesystem. For this host that is `5,368,709,120` bytes. The captured root free
space is short by `3,424,706,560` bytes, so activation is blocked even though
the new release itself fits.

An existing ext4 volume is mounted at `/mnt/vvault_runtime_storage` from
`/dev/sda`: `106,330,775,552` bytes total and `90,568,306,688` available.
Immutable releases and deployment backups currently consume approximately
`6,802,395,136` and `2,417,938,432` allocated bytes on root. The reviewed
`vvault-capacity-relocate.sh` plan copies both trees to the existing volume,
keeps a second byte-preserving original there for rollback, verifies them with
checksum-mode `rsync`, and bind-mounts them back at the unchanged paths. It
requires about `18,440,667,136` bytes on the volume and projects root free space
above 11 GiB without deleting a release, backup, or data record.

The relocation remains a separate, explicitly authorized maintenance action:

```sh
sudo scripts/deployment/vvault-capacity-relocate.sh preflight
sudo scripts/deployment/vvault-capacity-relocate.sh apply
# Only if relocation itself must be reversed:
sudo scripts/deployment/vvault-capacity-relocate.sh rollback
```

The apply path takes the immutable deployment lock, performs a live copy, stops
only the VVAULT backend for the final delta and mount switch, preserves original
trees on the volume, installs persistent bind entries, reruns the canonical
capacity calculation, restarts the backend, and requires readiness. Its error
trap restores the rootfs paths and service. Deployment must not proceed until
the post-relocation capacity gate reports PASS.

## SHA-pinned activation chain

The release SHA is supplied by the final PM receipt and must be the exact
40-character commit tested and independently accepted. Before activation:

```sh
RELEASE_SHA='<accepted 40-character release SHA>'
test "$(git rev-parse "$RELEASE_SHA^{commit}")" = "$RELEASE_SHA"
test "$(git rev-parse "$RELEASE_SHA^")" = 'f0dad3d71638096e348f9873800c6ec635531c1c'
git diff --quiet f0dad3d71638096e348f9873800c6ec635531c1c "$RELEASE_SHA" -- . \
  ':!vvault/server/chatty_body_service.py' \
  ':!vvault/server/construct_taxonomy.py' \
  ':!vvault/server/projection_classification.py' \
  ':!tests/test_vvault_workspace_projection_bounded.py' \
  ':!scripts/deployment/vvault-workspace-live-gate.sh' \
  ':!scripts/deployment/vvault-immutable-rollback.sh' \
  ':!docs/operations/VVAULT_WORKSPACE_RESTORE_RELEASE_RECEIPT.md'
git push https://github.com/TitaniumShinobi/vvault.git \
  "$RELEASE_SHA:refs/heads/production"
```

The ordinary production push triggers `.github/workflows/deploy-ci.yml`, which
invokes `/opt/deploy/trigger/deploy-trigger.sh vvault`, then the VVAULT trigger,
then root-owned `vvault-deploy.service`. The immutable deployer must materialize
`/opt/vvault-releases/$RELEASE_SHA` and change `/opt/vvault-public` only after
its staging checks succeed.

## Rollback

Before activation, verify the previous directory and Git SHA still equal
`f0dad3d71638096e348f9873800c6ec635531c1c`. If post-activation acceptance
fails, an authorized root operator runs:

```sh
sudo /opt/vvault-public/scripts/deployment/vvault-immutable-rollback.sh \
  --expected-current "$RELEASE_SHA" \
  --previous f0dad3d71638096e348f9873800c6ec635531c1c
```

The rollback script takes the established deployment lock, verifies both SHA
directories, atomically restores the previous live symlink, restarts only
`vvault-backend.service`, and requires canonical readiness. If readiness fails,
it restores the expected-current symlink and reports failure. This source-only
repair does not alter the frontend, database, schema, sessions, or stored data.

## Required post-activation gate

With two user-controlled authenticated cookie jars and a known canonical file:

```sh
VVAULT_BASE_URL=https://vvault.thewreck.org \
VVAULT_PRIMARY_COOKIE_JAR=/secure/path/primary.cookies \
VVAULT_SECONDARY_COOKIE_JAR=/secure/path/secondary.cookies \
VVAULT_CONSTRUCT_ID='<primary owner construct>' \
VVAULT_FILE_ID='<primary owner canonical file id>' \
scripts/deployment/vvault-workspace-live-gate.sh
```

The gate requires canonical readiness, an authenticated owner workspace root,
the requested construct and its opaque workspace reference, direct children,
an actual canonical file read, the authenticated Chatty construct-files path,
and 403/404 denial for the authorized second account. It rejects a root response
containing `QueryCanceled` and requires root projection in at most 4.5 seconds.
It reports a hash receipt rather than file contents or owner identifiers.

## Monitoring integration

Read-only inspection on `2026-10-25T23:40:32Z` found no VVAULT workspace health
timer, service, or cron entry. Existing deployment and Wazuh workflows check
`/api/ready`; that does not establish authenticated workspace functionality.

This release therefore includes `vvault-workspace-monitor.service` and its
five-minute timer. They run the same authenticated product gate as user
`vvault`. Before enabling them, an authorized operator must provision
`/etc/vvault/workspace-monitor.env` mode `0600`, root-owned, pointing at two
user-authorized cookie jars under `/var/lib/vvault-monitor` readable only by
the `vvault` service account. Then install and start the schedule:

```sh
sudo install -o root -g root -m 0644 \
  scripts/deployment/systemd/vvault-workspace-monitor.service \
  /etc/systemd/system/vvault-workspace-monitor.service
sudo install -o root -g root -m 0644 \
  scripts/deployment/systemd/vvault-workspace-monitor.timer \
  /etc/systemd/system/vvault-workspace-monitor.timer
sudo systemctl daemon-reload
sudo systemctl enable --now vvault-workspace-monitor.timer
sudo systemctl start vvault-workspace-monitor.service
```

Expired or revoked user ceremonies must fail the monitor rather than silently
fall back to readiness. Until those two user-controlled sessions are
provisioned, continuous authenticated monitoring remains NOT_VERIFIED and the
manual post-activation gate remains mandatory.
