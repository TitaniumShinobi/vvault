# VVAULT resource owner admission — Milestone 1

## End goal and authority

Before AUTH issues a VVAULT resource authorization, AUTH calls VVAULT's independent read-only admission boundary. VVAULT answers whether the authenticated AUTH issuer/subject is explicitly bound to a canonical VVAULT owner. This lookup neither resolves nor creates a workspace.

The authoritative source is `ovvaults.auth_resource_owner_bindings`, introduced by migration `0042_auth_resource_owner_admission`. Existing `external_identities` rows are deliberately not reused: they represent provider identities and do not prove equivalence to an AUTH issuer/subject. Migration 0042 performs no backfill, linking, or enrollment.

## Wire contract

`POST /api/v1/resource/owner-admission/resolve` accepts exactly the fields in `contracts/v1/resource-owner-admission.json`. Requests expire within 30 seconds and bind a UUID request ID, AUTH issuer/subject/session, registered client/application, audience, and exact capability set. The first supported tuple is `grid-windows` / `grid` / `https://vvault.thewreck.org` / `workspace:resolve`.

An admitted response echoes every authorization input, adds only canonical opaque `ownerId`, `policyVersion`, and an expiry no more than 60 seconds away. AUTH must compare every echoed field before issuance. Negative responses never disclose an owner and use:

- `ACCOUNT_LINK_REQUIRED` / `NO_OWNER_BINDING`
- `ENROLLMENT_REQUIRED` / `OWNER_ENROLLMENT_PENDING`
- `REAUTH_REQUIRED` / `OWNER_BINDING_REVOKED` or `OWNER_DISABLED`

Conflicting canonical evidence fails with `409 OWNER_ADMISSION_CONFLICT`; unavailable trust, database, or repository state fails with 503. Responses are `Cache-Control: no-store`.

## Workload authentication and integrity

The route accepts only an actual client certificate obtained from the direct TLS socket. Proxy/client-certificate headers are ignored. `VVAULT_AUTH_ADMISSION_MTLS_IDENTITIES_JSON` maps each accepted certificate SHA-256 fingerprint to exactly one AUTH issuer. TLS provides request/response integrity and server authentication. This endpoint must be exposed on a direct mTLS listener; placing it behind a header-forwarding TLS terminator will fail closed.

The dedicated listener is `python -m vvault.server.resource_owner_admission_host` with the service template at `scripts/deployment/vvault-owner-admission.service`. It refuses wildcard binds and requires:

- `VVAULT_OWNER_ADMISSION_HOST`: an explicit private or loopback address;
- `VVAULT_OWNER_ADMISSION_PORT`: an explicitly allocated port;
- `VVAULT_OWNER_ADMISSION_SERVER_CERT_PATH` and `VVAULT_OWNER_ADMISSION_SERVER_KEY_PATH`;
- `VVAULT_OWNER_ADMISSION_CLIENT_CA_PATH`;
- `VVAULT_AUTH_ADMISSION_MTLS_IDENTITIES_JSON`.

The listener exposes only the admission POST plus minimal `/healthz` and `/readyz` operations. All paths require a TLS client certificate at handshake time. Bodies are capped at 16 KiB, socket reads at five seconds, query strings are rejected, responses are non-cacheable, and general VVAULT routes are not mounted.

Read-only retries are safe. VVAULT keeps no positive cache and creates no nonce, owner, workspace, or instance. A replay cannot extend authorization because request and response lifetimes are bounded and AUTH must validate current session state before calling and before issuing.

## Enrollment and lifecycle

Bindings are provisioned only by a separately approved account-linking flow. This API contains no mutation path. `PENDING` and pending owner enrollment are non-admitted. A revoked binding or disabled/non-active owner is non-admitted immediately on the next lookup. Unknown and non-admitted callers receive no owner-enumerating data.

## Exclusions

No GRID code, workspace/application-instance allocation, transcript/asset storage, native browser-session bridge, production deployment, or production account mutation is part of this boundary.
