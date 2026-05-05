# VVAULT Runtime Closure Rubric

This rubric defines when VVAULT is actually ready after the runtime cutover. A running app is not the same as a closed runtime loop.

## Purpose

VVAULT must never confuse:

- configured with ready
- reachable process with healthy body database
- metadata/path/hash with materialized body
- degraded read with safe write
- OAuth configured with local auth persistence ready
- legacy Supabase provenance with runtime authority

## Hard Pass Requirements

All items must pass:

- `localhost:7784` serves the frontend.
- `localhost:8000` serves exactly one backend listener.
- `/api/status` returns `200`.
- `/api/health` reports VVAULT-native dependency status.
- `/api/ready` returns `200`.
- `body_database.ready` is `true`.
- `body_database.status` is `healthy`.
- `ovvaults.vault_files` is readable.
- `ovvaults.transcripts` is readable.
- Google OAuth health reports VVAULT-native auth readiness when OAuth is configured.
- OAuth login/callback store identity and sessions locally.
- Vault directory loads from `ovvaults.vault_files`.
- At least one text-like file preview opens from materialized content or VVAULT-native storage.
- Canonical writes persist through VVAULT-native repositories/storage.
- Focused VVAULT runtime, auth, file, frontend, body, and duplicate-name tests pass.

## Hard Fail Conditions

Any item fails runtime closure:

- `/api/ready` returns `503`.
- `/api/health` is used as proof of readiness.
- Google OAuth redirects while VVAULT auth persistence is unavailable.
- A fallback path mints or promotes a replacement LIFE id.
- A write silently succeeds without local persistence proof.
- Missing local body data is treated as nonexistence.
- Metadata/path/hash is called materialized body.
- Supabase is required for runtime readiness, auth, file storage, or body continuity.
- Duplicate backend listeners exist on `8000`.
- Generated data, runtime databases, or `.DS_Store` files are staged as feature work.

## VVAULT Readiness Rubric

1. Prove frontend reachability.
2. Prove backend reachability.
3. Prove `/api/status`.
4. Prove `/api/ready`.
5. Prove body DB URL resolution.
6. Prove readable `ovvaults.vault_files`.
7. Prove readable `ovvaults.transcripts`.
8. Prove `/api/auth/google/health` reports local auth authority, not legacy identity authority.

If local body database reads fail, VVAULT is not closed. Recover the local body database path first.

## Legacy Supabase Classification

Supabase references are allowed only as:

- migration/offboarding source extraction
- historical provenance
- quarantined compatibility tests for old modules

They are not runtime closure criteria.

## QFB Cleanup Rubric

QFB means quarantine, fix/flush, bless.

- Quarantine legacy docs, mocks, and old schema snapshots before removal.
- Fix active code and docs at the smallest verified seam.
- Flush proven generated noise only when it is not source material.
- Bless the canonical posture in `docs/operations/` or `docs/rubrics/`.
- Never remove backups, protected surfaces, or runtime state without explicit evidence.

## Evidence Receipt Requirements

A pass receipt must include:

- commands run
- test results
- live `/api/status`
- live `/api/ready`
- live `/api/auth/google/health`
- body database proof when readiness fails
- exact files changed
- merge/staging exclusions
- remaining risks
