from __future__ import annotations

import base64

from vvault.server import vvault_web_server as server


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)
OWNER_ID = "22222222-2222-4222-8222-222222222222"


def _image_row(*, content="", bucket="vvault-local", object_key="images/test.png", storage_path="instances/zen-001/assets/test.png"):
    return {
        "id": "image-1",
        "user_id": "user-1",
        "filename": "instances/zen-001/assets/test.png",
        "storage_path": storage_path,
        "bucket": bucket,
        "object_key": object_key,
        "content": content,
        "content_type": "image/png",
        "file_type": "binary",
        "metadata": {"mimeType": "image/png"},
        "is_system": False,
    }


def _client(monkeypatch, row, *, stored_bytes=None):
    server.app.config["TESTING"] = True
    monkeypatch.setattr(
        server,
        "db_get_session",
        lambda _token: {
            "id": OWNER_ID,
            "email": "dwoodson92@gmail.com",
            "role": "admin",
            "auth_mode": "session",
        },
    )
    monkeypatch.setattr(
        server.VAULT_FILE_REPOSITORY,
        "get_user_file",
        lambda *, file_id, user_id, construct_id=None: row
        if file_id == "image-1" and user_id == OWNER_ID
        else None,
    )
    monkeypatch.setattr(
        server.VAULT_FILE_REPOSITORY,
        "load_bytes",
        lambda _row: stored_bytes,
    )
    return server.app.test_client()


def test_placeholder_binary_content_is_rejected(monkeypatch):
    client = _client(
        monkeypatch,
        _image_row(content="[binary:image/png:108954]", storage_path=""),
    )

    response = client.get(
        "/api/vault/files/image-1/data-url",
        headers={"Authorization": "Bearer diagnostic"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "preview_unavailable"
    assert response.get_json()["reason"] == "missing_content"


def test_valid_inline_png_bytes_are_accepted(monkeypatch):
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    client = _client(monkeypatch, _image_row(content=encoded))

    response = client.get(
        "/api/vault/files/image-1/data-url",
        headers={"Authorization": "Bearer diagnostic"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert base64.b64decode(payload["data_url"].split(",", 1)[1], validate=True) == PNG_BYTES


def test_valid_storage_backed_png_bytes_are_accepted(monkeypatch):
    client = _client(
        monkeypatch,
        _image_row(content=""),
        stored_bytes=(PNG_BYTES, "image/png"),
    )

    response = client.get(
        "/api/vault/files/image-1/data-url",
        headers={"Authorization": "Bearer diagnostic"},
    )

    assert response.status_code == 200
    assert response.get_json()["data_url"].startswith("data:image/png;base64,")


def test_missing_storage_content_returns_clear_error(monkeypatch):
    client = _client(monkeypatch, _image_row(content=""), stored_bytes=None)

    response = client.get(
        "/api/vault/files/image-1/data-url",
        headers={"Authorization": "Bearer diagnostic"},
    )

    assert response.status_code == 422
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"] == "preview_unavailable"
    assert payload["reason"] == "storage_unavailable"
    assert payload["file_id"] == "image-1"
