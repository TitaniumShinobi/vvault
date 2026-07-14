# Canonical Cross-Product User Registry Spec

## Status

Canonical runtime spec. This file defines live VVAULT identity routing after the local auth/session cutover.

## Decision

Use a VVAULT-native identity model:

1. VVAULT account identity is stored in local Postgres under `ovvaults.users`.
2. VVAULT sessions are stored in local Postgres under `ovvaults.sessions`.
3. OAuth providers may verify external identity, but resulting users and sessions are persisted locally.
4. Legacy source import rows are provenance only.

## Canonical Rules

### 1) VVAULT Account Identity

- Primary VVAULT account identity is `ovvaults.users.id`.
- Email is the lookup key used to resolve or create a VVAULT-local user row.
- New VVAULT integrations must resolve local auth/session state before performing user operations.
- Session tokens are owned by `ovvaults.sessions`; raw bearer tokens must not be stored.

### 2) Product-Local Registry Separation

- Chatty may keep local registry artifacts.
- VVAULT keeps local body/file/auth state in VVAULT-owned storage.
- Other products may keep product-local user metadata for local features.
- Product-local registries must not silently override VVAULT's local account/session authority.

### 3) Mapping Contract

- Logical contract:
  - `vvault_user_id` = VVAULT-local account id.
  - `chatty_user_id` = Chatty-local runtime id when present.
  - `legacy_source_user_id` = provenance pointer only when imported from legacy source.
- If a legacy external id exists, it may be preserved as provenance, but it is not runtime authority.
- Do not introduce many-to-many identity mappings between local ids and legacy imported ids.

### 4) Auth and Service-to-Service Calls

- Service-to-service requests that cross product boundaries must carry authenticated user context.
- For Chatty -> VVAULT bridge calls, pass user identity headers and validate service token when configured.
- Runtime behavior must prefer authenticated local identity resolution over filesystem fallback heuristics.

### 5) Runtime Lock and Canonical Thread Safety

- Check runtime lock state before write/hydration operations from VVAULT or connected services.
- Canonical runtime threads must not be removed or overwritten during hydration/rehydration.
- If canonical threads are missing at login/hydration, restore before routing continues.

## Integration Requirements for Any New VVAULT-Connected App

1. Resolve the VVAULT user from local auth/session state.
2. Persist app profile data keyed by the resolved local user id.
3. Store local runtime artifacts in app-local or VVAULT-owned paths only.
4. When calling other products, send authenticated user context that can resolve back to the VVAULT user.
5. Reject flows that bypass local identity resolution and write directly into another product's user storage.

## Legacy Source Provenance

Legacy external source `users` data may appear in imported manifests, old docs, test fixtures, and offboarding scripts. Treat it as historical source provenance unless a current VVAULT-native route explicitly maps it into `ovvaults.users`.

## Credential Login Parity

VVAULT `/api/auth/login` returns hint flags for local credential parity when appropriate:

- **`oauthOnly`** / **`credentialLoginUnavailable`** when a VVAULT user is OAuth-only.
- **`lifeRegistryMatch`** when legacy or imported identity evidence exists but no VVAULT password credential exists.

Errors remain under **`success: false`** + **`error`** for the React login UI.

## Conflict Resolution

If another document conflicts with this spec, this file wins for VVAULT runtime identity. Update conflicting docs to align with this decision.
