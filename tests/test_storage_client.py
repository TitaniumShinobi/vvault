from __future__ import annotations

from types import SimpleNamespace

from packages.storage.client import StorageClient
from vvault.server import vvault_file_repository
from vvault.server import vvault_web_server as server


def _clear_storage_environment(monkeypatch):
    for key in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "VVAULT_OBJECT_STORAGE_URL",
        "VVAULT_OBJECT_STORAGE_SERVICE_KEY",
        "VVAULT_BODY_DB_URL",
        "VVAULT_BODY_DB_SERVICE_ROLE_KEY",
        "VVAULT_BODY_DB_ANON_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(key, raising=False)


def test_historical_object_storage_contract_downloads_bytes(monkeypatch):
    _clear_storage_environment(monkeypatch)
    monkeypatch.setenv("VVAULT_OBJECT_STORAGE_URL", "https://project.example")
    monkeypatch.setenv("VVAULT_OBJECT_STORAGE_SERVICE_KEY", "service-key")
    client = StorageClient()
    captured = {}

    class Response:
        content = b"png-bytes"
        headers = {"Content-Type": "image/png"}

        @staticmethod
        def raise_for_status():
            return None

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(client, "_rest_request", request)

    stored = client.download_bytes(bucket="vault-files", object_key="folder/My Image.png")

    assert client.provider == "object_storage_rest"
    assert stored.body == b"png-bytes"
    assert stored.content_type == "image/png"
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/storage/v1/object/vault-files/folder/My%20Image.png")
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer service-key"


def test_repository_uses_storage_path_for_imported_source_reference(monkeypatch):
    captured = {}

    class FakeStorageClient:
        def download_bytes(self, *, bucket, object_key):
            captured.update(bucket=bucket, object_key=object_key)
            return SimpleNamespace(body=b"binary", content_type="image/png")

    monkeypatch.setattr(vvault_file_repository, "StorageClient", FakeStorageClient)
    repository = vvault_file_repository.VVaultFileRepository()

    stored = repository.load_bytes({
        "bucket": "vault-files",
        "object_key": "real/path.png#source:row-id",
        "storage_path": "real/path.png",
    })

    assert stored == (b"binary", "image/png")
    assert captured == {"bucket": "vault-files", "object_key": "real/path.png"}


def test_storage_status_reports_rest_bridge(monkeypatch):
    _clear_storage_environment(monkeypatch)
    monkeypatch.setenv("VVAULT_OBJECT_STORAGE_URL", "https://project.example")
    monkeypatch.setenv("VVAULT_OBJECT_STORAGE_SERVICE_KEY", "service-key")

    status = server._storage_dependency_metadata()

    assert status["configured"] is True
    assert status["status"] == "configured"
    assert status["provider"] == "object_storage_rest"
