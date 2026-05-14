from pathlib import Path


def _source() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "vvault" / "server" / "vvault_web_server.py").read_text()


def test_runtime_origin_helpers_are_explicit_and_environment_aware():
    source = _source()
    assert "def _runtime_is_production() -> bool:" in source
    assert "def _resolve_frontend_origin() -> Optional[str]:" in source
    assert "def _resolve_backend_origin() -> Optional[str]:" in source
    assert "def _build_cors_origins():" in source


def test_cors_defaults_do_not_hardcode_production_localhost_mix():
    source = _source()
    assert '_cors_origins = _build_cors_origins()' in source
    assert '"https://vvault.thewreck.org"' not in source


def test_runtime_config_reports_explicit_origin_contract():
    source = _source()
    assert '"cors_origins": _cors_origins' in source
    assert '"runtime_environment": "production" if _runtime_is_production() else "development"' in source
    assert '"frontend_origin": _resolve_frontend_origin()' in source
    assert '"backend_origin": _resolve_backend_origin()' in source
