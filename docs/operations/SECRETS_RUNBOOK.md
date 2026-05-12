# Secrets Inventory and Rotation Runbook

Production secrets used by vvault. Document **owner** and **last rotated** for each; rotate per governance (two-person approval for production).

## Inventory

| Secret (env var) | Purpose | Owner | Last rotated |
|------------------|---------|-------|---------------|
| `FLASK_SECRET_KEY` | Flask session signing, CSRF | _assign_ | |
| `DATABASE_URL` | VVAULT runtime Postgres/body database | _assign_ | |
| `VVAULT_BODY_DATABASE_URL` | Optional explicit VVAULT body database override | _assign_ | |
| `SESSION_HASH_PEPPER` | Local session token hash hardening | _assign_ | |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID | _assign_ | |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret | _assign_ | |
| `VVAULT_SERVICE_TOKEN` | Service-to-service (Chatty, config API) | _assign_ | |
| `VVAULT_ENCRYPTION_KEY` | Credential encryption at rest | _assign_ | |
| `VVAULT_S3_ENDPOINT_URL` | S3-compatible VVAULT object storage endpoint | _assign_ | |
| `VVAULT_S3_ACCESS_KEY_ID` | S3-compatible VVAULT object storage access key | _assign_ | |
| `VVAULT_S3_SECRET_ACCESS_KEY` | S3-compatible VVAULT object storage secret key | _assign_ | |
| `VVAULT_ADMIN_EMAILS` | Comma-separated admin emails | _assign_ | |
| `TURNSTILE_SECRET_KEY` | Turnstile CAPTCHA (if used) | _assign_ | |
| `VXRUNNER_API_KEY` | VXRunner integration (if used) | _assign_ | |
| `SUPABASE_URL` | Optional legacy offboarding source URL only | _assign_ | |
| `SUPABASE_SERVICE_ROLE_KEY` | Optional legacy offboarding source extraction key only | _assign_ | |
| `SUPABASE_ANON_KEY` | Optional legacy offboarding source anon key only | _assign_ | |

**Optional / deployment-specific:** `VVAULT_FRONTEND_URL`, `VVAULT_BACKEND_URL`, `OAUTH_BASE_URL`, `REPLIT_DEV_DOMAIN`, `VVAULT_AUDIT_DB_PATH`, `VVAULT_AUDIT_SHIP_PATH` — document in this table if they contain or protect secrets.

## Rotation procedure

1. **Two-person approval:** Get a second named approval (out-of-band) before rotating any production secret.
2. **Create new value:** Generate a new key/token per provider (database/storage provider, Google Cloud Console, legacy offboarding source if needed, etc.). Do not reuse old value.
3. **Update in secure store:** Set the new value in your secrets store (e.g. env in hosting, vault). Do not commit to repo.
4. **Deploy / restart:** Restart the app (or deploy with new env) so it picks up the new value.
5. **Revoke old value:** In the provider (database/storage provider, Google, legacy offboarding source, etc.), revoke or delete the old key/token.
6. **Record:** Update the "Last rotated" column in this runbook and log the rotation in the audit runbook.

## Per-secret notes

- **FLASK_SECRET_KEY:** Changing invalidates all existing sessions; users must log in again.
- **DATABASE_URL / VVAULT_BODY_DATABASE_URL:** Rotate database credentials in the VVAULT runtime Postgres provider. Update env and restart; then revoke old credentials.
- **VVAULT_S3_*:** Rotate storage credentials in the VVAULT-native object storage provider. Update env and restart; then revoke old credentials.
- **SUPABASE_*:** Legacy offboarding/extraction only. Rotate in the legacy source provider if you still need import/provenance access; these keys must not be required for runtime readiness.
- **GOOGLE_OAUTH_CLIENT_SECRET:** Rotate in Google Cloud Console → APIs & Services → Credentials. Update env and restart; optionally revoke old secret.
- **VVAULT_SERVICE_TOKEN:** Generate a new random value (e.g. `openssl rand -hex 32`). Update in both vvault and any callers (e.g. Chatty); restart both; then treat old token as revoked.
- **VVAULT_ENCRYPTION_KEY:** Changing will prevent decryption of credentials already stored; re-encrypt or re-store credentials after rotation if needed.

## Chatty parity

If Chatty uses its own secrets (e.g. OpenAI, session secret), maintain a similar inventory and rotation runbook in the Chatty repo and link it from here.
