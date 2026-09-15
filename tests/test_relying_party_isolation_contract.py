from pathlib import Path

from vvault.server import relying_party_scope
from vvault.server import chatty_body_service
from vvault.server.chatty_body_service import BodyResult
from vvault.server.ovvaults_migrations import MIGRATIONS_DIR, OvvaultsMigrationRunner
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
    assert any(path.name == "0034_relying_party_data_isolation.up.sql" for path in OvvaultsMigrationRunner().migrations())
    sql = (MIGRATIONS_DIR / "0034_relying_party_data_isolation.up.sql").read_text()
    assert "DEFAULT 'chatty'" in sql
    assert "app.vvault_relying_party_id" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "cross-relying-party mutation is not permitted" in sql
    assert "(relying_party_id, user_id, anatomy_id)" in sql
    assert "vault_drive_nodes_relying_party_active_path_idx" in sql
    assert "vault_drive_nodes_relying_party_active_sibling_idx" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql


def test_projection_cache_keys_are_partitioned_for_same_owner_and_construct():
    relying_party_scope.set_relying_party_id("chatty")
    chatty_key = chatty_body_service._relying_party_cache_key("owner", "zen-001", "same")
    relying_party_scope.set_relying_party_id("chatty-cli")
    cli_key = chatty_body_service._relying_party_cache_key("owner", "zen-001", "same")
    assert chatty_key != cli_key
    assert chatty_key[0] == "chatty"
    assert cli_key[0] == "chatty-cli"
    relying_party_scope.set_relying_party_id("vvault")


def test_projection_cache_invalidation_preserves_the_other_relying_party():
    """A Chatty mutation must not evict or read Chatty CLI's same-owner cache."""
    owner = "owner"
    callsign = "zen-001"
    chatty_body_service._memory_projection_cache.clear()
    chatty_body_service._transcript_projection_cache.clear()
    chatty_body_service._construct_list_cache.clear()

    relying_party_scope.set_relying_party_id("chatty")
    chatty_memory_key = chatty_body_service._memory_cache_key(owner, callsign)
    chatty_transcript_key = chatty_body_service._relying_party_cache_key(owner, callsign, None)
    chatty_list_key = chatty_body_service._relying_party_cache_key(owner, False)
    chatty_body_service._memory_projection_cache[chatty_memory_key] = {"cached_at": 0}
    chatty_body_service._transcript_projection_cache[chatty_transcript_key] = (0, object())
    chatty_body_service._construct_list_cache[chatty_list_key] = (0, object())

    relying_party_scope.set_relying_party_id("chatty-cli")
    cli_memory_key = chatty_body_service._memory_cache_key(owner, callsign)
    cli_transcript_key = chatty_body_service._relying_party_cache_key(owner, callsign, None)
    cli_list_key = chatty_body_service._relying_party_cache_key(owner, False)
    chatty_body_service._memory_projection_cache[cli_memory_key] = {"cached_at": 0}
    chatty_body_service._transcript_projection_cache[cli_transcript_key] = (0, object())
    chatty_body_service._construct_list_cache[cli_list_key] = (0, object())

    relying_party_scope.set_relying_party_id("chatty")
    chatty_body_service.invalidate_construct_projection_caches(owner, callsign)

    assert chatty_memory_key not in chatty_body_service._memory_projection_cache
    assert chatty_transcript_key not in chatty_body_service._transcript_projection_cache
    assert chatty_list_key not in chatty_body_service._construct_list_cache
    assert cli_memory_key in chatty_body_service._memory_projection_cache
    assert cli_transcript_key in chatty_body_service._transcript_projection_cache
    assert cli_list_key in chatty_body_service._construct_list_cache
    relying_party_scope.set_relying_party_id("vvault")


def test_workspace_projection_cache_is_partitioned_by_verified_relying_party(monkeypatch):
    repository = VaultDriveRepository()

    relying_party_scope.set_relying_party_id("chatty")
    chatty_first = repository.workspace_root(owner_user_id="owner", constructs=[])
    chatty_second = repository.workspace_root(owner_user_id="owner", constructs=[])

    relying_party_scope.set_relying_party_id("chatty-cli")
    cli_first = repository.workspace_root(owner_user_id="owner", constructs=[])
    cli_second = repository.workspace_root(owner_user_id="owner", constructs=[])

    assert [node["name"] for node in chatty_first["children"]] == ["account", "instances", "library"]
    assert chatty_second["cacheState"] == "fresh"
    assert [node["name"] for node in cli_first["children"]] == ["account", "instances", "library"]
    assert cli_second["cacheState"] == "fresh"
    relying_party_scope.set_relying_party_id("vvault")


def test_native_vvault_workspace_merges_verified_lanes_and_restores_scope(monkeypatch):
    observed_scopes: list[str] = []

    def fake_list_constructs(user_id, **_kwargs):
        lane = relying_party_scope.current_relying_party_id()
        observed_scopes.append(lane)
        return BodyResult(
            status="body_native",
            route="/api/chatty/constructs",
            source_database="test",
            payload={
                "constructs": [{"callsign": f"{lane}-001", "displayName": lane}],
            },
        )

    monkeypatch.setattr(chatty_body_service, "list_constructs", fake_list_constructs)
    relying_party_scope.set_relying_party_id("vvault")
    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert observed_scopes == ["chatty", "chatty-cli", "vvault"]
    assert relying_party_scope.current_relying_party_id() == "vvault"
    assert [item["sourceRelyingPartyId"] for item in result.payload["constructs"]] == [
        "chatty", "chatty-cli", "vvault"
    ]
