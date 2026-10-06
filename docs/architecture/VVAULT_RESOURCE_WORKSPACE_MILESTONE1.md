# VVAULT resource workspace wire contract — Milestone 1

Status: source implementation; production activation disabled pending the
deployment gate. This contract does not allocate application instances or
write application data.

## Wire mapping

| Semantic field | Wire value |
|---|---|
| AUTH authorization contract | `life.auth.resource-authorization/v1` |
| VVAULT wire contract | `life.vvault.resource-workspace/v1` |
| JWT protected type | `life-resource+jwt` |
| Signature | Ed25519 with an operator-pinned `kid` |
| Issuer | exact operator-pinned `AUTH_RESOURCE_ISSUER` |
| Audience | `https://vvault.thewreck.org` |
| OAuth client | `grid-windows` |
| Application / relying party | `grid` |
| Capability | `workspace:resolve` |
| Resolver | `POST /api/v1/resource/workspace/resolve` |
| Online status | `POST {AUTH_RESOURCE_STATUS_URL}`; path must be `/api/auth/resource-status` |

The resource request uses `Authorization: Bearer <resource assertion>` and an
empty JSON object or no body. Query parameters, owner selectors, client
selectors, application selectors, and relying-party selectors are rejected.
Cookies and native VVAULT sessions are not consulted.

Success response:

```json
{
  "success": true,
  "contract": "life.vvault.resource-workspace/v1",
  "workspace": {
    "id": "opaque-uuid",
    "lifecycleStatus": "ACTIVE",
    "applicationId": "grid",
    "capabilities": ["workspace:resolve"]
  }
}
```

Failure responses contain only `success:false`, the wire contract, and a
stable `errorCode`. Authentication failures are 401, insufficient capability
or conflicting selectors are 403, missing pre-provisioned workspaces are 404,
and missing trust configuration or unavailable AUTH status is 503.

## Authorization order

1. Validate the exact signed JWT structure, key, issuer, audience, lifetime,
   registered client/application, owner UUID, grant identifiers, wire version,
   and capability.
2. Call AUTH status using the configured direct mTLS client identity, without
   redirects or positive-result caching.
3. Require the status response to match every locally verified authorization
   binding.
4. Install the signed owner and application as PostgreSQL RLS context.
5. Require enabled database admission, an ACTIVE VVAULT owner, and an existing
   ACTIVE opaque workspace.
6. Return the bounded opaque projection. Never create a workspace.

## Trust readiness

`/api/ready` publishes `resourceTrustReadiness` and embeds the same object at
`trustReadiness.resourceAuthorization`. It proves, independently of general
service readiness:

- a nonempty operator-pinned Ed25519 key ring and non-secret key IDs;
- explicit issuer and exact production audience;
- authenticated AUTH status reachability;
- consistent and enabled `grid-windows` → `grid` admission;
- applied migration `0041_resource_workspace_milestone1`.

The migration seeds the GRID registration disabled and creates no workspace.
Production activation therefore remains fail-closed until an operator enables
the reviewed admission, provisions disposable canary workspaces separately,
and satisfies the deployment provenance gate.
