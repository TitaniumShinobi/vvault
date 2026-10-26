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
