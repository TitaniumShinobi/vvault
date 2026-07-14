# Backup and Restore (vvault)

Nightly backup of vvault layers, config, and data so you can rebuild quickly after an outage or sabotage.

## What is backed up

The script `scripts/backup_vvault.py` zips:

- `vvault/layers/` (layer manifests)
- `vvault/config/` (e.g. vvault_dev_config.yaml)
- `vvault/data/` (ledger, registry, audit DB, etc.)

The current runtime body database and VVAULT-native object storage are **not** included in this repo-directory zip. Back up Postgres (`ovvaults.*`) and S3-compatible object storage separately. Legacy exports are historical artifacts only.

## Running a backup

From repo root:

```bash
# Default: writes to repo/backups/vvault_backup_YYYYMMDD_HHMMSS.zip
python scripts/backup_vvault.py

# Custom output directory (e.g. NFS or mounted bucket)
export VVAULT_BACKUP_OUTPUT_DIR=/mnt/backups/vvault
python scripts/backup_vvault.py
```

**Nightly (cron):**

```cron
0 2 * * * cd /path/to/vvault && VVAULT_BACKUP_OUTPUT_DIR=/mnt/offbox/vvault_backups python scripts/backup_vvault.py
```

## Storing backups off-box

- Store the zip in a **separate cloud account or provider** (e.g. different AWS account, different bucket with no delete for the app).
- Prefer **encryption at rest** (S3 SSE, bucket policy, or encrypt the file before upload). Example after running the script:
  - `gpg -c vvault_backup_*.zip` and upload the `.gpg` file, or
  - Upload to S3 with server-side encryption and a bucket in another account.

## Restore drill (monthly)

Run at least monthly to confirm backups are usable.

1. Pick a recent backup zip from your off-box location.
2. Copy it to a temp dir and unzip:
   ```bash
   unzip vvault_backup_YYYYMMDD_HHMMSS.zip -d /tmp/vvault_restore_drill
   ```
3. From the extracted tree, copy contents into the **repo** so that:
   - `vvault/layers/` is replaced (or merged) with the backup’s `vvault/layers/`
   - `vvault/config/` and `vvault/data/` similarly
4. Restart the app and verify:
   - Layer manifests load (e.g. witnessCustodian or layer API).
   - Config is applied (e.g. pocketverse_mode, layer1_enabled).
5. Record the drill in your runbook (date, backup used, result).

## Rehydrate a new environment

When standing up a new host or account from scratch:

1. **Secrets and env:** Restore `.env` (or equivalent) from a secure store; do not commit secrets. Set at least: `DATABASE_URL`, optional body DB override vars, local auth/session secrets, OAuth client ID/secret, S3-compatible storage vars, admin emails, and any `VVAULT_*` vars you use. Legacy env restoration is needed only for historical extraction.
2. **Repo and code:** Clone the repo or deploy the same code version that was backed up.
3. **Body database:** Restore VVAULT Postgres with the `ovvaults` schema, including `users`, `sessions`, `vault_files`, `transcripts`, and materialized body/provenance fields.
4. **Object storage:** Restore VVAULT-native S3-compatible buckets/objects for any binary rows referenced by `ovvaults.vault_files`.
5. **Layers, config, data:** From the latest backup zip, extract `vvault/layers`, `vvault/config`, and `vvault/data` into the repo’s `vvault/` directory (overwriting or merging as needed).
6. **Legacy exports:** Restore or keep them only if an offboarding/provenance drill requires them; do not use them as runtime truth.
7. **Verify:** Start the server; hit `/api/status`, `/api/health`, `/api/ready`, and focused auth/file/body endpoints; confirm constructs and config look correct.
8. **Audit:** Ensure `vvault/data/audit.db` exists if you use privileged audit logging; the shipper can resume from state.

Order: env → code → body DB → object storage → vvault dirs → legacy exports if applicable → start app → verify.
