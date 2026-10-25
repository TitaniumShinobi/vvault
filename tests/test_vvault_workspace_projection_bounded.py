from vvault.server import chatty_body_service, relying_party_scope


def test_workspace_projection_avoids_generic_query_canceled_path(monkeypatch):
    observed_scopes: list[str] = []

    def fail_generic_projection(*_args, **_kwargs):
        raise RuntimeError("QueryCanceled")

    def fake_rows(sql, params):
        lane = relying_party_scope.current_relying_party_id()
        observed_scopes.append(lane)
        assert params == ("owner",)
        assert "SELECT construct_id" in sql
        assert "user_id = %s" in sql
        assert "projectionExcluded" in sql
        assert "regexp_match" not in sql
        return [
            {
                "construct_id": f"{lane}-001",
                "filename": f"instances/{lane}-001/identity/prompt.json",
                "content": {"displayName": lane},
                "metadata": {},
                "is_system": False,
            }
        ]

    monkeypatch.setattr(chatty_body_service, "list_constructs", fail_generic_projection)
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
    def fake_rows(_sql, _params):
        if relying_party_scope.current_relying_party_id() != "vvault":
            return []
        return [
            {
                "construct_id": "codegpt-001",
                "filename": "instances/codegpt-001/config/metadata.json",
                "content": {"display_name": "Code definition"},
                "metadata": {},
                "is_system": True,
            },
            {
                "construct_id": "katana-001",
                "filename": "instances/katana-001/identity/prompt.json",
                "content": {"displayName": "Hidden selector"},
                "metadata": {},
                "is_system": False,
            },
            {
                "construct_id": "zen-001",
                "filename": "instances/zen-001/identity/avatar.png",
                "content": None,
                "metadata": {"displayName": "Misleading newest row"},
                "is_system": True,
            },
            {
                "construct_id": "zen-001",
                "filename": "instances/zen-001/config/metadata.json",
                "content": {"display_name": "Config name"},
                "metadata": {},
                "is_system": True,
            },
            {
                "construct_id": "zen-001",
                "filename": "instances/zen-001/identity/prompt.json",
                "content": {"displayName": "Canonical prompt name"},
                "metadata": {},
                "is_system": True,
            },
        ]

    monkeypatch.setattr(chatty_body_service, "_rows", fake_rows)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status == 200
    assert [item["callsign"] for item in result.payload["constructs"]] == ["zen-001"]
    assert result.payload["constructs"][0]["displayName"] == "Canonical prompt name"


def test_workspace_projection_fails_closed_and_restores_scope(monkeypatch):
    class QueryCanceled(RuntimeError):
        pass

    def query_canceled(_sql, _params):
        raise QueryCanceled("statement timeout")

    monkeypatch.setattr(chatty_body_service, "_rows", query_canceled)
    relying_party_scope.set_relying_party_id("vvault")

    result = chatty_body_service.list_constructs_for_vvault_workspace("owner")

    assert result.http_status != 200
    assert "QueryCanceled" in result.payload["reason"]
    assert relying_party_scope.current_relying_party_id() == "vvault"
