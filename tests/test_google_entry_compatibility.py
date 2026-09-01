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
