# Governance and Access Control

Operational runbook for preserving control of vvault (and chatty where noted). Reduces risk of competitor sabotage, account takeover, and unauthorized shutdown.

## 1. Account inventory and MFA

**All privileged accounts must use hardware-key MFA (e.g. YubiKey). No shared admin accounts; every privileged action is tied to a named user.**

| Account type | Scope | MFA requirement |
|-------------|--------|-------------------|
| Cloud (hosting, compute, storage) | vvault/chatty deploy and data | Hardware-key MFA |
| DNS / domain registrar | vvault.thewreck.org, chatty domains | Hardware-key MFA |
| GitHub (repo, org, CI) | vvault, chatty, related orgs | Hardware-key MFA |
| Package registry (npm, PyPI if publish) | Release and package publish | Hardware-key MFA |
| Email (admin, support) | Account recovery, notifications | Hardware-key MFA |
| OAuth / IdP admin (Google, etc.) | Callback URLs, client secrets | Hardware-key MFA |
| VVAULT Postgres/body DB and S3-compatible storage | vvault runtime data, backups, service roles | Hardware-key MFA |
| Legacy Supabase/offboarding source | migration provenance, source extraction only | Hardware-key MFA |

**Action:** Audit the above; enable hardware-key MFA on each; remove shared/generic admin logins.

---

## 2. Split privileges

Privileges must not all live under one credential. Separate where possible:

| Privilege | Description | Should not also hold |
|-----------|-------------|------------------------|
| Deploy | Push to prod, run deploy pipeline | Secrets, billing, DNS |
| Secrets | Env vars, API keys, OAuth client secrets | Deploy (ideally separate person or vault) |
| Billing | Payment, subscription, usage | Deploy, DNS |
| DNS / Registrar | Domain and DNS records | Deploy, OAuth config |
| OAuth config | Callback URLs, client IDs, scopes | Single point for all auth |
| Data export / wipe | Bulk export, delete, backup purge | Deploy |

**Action:** Document who (or which role) holds each; use separate accounts or vaults where feasible.

---

## 3. Two-person approval for destructive actions

The following actions **require a second, named approval** (out-of-band verification, e.g. second channel or voice) before execution:

- **Secret rotation** (production API keys, OAuth client secrets, database/storage credentials, legacy offboarding source keys, Flask SECRET_KEY).
- **OAuth callback URL changes** (any change to allowed redirect/callback URLs).
- **Environment variable wipe or bulk change** (production env).
- **Disabling providers** (turning off OAuth, VVAULT body DB/storage, legacy offboarding sources, or critical upstream).
- **Mass delete** (bulk file/capsule/user deletion, backup purge).
- **DNS or registrar changes** (delegation, nameservers, transfer).
- **Revoking admin access** or changing role assignments for admins.

**Action:** Before performing any of the above, confirm with a second trusted person. Log the approval (e.g. in incident runbook or audit).

---

## 4. Out-of-band verification for “urgent” requests

Any request that is **off-channel**, **urgent**, or **unusual** (e.g. “rotate this secret quickly,” “change callback URL now”) must be verified via a separate channel (e.g. different chat, voice, or in-person) before execution. Do not act on urgency alone.

---

## 5. Chatty parity

For the chatty repo, apply the same rules: same account inventory, MFA, privilege split, and two-person rules for the same class of destructive actions (secrets, OAuth callbacks, deploy, DNS, mass delete).

---

---

## Related runbooks

- [AUDIT_SHIPPER.md](AUDIT_SHIPPER.md) — Off-box audit log shipping
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — Backup script, restore drill, rehydrate
- [SECRETS_RUNBOOK.md](SECRETS_RUNBOOK.md) — Secrets inventory and rotation
- [INCIDENT_PACKET.md](INCIDENT_PACKET.md) — Ownership and evidence for incidents
- [ESCALATION_TEMPLATES.md](ESCALATION_TEMPLATES.md) — Provider escalation text

*Last updated: 2026-03-01. Review when accounts or roles change.*
