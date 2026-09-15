"""AUTH enrollment v1 bridge to the existing OVVAULTS enrollment repository.

No account is selected by email and no saved checkpoint is mutated by attestation.
The web host owns protected POST transport and native session creation.
"""
from __future__ import annotations
import base64
import hashlib
import json
import re
import time
import uuid
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey

VERSION = "auth.enrollment.v1"
class EnrollmentRejected(ValueError):
    pass

def _json(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()

def _b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")

def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False

def verify_handoff(token, *, public_keys, auth_issuer, authority_issuer, now=None):
    now = int(time.time()) if now is None else now
    if not isinstance(token, str) or len(token) > 16384:
        raise EnrollmentRejected("INVALID_HANDOFF")
    parts = token.split(".")
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
        raise EnrollmentRejected("INVALID_HANDOFF")
    try:
        signature = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        valid = False
        for key in public_keys:
            if not isinstance(key, Ed25519PublicKey):
                raise EnrollmentRejected("PINNED_KEY_REQUIRED")
            try:
                key.verify(signature, parts[0].encode("ascii")); valid = True; break
            except InvalidSignature:
                continue
        if not valid:
            raise EnrollmentRejected("INVALID_SIGNATURE")
        h = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    except (ValueError, UnicodeError) as exc:
        raise EnrollmentRejected("INVALID_HANDOFF") from exc
    if not isinstance(h, dict) or any(h.get(k) != v for k, v in {
        "version": VERSION, "kind": "HANDOFF", "issuer": auth_issuer, "audience": authority_issuer,
    }.items()):
        raise EnrollmentRejected("PROVENANCE_MISMATCH")
    if any(type(h.get(k)) is not int for k in ("issuedAt", "expiresAt")) or not h["issuedAt"] <= now < h["expiresAt"] or not 0 < h["expiresAt"]-h["issuedAt"] <= 300:
        raise EnrollmentRejected("EXPIRED_HANDOFF")
    identity = h.get("identity")
    if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not 0 < len(identity[k]) <= 512 for k in ("subject", "provider", "providerSubject", "providerIssuer")):
        raise EnrollmentRejected("AUTHENTICATED_IDENTITY_REQUIRED")
    if (not _uuid(h.get("transaction")) or not isinstance(h.get("sessionBinding"), str)
            or not 0 < len(h["sessionBinding"]) <= 512 or h.get("relyingParty") not in {"chatty", "chatty-cli"}
            or h.get("intent") not in {"SIGN_IN", "SIGN_UP"}
            or (h.get("ownerId") is not None and not _uuid(h["ownerId"]))):
        raise EnrollmentRejected("INVALID_HANDOFF")
    return h

def verified_canonical_consents(handoff, documents):
    """Return server-owned document triples only for fresh signed acceptance."""
    consent = handoff.get('canonicalConsents')
    if consent is None:
        return None
    if (handoff.get('intent') != 'SIGN_UP' or not isinstance(consent, dict)
            or consent.get('authority') != 'vvault' or type(consent.get('acceptedAt')) is not int
            or not handoff['issuedAt'] - 300 <= consent['acceptedAt'] <= handoff['issuedAt']):
        raise EnrollmentRejected('CANONICAL_CONSENT_REJECTED')
    supplied = consent.get('documents')
    if not isinstance(supplied, list) or len(supplied) != len(documents):
        raise EnrollmentRejected('CANONICAL_CONSENT_REJECTED')
    def triples(rows):
        result = []
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in ('key', 'version', 'sha256')):
                raise EnrollmentRejected('CANONICAL_CONSENT_REJECTED')
            result.append((row['key'], row['version'], row['sha256']))
        return result
    received = triples(supplied)
    expected = triples(documents)
    if not expected or len(set(received)) != len(received) or set(received) != set(expected):
        raise EnrollmentRejected('CANONICAL_CONSENT_DOCUMENTS_CHANGED')
    return [{key: row[key] for key in ('key', 'version', 'sha256')} for row in documents]


def resolve_owner(handoff, repository):
    identity = handoff["identity"]
    if identity['provider'] == 'email':
        if identity['providerIssuer'] != 'https://vvault.thewreck.org' or not _uuid(identity['providerSubject']):
            raise EnrollmentRejected('PROVIDER_IDENTITY_CONFLICT')
        row = repository.get_native_email_identity(identity_id=identity['providerSubject'])
    else:
        row = repository.get_external_identity(provider=identity["provider"], provider_subject=identity["providerSubject"])
    if row is None:
        if handoff.get("ownerId") is not None:
            raise EnrollmentRejected("OWNER_IDENTITY_CONFLICT")
        return None
    if row.get("issuer") != identity["providerIssuer"] or not row.get("verified_at"):
        raise EnrollmentRejected("PROVIDER_IDENTITY_CONFLICT")
    owner = str(row.get("user_id") or "")
    if not _uuid(owner) or handoff.get("ownerId") not in (None, owner):
        raise EnrollmentRejected("OWNER_IDENTITY_CONFLICT")
    return {**row, "id": owner}

def attest(handoff, *, repository, native_session, pending_session, documents):
    owner = resolve_owner(handoff, repository)
    if owner is None:
        return {"disposition": "SIGNUP_REQUIRED", "checkpoint": "AUTHORITY_STATUS_REQUIRED"}
    owner_id = owner["id"]
    current = native_session or pending_session
    if not current or str(current.get("user_id") or current.get("id") or "") != owner_id:
        return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "AUTHORITY_STATUS_REQUIRED"}
    legal = repository.has_current_legal_receipts(user_id=owner_id, required_documents=documents)
    kind = current.get("enrollment_session_kind")
    if kind == "PENDING_DEVICE":
        return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "DEVICE"}
    if not legal:
        return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "CONSENT"}
    if kind == "PENDING_ENROLLMENT":
        if not repository.list_active_webauthn_credentials(user_id=owner_id):
            return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "PASSKEY"}
        if not repository.enrollment_recovery_codes_ready(user_id=owner_id):
            return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "RECOVERY_CODES"}
        return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "ACTIVATION"}
    # Only fresh repository-backed NORMAL native sessions attest admission.
    if (native_session is None or owner.get("account_state") != "ACTIVE" or current.get("account_state") != "ACTIVE"
            or kind != "NORMAL" or current.get("enrollment_device_status") != "TRUSTED"
            or not current.get("session_id")):
        return {"disposition": "ENROLLMENT_REQUIRED", "checkpoint": "AUTHORITY_STATUS_REQUIRED"}
    return {"enrollment": "COMPLETED", "ownerId": owner_id, "identity": handoff["identity"],
            "requirements": {"session": "SATISFIED", "device": "SATISFIED", "policy": "SATISFIED"},
            "policyVersion": hashlib.sha256(_json(documents)).hexdigest(),
            "authoritySessionBinding": str(current["session_id"])}

def completion(handoff, evidence, *, signing_key, auth_issuer, authority_issuer, now=None):
    if evidence.get("enrollment") != "COMPLETED":
        return evidence
    now = int(time.time()) if now is None else now
    if handoff["expiresAt"] <= now:
        raise EnrollmentRejected("EXPIRED_HANDOFF")
    if (not _uuid(evidence.get("ownerId")) or handoff.get("ownerId") not in (None, evidence["ownerId"])
            or evidence.get("identity") != handoff["identity"]
            or any(evidence.get("requirements", {}).get(k) != "SATISFIED" for k in ("session", "device", "policy"))):
        raise EnrollmentRejected("ADMISSION_NOT_AUTHORIZED")
    payload = {"version": VERSION, "kind": "COMPLETION", "issuer": authority_issuer, "audience": auth_issuer,
        "issuedAt": now, "expiresAt": min(now+60, handoff["expiresAt"]),
        "transaction": handoff["transaction"], "relyingParty": handoff["relyingParty"],
        "sessionBinding": handoff["sessionBinding"], "identity": handoff["identity"],
        "ownerId": evidence["ownerId"], "handoffDigest": hashlib.sha256(_json(handoff)).hexdigest(),
        "enrollment": "COMPLETED", "requirements": evidence["requirements"],
        "policyVersion": evidence["policyVersion"], "authoritySessionBinding": evidence["authoritySessionBinding"]}
    if (not isinstance(signing_key, Ed25519PrivateKey)
            or any(not isinstance(evidence.get(k), str) or not 0 < len(evidence[k]) <= 512 for k in ("policyVersion", "authoritySessionBinding"))):
        raise EnrollmentRejected("ADMISSION_NOT_AUTHORIZED")
    body = _b64(_json(payload))
    return {"completion": body + "." + _b64(signing_key.sign(body.encode("ascii"))), "expiresAt": payload["expiresAt"]}
