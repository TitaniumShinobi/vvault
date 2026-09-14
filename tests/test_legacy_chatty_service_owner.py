from vvault.server import vvault_web_server as server


def test_legacy_chatty_service_owner_requires_token_and_existing_owner(monkeypatch):
    monkeypatch.setenv("VVAULT_SERVICE_TOKEN", "service-token")
    monkeypatch.setattr(
        server,
        "db_get_user",
        lambda email: {"id": "123e4567-e89b-42d3-a456-426614174000", "email": email},
    )

    with server.app.test_request_context(
        "/api/chatty/constructs",
        headers={"X-Chatty-Key": "service-token", "X-Chatty-User": "owner@example.com"},
    ):
        owner = server._legacy_chatty_service_owner()

    assert owner == {
        "id": "123e4567-e89b-42d3-a456-426614174000",
        "email": "owner@example.com",
        "auth_mode": "legacy_chatty_service",
        "relying_party_id": "chatty",
    }


def test_legacy_chatty_service_owner_rejects_unresolved_owner(monkeypatch):
    monkeypatch.setenv("VVAULT_SERVICE_TOKEN", "service-token")
    monkeypatch.setattr(server, "db_get_user", lambda _email: None)

    with server.app.test_request_context(
        "/api/chatty/constructs",
        headers={"X-Chatty-Key": "service-token", "X-Chatty-User": "owner@example.com"},
    ):
        assert server._legacy_chatty_service_owner() is None
