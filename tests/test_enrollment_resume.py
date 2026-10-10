from unittest.mock import Mock

from vvault.server import vvault_auth_crypto
from vvault.server import vvault_web_server as server


def test_pending_owner_provider_retry_reuses_same_browser_enrollment(monkeypatch):
    existing = {
        "user_id": "owner-a",
        "session_id": "pending-a",
        "enrollment_session_kind": "PENDING_ENROLLMENT",
    }
    monkeypatch.setattr(server, "_enrollment_session_from_request", lambda: existing)
    create = Mock(side_effect=AssertionError("a duplicate enrollment session was created"))
    monkeypatch.setattr(server.AUTH_REPOSITORY, "create_pending_enrollment_session", create)
    monkeypatch.setattr(vvault_auth_crypto, "opaque_token", lambda *_args: "opaque-device-secret-that-is-long-enough")

    with server.app.test_request_context("/api/auth/google/callback"):
        response = server._start_enrollment_session(
            {"id": "owner-a", "account_state": "PENDING_ENROLLMENT"},
            "https://vvault.example",
        )

    assert response.status_code == 302
    assert response.headers["Location"] == "https://vvault.example/?identity_pending=1"
    create.assert_not_called()


def test_another_owners_pending_cookie_is_never_reused(monkeypatch):
    existing = {
        "user_id": "owner-b",
        "session_id": "pending-b",
        "enrollment_session_kind": "PENDING_ENROLLMENT",
    }
    monkeypatch.setattr(server, "_enrollment_session_from_request", lambda: existing)
    monkeypatch.setattr(vvault_auth_crypto, "opaque_token", lambda *_args: "opaque-device-secret-that-is-long-enough")
    monkeypatch.setattr(vvault_auth_crypto, "keyed_digest", lambda value, _key: f"digest:{value}")
    monkeypatch.setattr(server, "_identity_hmac_key", lambda: b"fixture-key")
    create = Mock(return_value={"id": "pending-a"})
    monkeypatch.setattr(server.AUTH_REPOSITORY, "create_pending_enrollment_session", create)

    with server.app.test_request_context("/api/auth/google/callback", headers={"User-Agent": "fixture"}):
        response = server._start_enrollment_session(
            {"id": "owner-a", "account_state": "PENDING_ENROLLMENT"},
            "https://vvault.example",
        )

    assert response.status_code == 302
    create.assert_called_once()
    assert create.call_args.kwargs["user_id"] == "owner-a"
