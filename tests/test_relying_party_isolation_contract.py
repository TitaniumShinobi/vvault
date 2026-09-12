from pathlib import Path

from vvault.server import relying_party_scope
from vvault.server.vault_drive_repository import VaultDriveRepository


def test_verified_scope_rejects_header_like_untrusted_values():
    relying_party_scope.set_relying_party_id("chatty")
    assert relying_party_scope.current_relying_party_id() == "chatty"
    try:
        relying_party_scope.set_relying_party_id("x-chatty-client: chatty-cli")
    except ValueError:
        pass
    else:
        raise AssertionError("untrusted request value must not select a relying party")
    assert relying_party_scope.current_relying_party_id() == "chatty"
    relying_party_scope.set_relying_party_id("vvault")


def test_isolation_migration_is_additive_and_routed_by_verified_database_scope():
    migration = Path(__file__).parents[1] / "vvault" / "migrations" / "0037_relying_party_data_isolation.up.sql"
    assert migration.is_file()
    sql = migration.read_text()
    assert "DEFAULT 'chatty'" in sql
    assert "app.vvault_relying_party_id" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "cross-relying-party mutation is not permitted" in sql
    assert "(relying_party_id, user_id, anatomy_id)" in sql
    assert "vault_drive_nodes_relying_party_active_path_idx" in sql
    assert "vault_drive_nodes_relying_party_active_sibling_idx" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql


def test_workspace_projection_cache_is_partitioned_by_verified_relying_party(monkeypatch):
    repository = VaultDriveRepository()
    calls: list[str] = []

    def roots(owner_user_id: str):
        calls.append(relying_party_scope.current_relying_party_id())
        return [{"root_name": f"{relying_party_scope.current_relying_party_id()}-only"}]

    monkeypatch.setattr(repository, "_fetch_workspace_roots", roots)

    relying_party_scope.set_relying_party_id("chatty")
    chatty_first = repository.workspace_root(owner_user_id="owner", constructs=[])
    chatty_second = repository.workspace_root(owner_user_id="owner", constructs=[])

    relying_party_scope.set_relying_party_id("chatty-cli")
    cli_first = repository.workspace_root(owner_user_id="owner", constructs=[])
    cli_second = repository.workspace_root(owner_user_id="owner", constructs=[])

    assert calls == ["chatty", "chatty-cli"]
    assert chatty_first["materializedRootNames"] == ["chatty-only"]
    assert chatty_second["cacheState"] == "fresh"
    assert cli_first["materializedRootNames"] == ["chatty-cli-only"]
    assert cli_second["cacheState"] == "fresh"
    relying_party_scope.set_relying_party_id("vvault")
