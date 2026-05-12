# VVAULT GitHub Cleanup Note

This note records the cleanup posture before staging, pushing, or merging VVAULT.

## Current Rule

Do not merge one giant mixed worktree. Split by contract.

## Safe Slices

1. VVAULT-native runtime readiness and retired legacy outbox contracts.
2. OAuth/login/session bridge.
3. Custom file preview.
4. Launcher and local runtime.
5. Governance and GitHub policy.
6. Documentation and rubric centralization.
7. Quarantine/mock cleanup.

## Do Not Stage Blindly

- `node_modules/**`
- `.DS_Store`
- `.playwright-cli/**`
- `output/playwright/**`
- `vvault/data/audit.db`
- `vvault/data/vvault_continuity_ledger.json`
- deleted `backups/vault_files/*.json`
- protected governance/runtime files without a deliberate receipt

## Merge Gate

Before merge, prove:

- focused VVAULT runtime/OAuth/legacy outbox/frontend dependency tests pass
- focused preview tests pass
- `python3 -m pytest tests/test_duplicate_name_audit.py` passes
- webpack build check passes
- `/api/ready` is connected for release or explicitly documented as blocked by local VVAULT body database/runtime dependency health
