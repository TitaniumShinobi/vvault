from __future__ import annotations

import pytest

from vvault.server import vvault_web_server as server
from vvault.server.vvault_file_repository import VVaultFileRepository


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def _admin_client(monkeypatch):
    server.app.config["TESTING"] = True
    monkeypatch.setattr(
        server,
        "db_get_session",
        lambda _token: {
            "id": OWNER_B,
            "email": "owner-b@example.test",
            "role": "admin",
            "auth_mode": "session",
        },
    )
    return server.app.test_client()


def test_admin_file_list_is_scoped_to_native_session_owner(monkeypatch):
    calls = []

    def list_for_browser(*, user_id, is_admin, requested_path=""):
        calls.append((user_id, is_admin, requested_path))
        return []

    monkeypatch.setattr(server.VAULT_FILE_REPOSITORY, "list_for_browser", list_for_browser)
    client = _admin_client(monkeypatch)

    response = client.get(
        "/api/vault/files?path=instances/owner-a",
        headers={"Authorization": "Bearer owner-b-session"},
    )

    assert response.status_code == 200
    assert calls == [(OWNER_B, False, "instances/owner-a")]
    assert response.get_json()["user_root"] == "owner-b"


@pytest.mark.parametrize(
    "path",
    [
        "/api/vault/files/file-a",
        "/api/vault/files/file-a/data-url",
        "/api/vault/files/file-a/media",
        "/api/vault/files/file-a/archive",
        "/api/vault/drive/files/file-a/download",
    ],
)
def test_admin_cannot_read_another_owners_file_by_id(monkeypatch, path):
    calls = []

    def get_user_file(*, file_id, user_id, construct_id=None):
        calls.append((file_id, user_id, construct_id))
        return None

    monkeypatch.setattr(server.VAULT_FILE_REPOSITORY, "get_user_file", get_user_file)
    monkeypatch.setattr(
        server.VAULT_FILE_REPOSITORY,
        "get_by_id",
        lambda _file_id: pytest.fail("ordinary file routes must not perform a global ID lookup"),
    )
    client = _admin_client(monkeypatch)

    response = client.get(path, headers={"Authorization": "Bearer owner-b-session"})

    assert response.status_code == 404
    assert calls == [("file-a", OWNER_B, None)]


def test_preview_ignores_forged_owner_even_for_admin(monkeypatch):
    calls = []

    def find_preview(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(server, "_lookup_exact_vault_preview_row", find_preview)
    monkeypatch.setattr(server, "_lookup_materialized_capsule_backing_row", lambda *args, **kwargs: None)
    client = _admin_client(monkeypatch)

    response = client.post(
        "/api/vault/files/preview",
        headers={"Authorization": "Bearer owner-b-session"},
        json={
            "filename": "instances/owner-a/memup/private.capsule",
            "storage_path": "instances/owner-a/memup/private.capsule",
            "construct_id": "owner-a",
            "user_id": OWNER_A,
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "filename": "instances/owner-a/memup/private.capsule",
            "storage_path": "instances/owner-a/memup/private.capsule",
            "construct_id": "owner-a",
            "user_id": OWNER_B,
            "is_admin": False,
        }
    ]
    assert response.get_json()["file"]["user_id"] == OWNER_B


def test_repository_admin_flag_cannot_widen_exact_lookup(monkeypatch):
    repository = VVaultFileRepository()
    captured = {}

    def one(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return None

    monkeypatch.setattr(repository, "_one", one)

    repository.find_exact(
        filename="private.txt",
        storage_path="instances/owner-a/private.txt",
        user_id=OWNER_B,
        is_admin=True,
    )

    assert "user_id = %s" in captured["sql"]
    assert captured["params"][-1] == OWNER_B


def test_repository_admin_flag_cannot_widen_file_listing(monkeypatch):
    repository = VVaultFileRepository()
    captured = {}

    def fetch(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(repository, "_fetch", fetch)

    repository.list_for_browser(user_id=OWNER_B, is_admin=True)

    assert "user_id = %s" in captured["sql"]
    assert captured["params"] == (OWNER_B,)
