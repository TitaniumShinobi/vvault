# Canonical identity directory

`ovvaults.users` is the Life Technology directory. A user ID—not an email
address—is the only ownership reference for VVAULT resources.

## Identity rules

- An active `external_identities` row keyed by `(provider, provider_subject)`
  is the sign-in identity. Google and GitHub are supported now; the provider
  value is extensible.
- `managed_emails` contains verified contact addresses. It is deliberately not
  globally unique: two independently verified provider identities that report
  the same address are still two accounts.
- A first-seen provider subject or magic-link email creates a distinct
  `PENDING_ENROLLMENT` directory user. It receives no normal VVAULT data
  session or tenant resources.
- An identity can be linked only by an `ACTIVE` user with a recent provider
  re-authentication. A collision never merges accounts. The last usable sign-in
  method cannot be removed.

## Entry contracts

`POST /api/auth/oauth/{google|github}` creates a one-time PKCE/state/nonce
transaction. Its callback consumes the transaction once and records only a
verified provider subject and contact address. `POST /api/auth/email-magic-links`
always returns the same acceptance response; delivery is an adapter boundary.
Its raw token is never stored, logged, or placed in a query string. The browser
posts a fragment token once to `/api/auth/email-magic-links/consume`, which
returns `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

Password registration and password login are retired (`410`). This release does
not activate an account: Terms/Privacy receipts, WebAuthn, recovery codes,
trusted devices, session rotation, and tenant provisioning are Plan 3.
