from pathlib import Path
import time

import pytest

from vvault.server.ovvaults_migrations import MIGRATIONS_DIR, OvvaultsMigrationRunner
from vvault.server.vault_drive_repository import VaultDriveRepository


def test_authoritative_runner_discovers_drive_migration_and_immutable_receipts():
    migrations = OvvaultsMigrationRunner().migrations()
    migration = next(path for path in migrations if path.stem == "0013_vault_drive_nodes.up")
    assert migration.parent == MIGRATIONS_DIR
    sql = migration.read_text()
    assert "CREATE TABLE IF NOT EXISTS ovvaults.vault_drive_nodes" in sql
    assert "parent_node_id uuid REFERENCES ovvaults.vault_drive_nodes(id)" in sql
    assert "vault_drive_nodes_active_sibling_idx" in sql
    assert "drive_parent_node_id uuid REFERENCES ovvaults.vault_drive_nodes(id)" in sql
    assert "vault_drive_operation_receipts" in sql
    assert "BEFORE UPDATE OR DELETE" in sql


def test_authoritative_runner_discovers_full_trash_migration():
    migrations = OvvaultsMigrationRunner().migrations()
    migration = next(path for path in migrations if path.stem == "0014_vault_drive_trash.up")
    assert migration.parent == MIGRATIONS_DIR
    sql = migration.read_text()
    assert "batch_trash" in sql
    assert "permanent_delete" in sql
    assert "vault_drive_nodes_trash_idx" in sql
    assert "vault_files_drive_trash_idx" in sql


def test_drive_migration_preserves_legacy_rows_and_backfills_parent_links():
    sql = (MIGRATIONS_DIR / "0013_vault_drive_nodes.up.sql").read_text()
    assert "UPDATE ovvaults.vault_files file" in sql
    assert "SET drive_parent_node_id = parent.id" in sql
    assert "DELETE FROM ovvaults.vault_files" not in sql
    assert "UPDATE ovvaults.vault_files SET filename" not in sql


def test_folder_names_fail_closed_on_traversal_and_slashes():
    assert VaultDriveRepository._name("June") == "June"
    for value in ("", ".", "..", "../config", "June/July", "June\\July"):
        with pytest.raises(ValueError):
            VaultDriveRepository._name(value)


def test_child_dtos_are_stable_and_files_expose_file_id():
    folder = VaultDriveRepository._node_dto({
        "id": "folder-id", "name": "2025", "construct_id": "monday-001",
        "parent_node_id": "chatgpt-id", "semantic_kind": "folder",
        "protected": False, "trashed_at": None,
    })
    file_item = VaultDriveRepository._file_dto({
        "id": "file-id", "filename": "instances/monday-001/chatgpt/2025/June/a.txt",
        "construct_id": "monday-001", "drive_parent_node_id": "june-id",
        "size_bytes": 12, "sha256": "a" * 64, "file_type": "text/plain",
    })
    assert folder == {
        "nodeId": "folder-id", "nodeType": "folder", "name": "2025",
        "parentNodeId": "chatgpt-id", "constructId": "monday-001",
        "semanticKind": "folder", "protected": False, "trashed": False,
        "createdAt": None, "updatedAt": None,
    }
    assert file_item["nodeId"] == file_item["fileId"] == "file-id"
    assert file_item["name"] == "a.txt"
    assert file_item["parentNodeId"] == "june-id"

    protected_file = VaultDriveRepository._file_dto({
        "id": "prompt", "filename": "instances/monday-001/identity/prompt.json",
        "construct_id": "monday-001",
    })
    assert protected_file["protected"] is True


def test_warm_children_cache_is_bounded_and_sub_millisecond_without_database():
    repository = VaultDriveRepository()
    key = ("vvault", "owner", "monday-001", "root")
    repository._cache[key] = (time.monotonic(), {
        "parentNode": {"nodeId": "root"}, "breadcrumbs": [], "children": [],
        "count": 0, "cacheState": "miss", "refreshing": False,
    })
    started = time.perf_counter()
    for _ in range(1000):
        result = repository.children(
            owner_user_id="owner", construct_id="monday-001", parent_node_id="root"
        )
        assert result["cacheState"] == "fresh"
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 100


def test_workspace_root_exposes_only_contracted_roots_and_keeps_constructs_owner_scoped():
    repository = VaultDriveRepository()
    result = repository.workspace_root(
        owner_user_id="owner-a",
        constructs=[
            {"callsign": "zen-001", "displayName": "Zen"},
            {"callsign": "hello-001", "displayName": "Hello"},
        ],
    )
    assert [item["name"] for item in result["children"]] == [
        "account", "instances", "library"
    ]
    assert [item["name"] for item in result["children"][1]["childrenPreview"]] == [
        "Hello", "Zen"
    ]
    assert [item["logicalPath"] for item in result["children"][2]["childrenPreview"]] == [
        "library/assets", "library/gallery", "library/documents"
    ]
    serialized = str(result)
    assert "owner-a" not in serialized
    assert result["projectionSchemaVersion"] == "1.1.0"


def test_workspace_root_route_is_authenticated_and_owner_redacted():
    source = Path("vvault/server/vvault_web_server.py").read_text()
    assert "@app.route('/api/vault/drive/workspace-root')" in source
    route = source.split("def get_vault_drive_workspace_root", 1)[1].split("@app.route", 1)[0]
    assert "_get_authenticated_user_id()" in route
    assert '"ownerIdentifiersProjected": False' in route
    assert "chatty_body_service.list_constructs_for_vvault_workspace(" in route
    children_route = source.split("def get_vault_drive_children", 1)[1].split("@app.route", 1)[0]
    assert "with _native_vault_drive_read_scope():" in children_route
    assert "sourceScope" in Path("src/components/VaultBrowser.js").read_text()


def test_workspace_root_never_promotes_raw_storage_prefixes_to_browser_folders():
    repository = VaultDriveRepository()
    result = repository.workspace_root(
        owner_user_id="owner-a",
        constructs=[
            {
                "callsign": "zen-001",
                "displayName": "Zen",
                "sourceRelyingPartyId": "chatty",
            },
        ],
    )
    assert [item["name"] for item in result["children"]] == ["account", "instances", "library"]
    instance = result["children"][1]["childrenPreview"][0]
    assert instance["sourceRelyingPartyId"] == "chatty"
    assert "system" not in str(result).lower()


def test_upload_destination_is_canonical_ancestry_not_client_label():
    source = Path("vvault/server/vvault_web_server.py").read_text()
    assert "destinationFolderId" in source
    assert "VAULT_DRIVE_REPOSITORY.upload_context" in source
    assert "upload_kind conflicts with the canonical destination folder" in source
    assert "VAULT_DRIVE_REPOSITORY.ensure_upload_path" in source
    assert "canonical_directory = canonical_relative.rsplit('/', 1)[0]" in source
    assert "VAULT_DRIVE_REPOSITORY.bind_file" in source
    assert '"uploadReceipts": upload_receipts' in source
    assert '"operationReceipt": operation_receipt' in source


def test_upload_classification_uses_folder_ancestry_and_rejects_instance_root():
    transcript = VaultDriveRepository._upload_context_for_folder(
        "monday-001",
        {"id": "june", "logical_path": "instances/monday-001/chatgpt/2025/June"},
    )
    assert transcript == {
        "destinationNodeId": "june",
        "logicalPath": "instances/monday-001/chatgpt/2025/June",
        "uploadKind": "transcript",
        "knowledgeDestination": None,
        "provider": "chatgpt",
    }
    document = VaultDriveRepository._upload_context_for_folder(
        "monday-001",
        {"id": "legal", "logical_path": "instances/monday-001/documents/legal"},
    )
    assert document["uploadKind"] == "knowledge"
    assert document["knowledgeDestination"] == "documents"
    with pytest.raises(ValueError, match="assets, documents, or transcript-provider"):
        VaultDriveRepository._upload_context_for_folder(
            "monday-001",
            {"id": "root", "logical_path": "instances/monday-001"},
        )


def test_drive_routes_include_direct_children_and_mutation_receipts():
    source = Path("vvault/server/vvault_web_server.py").read_text()
    assert "@app.route('/api/vault/drive/children')" in source
    assert "@app.route('/api/vault/drive/folders', methods=['POST'])" in source
    assert "@app.route('/api/vault/drive/nodes/<node_id>', methods=['PATCH'])" in source
    assert "@app.route('/api/vault/drive/nodes/<node_id>', methods=['DELETE'])" in source
    assert "@app.route('/api/vault/drive/nodes/<node_id>/restore', methods=['POST'])" in source
    assert "VAULT_DRIVE_REPOSITORY.mutate_node" in source
    assert source.count("VAULT_DRIVE_REPOSITORY.set_node_trashed") == 2


def test_drive_routes_expose_atomic_batch_trash_restore_move_and_confirmed_permanent_delete():
    source = Path("vvault/server/vvault_web_server.py").read_text()
    for route in (
        "/api/vault/drive/trash",
        "/api/vault/drive/batch/move",
        "/api/vault/drive/batch/trash",
        "/api/vault/drive/batch/restore",
        "/api/vault/drive/batch/permanent-delete",
        "/api/vault/drive/files/<file_id>/download",
    ):
        assert route in source
    assert 'payload.get("confirmation") != "PERMANENTLY DELETE"' in source
    repository = Path("vvault/server/vault_drive_repository.py").read_text()
    assert "def trash_nodes_atomic" in repository
    assert "def restore_nodes_atomic" in repository
    assert "def move_nodes_atomic" in repository
    assert "def permanently_delete_nodes" in repository
    assert "protected semantic core files cannot be trashed" in repository


def test_real_vault_ui_has_visible_drive_and_trash_product_actions():
    source = Path("src/components/VaultBrowser.js").read_text()
    for label in (
        "New folder", "File upload", "Folder upload", "Download",
        "Move to trash", "Restore all", "Permanent delete", "Empty trash",
    ):
        assert label in source
    assert "webkitdirectory" in source
    assert "selectedNodeIds" in source
    assert "/api/vault/drive/batch/move" in source
    assert "/api/vault/drive/batch/trash" in source
    assert "/api/vault/drive/batch/restore" in source
    assert "window.confirm" in source
    server_source = Path("vvault/server/vvault_web_server.py").read_text()
    assert "VAULT_DRIVE_REPOSITORY.ensure_construct_root" in server_source
    assert '"driveRoot": drive_root' in server_source


def test_legacy_my_ai_files_url_resolves_to_home_without_a_sidebar_entry():
    source = Path("src/components/VaultBrowser.js").read_text()
    assert "segments[0] === 'my-ai-files'" in source
    assert "return { mode: 'home', constructId: '', nodeId: '', legacyPath: [] }" in source
    assert "navigate('/vault/my-ai-files')" not in source
    assert "onClick={navigateMyAiFiles}" not in source


def test_home_loads_and_projects_the_canonical_account_root():
    source = Path("src/components/VaultBrowser.js").read_text()
    assert "authFetch('/api/vault/drive/workspace-root')" in source
    assert "['home', 'instances'].includes(routeState.mode)) fetchWorkspaceRoot()" in source
    assert "workspaceState.children" in source
    assert "const folderEntries = routeState.mode === 'home'" in source
    assert "const fileList = routeState.mode === 'home'" in source
    assert "navigate(vaultLocationForLegacyPath([...currentPath, folderName]))" in source


def test_instances_workspace_preview_never_routes_to_the_legacy_empty_folder():
    source = Path("src/components/VaultBrowser.js").read_text()
    assert "segments[0] === 'instances' && !segments[1]" in source
    assert "return { mode: 'instances', constructId: '', nodeId: '', legacyPath: ['instances'] }" in source
    assert "folder?.semanticKind === 'instances_root'" in source
    assert "navigate('/vault/instances')" in source
    assert "const instanceEntries = Array.isArray(instanceRoot?.childrenPreview)" in source


def test_soft_deleted_rows_are_excluded_from_normal_and_chatty_projections():
    repository = Path("vvault/server/vvault_file_repository.py").read_text()
    chatty = Path("vvault/server/chatty_body_service.py").read_text()
    assert repository.count("drive_trashed_at IS NULL") >= 7
    assert "WHERE user_id = %s\n                  AND drive_trashed_at IS NULL" in chatty
    assert "prompt.drive_trashed_at IS NULL" in chatty


def test_code_owned_definitions_are_excluded_by_explicit_scope_not_basename():
    repository = Path("vvault/server/vvault_file_repository.py").read_text()
    server = Path("vvault/server/vvault_web_server.py").read_text()
    service = Path("vvault/server/chatty_body_service.py").read_text()
    classification = Path("vvault/server/projection_classification.py").read_text()
    assert "coalesce(is_system, false) = false" in repository
    assert "PROJECTABLE_METADATA_SQL" in repository
    assert "PROJECTABLE_METADATA_SQL" in service
    assert "projectionExcluded" in classification
    assert '"projectableToChatty"] = True' in service
    assert '"projectionKind"] = "owner_construct"' in service
    assert "never synthesized into user-facing DTOs" in service
    assert server.count("VVAULT_CONSTRUCT_NOT_PROJECTABLE") >= 5


def test_file_node_mutations_preserve_body_hash_and_timestamps_by_contract():
    source = Path("vvault/server/vault_drive_repository.py").read_text()
    assert "protected semantic core files cannot be renamed or moved" in source
    assert "protected semantic core files cannot be trashed" in source
    assert "SET filename=%s,storage_path=%s" in source
    assert "drive_parent_node_id=%s,updated_at=updated_at" in source
    assert "UPDATE ovvaults.vault_files SET drive_trashed_at=%s,updated_at=updated_at" in source
    # File mutation SQL must never rewrite receipt-bound content or SHA columns.
    file_mutation = source.split("def mutate_node", 1)[1].split("def set_trashed", 1)[0]
    assert "SET content=" not in file_mutation
    assert "SET sha256=" not in file_mutation
