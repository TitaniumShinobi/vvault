# VVAULT Runtime Closure Supabase Checklist

There is no failproof. There is only looping until the circle is closed.

This checklist is the operator and developer runbook for proving VVAULT is actually ready. It is not enough for the Flask process to answer, the webpack server to serve a page, or OAuth credentials to exist. VVAULT is ready only when Supabase identity and storage authority are proven by the steward.

## Status And Scope

- `/api/status` proves the local backend process is alive.
- `/api/health` may describe a degraded process and is not a readiness gate.
- `/api/ready` is the strict readiness gate.
- `SupabaseConnectionSteward` owns canonical Supabase proof.
- OAuth may be configured while identity authority is unavailable.
- Reads may soft-degrade only from trusted local/cached data.
- Writes must be confirmed, explicitly queued with receipt, or rejected.

## Supabase Steward Closure Gate

VVAULT is closed only when `/api/ready` returns `200` and the response includes:

- `ready: true`
- `status: "ready"`
- `supabase.connection.connection_state: "connected"`
- `supabase.connection.canonical: true`
- `supabase.connection.storage_mode: "supabase"`
- `supabase.connection.last_error_code: null`
- `supabase.connection.recovery_proven_at` present

Any other state is not ready. It may be running, degraded, reconnecting, blocked, or booting, but it is not closed.

## Strict Readiness Contract

Use this order. Do not skip layers.

1. Check `http://localhost:7784/api/status`.
2. Check `http://localhost:7784/api/ready`.
3. Check `http://localhost:7784/api/auth/google/health`.
4. If `/api/ready` fails, probe Supabase directly without printing secrets.
5. Split gateway reachability from database-backed table reads.
6. Fix the lowest broken layer.
7. Re-run the same checks.

## Supabase Probe Split

When Supabase appears degraded, use this classification:

- Host/root responds quickly: project host and DNS are reachable.
- `/rest/v1/` without key returns fast `401`: gateway is reachable.
- Bad key returns fast `401 Invalid API key`: project ref and gateway auth path are reachable.
- Real key table reads return slow `522`: database-backed REST path is degraded.
- Storage returns `544 DatabaseTimeout`: project database path is degraded.

Do not call a project-wide or global Supabase outage without direct evidence. Do not blame VVAULT until local process, proxy, steward, and direct Supabase probes are separated.

## Read/Write Runtime Matrix

| Operation | Connected steward | Degraded steward |
| --- | --- | --- |
| `/api/ready` | `200` ready | `503` not ready |
| OAuth health | available only when canonical | blocked with clear identity-authority error |
| OAuth login/callback | allowed only when canonical | fail closed before identity mutation |
| Canonical writes | allowed | `503` or queued only if an allowed durable receipt exists |
| Trusted reads | canonical Supabase reads | soft-degrade only from trusted local/cached data |
| Missing Supabase row | data-dependent | never proof of nonexistence |

## Frontend Closure Signals

The UI must not claim Supabase is connected unless `/api/ready` proves it.

- Treat `/api/ready` `503` as expected degraded state.
- Suppress repeated outage/session-expired spam.
- Stop authenticated fetch loops after local session expiry.
- Show that login and writes requiring identity authority are blocked until recovery.
- Never call degraded state healthy.

## Launcher And Duplicate Listener Hygiene

Before debugging Supabase, prove local listener shape:

1. There must be one frontend listener on `127.0.0.1:7784`.
2. There must be one backend listener on `127.0.0.1:8000`.
3. Ambiguous duplicate listeners are a startup failure signature.
4. `vvault` should reuse an existing good app instead of spawning duplicates.
5. `vvault` success must not be confused with canonical readiness if `/api/ready` is degraded.

## QFB Cleanup Posture

QFB means quarantine, fix/flush, bless.

1. Quarantine: isolate incorrect legacy docs, mocks, generated artifacts, and unsafe assumptions before deletion.
2. Fix/flush: repair the active contract or remove proven noise. Do not delete data or old docs only because they are annoying.
3. Bless: document what remains canonical, what was left as legacy, and what must not drift again.

For VVAULT cleanup:

- Do not stage generated data, `.DS_Store`, Playwright output, or runtime databases with feature work.
- Do not stage deleted backups without an explicit data-retention decision.
- Do not merge old schema docs as current truth unless they mention `/api/ready`, the steward, `users`, and `vault_files`.
- Do not stage protected governance/runtime surfaces without a deliberate review receipt.

## Receipt Template

Every closure receipt must include:

- exact commands run
- exact status codes observed
- primary blocker
- lowest broken layer
- repair performed
- contracts preserved
- remaining risk

## Validation Commands

Run the focused contracts when touching this lane:

```bash
.venv/bin/python -m pytest tests/test_auth_life_identity_contract.py tests/test_supabase_connection_steward.py tests/test_supabase_write_outbox.py tests/test_supabase_system_file_outbox_replay.py tests/test_supabase_timeout_contract.py tests/test_frontend_outage_contract_static.py
.venv/bin/python -m pytest tests/test_vault_file_preview_api.py tests/test_vault_browser_capsule_preview_static.py tests/test_capsule_transcript_preview_fallback.py
python3 -m pytest tests/test_duplicate_name_audit.py
```

Use a webpack build check when touching frontend runtime code:

```bash
./node_modules/.bin/webpack --mode development --output-path /private/tmp/vvault-webpack-check
```
