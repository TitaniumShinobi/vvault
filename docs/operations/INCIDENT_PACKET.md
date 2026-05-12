# Incident Packet

Single reference for ownership, evidence, and contacts when responding to an incident or defending against bogus complaints (e.g. competitor sabotage, abuse reports). Update when details change.

## Ownership and authority

- **Product / system:** VVAULT (Verified Vectored Anatomy Unconsciously Lingering Together) and related Chatty integration.
- **Owner / operator:** _[Name or entity]_
- **Billing and account holder:** _[Account holder for cloud, DNS, registrar, OAuth]_

## Evidence to have ready

- **Billing proof:** Screenshots or statements showing account ownership and payment history for hosting, DNS, domain, OAuth provider.
- **Trademark / use:** Evidence of legitimate use of the product name and domain (e.g. terms of service, privacy notice, live site).
- **Abuse-policy compliance:** Short description of abuse policy (e.g. no illegal content, no impersonation); link to public ToS/abuse policy if any.
- **Architecture summary:** One-paragraph description of vvault (capsule memory vault, layer manifests, web API, VVAULT-native Postgres/body storage, OAuth provider integration) and Chatty integration. Point to `docs/architecture/` or README.
- **Security contact:** Email or channel for abuse/security reports (e.g. abuse@domain, security@domain).

## When to use

- Responding to a **bogus abuse or takedown request** (e.g. from a competitor): assemble this packet and send with your response to the provider (GitHub, cloud host, registrar, OAuth provider).
- **Incident response:** Use as the first place to check ownership, contacts, and evidence before escalating.

## Escalation templates

Pre-written templates live in `docs/operations/ESCALATION_TEMPLATES.md`. Use them to contact GitHub, cloud host, registrar, OAuth provider, or model vendor when you need to report an issue or respond to an abuse/account action.

## Chatty 502 / Replit asleep

When Chatty calls VVAULT and receives a **502** (or 503), the failure may occur at the **Replit edge**, not inside VVAULT:

- **502 from Chatty → VVAULT** can be caused by the Replit host being asleep.
- Check the response for **503** and the header/value **`Replit-Proxy-Error: asleep`**.
- If present, the failure is at Replit’s edge; **no VVAULT application code ran**, and there are no VVAULT app-level logs for that request.
- Mitigations: use an always-on Replit plan, point `VVAULT_URL` at a non-Replit host, or add retry/health-check in Chatty (see `docs/platform/vvault_chatty_integration_report.md`).
- **Chatty retry:** For `/api/vvault/auth/token`, the client retries on 503 + `VVAULT_HOST_ASLEEP` (e.g. 3 retries, 2s delay) before surfacing the "VVAULT host is sleeping…" message, giving the Replit host time to wake.
