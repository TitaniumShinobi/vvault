from vvault.server import vvault_web_server as server


ACTIVE_SESSION = {
    "id": "owner-a",
    "session_id": "session-a",
    "email": "owner@example.test",
    "name": "Owner",
    "role": "user",
    "account_state": "ACTIVE",
    "enrollment_session_kind": "NORMAL",
    "enrollment_device_status": "",
}


def _client_with_cookie():
    client = server.app.test_client()
    client.set_cookie("vvault_session", "persisted-token")
    return client


def test_cookie_session_restores_in_a_new_browser_process(monkeypatch):
    monkeypatch.setattr(server, "db_get_session", lambda token: ACTIVE_SESSION if token == "persisted-token" else None)
    monkeypatch.setattr(server, "db_get_user", lambda _email: {"name": "Owner"})
    assert _client_with_cookie().get("/api/auth/verify").status_code == 200
    restarted_client = _client_with_cookie()
    payload = restarted_client.get("/api/auth/verify").get_json()
    assert payload["success"] is True
    assert payload["user"]["email"] == "owner@example.test"
    assert payload["token"] is None


def test_expired_session_is_rejected_with_explicit_outcome(monkeypatch):
    monkeypatch.setattr(server, "db_get_session", lambda _token: None)
    response = _client_with_cookie().get("/api/auth/verify")
    assert response.status_code == 401
    assert response.get_json() == {"success": False, "error": "Invalid or expired token"}


def test_logout_revokes_server_session_clears_cookie_and_rejects_reuse(monkeypatch):
    revoked = set()
    monkeypatch.setattr(server, "db_get_session", lambda token: None if token in revoked else ACTIVE_SESSION)
    monkeypatch.setattr(server, "db_get_user", lambda _email: {"name": "Owner"})
    monkeypatch.setattr(server, "db_delete_session", lambda token: revoked.add(token) or True)
    client = _client_with_cookie()
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert "vvault_session=;" in "\n".join(response.headers.getlist("Set-Cookie"))
    client.set_cookie("vvault_session", "persisted-token")
    assert client.get("/api/auth/verify").status_code == 401
