# Canonical Cross-Product User Registry Spec

## Status

Canonical runtime spec. This file defines live behavior for Chatty, VVAULT, Neat, and FXShinobi user identity routing.

## Decision

Use a hybrid model:

1. Shared identity root in Supabase `users` table.
2. Product-local registries and storage remain separate.
3. Cross-product access resolves identity through Supabase first, then maps into product-local runtime paths.

## Canonical Rules

### 1) Shared Account Identity

- Primary account identity is the Supabase `users.id` (UUID).
- Email is the cross-product lookup key used to resolve or create a Supabase `users` row.
- New app integrations must resolve Supabase user before performing product-specific user operations.

### 2) Product-Local Registry Separation

- Chatty may keep local registry artifacts (for example `users.json`, sharded folders, local profile materialization).
- VVAULT may keep separate vault file structures and per-user construct paths.
- Neat and FXShinobi may keep product-local user metadata for local features.
- Product-local registries do not replace Supabase identity and must not create a conflicting global account model.

### 3) Mapping Contract

- Logical contract:
  - `supabase_user_id` = global identity root.
  - `chatty_user_id` = Chatty-local runtime id (if present).
  - `vvault_user_id` = VVAULT-local runtime id/path binding (if present).
- If a local id exists, it must be linked to one `supabase_user_id`.
- Do not introduce many-to-many identity mappings between local ids and Supabase users.

### 4) Auth and Service-to-Service Calls

- Service-to-service requests that cross product boundaries must carry authenticated user context.
- For Chatty -> VVAULT bridge calls, pass user identity headers and validate service token when configured.
- Runtime behavior must prefer authenticated identity resolution over filesystem fallback heuristics.

### 5) Runtime Lock and Canonical Thread Safety

- Check runtime lock state before write/hydration operations from VVAULT or connected services.
- Canonical runtime threads (including Synth) must not be removed or overwritten during hydration/rehydration.
- If canonical threads are missing at login/hydration, restore before routing continues.

## Integration Requirements for Any New App

1. Resolve/create Supabase user using authenticated email/session.
2. Persist local app profile keyed by resolved `supabase_user_id`.
3. Store local runtime artifacts in app-local paths only.
4. When calling other products, send authenticated user context that can resolve back to the same `supabase_user_id`.
5. Reject flows that bypass identity resolution and write directly into another product's user storage.

## Non-Goals

- This spec does not force a single physical database for all product-local runtime data.
- This spec does not remove local registries that existing products require for runtime behavior.

## Credential login parity (VVAULT web)

VVAULT `/api/auth/login` returns the same hint flags as the rest of the LIFE stack when appropriate:

- **`oauthOnly`** / **`credentialLoginUnavailable`** when Supabase user has no VVAULT password but **`auth_provider`** is OAuth.
- **`lifeRegistryMatch`** when the row has **Chatty** `auth_password_hash` but no VVAULT `password_hash`, or no password fields at all — finish product sign-up instead of a generic invalid login.

Errors remain under **`success: false`** + **`error`** for the React login UI.

## Conflict Resolution

If another document conflicts with this spec, this file wins. Update conflicting docs to align with this decision.
