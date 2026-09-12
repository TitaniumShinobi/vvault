from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vvault.server import vvault_access_assertion


NOW = int(datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp())
OWNER = "aaaaaaaa-bbbb-4ccc-9ddd-eeeeeeeeeeee"


def _b64(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _key_pair():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    kid = hashlib.sha256(public_pem.encode()).hexdigest()
    return private, public_pem, kid


def _assertion(private, kid, **overrides):
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    claims = {
        "version": vvault_access_assertion.ASSERTION_VERSION,
        "iss": "quantum-auth",
        "aud": "vvault",
        "sub": "canary-subject",
        "vvault_owner_id": OWNER,
        "sid": "canary-session",
        "jti": "canary-assertion",
        "iat": NOW,
        "exp": NOW + 60,
        "scopes": ["constructs:read", "transcripts:append"],
        "relying_party_id": "chatty",
    }
    claims.update(overrides)
    signing = f"{_b64(header)}.{_b64(claims)}"
    signature = base64.urlsafe_b64encode(private.sign(signing.encode())).decode().rstrip("=")
    return f"{signing}.{signature}"


def test_valid_assertion_and_overlapping_key_rotation():
    current_private, current_pem, current_kid = _key_pair()
    _, next_pem, next_kid = _key_pair()
    verified = vvault_access_assertion.verify_access_assertion(
        _assertion(current_private, current_kid),
        public_keys={current_kid: current_pem, next_kid: next_pem},
        now_seconds=NOW,
    )
    assert verified["ownerUserId"] == OWNER
    assert verified["keyId"] == current_kid
    assert "transcripts:append" in verified["scopes"]
    assert verified["relyingPartyId"] == "chatty"


@pytest.mark.parametrize("override", [
    {"aud": "chatty"},
    {"iss": "untrusted"},
    {"vvault_owner_id": "not-a-canonical-owner"},
    {"exp": NOW},
    {"exp": NOW + 61},
    {"scopes": []},
    {"scopes": ["admin:all"]},
    {"relying_party_id": "header-spoof"},
])
def test_invalid_claims_are_rejected(override):
    private, pem, kid = _key_pair()
    with pytest.raises(vvault_access_assertion.AccessAssertionRejected):
        vvault_access_assertion.verify_access_assertion(
            _assertion(private, kid, **override),
            public_keys={kid: pem},
            now_seconds=NOW,
        )


def test_tampering_and_unknown_key_are_rejected():
    private, pem, kid = _key_pair()
    token = _assertion(private, kid)
    parts = token.split(".")
    signature = bytearray(base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4)))
    signature[0] ^= 1
    parts[2] = base64.urlsafe_b64encode(bytes(signature)).decode().rstrip("=")
    with pytest.raises(vvault_access_assertion.AccessAssertionRejected):
        vvault_access_assertion.verify_access_assertion(
            ".".join(parts),
            public_keys={kid: pem},
            now_seconds=NOW,
        )
    other_private, other_pem, other_kid = _key_pair()
    with pytest.raises(vvault_access_assertion.AccessAssertionRejected):
        vvault_access_assertion.verify_access_assertion(
            _assertion(other_private, other_kid),
            public_keys={kid: pem},
            now_seconds=NOW,
        )


def test_scope_contract_rejects_unknown_mutations():
    assert vvault_access_assertion.required_scope("GET", "/api/chatty/constructs") == "constructs:read"
    assert vvault_access_assertion.required_scope("POST", "/api/chatty/transcript/zen/message") == "transcripts:append"
    assert vvault_access_assertion.required_scope("DELETE", "/api/chatty/construct/zen") is None


def test_auto_context_requires_every_joined_authority_scope():
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/context"
    ) == frozenset({"identity:read", "transcripts:read", "knowledge:read"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/exchanges"
    ) == frozenset({"transcripts:append"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/hydro/events"
    ) == frozenset({"transcripts:append"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/actions/grants"
    ) == frozenset({"transcripts:read", "transcripts:append"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/actions/events"
    ) == frozenset({"transcripts:append"})
    assert not vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/register"
    )
    assert not vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/system-runtimes/auto-001/registration/preflight"
    )


def test_work_loop_read_projections_and_mutations_use_least_privilege_scopes():
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/work-programs/scope-resolve"
    ) == frozenset({"work:read"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/work-programs/program-canary/evidence/resolve"
    ) == frozenset({"work:read"})
    assert vvault_access_assertion.required_scopes(
        "GET", "/api/chatty/work-programs/program-canary"
    ) == frozenset({"work:read"})
    assert vvault_access_assertion.required_scopes(
        "POST", "/api/chatty/work-programs/program-canary/events"
    ) == frozenset({"work:append"})
