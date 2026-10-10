from urllib.parse import parse_qs, urlparse

from vvault.server import vvault_auth_crypto
from vvault.server import vvault_web_server as server


def _transaction(provider="google"):
    return {
        "provider": provider,
        "purpose": "signin",
        "redirect_uri": f"https://vvault.example/api/auth/{'google/callback' if provider == 'google' else f'oauth/{provider}/callback'}",
        "frontend_origin": "https://vvault.example",
    }


def _callback_dependencies(monkeypatch, transaction):
    monkeypatch.setattr(server, "_rate_limit_key", lambda _key: False)
    monkeypatch.setattr(server, "_identity_hmac_key", lambda: b"fixture-key")
    monkeypatch.setattr(server, "_get_frontend_url", lambda: "https://vvault.example")
    monkeypatch.setattr(server, "_allowed_redirect_base", lambda value: value == "https://vvault.example")
    monkeypatch.setattr(server, "_identity_callback_url", lambda provider: _transaction(provider)["redirect_uri"])
    monkeypatch.setattr(server, "_oauth_state_intent", lambda _crypto, _state: "signin")
    monkeypatch.setattr(vvault_auth_crypto, "keyed_digest", lambda value, _key: f"digest:{value}")
    monkeypatch.setattr(server.AUTH_REPOSITORY, "consume_oauth_transaction", lambda digest: transaction if digest == "digest:state" else None)


def test_provider_cancellation_consumes_state_and_returns_retryable_browser_outcome(monkeypatch):
    _callback_dependencies(monkeypatch, _transaction())
    response = server.app.test_client().get(
        "/api/auth/google/callback?error=access_denied&state=state",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["Location"]).query)
    assert query == {"oauth_error": ["access_denied"], "oauth_retry": ["1"]}


def test_rejected_or_replayed_callback_has_explicit_html_and_json_recovery(monkeypatch):
    _callback_dependencies(monkeypatch, None)
    html = server.app.test_client().get(
        "/api/auth/google/callback?code=rejected&state=state",
        headers={"Accept": "text/html"},
    )
    assert html.status_code == 302
    assert parse_qs(urlparse(html.headers["Location"]).query) == {
        "oauth_error": ["authorization_rejected"], "oauth_retry": ["1"]
    }
    api = server.app.test_client().get(
        "/api/auth/google/callback?code=rejected&state=state",
        headers={"Accept": "application/json"},
    )
    assert api.status_code == 400
    assert api.get_json() == {"success": False, "error": "OAuth authorization was rejected", "retry": True}


def test_missing_state_is_recoverable_for_browser_callback(monkeypatch):
    _callback_dependencies(monkeypatch, None)
    response = server.app.test_client().get(
        "/api/auth/google/callback?error=access_denied",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["Location"]).query) == {
        "oauth_error": ["authorization_rejected"], "oauth_retry": ["1"]
    }


def test_frontend_exposes_cancel_and_retry_recovery_without_restoring_device_gate():
    source = (server._repo_root / "src" / "components" / "CinematicLogin.js").read_text(encoding="utf-8")
    assert "Provider sign-in was cancelled. You can try again when ready." in source
    assert "Provider sign-in could not be completed. Please try again." in source
    assert "oauth_retry" in source
    assert "device approval" not in source.lower()


def test_oauth_entry_intent_is_integrity_bound(monkeypatch):
    monkeypatch.setattr(server, "_identity_hmac_key", lambda: b"fixture-key")
    monkeypatch.setattr(vvault_auth_crypto, "opaque_token", lambda: "nonce")
    monkeypatch.setattr(
        vvault_auth_crypto,
        "keyed_digest",
        lambda value, _key: f"signature-{value}",
    )
    state = server._new_oauth_state(vvault_auth_crypto, "signup")
    assert server._oauth_state_intent(vvault_auth_crypto, state) == "signup"
    try:
        server._oauth_state_intent(vvault_auth_crypto, state.replace("signup", "signin", 1))
    except ValueError:
        pass
    else:
        raise AssertionError("tampered OAuth entry intent was accepted")


def test_signin_callback_cannot_create_an_unknown_identity(monkeypatch):
    _callback_dependencies(monkeypatch, _transaction())
    monkeypatch.setattr(server, "_verified_provider_claims", lambda *_args: (
        "new-subject", "new@example.test", "New", "https://accounts.google.com",
    ))
    observed = {}

    def admit(**kwargs):
        observed.update(kwargs)
        return None, False

    monkeypatch.setattr(server.AUTH_REPOSITORY, "admit_verified_identity", admit)
    response = server.app.test_client().get(
        "/api/auth/google/callback?code=verified&state=state",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["Location"]).query) == {"signup_required": ["1"]}
    assert observed["allow_create"] is False


def test_signup_callback_is_the_only_provider_path_that_can_create(monkeypatch):
    _callback_dependencies(monkeypatch, _transaction())
    monkeypatch.setattr(server, "_oauth_state_intent", lambda _crypto, _state: "signup")
    monkeypatch.setattr(server, "_verified_provider_claims", lambda *_args: (
        "new-subject", "new@example.test", "New", "https://accounts.google.com",
    ))
    observed = {}

    def admit(**kwargs):
        observed.update(kwargs)
        return {"id": "owner", "account_state": "PENDING_ENROLLMENT"}, True

    monkeypatch.setattr(server.AUTH_REPOSITORY, "admit_verified_identity", admit)
    monkeypatch.setattr(server, "_start_enrollment_session", lambda _user, _frontend: ("pending", 202))
    response = server.app.test_client().get(
        "/api/auth/google/callback?code=verified&state=state",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 202
    assert observed["allow_create"] is True
