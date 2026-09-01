"""Mock-only wire proof for the VVAULT-to-Chatty pairing boundary."""
from unittest.mock import patch

from vvault.server import vvault_web_server as server


CALLBACK = "http://127.0.0.1:5050/api/vvault/pairing/callback"
CLIENT_ID = "chatty-developer-local"
CLIENT_SECRET = "test-chatty-pairing-secret"
VVAULT_USER_ID = "11111111-1111-4111-8111-111111111111"
VVAULT_SESSION_ID = "22222222-2222-4222-8222-222222222222"
CHATTY_ACCOUNT_ID = "33333333-3333-4333-8333-333333333333"
LINK_ID = "44444444-4444-4444-8444-444444444444"


def _active_session():
    return {
        "id": VVAULT_USER_ID,
        "session_id": VVAULT_SESSION_ID,
        "account_state": "ACTIVE",
        "enrollment_session_kind": "NORMAL",
        "enrollment_device_status": "TRUSTED",
    }


def _pairing_constants():
    return patch.multiple(
        server,
        CHATTY_PAIRING_CALLBACK_URL=CALLBACK,
        CHATTY_PAIRING_CLIENT_ID=CLIENT_ID,
        CHATTY_PAIRING_CLIENT_SECRET=CLIENT_SECRET,
    )


def test_browser_pairing_issue_response_is_only_opaque_contract_fields():
    with (
        _pairing_constants(),
        patch.object(server, "get_current_user", return_value=(_active_session(), "vvault-session")),
        patch.object(server, "_get_frontend_url", return_value="http://127.0.0.1:7784"),
        patch.object(server, "_identity_hmac_key", return_value="test-pairing-hmac-key-that-is-long-enough"),
        patch.object(server.AUTH_REPOSITORY, "create_chatty_pairing_intent", return_value=True) as create,
    ):
        response = server.app.test_client().post(
            "/api/auth/pairing-intents/chatty",
            headers={"Authorization": "Bearer vvault-session", "Origin": "http://127.0.0.1:7784"},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {"success", "audience", "pairing_code", "callback_uri", "expires_in"}
    assert payload["success"] is True
    assert payload["audience"] == CLIENT_ID
    assert payload["callback_uri"] == CALLBACK
    assert payload["expires_in"] == 60
    assert len(payload["pairing_code"]) >= 32
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert create.call_args.kwargs["user_id"] == VVAULT_USER_ID
    assert create.call_args.kwargs["session_id"] == VVAULT_SESSION_ID
    assert "email" not in payload and "provider" not in payload and "session" not in payload


def test_server_redemption_requires_exact_audience_callback_and_single_use_code():
    consumed = [{"audience": CLIENT_ID, "link_id": LINK_ID}, None]
    with (
        _pairing_constants(),
        patch.object(server, "_identity_hmac_key", return_value="test-pairing-hmac-key-that-is-long-enough"),
        patch.object(server.AUTH_REPOSITORY, "consume_chatty_pairing_intent", side_effect=consumed) as consume,
    ):
        client = server.app.test_client()
        headers = {"Authorization": f"Bearer {CLIENT_SECRET}", "X-Chatty-Client-Id": CLIENT_ID}
        rejected_callback = client.post(
            "/api/auth/pairing-intents/chatty/redeem",
            headers=headers,
            json={"pairing_code": "p" * 43, "audience": CLIENT_ID, "callback_uri": "http://127.0.0.1:5050/not-the-callback", "chatty_account_id": CHATTY_ACCOUNT_ID},
        )
        rejected_audience = client.post(
            "/api/auth/pairing-intents/chatty/redeem",
            headers=headers,
            json={"pairing_code": "p" * 43, "audience": "wrong-audience", "callback_uri": CALLBACK, "chatty_account_id": CHATTY_ACCOUNT_ID},
        )
        first = client.post(
            "/api/auth/pairing-intents/chatty/redeem",
            headers=headers,
            json={"pairing_code": "p" * 43, "audience": CLIENT_ID, "callback_uri": CALLBACK, "chatty_account_id": CHATTY_ACCOUNT_ID, "email": "ignored-by-vvault@example.test"},
        )
        replay = client.post(
            "/api/auth/pairing-intents/chatty/redeem",
            headers=headers,
            json={"pairing_code": "p" * 43, "audience": CLIENT_ID, "callback_uri": CALLBACK, "chatty_account_id": CHATTY_ACCOUNT_ID},
        )

    assert rejected_callback.status_code == 400
    assert rejected_audience.status_code == 400
    assert consume.call_count == 2
    assert first.status_code == 200
    assert first.get_json() == {"success": True, "audience": CLIENT_ID, "link_id": LINK_ID}
    assert replay.status_code == 400
    assert consume.call_args_list[0].kwargs["callback_uri"] == CALLBACK
    assert consume.call_args_list[0].kwargs["chatty_account_id"] == CHATTY_ACCOUNT_ID
    assert first.headers["Cache-Control"] == "no-store"
    assert first.headers["Referrer-Policy"] == "no-referrer"


def test_redemption_rejects_missing_or_wrong_server_client_credentials():
    with _pairing_constants(), patch.object(server.AUTH_REPOSITORY, "consume_chatty_pairing_intent") as consume:
        response = server.app.test_client().post(
            "/api/auth/pairing-intents/chatty/redeem",
            json={"pairing_code": "p" * 43, "audience": CLIENT_ID, "callback_uri": CALLBACK, "chatty_account_id": CHATTY_ACCOUNT_ID},
        )
        wrong = server.app.test_client().post(
            "/api/auth/pairing-intents/chatty/redeem",
            headers={"Authorization": "Bearer wrong", "X-Chatty-Client-Id": CLIENT_ID},
            json={"pairing_code": "p" * 43, "audience": CLIENT_ID, "callback_uri": CALLBACK, "chatty_account_id": CHATTY_ACCOUNT_ID},
        )
    assert response.status_code == 401
    assert wrong.status_code == 401
    consume.assert_not_called()
