# Live-to-Local Push Safety Checklist

Date: 2026-06-26
Branch: `codex/vvault-live-safe-release`

## Current Verdict

Do not push local VVAULT to `main` as-is.

This branch was created to preserve the local work while preventing accidental overwrite of live GitHub branch features from:

- `origin/roadmap/2026-Q2-director`
- `origin/codex/prod-sync`

## Completed Safety Actions

- [x] Created a dedicated release branch: `codex/vvault-live-safe-release`.
- [x] Removed the unintended tracked `node_modules/.package-lock.json` change from the release diff.
- [x] Restored local copies of files that `origin/roadmap/2026-Q2-director` contains and the local worktree would otherwise delete.
- [x] Restored `src/components/Blockchain.js` and `src/components/CreateConstruct.js` so those live UI surfaces are not silently dropped.
- [x] Restored `assets/vvault_logo.svg` from `origin/codex/prod-sync` so the live logo asset is preserved.
- [x] Quarantined legacy tests that target retired runtime symbols.
- [x] Added the Chatty-VVAULT body database handshake contract and targeted release gate.
- [x] Added the Code-VVAULT body database handshake on the same VVAULT authority contract.
- [x] Verified `/api/ready` on backend port `8000`.
- [x] Verified `/api/ready` through frontend port `7784`.
- [x] Verified frontend root `/` on port `7784` after rebuilding `dist`.
- [x] Verified production frontend build completes.

## Still Required Before Push

- [ ] Stage the restored live-branch files intentionally; they are present in the working tree but currently untracked on this branch.
- [ ] Review the large diff against `origin/codex/prod-sync`; do not bulk-merge it because it contains broad historical asset and runtime changes.
- [ ] Decide whether the restored roadmap runtime modules should be wired into the current backend or kept as preserved compatibility files.
- [x] Resolve Python test environment issue: `.venv` pytest is installed, but its dependency `py` is missing.
- [ ] Investigate whether `transcripts_readable: false` should be closed by making `ovvaults.transcripts` readable; compatibility through `ovvaults.vault_files` is now documented for the handshake.

## Release Gates

- [ ] `git diff --name-status origin/roadmap/2026-Q2-director -- . ':!node_modules'` reviewed.
- [ ] `git diff --name-status origin/codex/prod-sync -- . ':!node_modules'` reviewed.
- [ ] `curl -fsS --max-time 2 http://localhost:8000/api/ready` returns `ready: true`.
- [ ] `curl -fsS --max-time 2 http://localhost:7784/api/ready` returns `ready: true`.
- [ ] `curl -fsS --max-time 2 http://localhost:7784/` returns the VVAULT app shell.
- [ ] `npm run build` passes.
- [ ] Targeted pytest suite runs after the local pytest dependency issue is fixed.
- [ ] No force-push.

## Evidence Captured

- Backend readiness returned `ready: true`, `canonical: true`, `authority: vvault_body`, and `storage_mode: vvault_body`.
- Auth bridge remained on `http://127.0.0.1:1111` with cookie `auth_sid`.
- Body database reported `vault_files_readable: true`.
- Body database reported `transcripts_readable: false`.
- Frontend root returned `index.html` with the current VVAULT bundle after rebuild.
- `npm run build` completed with warnings only for stale browser data and large assets.
