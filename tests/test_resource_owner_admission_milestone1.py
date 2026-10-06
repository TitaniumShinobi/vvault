from __future__ import annotations

from copy import deepcopy

import pytest

from vvault.server import resource_owner_admission as admission
from vvault.server import vvault_web_server as server


NOW = 1_800_000_000
OWNER = "aaaaaaaa-bbbb-4ccc-9ddd-eeeeeeeeeeee"


def request_payload(**overrides):
    value = {
        "contract": admission.CONTRACT,
        "requestId": "11111111-2222-4333-8444-555555555555",
        "issuer": "https://auth.example.test",
        "subject": "subject-a",
        "sessionId": "session-a",
        "clientId": "grid-windows",
        "applicationId": "grid",
        "audience": "https://vvault.thewreck.org",
        "capabilities": ["workspace:resolve"],
        "issuedAt": NOW,
        "expiresAt": NOW + 30,
    }
    value.update(overrides)
    return value


class Repository:
    def __init__(self, state="ACTIVE"):
        self.state = state
        self.calls = []

    def lookup(self, **query):
        self.calls.append(query)
        result = {"state": self.state}
        if self.state == "ACTIVE":
            result.update(ownerId=OWNER, policyVersion="owner-admission/1")
        return result


def test_admitted_subject_returns_only_canonical_binding():
    repo = Repository()
    result = admission.resolve(request_payload(), repo, now_seconds=NOW)
    assert result["admitted"] is True
    assert result["ownerId"] == OWNER
    assert "workspaceId" not in result and "instanceId" not in result
    assert repo.calls == [{
        "issuer": "https://auth.example.test", "subject": "subject-a",
        "client_id": "grid-windows", "application_id": "grid",
        "audience": "https://vvault.thewreck.org", "capability": "workspace:resolve",
    }]


@pytest.mark.parametrize(("state", "disposition", "reason"), [
    ("UNKNOWN", "ACCOUNT_LINK_REQUIRED", "NO_OWNER_BINDING"),
    ("PENDING", "ENROLLMENT_REQUIRED", "OWNER_ENROLLMENT_PENDING"),
    ("REVOKED", "REAUTH_REQUIRED", "OWNER_BINDING_REVOKED"),
    ("DISABLED", "REAUTH_REQUIRED", "OWNER_DISABLED"),
])
def test_non_admitted_states_are_explicit_and_non_enumerating(state, disposition, reason):
    result = admission.resolve(request_payload(), Repository(state), now_seconds=NOW)
    assert result["admitted"] is False
    assert result["disposition"] == disposition and result["reasonCode"] == reason
    assert "ownerId" not in result


def test_conflicting_binding_and_service_failure_fail_closed():
    with pytest.raises(admission.OwnerAdmissionError, match="OWNER_ADMISSION_CONFLICT"):
        admission.resolve(request_payload(), Repository("CONFLICT"), now_seconds=NOW)
    with pytest.raises(admission.OwnerAdmissionError, match="OWNER_ADMISSION_UNAVAILABLE"):
        admission.resolve(request_payload(), Repository("ERROR"), now_seconds=NOW)

    class Unavailable:
        def lookup(self, **_query):
            raise ConnectionError("database unavailable")

    with pytest.raises(admission.OwnerAdmissionError) as error:
        admission.resolve(request_payload(), Unavailable(), now_seconds=NOW)
    assert error.value.code == "OWNER_ADMISSION_UNAVAILABLE"
    assert error.value.http_status == 503


@pytest.mark.parametrize("override", [
    {"clientId": "spoofed"}, {"applicationId": "chatty"},
    {"subject": "subject-b", "issuer": "https://wrong.example.test"},
    {"audience": "https://wrong.example.test"}, {"expiresAt": NOW + 31},
])
def test_exact_request_binding_rejects_spoofing_substitution_and_expiry(override):
    with pytest.raises(admission.OwnerAdmissionError):
        admission.validate_request(request_payload(**override), "https://auth.example.test", now_seconds=NOW)


def test_direct_mtls_rejects_headers_and_non_tls_socket():
    with pytest.raises(admission.OwnerAdmissionError) as error:
        admission.authenticate_direct_mtls({
            "HTTP_X_SSL_CLIENT_CERT": "spoofed", "werkzeug.socket": object()
        })
    assert error.value.code == "OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED"


def test_route_is_read_only_and_never_touches_workspace(monkeypatch):
    calls = []
    monkeypatch.setattr(admission, "authenticate_direct_mtls", lambda _env: "https://auth.example.test")
    monkeypatch.setattr(admission.time, "time", lambda: NOW)
    monkeypatch.setattr(server.RESOURCE_OWNER_ADMISSION_REPOSITORY, "lookup", lambda **query: calls.append(query) or {
        "state": "ACTIVE", "ownerId": OWNER, "policyVersion": "owner-admission/1"
    })
    monkeypatch.setattr(server.RESOURCE_WORKSPACE_REPOSITORY, "resolve", lambda **_kwargs: pytest.fail("workspace accessed"))
    response = server.app.test_client().post(
        "/api/v1/resource/owner-admission/resolve", json=deepcopy(request_payload())
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["ownerId"] == OWNER and len(calls) == 1


def test_route_wrong_workload_is_rejected_before_repository(monkeypatch):
    monkeypatch.setattr(admission, "authenticate_direct_mtls", lambda _env: (_ for _ in ()).throw(
        admission.OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED", 401)
    ))
    monkeypatch.setattr(server.RESOURCE_OWNER_ADMISSION_REPOSITORY, "lookup", lambda **_query: pytest.fail("repository accessed"))
    response = server.app.test_client().post(
        "/api/v1/resource/owner-admission/resolve", json=request_payload()
    )
    assert response.status_code == 401
