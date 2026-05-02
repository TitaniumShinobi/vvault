# VVAULT Runtime Closure Rubric

This rubric defines when VVAULT is actually ready. It exists because a running app is not the same as a closed runtime loop.

## Purpose

VVAULT must never confuse:

- configured with connected
- reachable gateway with healthy database
- missing data with nonexistent data
- degraded read with safe write
- OAuth configured with identity authority proven
- local fallback with canonical truth

## Hard Pass Requirements

All items must pass:

- `localhost:7784` serves the frontend.
- `localhost:8000` serves exactly one backend listener.
- `/api/status` returns `200`.
- `/api/ready` returns `200`.
- steward state is `connected`.
- `canonical` is `true`.
- `storage_mode` is `supabase`.
- Google OAuth health says identity authority is available.
- OAuth login and callback do not bypass the steward.
- Vault directory loads after login.
- At least one text-like file preview opens.
- Canonical writes are allowed only while Supabase is connected.
- Focused Supabase, OAuth, preview, and duplicate-name tests pass.

## Hard Fail Conditions

Any item fails runtime closure:

- `/api/ready` returns `503`.
- `/api/health` is used as proof of canonical readiness.
- Google OAuth redirects while identity authority is unavailable.
- A fallback path mints or promotes a replacement LIFE id.
- A write silently succeeds while Supabase is degraded.
- Missing Supabase data is treated as nonexistence.
- Direct Supabase real-key table reads return `522`.
- Storage returns `DatabaseTimeout`.
- Duplicate backend listeners exist on `8000`.
- Generated data, runtime databases, or `.DS_Store` files are staged as feature work.

## Supabase Readiness Rubric

1. Prove gateway reachability.
2. Prove bad-key failure is fast.
3. Prove real-key `users` metadata read.
4. Prove real-key `vault_files` metadata read.
5. Prove steward state reaches `connected`.
6. Prove `/api/ready` returns `200`.
7. Prove OAuth health reports identity authority available.

If real-key table reads return slow `522`, VVAULT is not closed. Recover the Supabase project database path first.

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
- direct Supabase probe outcome when readiness fails
- exact files changed
- merge/staging exclusions
- remaining risks
