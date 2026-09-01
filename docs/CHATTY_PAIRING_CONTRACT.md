# Chatty pairing contract

This is an explicit optional link between an active trusted VVAULT session and
an already-active Chatty account. It is not account discovery, email matching,
shared login, or data access.

## Browser issue

`POST /api/auth/pairing-intents/chatty` requires a same-origin active trusted
VVAULT session. It returns only `pairing_code`, `audience`, `callback_uri`, and
`expires_in`. The opaque code expires in 60 seconds. No email, provider
subject, VVAULT session, owner ID, or VVAULT data is returned.

## Server redemption

Only the configured Chatty server calls
`POST /api/auth/pairing-intents/chatty/redeem`. It authenticates with the
configured client ID and server secret and sends exactly the opaque code,
fixed audience, fixed callback URI, and the authenticated Chatty account UUID.
The VVAULT response contains only `success`, `audience`, and opaque `link_id`.

Wrong client credentials, audience, callback URI, expired codes, and replayed
codes are rejected. A pair binds at most one Chatty account. VVAULT identity,
cookies, provider tokens, files, transcripts, and other owner data never cross
this boundary.
