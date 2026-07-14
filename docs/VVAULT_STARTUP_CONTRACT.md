# VVAULT Startup Contract

This document defines the canonical startup behavior for launching VVAULT in a browser during local development.

If shell behavior, launcher scripts, and product docs disagree, this file is the contract.

## Canonical operator behavior

Typing `vvault` in a terminal should:

1. start VVAULT on `http://localhost:7784` if it is not already reachable
2. reuse the existing local VVAULT process if the app is already live
3. ensure the paired backend on `8000` is reachable and report whether strict `/api/ready` is ready or degraded
4. open the default browser to `http://localhost:7784`
5. print:

```text
VVAULT is running at http://localhost:7784
```

It should not dump the raw webpack or Flask logs into the user shell as the primary behavior of the `vvault` command.

Raw repo commands stay terminal-first:

```bash
npm run dev
npm run dev:full
```

## Canonical launcher chain

The expected local launcher chain is:

1. shell command `vvault`
2. shell function in [`~/.zshrc`](/Users/devonwoodson/.zshrc)
3. launcher script [`scripts/open-vvault-standalone.sh`](../scripts/open-vvault-standalone.sh)
4. raw full-stack dev command `npm run dev:full`

Recommended shell function:

```zsh
vvault() {
  /bin/bash "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Documents/GitHub/vvault/scripts/open-vvault-standalone.sh" "$@"
}
```

## Required port contract

- `7784` = VVAULT frontend
- `8000` = VVAULT backend API
- `1111` = local auth service

The operator-facing `vvault` command is about the browser entrypoint on `7784`, but runtime closure is stricter than process health. `/api/health` can prove the backend is alive. `/api/ready` proves VVAULT-native body/runtime readiness.

## Runtime readiness contract

- `/api/status` proves the local backend process is alive.
- `/api/health` may return a degraded process state.
- `/api/ready` is the strict gate for VVAULT-native runtime readiness.
- VVAULT is ready only when `/api/ready` returns `200`, `storage_mode: "vvault_body"`, and `body_database` proves canonical OVVAULTS ownership.
- The canonical source database is `vvault_body_20260504t123219z`; the canonical schema is `ovvaults`.
- Readiness must name `storage_owner: "ovvaults.vault_files"` and `transcript_owner: "ovvaults.transcripts"`.
- If `/api/ready` returns `503`, VVAULT may be running in degraded mode, but unsafe writes and auth/session mutations must remain blocked unless a route has an explicit local durable receipt.
- The operational closure loop lives in `docs/operations/RUNTIME_CLOSURE_CHECKLIST.md`.

Expected local readiness check:

```bash
curl -fsS http://localhost:7784/api/ready
```

Minimum healthy shape:

```json
{
  "ready": true,
  "storage_mode": "vvault_body",
  "storage_owner": "ovvaults.vault_files",
  "transcript_owner": "ovvaults.transcripts",
  "body_database": {
    "schema": "ovvaults",
    "source_database": "vvault_body_20260504t123219z"
  }
}
```

If `/api/ready` returns `index.html`, the backend readiness route is missing or not loaded and Flask's frontend catch-all is masking the failure.

## OVVAULTS environment contract

The local VVAULT process must load body database configuration before creating its REST client:

```bash
VVAULT_BODY_DB_URL=...
VVAULT_BODY_DB_SERVICE_ROLE_KEY=...
VVAULT_BODY_DB_SCHEMA=ovvaults
VVAULT_BODY_DB_SOURCE_DATABASE=vvault_body_20260504t123219z
```

These variables are not language cleanup targets. Removing them disconnects the local shell from OVVAULTS and produces `body_database_not_configured`.

## OAuth contract

Local OAuth starts through the browser-facing frontend/proxy:

```text
http://localhost:7784/api/auth/google
```

The generated Google redirect must contain:

```text
redirect_uri=http://localhost:7784/api/auth/google/callback
```

The callback must not use backend port `8000`. The proxy preserves browser origin with `X-Forwarded-Host: localhost:7784` and `X-Forwarded-Proto: http`. Local HTTP OAuth requires `OAUTHLIB_INSECURE_TRANSPORT=1` only outside production.

## Chatty bridge contract

Chatty treats VVAULT as the canonical transcript/body authority. When Chatty shows "Connecting to VVAULT", verify:

```bash
curl -fsS http://localhost:5050/api/vvault/ready
```

This route must return `ready: true` from VVAULT's `/api/ready`. Local conversation state is not a transcript authority fallback.

## Code handshake contract

Code must prove VVAULT memory authority before claiming live OVVAULTS-backed memory. The local Code frontend uses:

```bash
curl -fsS http://localhost:2048/api/code/handshake
```

Code proxies that check to VVAULT:

```bash
curl -fsS http://localhost:8000/api/code/handshake
```

The VVAULT route must return JSON, never `index.html`. A healthy response names `authority: "vvault_body"`, `storage_owner: "ovvaults.vault_files"`, `transcript_owner: "ovvaults.transcripts"`, and `transcript_compatibility_owner: "ovvaults.vault_files"`. If this route returns HTML, Code will correctly block with "VVAULT handshake returned non-JSON."

## Anti-regression rules

The following are regressions:

- `vvault` resolves to the auto-generated `repo_up "vvault"` alias instead of the launcher function
- typing `vvault` drops into raw `npm run dev:full` logs as the primary UX
- typing `vvault` does not open the browser
- typing `vvault` does not print `VVAULT is running at http://localhost:7784`
- the launcher stops checking or reusing the existing app on `7784`
- ambiguous duplicate backend listeners exist on `8000`
- launcher output treats degraded `/api/ready` as canonical readiness
- the launcher script is invoked from zsh instead of bash and emits a `BASH_SOURCE[0]: parameter not set` warning
- `/api/ready` returns frontend HTML instead of JSON
- Google OAuth redirects to `localhost:8000` or `127.0.0.1:8000`
- cleanup of old wording removes `VVAULT_BODY_DB_*` or OVVAULTS readiness ownership

## Known shell failure mode

[`~/.zshrc`](/Users/devonwoodson/.zshrc) auto-generates repo aliases via `repo_make_aliases`.

If that alias layer is still what your current shell has loaded, `vvault` can resolve to:

```zsh
alias vvault='repo_up "vvault"'
```

That is not the operator contract. It causes the wrong UX: raw `npm run dev:full` output in the terminal, no browser-open behavior, and no success line.

The launcher function defined later in `~/.zshrc` must win.

## Recovery rule

If `vvault` still behaves like the raw repo alias:

1. reload your shell with `source ~/.zshrc` or open a fresh terminal window
2. confirm with `type vvault`
3. use the direct fallback command if needed:

```bash
/bin/bash "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Documents/GitHub/vvault/scripts/open-vvault-standalone.sh"
```

## Developer checklist

If you change local startup behavior, verify:

1. `vvault` from a fresh shell opens `http://localhost:7784`
2. `vvault` prints `VVAULT is running at http://localhost:7784`
3. running `vvault` again reuses the existing app instead of spawning duplicates
4. `type vvault` reports a shell function from `~/.zshrc`, not a repo alias
5. `npm run dev` still works as the raw frontend path
6. `npm run dev:full` still works as the raw full-stack path
7. `/api/ready` is checked separately from `/api/health`
8. duplicate listeners on `8000` are rejected or reported as ambiguous
9. docs in `README.md`, this file, and the launcher/runtime rubrics stay aligned
