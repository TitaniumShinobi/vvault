from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vvault.server import resource_authorization as resource
from vvault.server import vvault_web_server as server


NOW = 1_800_000_000
OWNER_A = "aaaaaaaa-bbbb-4ccc-9ddd-eeeeeeeeeeee"
OWNER_B = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"


def _b64(value: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode()
    ).decode().rstrip("=")


@pytest.fixture
def signed_assertion():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    kid = hashlib.sha256(public_pem.encode()).hexdigest()

    def issue(**overrides):
        header = {"alg": "EdDSA", "typ": resource.TOKEN_TYPE, "kid": kid}
        claims = {
            "contract": resource.AUTH_CONTRACT,
            "wire_contract": resource.WIRE_CONTRACT,
            "iss": "https://auth.example.test",
            "sub": "auth-subject-a",
            "aud": resource.PRODUCTION_AUDIENCE,
            "client_id": "grid-windows",
            "application_id": "grid",
            "owner_id": OWNER_A,
            "sid": "session-a",
            "jti": "assertion-a",
            "grant_id": "grant-a",
            "capabilities": [resource.WORKSPACE_RESOLVE_CAPABILITY],
            "admission_version": "grid-admission/1",
            "iat": NOW,
            "exp": NOW + 60,
        }
        claims.update(overrides)
        signing = f"{_b64(header)}.{_b64(claims)}"
        signature = base64.urlsafe_b64encode(private.sign(signing.encode())).decode().rstrip("=")
        return f"{signing}.{signature}", {kid: public_pem}

    return issue


def _verify(issue, **overrides):
    token, ring = issue(**overrides)
    return token, resource.verify_resource_assertion(
        token,
        public_keys=ring,
        issuer="https://auth.example.test",
        audience=resource.PRODUCTION_AUDIENCE,
        now_seconds=NOW,
    )


def test_valid_resource_assertion_binds_registered_client_owner_and_capability(signed_assertion):
    _token, verified = _verify(signed_assertion)
    assert verified["clientId"] == "grid-windows"
    assert verified["applicationId"] == verified["relyingPartyId"] == "grid"
    assert verified["ownerUserId"] == OWNER_A
    assert verified["capabilities"] == (resource.WORKSPACE_RESOLVE_CAPABILITY,)


@pytest.mark.parametrize("overrides", [
    {"exp": NOW},
    {"aud": "https://wrong.example.test"},
    {"client_id": "unknown-client"},
    {"application_id": "chatty"},
    {"capabilities": ["workspace:allocate"]},
])
def test_resource_assertion_rejects_expired_wrong_authority_client_or_capability(
    signed_assertion, overrides
):
    with pytest.raises(resource.ResourceAuthorizationError) as error:
        _verify(signed_assertion, **overrides)
    assert error.value.http_status == 401


def _status_payload(verified):
    return {
        "active": True,
        "contract": verified["contract"],
        "wire_contract": verified["wireContract"],
        "issuer": verified["issuer"],
        "subject": verified["subject"],
        "sid": verified["sessionId"],
        "jti": verified["assertionId"],
        "grant_id": verified["grantId"],
        "audience": verified["audience"],
        "client_id": verified["clientId"],
        "application_id": verified["applicationId"],
        "owner_id": verified["ownerUserId"],
        "capabilities": list(verified["capabilities"]),
        "admission_version": verified["admissionVersion"],
        "expires_at": verified["expiresAt"],
    }


def test_online_status_requires_full_binding_and_fails_closed(signed_assertion, monkeypatch):
    token, verified = _verify(signed_assertion)
    monkeypatch.setattr(resource.time, "time", lambda: NOW)

    def post(_url, **_kwargs):
        return SimpleNamespace(status_code=200, json=lambda: _status_payload(verified))

    client = resource.ResourceStatusClient("https://auth.example.test/api/auth/resource-status", "cert", "key", "ca", post=post)
    assert client.validate(token, verified)["active"] is True

    def mismatched(_url, **_kwargs):
        payload = _status_payload(verified)
        payload["owner_id"] = OWNER_B
        return SimpleNamespace(status_code=200, json=lambda: payload)

    with pytest.raises(resource.ResourceAuthorizationError, match="RESOURCE_STATUS_BINDING_MISMATCH"):
        resource.ResourceStatusClient("url", "cert", "key", "ca", post=mismatched).validate(token, verified)

    def unavailable(_url, **_kwargs):
        raise resource.requests.ConnectionError("offline")

    with pytest.raises(resource.ResourceTrustUnavailable):
        resource.ResourceStatusClient("url", "cert", "key", "ca", post=unavailable).validate(token, verified)

    def revoked(_url, **_kwargs):
        return SimpleNamespace(
            status_code=401,
            json=lambda: {"active": False, "errorCode": "RESOURCE_GRANT_INACTIVE"},
        )

    with pytest.raises(resource.ResourceAuthorizationError) as error:
        resource.ResourceStatusClient("url", "cert", "key", "ca", post=revoked).validate(token, verified)
    assert error.value.code == "RESOURCE_GRANT_INACTIVE"
    assert error.value.http_status == 401


def _route_verified(owner=OWNER_A, capabilities=(resource.WORKSPACE_RESOLVE_CAPABILITY,)):
    return {
        "contract": resource.AUTH_CONTRACT,
        "wireContract": resource.WIRE_CONTRACT,
        "issuer": "https://auth.example.test",
        "subject": "subject-a",
        "audience": resource.PRODUCTION_AUDIENCE,
        "clientId": "grid-windows",
        "applicationId": "grid",
        "relyingPartyId": "grid",
        "ownerUserId": owner,
        "sessionId": "session-a",
        "assertionId": "assertion-a",
        "grantId": "grant-a",
        "capabilities": tuple(capabilities),
        "admissionVersion": "grid-admission/1",
        "issuedAt": NOW,
        "expiresAt": NOW + 60,
        "keyId": "fixture-key",
        "ownerFingerprint": "fixture-owner",
    }


def _enable_route(monkeypatch, verified):
    monkeypatch.setattr(resource, "verify_resource_assertion", lambda _token: verified)
    monkeypatch.setattr(
        resource.ResourceStatusClient,
        "from_environment",
        classmethod(lambda _cls: SimpleNamespace(validate=lambda _token, _verified: {"active": True})),
    )


def test_workspace_route_rejects_spoofed_selector_and_insufficient_capability(monkeypatch):
    _enable_route(monkeypatch, _route_verified())
    client = server.app.test_client()
    spoofed = client.post(
        "/api/v1/resource/workspace/resolve?owner_id=" + OWNER_B,
        headers={"Authorization": "Bearer fixture"},
        json={},
    )
    assert spoofed.status_code == 403
    assert spoofed.get_json()["errorCode"] == "UNTRUSTED_SELECTOR"

    _enable_route(monkeypatch, _route_verified(capabilities=()))
    insufficient = client.post(
        "/api/v1/resource/workspace/resolve",
        headers={"Authorization": "Bearer fixture"},
        json={},
    )
    assert insufficient.status_code == 403
    assert insufficient.get_json()["errorCode"] == "INSUFFICIENT_CAPABILITY"


def test_workspace_route_uses_only_signed_owner_and_returns_opaque_projection(monkeypatch):
    observed = []
    _enable_route(monkeypatch, _route_verified(owner=OWNER_A))

    def resolve(**kwargs):
        observed.append(kwargs)
        return {
            "workspaceId": "11111111-2222-4333-8444-555555555555",
            "lifecycleStatus": "ACTIVE",
            "applicationId": "grid",
            "relyingPartyId": "grid",
            "capabilities": [resource.WORKSPACE_RESOLVE_CAPABILITY],
        }

    monkeypatch.setattr(server.RESOURCE_WORKSPACE_REPOSITORY, "resolve", resolve)
    response = server.app.test_client().post(
        "/api/v1/resource/workspace/resolve",
        headers={"Authorization": "Bearer fixture"},
        json={},
    )
    assert response.status_code == 200
    assert observed == [{
        "owner_user_id": OWNER_A,
        "client_id": "grid-windows",
        "application_id": "grid",
    }]
    assert response.get_json() == {
        "success": True,
        "contract": resource.WIRE_CONTRACT,
        "workspace": {
            "id": "11111111-2222-4333-8444-555555555555",
            "lifecycleStatus": "ACTIVE",
            "applicationId": "grid",
            "capabilities": [resource.WORKSPACE_RESOLVE_CAPABILITY],
        },
    }


def test_owner_a_and_owner_b_resolution_never_share_repository_context(monkeypatch):
    observed = []

    def resolve(**kwargs):
        observed.append(kwargs)
        suffix = "a" if kwargs["owner_user_id"] == OWNER_A else "b"
        return {
            "workspaceId": f"11111111-2222-4333-8444-55555555555{suffix}",
            "lifecycleStatus": "ACTIVE",
            "applicationId": "grid",
            "relyingPartyId": "grid",
            "capabilities": [resource.WORKSPACE_RESOLVE_CAPABILITY],
        }

    monkeypatch.setattr(server.RESOURCE_WORKSPACE_REPOSITORY, "resolve", resolve)
    client = server.app.test_client()
    returned = []
    for owner in (OWNER_A, OWNER_B):
        _enable_route(monkeypatch, _route_verified(owner=owner))
        response = client.post(
            "/api/v1/resource/workspace/resolve",
            headers={"Authorization": "Bearer fixture"},
            json={},
        )
        assert response.status_code == 200
        returned.append(response.get_json()["workspace"]["id"])
    assert returned[0] != returned[1]
    assert [item["owner_user_id"] for item in observed] == [OWNER_A, OWNER_B]


def test_workspace_route_fails_closed_when_auth_status_is_unavailable(monkeypatch):
    monkeypatch.setattr(resource, "verify_resource_assertion", lambda _token: _route_verified())

    def reject(_token, _verified):
        raise resource.ResourceTrustUnavailable("RESOURCE_STATUS_UNAVAILABLE")

    monkeypatch.setattr(
        resource.ResourceStatusClient,
        "from_environment",
        classmethod(lambda _cls: SimpleNamespace(validate=reject)),
    )
    response = server.app.test_client().post(
        "/api/v1/resource/workspace/resolve",
        headers={"Authorization": "Bearer fixture"},
        json={},
    )
    assert response.status_code == 503
    assert response.get_json()["errorCode"] == "RESOURCE_STATUS_UNAVAILABLE"


def test_trust_readiness_requires_keys_status_admission_and_migration(monkeypatch, signed_assertion):
    _token, ring = signed_assertion()
    monkeypatch.setenv("AUTH_RESOURCE_ISSUER", "https://auth.example.test")
    monkeypatch.setenv("AUTH_RESOURCE_PUBLIC_KEYS_JSON", json.dumps(ring))
    monkeypatch.setenv("AUTH_RESOURCE_STATUS_URL", "https://auth.example.test/api/auth/resource-status")
    monkeypatch.setenv("AUTH_RESOURCE_MTLS_CERT_PATH", "/cert")
    monkeypatch.setenv("AUTH_RESOURCE_MTLS_KEY_PATH", "/key")
    monkeypatch.setenv("AUTH_RESOURCE_CA_BUNDLE_PATH", "/ca")
    monkeypatch.setattr(resource.ResourceStatusClient, "probe", lambda _self: True)
    repository = SimpleNamespace(readiness=lambda: {
        "migrationApplied": True,
        "admissionConsistent": True,
        "admissionEnabled": True,
    })
    readiness = resource.trust_readiness(repository)
    assert readiness["ready"] is True
    assert readiness["authStatus"]["workloadAuthenticated"] is True
    assert readiness["migration"]["applied"] is True

    monkeypatch.delenv("AUTH_RESOURCE_PUBLIC_KEYS_JSON")
    assert resource.trust_readiness(repository)["ready"] is False


def test_migration_adds_grid_consistently_without_creating_workspaces():
    source = (server._repo_root / "vvault/migrations/0041_resource_workspace_milestone1.up.sql").read_text()
    assert "'chatty', 'chatty-cli', 'vvault', 'grid'" in source
    assert "('grid-windows', 'grid', 'grid'" in source
    assert "enabled boolean NOT NULL DEFAULT false" in source
    assert "INSERT INTO ovvaults.owner_workspaces" not in source
    assert "FORCE ROW LEVEL SECURITY" in source
