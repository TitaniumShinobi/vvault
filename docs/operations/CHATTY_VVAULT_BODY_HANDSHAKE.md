# Chatty-VVAULT Body Database Handshake

Date: 2026-06-26

## Contract

Chatty must treat VVAULT as the database authority for construct files,
transcripts, identity documents, and memory materialization.
Code must use the same VVAULT body database handshake when opening or editing
VVAULT-backed files.

- runtime authority: `vvault_body`
- canonical schema: `ovvaults`
- storage owner: `ovvaults.vault_files`
- transcript owner: `ovvaults.transcripts`
- transcript compatibility owner: `ovvaults.vault_files`
- session bridge: `/api/vault/session-bridge`
- auth cookie: `auth_sid`
- Code private origin: `http://localhost:2048`
- Code public origin: `https://code.thewreck.org`

Legacy environment names are compatibility aliases only.
The required VVAULT-native runtime variables are `VVAULT_BODY_DB_URL`,
`VVAULT_BODY_DB_SERVICE_ROLE_KEY`, `VVAULT_BODY_DB_SCHEMA`, and
`VVAULT_BODY_DB_SOURCE_DATABASE`.

## Active Handshake

The shared door contract lives at `config/chatty-vvault-doors.json`.

Each door must define:

- Chatty browser/API origin
- VVAULT origin
- Auth API/public origin
- browser origins allowed for CORS
- VVAULT body database authority fields
- session bridge path and auth cookie name

VVAULT exposes the resolved contract through `/api/ready` as `door_contract`.
Chatty and Code can use that readiness response to confirm the backend, auth
bridge, and body database are all speaking the same authority language before
exchanging sessions or writing transcript content.

Code also has a dedicated handshake endpoint:

```text
GET /api/code/handshake
```

The endpoint returns JSON only. It exposes the resolved Code origin, VVAULT
origin, auth cookie, session bridge path, and the body database ownership
fields. If the VVAULT body database is not ready, the endpoint still returns a
structured JSON error so Code never mistakes frontend HTML for memory authority.

## Transcript Compatibility

The canonical transcript table is `ovvaults.transcripts`. During migration,
VVAULT may report `body_database_compatibility` when `ovvaults.transcripts` is
not readable but transcript material is still available through
`ovvaults.vault_files`.

This is allowed only when:

- `/api/ready` still returns `ready: true`
- `ovvaults.vault_files` is readable
- Chatty transcript routes read/write through the VVAULT body database
- the compatibility owner is declared as `ovvaults.vault_files`

## Active Release Gate

Run only the targeted gate for this handshake:

```bash
.venv/bin/python -m pytest \
  tests/test_chatty_vvault_body_handshake.py \
  tests/test_vvault_ready_body_database_contract.py \
  tests/test_chatty_body_database_routes.py \
  tests/test_runtime_body_database_contract.py
```

Do not use the quarantined legacy tests as release blockers unless the
retired contract is being intentionally audited.
