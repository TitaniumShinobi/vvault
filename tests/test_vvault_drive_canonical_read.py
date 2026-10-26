import pytest

from vvault.server import vvault_web_server
from vvault.server.vault_drive_repository import VaultDriveRepository


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _row(path, *, content_type="application/json", file_type="text"):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "filename": path,
        "storage_path": path,
        "construct_id": "zen-001",
        "drive_parent_node_id": None,
        "drive_trashed_at": None,
        "size_bytes": 42,
        "sha256": "a" * 64,
        "content_type": content_type,
        "file_type": file_type,
        "created_at": None,
        "updated_at": None,
    }


def test_owner_can_navigate_projected_identity_and_prompt(monkeypatch):
    cursor = _Cursor([_row("instances/zen-001/identity/prompt.json")])
    repository = VaultDriveRepository()
    monkeypatch.setattr(repository, "_connect", lambda: _Connection(cursor))

    root = repository.projected_children(
        owner_user_id="owner-a", construct_id="zen-001", logical_path="instances/zen-001"
    )
    assert ("folder", "identity") in [
        (item["nodeType"], item["name"]) for item in root["children"]
    ]
    identity = repository.projected_children(
        owner_user_id="owner-a",
        construct_id="zen-001",
        logical_path="instances/zen-001/identity",
    )
    assert [(item["nodeType"], item["name"]) for item in identity["children"]] == [
        ("file", "prompt.json")
    ]
    assert identity["children"][0]["id"] == identity["children"][0]["fileId"]
    assert cursor.params[:2] == ("owner-a", "zen-001")
    assert "projectionExcluded" in cursor.sql
    assert "coalesce(is_system,false)=false" not in cursor.sql


def test_workspace_folder_reference_is_owner_bound():
    with vvault_web_server.app.app_context():
        token = vvault_web_server._workspace_folder_ref(
            "owner-a", "zen-001", "chatty", "instances/zen-001/identity"
        )
        assert vvault_web_server._workspace_folder_path(
            token, "owner-a", "zen-001", "chatty"
        ) == "instances/zen-001/identity"
        with pytest.raises(PermissionError):
            vvault_web_server._workspace_folder_path(
                token, "owner-b", "zen-001", "chatty"
            )


def test_canonical_prompt_and_transcript_content_are_readable():
    prompt = vvault_web_server._derive_vault_preview_payload({
        "filename": "instances/zen-001/identity/prompt.json",
        "storage_path": "instances/zen-001/identity/prompt.json",
        "file_type": "text",
        "content_type": "application/json",
        "content": '{"displayName":"Zen"}',
    })
    transcript_text = "# Canonical transcript\n\nOwner-scoped content."
    transcript = vvault_web_server._derive_vault_preview_payload({
        "filename": "instances/zen-001/chatty/chat_with_zen-001.md",
        "storage_path": "instances/zen-001/chatty/chat_with_zen-001.md",
        "file_type": "transcript",
        "content_type": "application/octet-stream",
        "content": transcript_text,
    })
    assert prompt["preview_kind"] == "json"
    assert vvault_web_server._vault_preview_has_canonical_content(prompt)
    assert transcript["content"] == transcript_text
    assert transcript["preview_kind"] == "text"
    assert vvault_web_server._vault_preview_has_canonical_content(transcript)


def test_empty_text_and_unavailable_capsule_are_not_false_success():
    empty = vvault_web_server._derive_vault_preview_payload({
        "filename": "instances/zen-001/identity/prompt.json",
        "file_type": "text",
        "content": "",
    })
    unavailable = vvault_web_server._derive_vault_preview_payload({
        "filename": "instances/zen-001/chatty/legacy.capsule",
        "file_type": "capsule",
        "content": "",
    }, preview_budget_ms=0)
    assert not vvault_web_server._vault_preview_has_canonical_content(empty)
    assert unavailable["preview_status"] == "unavailable"
    assert not vvault_web_server._vault_preview_has_canonical_content(unavailable)


def _http_file_row(*, owner="owner-a", content='{"displayName":"Zen"}'):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "user_id": owner,
        "filename": "instances/zen-001/identity/prompt.json",
        "storage_path": "instances/zen-001/identity/prompt.json",
        "construct_id": "zen-001",
        "file_type": "text",
        "content_type": "application/json",
        "content": content,
        "metadata": {},
        "is_system": True,
    }


def _authenticated_client(monkeypatch, row, *, owner="owner-a"):
    monkeypatch.setattr(
        vvault_web_server, "get_current_user",
        lambda: ({"id": owner, "user_id": owner, "email": "owner@example.test", "role": "user"}, "token"),
    )
    monkeypatch.setattr(vvault_web_server, "_get_authenticated_user_id", lambda: owner)
    monkeypatch.setattr(vvault_web_server.VAULT_FILE_REPOSITORY, "get_by_id", lambda _file_id: row)
    monkeypatch.setattr(vvault_web_server, "_lookup_materialized_capsule_backing_row", lambda *_args, **_kwargs: None)
    vvault_web_server.app.config["TESTING"] = True
    return vvault_web_server.app.test_client()


def test_http_owner_gets_nonempty_canonical_body(monkeypatch):
    client = _authenticated_client(monkeypatch, _http_file_row())
    response = client.get("/api/vault/files/11111111-1111-4111-8111-111111111111")
    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["file"]["content"] == '{"displayName":"Zen"}'
    assert body["file"]["preview_status"] == "inline"


def test_http_other_owner_is_denied_before_preview(monkeypatch):
    client = _authenticated_client(monkeypatch, _http_file_row(owner="owner-b"))
    monkeypatch.setattr(
        vvault_web_server, "_derive_vault_preview_payload",
        lambda *_args, **_kwargs: pytest.fail("preview must not run before owner denial"),
    )
    response = client.get("/api/vault/files/11111111-1111-4111-8111-111111111111")
    assert response.status_code == 403
    assert response.get_json()["success"] is False


def test_http_owner_empty_text_returns_422(monkeypatch):
    client = _authenticated_client(monkeypatch, _http_file_row(content=""))
    response = client.get("/api/vault/files/11111111-1111-4111-8111-111111111111")
    body = response.get_json()
    assert response.status_code == 422
    assert body["success"] is False
    assert body["error"] == "preview_unavailable"
