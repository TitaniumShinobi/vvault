import json
from pathlib import Path


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


def test_door_contract_matches_chatty_sibling_when_available():
    sibling_candidates = [
        Path("/Users/devonwoodson/Documents/GitHub/chatty-auth-origin-clean/config/chatty-vvault-doors.json"),
        Path("/Users/devonwoodson/Documents/GitHub/chatty/config/chatty-vvault-doors.json"),
    ]

    sibling_path = next((candidate for candidate in sibling_candidates if candidate.exists()), None)
    assert sibling_path is not None, "Expected a sibling Chatty door contract for parity verification"

    assert DOOR_PATH.read_text(encoding="utf-8") == sibling_path.read_text(encoding="utf-8")
