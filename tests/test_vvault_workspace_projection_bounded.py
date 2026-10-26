from pathlib import Path

from vvault.server import chatty_body_service, relying_party_scope


def test_workspace_projection_avoids_generic_query_canceled_path(monkeypatch):
    observed_scopes: list[str] = []

    def fail_generic_projection(*_args, **_kwargs):
        raise RuntimeError("QueryCanceled")

    def fake_rows(sql, params):
        lane = relying_party_scope.current_relying_party_id()
        observed_scopes.append(lane)
        assert "WHERE user_id=%s" in sql
        assert "regexp_match" not in sql
        assert "projectionExcluded" in sql
        assert params == ("owner", lane, "owner", lane)
        return [{
            "construct_id": f"{lane}-001",
            "is_system": False,
            "storage_path": f"instances/{lane}-001/identity/prompt.json",
            "content": {"displayName": lane},
        }]

    monkeypatch.setattr(chatty_body_service, "list_constructs", fail_generic_projection)
    monkeypatch.setattr(chatty_body_service, "_vault_files_supports_relying_party_partition", lambda: True)
    monkeypatch.setattr(chatty_body_service, "_rows", fake_rows)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status == 200
    assert observed_scopes == ["chatty", "chatty-cli", "vvault"]
    assert relying_party_scope.current_relying_party_id() == "vvault"
    assert [item["sourceRelyingPartyId"] for item in result.payload["constructs"]] == [
        "chatty",
        "chatty-cli",
        "vvault",
    ]


def test_workspace_projection_preserves_visibility_and_name_precedence(monkeypatch):
    def fake_rows(sql, _params):
        if relying_party_scope.current_relying_party_id() != "vvault":
            return []
        return [
            {
                "construct_id": "codegpt-001", "is_system": True,
                "storage_path": "instances/codegpt-001/config/metadata.json",
                "content": {"display_name": "Excluded code definition"},
            },
            {
                "construct_id": "katana-001", "is_system": False,
                "storage_path": "instances/katana-001/identity/prompt.json",
                "content": {"displayName": "Hidden selector"},
            },
            {
                "construct_id": "zen-001",
                "is_system": True,
                "storage_path": "instances/zen-001/config/metadata.json",
                "filename": "/vvault_files/users/shard_0000/owner/instances/zen-001/config/metadata.json",
                "content": {"display_name": "Config name"},
            },
            {
                "construct_id": "zen-001",
                "is_system": True,
                "storage_path": "instances/zen-001/identity/prompt.json",
                "filename": "/vvault_files/users/shard_0000/owner/instances/zen-001/identity/prompt.json",
                "content": {"displayName": "Canonical prompt name"},
            },
        ]

    monkeypatch.setattr(chatty_body_service, "_vault_files_supports_relying_party_partition", lambda: True)
    monkeypatch.setattr(chatty_body_service, "_rows", fake_rows)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status == 200
    assert [item["callsign"] for item in result.payload["constructs"]] == ["zen-001"]
    assert result.payload["constructs"][0]["displayName"] == "Canonical prompt name"


def test_workspace_projection_sql_is_owner_and_construct_bounded(monkeypatch):
    statements = []

    def fake_rows(sql, params):
        statements.append((sql, params))
        return [{"construct_id": "zen-001", "is_system": False,
                 "storage_path": "instances/zen-001/identity/prompt.json", "content": {}}]

    monkeypatch.setattr(chatty_body_service, "_rows", fake_rows)
    monkeypatch.setattr(chatty_body_service, "_vault_files_supports_relying_party_partition", lambda: False)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status == 200
    assert len(statements) == 1
    for sql, params in statements:
        assert "WHERE user_id=%s" in sql
        assert "regexp_match" not in sql
        assert "storage_path ||" not in sql
    identity_sql, identity_params = statements[0]
    assert "storage_path = ANY(ARRAY(SELECT path FROM identity_paths))" in identity_sql
    assert identity_params == ("owner", "owner")


def test_workspace_projection_fails_closed_and_restores_scope(monkeypatch):
    class QueryCanceled(RuntimeError):
        pass

    def query_canceled(_sql, _params):
        raise QueryCanceled("statement timeout")

    monkeypatch.setattr(chatty_body_service, "_rows", query_canceled)
    monkeypatch.setattr(chatty_body_service, "_vault_files_supports_relying_party_partition", lambda: False)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status != 200
    assert "QueryCanceled" in result.payload["reason"]
    assert relying_party_scope.current_relying_party_id() == "vvault"


def test_rollback_verifies_current_and_previous_release_git_heads():
    source = Path("scripts/deployment/vvault-immutable-rollback.sh").read_text()
    assert 'git -c safe.directory="$current_path" -C "$current_path" rev-parse HEAD' in source
    assert '== "$expected_current"' in source
    assert 'git -c safe.directory="$previous_path" -C "$previous_path" rev-parse HEAD' in source
    assert '== "$previous"' in source


def test_live_gate_checks_chatty_and_second_account_workspace_paths():
    source = Path("scripts/deployment/vvault-workspace-live-gate.sh").read_text()
    assert "/api/chatty/constructs" in source
    assert "/api/chatty/construct/$VVAULT_CONSTRUCT_ID/files" in source
    assert 'VVAULT_SECONDARY_COOKIE_JAR" "$tmp_dir/secondary-root.json' in source
    assert "second-account isolation failed" in source
