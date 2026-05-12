# VVAULT Runtime Closure Checklist

There is no failproof. There is only looping until the circle is closed.

This checklist is the operator and developer runbook for proving VVAULT is actually ready after the runtime cutover. It is not enough for the Flask process to answer, the webpack server to serve a page, or OAuth credentials to exist. VVAULT is ready only when local body, auth, file/storage, and runtime dependencies are proven.

## Status And Scope

- `/api/status` proves the local backend process is alive.
- `/api/health` reports VVAULT-native dependency status.
- `/api/ready` is the strict readiness gate.
- `body_database` is the readiness-blocking dependency.
- `auth` and `storage` are reported as dependency metadata unless a route explicitly requires them.
- Writes must be confirmed in VVAULT-native storage, explicitly rejected, or handled by a named local durability mechanism.
- Supabase is legacy extraction/offboarding provenance only.

## VVAULT Closure Gate

VVAULT is closed only when `/api/ready` returns `200` and the response includes:

- `ready: true`
- `status: "ready"`
- `body_database.ready: true`
- `body_database.status: "healthy"`
- `body_database.schema: "ovvaults"`
- readable `ovvaults.vault_files`
- readable `ovvaults.transcripts`

Any other state is not ready. It may be running, degraded, blocked, or booting, but it is not closed.

## Strict Readiness Contract

Use this order. Do not skip layers.

1. Check `http://localhost:7784/api/status`.
2. Check `http://localhost:7784/api/ready`.
3. Check `http://localhost:7784/api/auth/google/health`.
4. If `/api/ready` fails, inspect the VVAULT body database connection and schema checks.
5. Split process reachability from database-backed table reads.
6. Fix the lowest broken VVAULT-native layer.
7. Re-run the same checks.

## Read/Write Runtime Matrix

| Operation | VVAULT ready | VVAULT not ready |
| --- | --- | --- |
| `/api/ready` | `200` ready | `503` not ready |
| OAuth health | reports local auth readiness | reports local auth unavailable/config issue |
| OAuth login/callback | allowed only when local auth persistence is ready | fail closed before identity/session mutation |
| Canonical writes | local transaction/storage owner confirmed | `503` or explicit local blocker |
| Trusted reads | VVAULT body/file repository reads | degraded only when route has trusted local data |
| Missing row | data-dependent | never proof of nonexistence without local DB proof |

## Frontend Closure Signals

The UI must not claim VVAULT is ready unless `/api/ready` proves it.

- Treat `/api/ready` `503` as expected degraded state.
- Suppress repeated dependency/session-expired spam.
- Stop authenticated fetch loops after local session expiry.
- Show that login and writes requiring local auth/storage are blocked until recovery.
- Never call degraded state healthy.

## Launcher And Duplicate Listener Hygiene

Before debugging dependencies, prove local listener shape:

1. There must be one frontend listener on `127.0.0.1:7784`.
2. There must be one backend listener on `127.0.0.1:8000`.
3. Ambiguous duplicate listeners are a startup failure signature.
4. `vvault` should reuse an existing good app instead of spawning duplicates.
5. `vvault` success must not be confused with runtime readiness if `/api/ready` is degraded.

## Legacy Supabase References

Supabase references are allowed only when labeled as:

- migration/offboarding tooling
- historical provenance
- legacy compatibility tests for quarantined modules

They must not be described as current runtime truth.

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
.venv/bin/python3 -m pytest tests/test_vvault_runtime_cutover_contract.py tests/test_auth_life_identity_contract.py tests/test_vvault_file_routes_native.py tests/test_chatty_body_service.py tests/test_frontend_outage_contract_static.py
.venv/bin/python3 -m pytest tests/test_runtime_supabase_dependency_static.py
python3 -m pytest tests/test_duplicate_name_audit.py
```

Use a webpack build check when touching frontend runtime code:

```bash
./node_modules/.bin/webpack --mode development --output-path /private/tmp/vvault-webpack-check
```
