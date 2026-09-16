from pathlib import Path

from vvault.server import chatty_body_service
from vvault.server.relying_party_scope import current_relying_party_id, set_relying_party_id
from vvault.server.vault_drive_repository import VaultDriveRepository


def test_workspace_root_has_only_contracted_roots_and_retains_lane_provenance():
    result = VaultDriveRepository().workspace_root(
        owner_user_id="owner-a",
        constructs=[
            {
                "callsign": "zen-001",
                "displayName": "Zen",
                "sourceRelyingPartyId": "chatty",
            }
        ],
    )

    assert [child["name"] for child in result["children"]] == [
        "account", "instances", "library"
    ]
    instance = result["children"][1]["childrenPreview"][0]
    assert instance["constructId"] == "zen-001"
    assert instance["sourceRelyingPartyId"] == "chatty"
    assert "system" not in str(result).lower()


def test_workspace_root_lane_qualifies_duplicate_construct_cards():
    result = VaultDriveRepository().workspace_root(
        owner_user_id="owner-a",
        constructs=[
            {"callsign": "zen-001", "displayName": "Zen", "sourceRelyingPartyId": lane}
            for lane in ("chatty", "chatty-cli", "vvault")
        ],
    )

    instances = result["children"][1]["childrenPreview"]
    assert [item["nodeId"] for item in instances] == [
        "instance:chatty:zen-001",
        "instance:chatty-cli:zen-001",
        "instance:vvault:zen-001",
    ]
    assert [item["name"] for item in instances] == [
        "Zen (chatty)",
        "Zen (chatty-cli)",
        "Zen (vvault)",
    ]


def test_workspace_routes_derive_owner_and_lane_server_side():
    source = Path("vvault/server/vvault_web_server.py").read_text()

    assert "@app.route('/api/vault/drive/workspace-root')" in source
    assert "def get_vault_drive_workspace_root" in source
    assert "_get_authenticated_user_id()" in source
    assert "ownerIdentifiersProjected\": False" in source
    assert "def _workspace_construct_for_authenticated_owner" in source
    helper = source.split(
        "def _workspace_construct_for_authenticated_owner", 1
    )[1].split("def _vault_drive_error_response", 1)[0]
    assert "sourceScope" not in helper
    assert 'request.args.get("workspaceRef")' in helper
    assert "_workspace_ref_serializer().loads(workspace_ref)" in helper
    assert "workspace reference does not belong to this account" in helper
    assert "with _native_vault_drive_read_scope(source_scope):" in source
    browser = Path("src/components/VaultBrowser.js").read_text()
    assert "workspaceRef" in browser
    assert "parentNodeId: nodeId || 'root', workspaceRef" in browser


def test_plain_instances_route_is_a_workspace_projection_not_a_legacy_folder():
    browser = Path("src/components/VaultBrowser.js").read_text()

    assert "mode: 'instances'" in browser
    assert "['home', 'instances'].includes(routeState.mode)" in browser
    assert "fetchWorkspaceRoot" in browser
    assert "semanticKind === 'instances_root'" in browser
    assert "childrenPreview" in browser
    assert "sourceScope" in browser
    assert "No instances are available" in browser
    assert "routeState.mode === 'instances' ? 'No instances are available' : 'This folder is empty'" in browser


def test_workspace_projection_queries_are_owner_scoped():
    source = Path("vvault/server/chatty_body_service.py").read_text()
    helper = source.split("def list_constructs_for_vvault_workspace", 1)[1].split(
        "def construct_files", 1
    )[0]

    assert "WHERE user_id=%s" in helper
    assert 'for lane in ("chatty", "chatty-cli", "vvault")' in helper
    assert "set_relying_party_id(lane)" in helper


def test_workspace_projection_keeps_owner_and_lane_bound(monkeypatch):
    calls = []

    def fake_rows(sql, params):
        calls.append((current_relying_party_id(), params))
        return [{
            "id": "row", "filename": "instances/zen-001/chatty/thread.md",
            "storage_path": "instances/zen-001/chatty/thread.md",
            "object_key": "instances/zen-001/chatty/thread.md",
            "construct_id": "zen-001",
            "metadata": {},
            "created_at": None,
        }]

    monkeypatch.setattr(chatty_body_service, "_rows", fake_rows)
    set_relying_party_id("vvault")
    result = chatty_body_service.list_constructs_for_vvault_workspace("owner-a")

    assert result.http_status == 200
    assert [lane for lane, _ in calls] == ["chatty", "chatty-cli", "vvault"]
    assert all(params[0] == "owner-a" for _, params in calls)
    assert current_relying_party_id() == "vvault"
    assert {item["sourceRelyingPartyId"] for item in result.payload["constructs"]} == {
        "chatty", "chatty-cli", "vvault"
    }
