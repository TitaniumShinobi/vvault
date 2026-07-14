# VVAULT Local Restore Drill

This document is the durable source of truth for proving that local VVAULT can come back as a working system after a Mac reset.

## Runtime Map

- Canonical local repo: `/Users/devon/Library/Mobile Documents/com~apple~CloudDocs/Documents/GitHub/vvault`
- Live VVAULT frontend: `http://localhost:7784`
- Live VVAULT backend: `http://localhost:8000`
- Live auth service: `http://localhost:1111`
- Restore drill frontend: `http://localhost:17784`
- Restore drill backend: `http://localhost:18000`
- Restore drill auth service: `http://localhost:1112`

The live ports are not drill ports. A restore drill must not replace or stop `7784`, `8000`, or `1111`.

## OVVAULTS Authority

The local app shell can run on localhost, but the canonical data authority is OVVAULTS:

- source database: `vvault_body_20260504t123219z`
- schema: `ovvaults`
- runtime owners: `ovvaults.vault_files`, `ovvaults.transcripts`, `ovvaults.users`, `ovvaults.sessions`

Localhost means local process routing only. It does not mean the database is local. A healthy local dashboard reads the same canonical OVVAULTS body database through VVAULT-native environment variables:

```bash
VVAULT_BODY_DB_URL=...
VVAULT_BODY_DB_SERVICE_ROLE_KEY=...
VVAULT_BODY_DB_SCHEMA=ovvaults
VVAULT_BODY_DB_SOURCE_DATABASE=vvault_body_20260504t123219z
```

Never remove or rename these variables during wording cleanup. If they are missing, Vault routes can degrade to `body_database_not_configured` even though the data still exists.

## What Counts As Restored

VVAULT is locally restored when all of these are true:

- `GET /api/ready` returns `ready: true`.
- Readiness reports `storage_mode: vvault_body`.
- Readiness reports `canonical_schema: ovvaults`.
- An authenticated `GET /api/vault/files` returns `200` and body database file rows.
- An authenticated `GET /api/chatty/constructs` returns `200` and expected construct data such as `zen-001`.
- The dashboard can be opened from the restore drill frontend.

Google OAuth is not required for this proof. OAuth on the drill frontend only works if the provider allows `http://localhost:17784/api/auth/google/callback`. If that callback is not registered, use the local bridge session and database dashboard proof instead.

## OAuth Callback Contract

For the live local stack, Google OAuth starts at:

```text
http://localhost:7784/api/auth/google
```

The callback must remain on the browser-facing frontend/proxy origin:

```text
http://localhost:7784/api/auth/google/callback
```

It must not drift to backend port `8000`. The frontend proxy passes the browser origin to the backend with:

```text
X-Forwarded-Host: localhost:7784
X-Forwarded-Proto: http
```

Local OAuth uses HTTP. The backend may set `OAUTHLIB_INSECURE_TRANSPORT=1` only outside production. Production must use HTTPS.

## Chatty Bridge Readiness

Chatty depends on VVAULT readiness instead of treating local conversation state as authority. The bridge is healthy only when:

```bash
curl -fsS http://localhost:5050/api/vvault/ready
```

reports `ready: true`, `storage_mode: vvault_body`, and OVVAULTS ownership. If Chatty shows "Connecting to VVAULT", verify VVAULT first:

```bash
curl -fsS http://localhost:7784/api/ready
curl -fsS http://localhost:8000/api/ready
```

Expected readiness includes `storage_owner: "ovvaults.vault_files"` and `transcript_owner: "ovvaults.transcripts"`. If `/api/ready` returns HTML, the backend route is missing or not loaded and the frontend catch-all is serving `index.html`.

## Drill Command

From the canonical repo:

```bash
scripts/vvault-restore-drill.sh
```

For agent-safe verification without opening a browser:

```bash
scripts/vvault-restore-drill.sh --no-open
```

The script creates a fresh temp copy under `/private/tmp/vvault-restore-drill-*`, starts the drill stack on `17784/18000/1112`, verifies the runtime gates, writes `RESTORE_RECEIPT.md` inside the temp copy, and prints the exact cleanup command.

## Human Dashboard Check

After the drill reports success, open the drill frontend printed by the script and confirm the Vault dashboard renders from body database reads. Do not use Google OAuth as the restore proof unless the drill callback has been registered with the provider.

The script intentionally leaves the drill stack running for inspection. Cleanup happens only after human verification.

## Cleanup

Run the cleanup command printed by the drill, shaped like:

```bash
scripts/vvault-restore-drill.sh --cleanup /private/tmp/vvault-restore-drill-...
```

Cleanup stops only the PIDs recorded inside the drill temp copy and removes only that temp copy. After cleanup, verify the live runtime still answers:

```bash
curl -fsS http://localhost:7784/api/ready
```

## Safety Rules

- No GitHub push.
- No droplet deploy.
- No schema migration.
- No destructive database action.
- No secrets in docs, receipts, logs, or chat output.
- No transcript deletion.
- No stale alias pointing directly at a temp repo.

If a `vvault-test` shell alias is used, it must call `scripts/vvault-restore-drill.sh` from the canonical repo. It must never point at a specific `/private/tmp/vvault-restore-drill-*` directory.
