import json
from pathlib import Path

from vvault.server import vvault_web_server as server


ROOT = Path(__file__).resolve().parents[1]
DOOR_PATH = ROOT / "config" / "chatty-vvault-doors.json"


def test_door_contract_names_vvault_body_database_authority():
    contract = json.loads(DOOR_PATH.read_text(encoding="utf-8"))

    for door_name in ("private", "public"):
        door = contract["doors"][door_name]
        assert door["codePublicOrigin"]
        assert door["codeApiOrigin"]
        assert door["databaseAuthority"] == "vvault_body"
        assert door["runtimeMemoryAuthority"] == "vvault_body"
        assert door["canonicalSchema"] == "ovvaults"
        assert door["storageOwner"] == "ovvaults.vault_files"
        assert door["transcriptOwner"] == "ovvaults.transcripts"
        assert door["transcriptCompatibilityOwner"] == "ovvaults.vault_files"
        assert door["sessionBridgePath"] == "/api/vault/session-bridge"
        assert door["authCookieName"] == "auth_sid"
        assert door["allowLegacyExchange"] is False


def test_vvault_server_has_current_body_database_runtime_symbols():
    source = (ROOT / "vvault" / "server" / "vvault_web_server.py").read_text(encoding="utf-8")

    assert "import chatty_body_service" in source
    assert "def _body_database_dependency_status" in source
    assert "chatty_body_service._connect()" in source


def test_code_vvault_handshake_endpoint_exposes_body_database_contract(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    body_status = {
        "authority": "vvault_body",
        "canonical": True,
        "canonical_schema": "ovvaults",
        "configured": True,
        "connection_state": "connected",
        "read_path": "body_database_compatibility",
        "ready": True,
        "schema": "ovvaults",
        "source_database": "vvault_body_20260504t123219z",
        "status": "healthy",
        "storage_mode": "vvault_body",
        "checks": {
            "vault_files_readable": True,
            "transcripts_readable": False,
        },
    }
    private_door = {
        "ok": True,
        "selected_door": "private",
        "code_origin": "http://localhost:2048",
        "code_api_origin": "http://127.0.0.1:2048",
        "vvault_origin": "http://127.0.0.1:8000",
        "session_bridge_path": "/api/vault/session-bridge",
        "auth_cookie_name": "auth_sid",
        "storage_owner": "ovvaults.vault_files",
        "transcript_owner": "ovvaults.transcripts",
        "transcript_compatibility_owner": "ovvaults.vault_files",
    }

    monkeypatch.setattr(server, "_body_database_dependency_status", lambda: body_status)
    monkeypatch.setattr(server, "_resolve_chatty_vvault_door", lambda: private_door, raising=False)
    response = client.get("/api/code/handshake")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["client"] == "code"
    assert payload["authority"] == "vvault_body"
    assert payload["code_origin"] == "http://localhost:2048"
    assert payload["code_api_origin"] == "http://127.0.0.1:2048"
    assert payload["storage_owner"] == "ovvaults.vault_files"
    assert payload["transcript_owner"] == "ovvaults.transcripts"
    assert payload["transcript_compatibility_owner"] == "ovvaults.vault_files"
    assert payload["body_database"]["checks"]["vault_files_readable"] is True
