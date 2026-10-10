from unittest.mock import patch

from vvault.server import vvault_auth_crypto
from vvault.server import vvault_web_server as server


def _healthy_authority():
    return True, {"source_database": "vvault_body_20260504t123219z"}


def test_google_is_presented_only_when_configuration_and_authority_are_ready(monkeypatch):
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "configured-google-client")
    monkeypatch.setattr(server, "GOOGLE_CLIENT_SECRET", "configured-google-secret")
    monkeypatch.setattr(server, "_oauth_identity_authority_available", _healthy_authority)
    monkeypatch.setattr(vvault_auth_crypto, "valid_transaction_encryption_key", lambda _value: True)
    with patch.dict(server.os.environ, {"VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY": "fixture"}):
        response = server.app.test_client().get("/api/auth/providers/google/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["available"] is True
    assert payload["configured"] is True
    assert payload["provider"] == "google"
    assert payload["callback_url"].endswith("/api/auth/google/callback")
    assert payload["oauth_transaction_protection_ready"] is True
    assert payload["source_database"] == "vvault_body_20260504t123219z"
    assert payload["vvault_auth_ready"] is True
    assert payload["error"] is None


def test_placeholder_github_configuration_is_explicitly_disabled(monkeypatch):
    monkeypatch.setattr(server, "GITHUB_CLIENT_ID", "your-github-client-id")
    monkeypatch.setattr(server, "GITHUB_CLIENT_SECRET", "your-github-client-secret")
    monkeypatch.setattr(server, "_oauth_identity_authority_available", _healthy_authority)
    monkeypatch.setattr(vvault_auth_crypto, "valid_transaction_encryption_key", lambda _value: True)
    with patch.dict(server.os.environ, {"VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY": "fixture"}):
        response = server.app.test_client().get("/api/auth/providers/github/health")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["available"] is False
    assert payload["configured"] is False
    assert payload["provider"] == "github"


def test_unsupported_provider_is_not_advertised():
    response = server.app.test_client().get("/api/auth/providers/apple/health")
    assert response.status_code == 404
    assert response.get_json()["available"] is False
