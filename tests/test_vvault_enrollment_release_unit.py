from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from vvault.server import vvault_enrollment
from vvault.server import vvault_web_server as server


FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
HMAC_KEY = "enrollment-test-key-that-is-long-enough"


class FakeGoogleClient:
    def prepare_request_uri(self, endpoint, **kwargs):
        return f"{endpoint}?state={kwargs['state']}&nonce={kwargs['nonce']}"

    def prepare_token_request(self, endpoint, **kwargs):
        return endpoint, {"Accept": "application/json"}, f"code_verifier={kwargs['code_verifier']}"


class FakeTokenResponse:
    ok = True
    status_code = 200

    def json(self):
        return {"id_token": "signed-id-token"}


class StatefulEnrollmentRepository:
    def __init__(self):
        self.transaction = None
        self.transaction_consumed = False
        self.users = {}
        self.identities = {}
        self.devices = {}
        self.sessions_by_hash = {}
        self.consented = set()
        self.challenges = {}
        self.credentials = set()
        self.recovery = set()
        self.recovery_digests = {}
        self.used_recovery = set()
        self.events = []
        self.next_device = 0

    def create_oauth_transaction(self, **values):
        self.transaction = values
        self.transaction_consumed = False

    def consume_oauth_transaction(self, state_digest):
        if self.transaction_consumed or not self.transaction or state_digest != self.transaction["state_digest"]:
            return None
        self.transaction_consumed = True
        return {
            "nonce_digest": self.transaction["nonce_digest"],
            "nonce_ciphertext": self.transaction["nonce_ciphertext"],
            "pkce_verifier_digest": self.transaction["verifier_digest"],
            "pkce_verifier_ciphertext": self.transaction["verifier_ciphertext"],
            "redirect_uri": self.transaction["redirect_uri"],
            "invitation_digest": self.transaction["invitation_digest"],
            "frontend_origin": self.transaction["frontend_origin"],
            "operation": self.transaction["operation"],
            "link_user_id": self.transaction["link_user_id"],
            "link_session_id": self.transaction["link_session_id"],
        }

    def get_identity(self, *, issuer, subject):
        user_id = self.identities.get((issuer, subject))
        return self.users.get(user_id)

    def admit_oidc_identity(self, *, issuer, subject, email, invitation_digest, **_kwargs):
        if not invitation_digest or email == "collision@example.com":
            return None
        user = {
            "id": "user-a", "email": email, "name": "User A", "role": "user",
            "enrollment_status": vvault_enrollment.PENDING,
        }
        self.users[user["id"]] = user
        self.identities[(issuer, subject)] = user["id"]
        return user

    def create_pending_device(self, *, user_id, device_digest, **_kwargs):
        self.next_device += 1
        device_id = f"device-{self.next_device}"
        self.devices[device_id] = {
            "id": device_id, "user_id": user_id, "digest": device_digest, "status": "PENDING",
        }
        return {"id": device_id, "status": "PENDING"}

    def issue_pending_oauth_session(self, *, user_id, token_hash, device_id, **_kwargs):
        self.sessions_by_hash[token_hash] = self._session(user_id, f"pending-{device_id}", device_id, "pending")

    def issue_device_pending_session(self, *, user_id, token_hash, device_id, **_kwargs):
        self.sessions_by_hash[token_hash] = self._session(
            user_id, f"device-pending-{device_id}", device_id,
            "device_pending", status=vvault_enrollment.ACTIVE,
        )

    def _session(self, user_id, session_id, device_id, kind, *, role="user", status=None):
        enrollment = status or (vvault_enrollment.ACTIVE if kind == "normal" else vvault_enrollment.PENDING)
        return {
            "session_id": session_id, "user_id": user_id, "email": f"{user_id}@example.com",
            "name": user_id, "role": role, "auth_provider": "google",
            "enrollment_status": enrollment, "session_kind": kind, "device_id": device_id,
            "device_status": "TRUSTED" if kind == "normal" else "PENDING",
            "expires_at": datetime.now(timezone.utc), "session_created_at": datetime.now(timezone.utc),
        }

    def add_normal_session(self, raw_token, *, user_id, device_id, role="user"):
        digest = server._session_token_hash(raw_token)
        self.sessions_by_hash[digest] = self._session(user_id, f"normal-{user_id}", device_id, "normal", role=role)
        self.devices[device_id] = {"id": device_id, "user_id": user_id, "status": "TRUSTED", "digest": "trusted"}

    def get_session_by_hash(self, token_hash):
        session = self.sessions_by_hash.get(token_hash)
        if not session:
            return None
        device = self.devices.get(session["device_id"], {})
        if device.get("status") == "REVOKED":
            return None
        return dict(session, device_status=device.get("status", session["device_status"]))

    def revoke_session_by_hash(self, token_hash):
        self.sessions_by_hash.pop(token_hash, None)

    def link_oidc_identity(self, *, user_id, actor_session_id, issuer, subject):
        actor = next(
            (value for value in self.sessions_by_hash.values()
             if value["session_id"] == actor_session_id),
            None,
        )
        if not actor or actor["user_id"] != user_id or actor["session_kind"] != "normal":
            return False
        existing = self.identities.get((issuer, subject))
        if existing:
            return existing == user_id
        self.identities[(issuer, subject)] = user_id
        return True

    def record_consents(self, *, user_id, **_kwargs):
        self.consented.add(user_id)

    def record_security_event(self, **event):
        self.events.append(event)

    def consents_complete(self, *, user_id, **_kwargs):
        return user_id in self.consented

    def create_webauthn_challenge(self, *, challenge_digest, user_id, session_id, rp_id, allowed_origin, **_kwargs):
        self.challenges[challenge_digest] = {
            "user_id": user_id, "session_id": session_id, "rp_id": rp_id,
            "allowed_origin": allowed_origin, "consumed": False,
        }

    def get_webauthn_challenge(self, *, user_id, session_id, challenge_digest):
        value = self.challenges.get(challenge_digest)
        if not value or value["consumed"] or value["user_id"] != user_id or value["session_id"] != session_id:
            return None
        return value

    def consume_webauthn_challenge_and_save_credential(self, *, user_id, session_id, challenge_digest, **_kwargs):
        value = self.get_webauthn_challenge(user_id=user_id, session_id=session_id, challenge_digest=challenge_digest)
        if not value:
            return False
        value["consumed"] = True
        self.credentials.add(user_id)
        return True

    def issue_recovery_codes(self, *, user_id, digests, **_kwargs):
        if user_id not in self.consented or user_id not in self.credentials or user_id in self.recovery:
            return False
        self.recovery.add(user_id)
        self.recovery_digests[user_id] = set(digests)
        return True

    def enrollment_progress(self, user_id):
        return {
            "consents": ([{"document_key": document["key"], "document_version": document["version"], "document_sha256": document["sha256"]} for document in vvault_enrollment.legal_documents(server._repo_root)] if user_id in self.consented else []),
            "mfa": user_id in self.credentials,
            "recovery": user_id in self.recovery,
            "devices": [{"id": device["id"], "status": device["status"]} for device in self.devices.values() if device["user_id"] == user_id],
        }

    def approve_device_and_rotate_session(
        self, *, owner_user_id, device_id, pending_session_id, actor_session_id,
        normal_token_hash, device_secret_digest, **_kwargs,
    ):
        device = self.devices.get(device_id)
        actor = next((value for value in self.sessions_by_hash.values() if value["session_id"] == actor_session_id), None)
        permitted = device and device["status"] == "TRUSTED" or bool(
            actor and (actor["user_id"] == owner_user_id or actor["role"] == "admin")
        )
        if (
            not device or device["user_id"] != owner_user_id or device["digest"] != device_secret_digest
            or not permitted or owner_user_id not in self.consented
            or owner_user_id not in self.credentials or owner_user_id not in self.recovery
        ):
            return None
        device["status"] = "TRUSTED"
        self.users[owner_user_id]["enrollment_status"] = vvault_enrollment.ACTIVE
        self.sessions_by_hash[normal_token_hash] = self._session(owner_user_id, "normal-user-a", device_id, "normal")
        return {"id": "normal-user-a", "user_id": owner_user_id, "device_id": device_id}

    def approve_pending_device(self, *, target_user_id, device_id, actor_session_id):
        actor = next((value for value in self.sessions_by_hash.values() if value["session_id"] == actor_session_id), None)
        device = self.devices.get(device_id)
        if (
            not actor or not device or device["user_id"] != target_user_id
            or (actor["user_id"] != target_user_id and actor["role"] != "admin")
        ):
            return False
        device["status"] = "TRUSTED"
        return True

    def revoke_device(self, *, device_id, actor_session_id):
        actor = next((value for value in self.sessions_by_hash.values() if value["session_id"] == actor_session_id), None)
        device = self.devices.get(device_id)
        if not actor or not device or (actor["user_id"] != device["user_id"] and actor["role"] != "admin"):
            return False
        device["status"] = "REVOKED"
        return True

    def recover_device_and_rotate_session(
        self, *, owner_user_id, device_id, pending_session_id,
        recovery_code_digest, device_secret_digest, normal_token_hash, **_kwargs,
    ):
        pending = next(
            (value for value in self.sessions_by_hash.values()
             if value["session_id"] == pending_session_id),
            None,
        )
        device = self.devices.get(device_id)
        if (
            not pending or pending["session_kind"] != "device_pending"
            or not device or device["user_id"] != owner_user_id
            or device["digest"] != device_secret_digest
            or owner_user_id not in self.recovery
            or owner_user_id in self.used_recovery
            or recovery_code_digest not in self.recovery_digests.get(owner_user_id, set())
        ):
            return None
        self.used_recovery.add(owner_user_id)
        device["status"] = "TRUSTED"
        self.sessions_by_hash = {
            key: value for key, value in self.sessions_by_hash.items()
            if value["session_id"] != pending_session_id
        }
        self.sessions_by_hash[normal_token_hash] = self._session(
            owner_user_id, "recovered-normal", device_id, "normal",
        )
        return {"id": "recovered-normal", "user_id": owner_user_id, "device_id": device_id}


@pytest.fixture
def enrollment_app(monkeypatch):
    repository = StatefulEnrollmentRepository()
    monkeypatch.setenv("VVAULT_ENROLLMENT_HMAC_KEY", HMAC_KEY)
    monkeypatch.setenv("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.setenv("VVAULT_WEBAUTHN_ORIGIN", "http://localhost:7784")
    monkeypatch.setenv("VVAULT_WEBAUTHN_RP_ID", "localhost")
    monkeypatch.setattr(server, "AUTH_REPOSITORY", repository)
    monkeypatch.setattr(server, "google_client", FakeGoogleClient())
    monkeypatch.setattr(server, "_rate_limit_key", lambda _bucket: False)
    monkeypatch.setattr(server, "_oauth_identity_authority_available", lambda: (True, {"status": "healthy"}))
    monkeypatch.setattr(server, "_google_oauth_ready", lambda: True)
    monkeypatch.setattr(server, "_get_frontend_url", lambda *_args: "http://localhost:7784")
    monkeypatch.setattr(server, "_get_backend_url", lambda: "http://127.0.0.1:8000")
    monkeypatch.setattr(server, "_allowed_redirect_base", lambda value: value == "http://localhost:7784")
    monkeypatch.setattr(server, "_google_provider_config", lambda: {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    })
    observed_posts = []

    def post(url, **kwargs):
        observed_posts.append((url, kwargs))
        return FakeTokenResponse()

    monkeypatch.setattr(server.requests, "post", post)
    monkeypatch.setattr(server, "_google_id_token_claims", lambda *_args, **_kwargs: {
        "iss": "accounts.google.com", "sub": "subject-a", "email": "a@example.com",
        "email_verified": True, "name": "User A",
    })
    monkeypatch.setattr(server, "_body_database_dependency_status", lambda: {"ready": True})
    server.app.config.update(TESTING=True, SECRET_KEY="test-session-secret")
    return server.app.test_client(), repository, observed_posts


def _oauth_callback(client, invitation=None):
    start = client.post("/api/auth/google", json={} if invitation is None else {"invitation": invitation})
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    return client.get(f"/api/auth/google/callback?code=provider-code&state={state}"), state


def test_uninvited_signin_is_zero_state_and_callback_replay_is_denied(enrollment_app):
    client, repository, observed_posts = enrollment_app
    assert client.get("/api/auth/google?invitation=secret").status_code == 405
    callback, state = _oauth_callback(client)
    assert callback.status_code == 302
    assert "secret" not in callback.headers["Location"]
    assert not repository.users
    assert not repository.devices
    assert not repository.sessions_by_hash
    assert observed_posts[0][1]["timeout"] == (3.05, 5)
    replay = client.get(f"/api/auth/google/callback?code=provider-code&state={state}")
    assert replay.status_code == 400
    assert replay.headers["Cache-Control"].startswith("no-store")


@pytest.mark.parametrize(
    "rejection",
    ["issuer", "audience", "signature", "nonce", "expiry", "unverified-email"],
)
def test_invalid_google_identity_proof_is_denied_without_mutation(enrollment_app, monkeypatch, rejection):
    client, repository, _observed_posts = enrollment_app

    def reject(*_args, **_kwargs):
        raise ValueError(f"invalid {rejection}")

    monkeypatch.setattr(server, "_google_id_token_claims", reject)
    callback, _state = _oauth_callback(client, invitation="one-time-secret")
    assert callback.status_code == 500
    assert not repository.users
    assert not repository.devices
    assert not repository.sessions_by_hash


@pytest.mark.parametrize("tamper", ["pkce", "redirect_uri"])
def test_oauth_transaction_integrity_failure_is_zero_state(enrollment_app, tamper):
    client, repository, _observed_posts = enrollment_app
    start = client.post("/api/auth/google", json={"invitation": "one-time-secret"})
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    if tamper == "pkce":
        repository.transaction["verifier_digest"] = "tampered"
    else:
        repository.transaction["redirect_uri"] = "https://attacker.example/callback"
    callback = client.get(f"/api/auth/google/callback?code=provider-code&state={state}")
    assert callback.status_code == 500
    assert not repository.users
    assert not repository.devices
    assert not repository.sessions_by_hash


def test_existing_email_collision_cannot_link_identity(enrollment_app, monkeypatch):
    client, repository, _observed_posts = enrollment_app
    monkeypatch.setattr(server, "_google_id_token_claims", lambda *_args, **_kwargs: {
        "iss": "https://accounts.google.com", "sub": "new-subject",
        "email": "collision@example.com", "email_verified": True,
    })
    callback, _state = _oauth_callback(client, invitation="valid-invitation")
    assert callback.status_code == 302
    assert "oauth_error=" in callback.headers["Location"]
    assert not repository.users
    assert not repository.identities
    assert not repository.devices
    assert not repository.sessions_by_hash


def test_identity_link_requires_the_original_session_to_remain_valid(enrollment_app):
    client, repository, _observed_posts = enrollment_app
    owner_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    repository.users[owner_id] = {
        "id": owner_id, "email": "owner@example.com", "name": "Owner",
        "role": "user", "enrollment_status": vvault_enrollment.ACTIVE,
    }
    repository.add_normal_session("owner-token", user_id=owner_id, device_id="owner-device")
    client.set_cookie("vvault_session", "owner-token")
    start = client.post("/api/auth/identity-links/google", json={})
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

    assert client.post("/api/auth/logout").status_code == 200
    callback = client.get(f"/api/auth/google/callback?code=provider-code&state={state}")
    assert callback.status_code == 302
    assert "oauth_error=" in callback.headers["Location"]
    assert ("https://accounts.google.com", "subject-a") not in repository.identities


def test_logout_revokes_pending_enrollment_authority(enrollment_app):
    client, repository, _observed_posts = enrollment_app
    callback, _state = _oauth_callback(client, invitation="one-time-secret")
    assert callback.status_code == 302
    assert repository.sessions_by_hash

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert not repository.sessions_by_hash
    cleared = "\n".join(logout.headers.getlist("Set-Cookie"))
    assert "vvault_pending_session=" in cleared
    assert "vvault_pending_device=" in cleared
    assert client.get("/api/auth/enrollment/status").status_code == 401
    assert client.post("/api/auth/enrollment/consents").status_code == 401


def test_logout_revokes_cookie_session_even_when_bearer_header_is_present(enrollment_app):
    client, repository, _observed_posts = enrollment_app
    repository.add_normal_session("cookie-token", user_id="user-a", device_id="device-a")
    client.set_cookie("vvault_session", "cookie-token")
    logout = client.post(
        "/api/auth/logout", headers={"Authorization": "Bearer unrelated-token"},
    )
    assert logout.status_code == 200
    client.set_cookie("vvault_session", "cookie-token")
    assert client.get("/api/auth/verify").status_code == 401


def test_returning_user_can_recover_one_new_device_once(enrollment_app):
    client, repository, _observed_posts = enrollment_app
    repository.users["user-a"] = {
        "id": "user-a", "email": "a@example.com", "name": "User A",
        "role": "user", "enrollment_status": vvault_enrollment.ACTIVE,
    }
    repository.identities[("https://accounts.google.com", "subject-a")] = "user-a"
    repository.recovery.add("user-a")
    repository.recovery_digests["user-a"] = {
        vvault_enrollment.keyed_digest("one-time-code", HMAC_KEY),
    }
    callback, _state = _oauth_callback(client)
    assert callback.status_code == 302
    device_id = next(iter(repository.devices))

    assert client.post(
        f"/api/auth/devices/{device_id}/recover",
        json={"recovery_code": "wrong-code"},
    ).status_code == 403

    recovered = client.post(
        f"/api/auth/devices/{device_id}/recover",
        json={"recovery_code": "one-time-code"},
    )
    assert recovered.status_code == 200
    assert recovered.headers["Cache-Control"].startswith("no-store")
    assert client.get("/api/auth/verify").status_code == 200

    second_client = server.app.test_client()
    second_callback, _state = _oauth_callback(second_client)
    assert second_callback.status_code == 302
    second_device_id = max(repository.devices, key=lambda value: int(value.split('-')[-1]))
    replay = second_client.post(
        f"/api/auth/devices/{second_device_id}/recover",
        json={"recovery_code": "one-time-code"},
    )
    assert replay.status_code == 403


def test_pending_enrollment_order_cross_user_approval_and_immediate_revocation(enrollment_app, monkeypatch):
    client, repository, _observed_posts = enrollment_app
    callback, _state = _oauth_callback(client, invitation="one-time-secret")
    assert callback.status_code == 302
    assert repository.users["user-a"]["enrollment_status"] == vvault_enrollment.PENDING
    assert len(repository.devices) == 1 and len(repository.sessions_by_hash) == 1
    initial_status = client.get("/api/auth/enrollment/status")
    assert initial_status.status_code == 200
    assert initial_status.get_json()["enrollment_status"] == vvault_enrollment.PENDING

    assert client.post("/api/auth/enrollment/recovery-codes").status_code == 409
    assert client.post("/api/auth/enrollment/webauthn/challenge").status_code == 409
    consent = client.post("/api/auth/enrollment/consents")
    assert consent.status_code == 200
    challenge_response = client.post("/api/auth/enrollment/webauthn/challenge")
    assert challenge_response.status_code == 200
    challenge = challenge_response.get_json()["publicKey"]["challenge"]
    client_data = vvault_enrollment.b64url_encode(json.dumps({
        "type": "webauthn.create", "challenge": challenge,
        "origin": "http://localhost:7784",
    }).encode())
    credential = {"id": "credential-a", "rawId": "credential-a", "type": "public-key", "response": {"clientDataJSON": client_data}}
    verification_call = {}

    def verify_registration(*_args, **kwargs):
        verification_call.update(kwargs)
        return {"credential_id": "credential-a", "public_key": b"public-key", "sign_count": 0}

    monkeypatch.setattr(vvault_enrollment, "verify_registration_credential", verify_registration)
    assert client.post("/api/auth/enrollment/webauthn/register", json=credential).status_code == 200
    assert verification_call["rp_id"] == "localhost"
    assert verification_call["allowed_origin"] == "http://localhost:7784"
    assert client.post("/api/auth/enrollment/webauthn/register", json=credential).status_code in {400, 409}
    recovery = client.post("/api/auth/enrollment/recovery-codes")
    assert recovery.status_code == 200
    assert len(recovery.get_json()["recovery_codes"]) == 10
    assert recovery.headers["Cache-Control"].startswith("no-store")

    device_id = next(iter(repository.devices))
    monkeypatch.setattr(server, "_is_uuid", lambda value: bool(value))
    repository.add_normal_session("user-b-token", user_id="user-b", device_id="device-b")
    client.set_cookie("vvault_session", "user-b-token")
    assert client.post(
        f"/api/auth/devices/{device_id}/approve", json={"target_user_id": "user-a"},
    ).status_code == 403
    assert repository.users["user-a"]["enrollment_status"] == vvault_enrollment.PENDING

    repository.add_normal_session("admin-token", user_id="admin", device_id="device-admin", role="admin")
    client.set_cookie("vvault_session", "admin-token")
    assert client.post(
        f"/api/auth/devices/{device_id}/approve", json={"target_user_id": "user-a"},
    ).status_code == 200
    client.delete_cookie("vvault_session")
    approved = client.post(f"/api/auth/enrollment/devices/{device_id}/approve")
    assert approved.status_code == 200
    assert "token" not in approved.get_json()
    assert repository.users["user-a"]["enrollment_status"] == vvault_enrollment.ACTIVE
    assert approved.headers["Cache-Control"].startswith("no-store")

    assert client.post(f"/api/auth/devices/{device_id}/revoke").status_code == 200
    assert client.post(f"/api/auth/devices/{device_id}/revoke").status_code == 401


def test_session_owner_overrides_all_forged_owner_inputs(enrollment_app, monkeypatch):
    client, repository, _observed_posts = enrollment_app
    owner_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    repository.add_normal_session("user-b-token", user_id=owner_b, device_id="device-b")
    client.set_cookie("vvault_session", "user-b-token")
    observed = {}

    class Result:
        def to_response(self):
            return {"success": True, "content": "B-only"}, 200

    def transcript_body(_construct_id, *, owner_user_id, **_kwargs):
        observed["owner"] = owner_user_id
        return Result()

    monkeypatch.setattr(server, "_chatty_construct_actor_user_id", lambda _construct_id: (server._get_authenticated_user_id(), None))
    monkeypatch.setattr(server.chatty_body_service, "transcript_body", transcript_body)
    response = client.get(
        "/api/chatty/transcript/zen-001?owner_user_id=user-a",
        headers={"X-Chatty-User": "a@example.com", "X-Chatty-User-Id": "user-a", "X-VVAULT-Owner-Override": "user-a"},
    )
    assert response.status_code == 200
    assert observed["owner"] == owner_b

    def update_transcript_body(_construct_id, _payload, *, owner_user_id):
        observed["write_owner"] = owner_user_id
        return Result()

    monkeypatch.setattr(server.chatty_body_service, "update_transcript_body", update_transcript_body)
    write_response = client.post(
        "/api/chatty/transcript/zen-001?owner_user_id=user-a",
        json={"content": "forged", "owner_user_id": "user-a"},
        headers={"X-Chatty-User-Id": "user-a", "X-VVAULT-Owner-Override": "user-a"},
    )
    assert write_response.status_code == 200
    assert observed["write_owner"] == owner_b

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/chatty/transcript/zen-001").status_code == 401


def test_service_token_has_no_owner_and_forged_owner_headers_still_fail(
    enrollment_app, monkeypatch,
):
    client, _repository, _observed_posts = enrollment_app
    monkeypatch.setattr(server, "_service_token_matches", lambda *_args, **_kwargs: True)

    with server.app.test_request_context(
        "/api/chatty/internal/service-proof",
        headers={"X-Chatty-Key": "service-secret"},
    ):
        decorated = server.require_chatty_auth(
            lambda: getattr(server.request, "current_user", {}).get("auth_mode")
        )
        assert decorated() == "service_token"
        assert server._get_authenticated_user_id() is None

    response = client.get(
        "/api/chatty/transcript/zen-001?owner_user_id=user-a",
        headers={
            "X-Chatty-Key": "service-secret",
            "X-Chatty-User": "a@example.com",
            "X-Chatty-User-Id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert response.status_code == 403


def test_google_discovery_requires_https_allowlist_status_and_timeout(monkeypatch):
    observed = {}

    class DiscoveryResponse:
        def raise_for_status(self):
            observed["status_checked"] = True

        def json(self):
            return {
                "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            }

    def get(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return DiscoveryResponse()

    monkeypatch.setattr(server.requests, "get", get)
    assert server._google_provider_config()["token_endpoint"].startswith("https://")
    assert observed["timeout"] == (3.05, 5)
    assert observed["status_checked"] is True
