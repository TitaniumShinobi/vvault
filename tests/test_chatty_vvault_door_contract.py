import json
from pathlib import Path

import pytest
from vvault.server import vvault_web_server as server


ROOT = Path(__file__).resolve().parents[1]
DOOR_PATH = ROOT / "config" / "chatty-vvault-doors.json"
SERVER_PATH = ROOT / "vvault" / "server" / "vvault_web_server.py"


def test_door_contract_defines_private_and_public_doors():
    contract = json.loads(DOOR_PATH.read_text(encoding="utf-8"))

    assert contract["version"] == 1
    assert contract["doors"]["private"]["chattyPublicOrigin"] == "http://localhost:5173"
    assert contract["doors"]["private"]["vvaultOrigin"] == "http://127.0.0.1:8000"
    assert contract["doors"]["private"]["authApiOrigin"] == "http://127.0.0.1:1111"
    assert contract["doors"]["public"]["chattyPublicOrigin"] == "https://chatty.thewreck.org"
    assert contract["doors"]["public"]["vvaultOrigin"] == "https://vvault.thewreck.org"
    assert contract["doors"]["public"]["authApiOrigin"] == "https://auth.thewreck.org"


def test_vvault_server_reads_door_contract_for_origin_truth():
    source = SERVER_PATH.read_text(encoding="utf-8")

    assert "DOOR_CONTRACT_PATH" in source
    assert "def _load_chatty_vvault_door_contract()" in source
    assert "def _resolve_chatty_vvault_door()" in source
    assert "_cors_origins = _build_cors_origins()" in source
    assert '"door_contract": door' in source


@pytest.mark.parametrize(
    "value",
    [
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?next=evil",
        "https://*.example.com",
        "https://example.com:invalid",
        "https://example[.]com",
    ],
)
def test_origin_normalization_rejects_nonliteral_origins(value):
    assert server._normalize_origin(value) is None


def test_origin_normalization_accepts_literal_origins():
    assert server._normalize_origin("https://example.com/") == "https://example.com"
    assert server._normalize_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_private_door_drives_cors_and_authority(monkeypatch):
    for name in (
        "NODE_ENV",
        "VVAULT_RUNTIME_DOOR",
        "VVAULT_FRONTEND_URL",
        "VVAULT_BACKEND_URL",
        "OAUTH_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "private")
    monkeypatch.setattr(server, "_door_contract_cache", None)

    door = server._resolve_chatty_vvault_door()

    assert door["ok"] is True
    assert door["selected_door"] == "private"
    assert door["code_origin"] == "http://localhost:2048"
    assert door["code_api_origin"] == "http://127.0.0.1:2048"
    assert door["database_authority"] == "vvault_body"
    assert door["runtime_memory_authority"] == "vvault_body"
    assert door["canonical_schema"] == "ovvaults"
    assert server._build_cors_origins() == [
        "http://localhost:7784",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:2048",
        "http://127.0.0.1:2048",
    ]


def test_public_door_drives_origins_and_explicit_private_wins(monkeypatch):
    for name in (
        "CHATTY_VVAULT_DOOR",
        "VVAULT_RUNTIME_DOOR",
        "VVAULT_FRONTEND_URL",
        "VVAULT_BACKEND_URL",
        "OAUTH_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setattr(server, "_door_contract_cache", None)

    door = server._resolve_chatty_vvault_door()

    assert door["ok"] is True
    assert door["selected_door"] == "public"
    assert server._build_cors_origins() == [
        "https://vvault.thewreck.org",
        "https://chatty.thewreck.org",
        "https://code.thewreck.org",
    ]
    assert server._get_frontend_url() == "https://vvault.thewreck.org"
    assert server._get_backend_url() == "https://vvault.thewreck.org"

    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "private")
    assert server._resolve_chatty_vvault_door_name() == "private"


def test_door_contract_rejects_wrong_origin_locality(monkeypatch):
    contract = json.loads(DOOR_PATH.read_text(encoding="utf-8"))
    contract["doors"]["public"]["codePublicOrigin"] = "http://localhost:2048"
    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "public")
    monkeypatch.setattr(server, "_door_contract_cache", contract)

    door = server._resolve_chatty_vvault_door()

    assert door["ok"] is False
    assert "door_public_with_localhost_target" in door["problems"]
    with pytest.raises(RuntimeError, match="Invalid Chatty-VVAULT door contract"):
        server._build_cors_origins()


def test_door_contract_loader_fails_closed_for_missing_or_malformed_files(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing-doors.json"
    monkeypatch.setattr(server, "DOOR_CONTRACT_PATH", str(missing_path))
    monkeypatch.setattr(server, "_door_contract_cache", None)
    with pytest.raises(FileNotFoundError):
        server._load_chatty_vvault_door_contract()

    malformed_path = tmp_path / "malformed-doors.json"
    malformed_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(server, "DOOR_CONTRACT_PATH", str(malformed_path))
    with pytest.raises(json.JSONDecodeError):
        server._load_chatty_vvault_door_contract()


def test_replit_deployment_and_production_port_select_public_door(monkeypatch):
    for name in (
        "CHATTY_VVAULT_DOOR",
        "VVAULT_RUNTIME_DOOR",
        "NODE_ENV",
        "VVAULT_FRONTEND_URL",
        "VVAULT_BACKEND_URL",
        "OAUTH_BASE_URL",
        "PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("REPL_DEPLOYMENT", "1")
    assert server._resolve_chatty_vvault_door_name() == "public"

    monkeypatch.delenv("REPL_DEPLOYMENT")
    monkeypatch.setenv("PORT", "5000")
    assert server._resolve_chatty_vvault_door_name() == "public"


def test_ready_fails_closed_when_door_contract_is_invalid(monkeypatch):
    contract = json.loads(DOOR_PATH.read_text(encoding="utf-8"))
    contract["doors"]["public"]["vvaultOrigin"] = "http://localhost:8000"
    runtime_status = {
        "ready": True,
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": True,
        "connection_state": "connected",
        "runtime": {},
        "body_database": {},
        "storage": {},
        "auth": {},
    }
    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "public")
    monkeypatch.setattr(server, "_door_contract_cache", contract)
    monkeypatch.setattr(server, "_get_vvault_runtime_status", lambda: runtime_status)

    response = server.app.test_client().get("/api/ready")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["ready"] is False
    assert payload["door_contract"]["ok"] is False
    assert "door_public_with_localhost_target" in payload["door_contract"]["problems"]


def test_chatty_sibling_accepts_vvault_authority_when_available():
    sibling_path = ROOT.parent / "chatty" / "config" / "chatty-vvault-doors.json"
    if not sibling_path.is_file():
        pytest.skip("Sibling Chatty checkout is not available")

    vvault = json.loads(DOOR_PATH.read_text(encoding="utf-8"))
    chatty = json.loads(sibling_path.read_text(encoding="utf-8"))

    assert chatty["authority"]["database"] == "VVAULT"
    assert chatty["authority"]["canonical"] is True

    for door in vvault["doors"].values():
        assert chatty["authority"]["storage_mode"] == door["databaseAuthority"]
        assert door["runtimeMemoryAuthority"] == "vvault_body"
        assert chatty["body_database"]["files_owner"] == door["storageOwner"]
        assert chatty["body_database"]["transcripts_owner"] == door["transcriptOwner"]
        assert chatty["body_database"]["compatibility_fallback"] == door["transcriptCompatibilityOwner"]


def test_ready_exposes_resolved_vvault_door_contract(monkeypatch):
    runtime_status = {
        "ready": True,
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": True,
        "connection_state": "connected",
        "runtime": {},
        "body_database": {},
        "storage": {},
        "auth": {},
    }
    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "private")
    monkeypatch.setattr(server, "_door_contract_cache", None)
    monkeypatch.setattr(server, "_get_vvault_runtime_status", lambda: runtime_status)

    response = server.app.test_client().get("/api/ready")

    assert response.status_code == 200
    door = response.get_json()["door_contract"]
    assert door["ok"] is True
    assert door["selected_door"] == "private"
    assert door["database_authority"] == "vvault_body"
    assert door["storage_owner"] == "ovvaults.vault_files"
    assert response.get_json()["storage_owner"] == "ovvaults.vault_files"
    assert response.get_json()["transcript_owner"] == "ovvaults.transcripts"


def test_cors_emits_only_an_exact_configured_origin(monkeypatch):
    runtime_status = {
        "ready": True,
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": True,
        "connection_state": "connected",
        "runtime": {},
        "body_database": {},
        "storage": {},
        "auth": {},
    }
    monkeypatch.setattr(server, "_get_vvault_runtime_status", lambda: runtime_status)
    allowed_origin = server._cors_origins[0]
    client = server.app.test_client()

    allowed = client.get("/api/ready", headers={"Origin": allowed_origin})
    preflight = client.options(
        "/api/ready",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.get("/api/ready", headers={"Origin": "https://attacker.example"})

    assert allowed.headers["Access-Control-Allow-Origin"] == allowed_origin
    assert "Access-Control-Allow-Credentials" not in allowed.headers
    assert preflight.headers["Access-Control-Allow-Origin"] == allowed_origin
    assert "GET" in preflight.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_oauth_callback_origin_cannot_be_overridden_by_request_headers(monkeypatch):
    captured = {}

    class FakeAuthRepository:
        @staticmethod
        def create_oauth_transaction(**kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("CHATTY_VVAULT_DOOR", "private")
    monkeypatch.setattr(server, "_door_contract_cache", None)
    monkeypatch.setattr(server, "AUTH_REPOSITORY", FakeAuthRepository())
    monkeypatch.setattr(server, "_google_oauth_ready", lambda: True)
    monkeypatch.setattr(server, "_identity_hmac_key", lambda: "h" * 32)
    monkeypatch.setattr(server, "_identity_transaction_key", lambda: "6mP1wtOOpiMCBeihOVOJrZuOGK-R-zfkqrFHNUjUn9Q=")
    monkeypatch.setattr(server, "_rate_limit_key", lambda _route_type: None)

    response = server.app.test_client().get(
        "/api/auth/google",
        headers={
            "Origin": "https://attacker.replit.dev",
            "Referer": "https://attacker.replit.dev/login",
            "X-Forwarded-Host": "attacker.replit.dev",
            "Host": "attacker.replit.dev",
        },
    )

    assert response.status_code == 302
    assert captured["redirect_uri"] == "http://127.0.0.1:8000/api/auth/google/callback"
