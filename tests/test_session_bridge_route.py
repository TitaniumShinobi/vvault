from pathlib import Path

from vvault.server import vvault_web_server as server


def _source() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "vvault" / "server" / "vvault_web_server.py").read_text()


def test_session_bridge_route_is_mounted():
    rules = [
        rule
        for rule in server.app.url_map.iter_rules()
        if rule.rule == "/api/vault/session-bridge"
    ]

    assert len(rules) == 1
    assert rules[0].endpoint == "session_bridge_from_standalone_auth"
    assert {"POST", "OPTIONS"} <= rules[0].methods


def test_session_bridge_route_fails_closed_with_json_contract():
    source = _source()
    assert 'Session bridge is not configured (AUTH_SESSION_SECRET)' in source
    assert 'No auth session cookie' in source
    assert 'Invalid or expired auth session' in source
    assert '"success": False' in source


def test_session_bridge_route_mints_vvault_session_token():
    source = _source()
    assert "db_create_session(email, role, session_token, expires_at, remember_me=True)" in source
    assert '"token": session_token' in source
    assert '"expires_at": expires_at.isoformat()' in source
