from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "vvault/server/vvault_web_server.py").read_text(encoding="utf-8")


def test_restored_legacy_google_entry_delegates_to_canonical_oauth():
    assert "@app.route('/api/auth/google', methods=['GET'])" in SOURCE
    assert "def begin_legacy_google_compatibility_oauth():" in SOURCE
    start = SOURCE.index("def begin_legacy_google_compatibility_oauth():")
    assert 'return _begin_identity_oauth("google")' in SOURCE[start:start + 260]


def test_google_health_reports_transaction_protection_without_exposing_key_material():
    assert '"oauth_transaction_protection_ready": transaction_key_ready' in SOURCE
    assert "OAuth transaction protection is unavailable" in SOURCE
    assert '"VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY"' in SOURCE
    assert "valid_transaction_encryption_key" in SOURCE


def test_google_authorization_uses_pinned_google_owned_endpoints_without_live_discovery():
    start = SOURCE.index('if provider != "google" or not _google_oauth_ready():')
    end = SOURCE.index("\n\ndef _identity_callback_url", start)
    provider_config = SOURCE[start:end]
    assert "https://accounts.google.com/o/oauth2/v2/auth" in provider_config
    assert "https://oauth2.googleapis.com/token" in provider_config
    assert "https://www.googleapis.com/oauth2/v3/certs" in provider_config
    assert "requests.get(GOOGLE_DISCOVERY_URL" not in provider_config


def test_google_entry_reports_only_safe_failure_categories():
    assert '"identity_transaction_unavailable"' in SOURCE
    assert '"transaction_protection_unavailable"' in SOURCE
    assert 'logger.warning("identity OAuth begin rejected: %s", type(exc).__name__)' in SOURCE
