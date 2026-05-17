import base64
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vvault.server.vvault_file_repository import VVaultFileRepository
from vvault.server import vvault_web_server as server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vvault" / "server"))
import memup_sync  # noqa: E402


USER_ID = "7e34f6b8-e33a-48b5-8ddb-95b94d18e296"


class _NoSupabaseRuntime:
    def __init__(self):
        self.table = Mock(side_effect=AssertionError("Supabase table must not be used"))
        self.storage = Mock()
        self.storage.from_ = Mock(side_effect=AssertionError("Supabase Storage must not be used"))


class _FakeCursor:
    def __init__(self, row=None):
        self.row = row or {"id": "system-file", "inserted": False}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


class TestVVaultFileRoutesNative(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def _auth_headers(self):
        return {"Authorization": "Bearer test-session-token"}

    def _service_headers(self):
        return {"Authorization": "Bearer svc-token"}

    def _session(self, role="user"):
        return {"id": USER_ID, "email": "devon@example.com", "name": "Devon", "role": role}

    def test_vault_files_list_uses_local_repository_only(self):
        fake_supabase = _NoSupabaseRuntime()
        rows = [
            {
                "id": "file-1",
                "user_id": USER_ID,
                "filename": "instances/lin-001/identity/prompt.json",
                "storage_path": "instances/lin-001/identity/prompt.json",
                "construct_id": "lin-001",
                "file_type": "application/json",
                "metadata": {"folder": "identity"},
                "is_system": False,
                "created_at": "2026-05-05T00:00:00+00:00",
            }
        ]

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "list_for_browser", return_value=rows) as list_rows:
            response = self.client.get("/api/vault/files", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["storage_owner"], "ovvaults.vault_files")
        self.assertEqual(payload["count"], 1)
        list_rows.assert_called_once()
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_knowledge_upload_writes_local_repository_only(self):
        fake_supabase = _NoSupabaseRuntime()

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "list_knowledge_files", return_value=[]), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "file-2", "path": "instances/lin-001/documents/notes.txt"},
        ) as upsert:
            response = self.client.post(
                "/api/vault/knowledge-files/upload",
                headers=self._auth_headers(),
                data={
                    "construct_id": "lin-001",
                    "files": (io.BytesIO(b"local body"), "notes.txt"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["created"], 1)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0]["content"], "local body")
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_markdown_file_downloads_native_utf8_bytes(self):
        fake_supabase = _NoSupabaseRuntime()
        row = {
            "id": "file-md",
            "user_id": USER_ID,
            "filename": "notes.md",
            "storage_path": "instances/lin-001/documents/notes.md",
            "file_type": "text/markdown",
            "content_type": "text/markdown",
            "content": "# Notes\nBody\n",
            "is_system": False,
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes", return_value=None
        ) as load_bytes:
            response = self.client.get("/api/vault/files/file-md/download", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"# Notes\nBody\n")
        self.assertTrue(response.headers["Content-Type"].startswith("text/markdown"))
        self.assertIn('attachment; filename="notes.md"', response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Content-Length"], str(len(response.data)))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        load_bytes.assert_called_once_with(row)
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_pdf_file_downloads_decoded_native_bytes(self):
        fake_supabase = _NoSupabaseRuntime()
        pdf_bytes = b"%PDF-1.4\nnative pdf bytes"
        row = {
            "id": "file-pdf",
            "user_id": USER_ID,
            "filename": "report.pdf",
            "storage_path": "instances/lin-001/documents/report.pdf",
            "file_type": "application/pdf",
            "content_type": "application/pdf",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
            "is_system": False,
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes", return_value=None
        ) as load_bytes:
            response = self.client.get("/api/vault/files/file-pdf/download", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, pdf_bytes)
        self.assertTrue(response.headers["Content-Type"].startswith("application/pdf"))
        self.assertIn('attachment; filename="report.pdf"', response.headers["Content-Disposition"])
        load_bytes.assert_called_once_with(row)
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_download_prefers_repository_native_bytes_when_available(self):
        row = {
            "id": "file-native",
            "user_id": USER_ID,
            "filename": "image.png",
            "storage_path": "instances/lin-001/documents/image.png",
            "file_type": "image/png",
            "content_type": "image/png",
            "content": "not-db-content",
            "is_system": False,
        }

        with patch.object(server, "db_get_session", return_value=self._session()), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row
        ), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes", return_value=(b"\x89PNG\r\nnative", "image/png")
        ):
            response = self.client.get("/api/vault/files/file-native/download", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"\x89PNG\r\nnative")
        self.assertTrue(response.headers["Content-Type"].startswith("image/png"))

    def test_file_download_denies_non_owner(self):
        fake_supabase = _NoSupabaseRuntime()
        row = {
            "id": "file-other",
            "user_id": "dc9d48a4-49ce-4d71-99b6-9a41380b92e0",
            "filename": "notes.md",
            "storage_path": "instances/lin-001/documents/notes.md",
            "file_type": "text/markdown",
            "content": "# Other\n",
            "is_system": False,
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes"
        ) as load_bytes:
            response = self.client.get("/api/vault/files/file-other/download", headers=self._auth_headers())

        self.assertEqual(response.status_code, 403)
        load_bytes.assert_not_called()
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_file_download_missing_file_returns_404(self):
        fake_supabase = _NoSupabaseRuntime()

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=None), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes"
        ) as load_bytes:
            response = self.client.get("/api/vault/files/missing/download", headers=self._auth_headers())

        self.assertEqual(response.status_code, 404)
        load_bytes.assert_not_called()
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_system_file_upsert_is_synchronous_local_write(self):
        fake_supabase = _NoSupabaseRuntime()
        system_row = {
            "id": "system-file",
            "filename": "system/current.md",
            "storage_path": "system/current.md",
            "content": "hello",
            "is_system": True,
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(server.VAULT_FILE_REPOSITORY, "get_system_file", side_effect=[None, system_row]), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "system-file", "path": "system/current.md"},
        ) as upsert:
            response = self.client.post(
                "/api/vault/system-files",
                headers=self._service_headers(),
                json={"storage_path": "system/current.md", "content": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["action"], "created")
        upsert.assert_called_once()
        self.assertFalse(hasattr(server, "SUPABASE_WRITE_OUTBOX"))
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_system_file_upsert_infers_construct_id_from_instances_path(self):
        fake_supabase = _NoSupabaseRuntime()
        storage_path = "instances/zen-001/codex/foo.md"
        system_row = {
            "id": "system-file",
            "filename": storage_path,
            "storage_path": storage_path,
            "construct_id": "zen-001",
            "content": "# Foo\n",
            "is_system": True,
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server, "legacy_remote_client", fake_supabase, create=True
        ), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_system_file", side_effect=[None, system_row]
        ), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "system-file", "path": storage_path},
        ) as upsert:
            response = self.client.post(
                "/api/vault/system-files",
                headers=self._service_headers(),
                json={"storage_path": storage_path, "content": "# Foo\n"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        upsert.assert_called_once()
        record = upsert.call_args.args[0]
        self.assertEqual(record["storage_path"], storage_path)
        self.assertEqual(record["filename"], storage_path)
        self.assertEqual(record["construct_id"], "zen-001")
        self.assertTrue(record["is_system"])
        self.assertIsNone(record["user_id"])
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_system_file_upsert_updates_existing_row_after_construct_inference(self):
        fake_supabase = _NoSupabaseRuntime()
        storage_path = "instances/zen-001/codex/foo.md"
        existing_row = {
            "id": "system-file",
            "filename": storage_path,
            "storage_path": storage_path,
            "construct_id": None,
            "content": "old body",
            "created_at": "2026-05-09T00:00:00+00:00",
            "is_system": True,
        }
        updated_row = {
            **existing_row,
            "construct_id": "zen-001",
            "content": "new body",
            "sha256": "new-sha",
            "updated_at": "2026-05-09T00:01:00+00:00",
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server, "legacy_remote_client", fake_supabase, create=True
        ), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_system_file", side_effect=[existing_row, updated_row]
        ), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "updated", "id": "system-file", "path": storage_path},
        ) as upsert:
            response = self.client.post(
                "/api/vault/system-files",
                headers=self._service_headers(),
                json={"storage_path": storage_path, "content": "new body"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["action"], "updated")
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["file"]["content"], "new body")
        self.assertEqual(payload["file"]["construct_id"], "zen-001")
        record = upsert.call_args.args[0]
        self.assertEqual(record["created_at"], existing_row["created_at"])
        self.assertEqual(record["construct_id"], "zen-001")
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_system_file_readback_is_vvault_body(self):
        storage_path = "instances/zen-001/codex/foo.md"
        system_row = {
            "id": "system-file",
            "filename": storage_path,
            "storage_path": storage_path,
            "construct_id": "zen-001",
            "content": "# Foo\n",
            "is_system": True,
            "storage_mode": "ignored-client-value",
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_system_file", return_value=system_row
        ):
            response = self.client.get(
                f"/api/vault/system-files?storage_path={storage_path}",
                headers=self._service_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["file"]["storage_path"], storage_path)

    def test_repository_system_file_upsert_uses_bucket_object_key_conflict_update(self):
        repo = VVaultFileRepository.__new__(VVaultFileRepository)
        cursor = _FakeCursor({"id": "system-file", "inserted": False})
        connection = _FakeConnection(cursor)

        with patch.object(repo, "_system_user_id", return_value="system-user-id"), patch.object(
            repo, "_connect", return_value=connection
        ):
            result = repo.upsert(
                {
                    "storage_path": "instances/zen-001/codex/foo.md",
                    "content": "updated body",
                    "metadata": {"sourceSessionId": "019e-test"},
                    "sha256": "sha-updated",
                    "construct_id": "zen-001",
                    "is_system": True,
                    "user_id": None,
                    "created_at": "2026-05-10T00:00:00+00:00",
                    "updated_at": "2026-05-10T00:01:00+00:00",
                }
            )

        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["id"], "system-file")
        self.assertTrue(connection.committed)
        self.assertEqual(len(cursor.calls), 1)
        sql, params = cursor.calls[0]
        self.assertIn("ON CONFLICT (bucket, object_key) DO UPDATE", sql)
        self.assertIn("created_at", sql)
        self.assertNotIn("created_at = EXCLUDED.created_at", sql)
        self.assertIn("content = EXCLUDED.content", sql)
        self.assertIn("metadata = EXCLUDED.metadata", sql)
        self.assertIn("construct_id = EXCLUDED.construct_id", sql)
        self.assertEqual(params[0], "system-user-id")
        self.assertEqual(params[1], "vvault-local")
        self.assertEqual(params[2], "system/instances/zen-001/codex/foo.md")
        self.assertEqual(params[8], "updated body")
        self.assertEqual(params[10], "zen-001")

    def test_admin_browser_listing_uses_construct_or_instances_path_prefix(self):
        repo = VVaultFileRepository.__new__(VVaultFileRepository)
        row = {
            "id": "system-file",
            "filename": "foo.md",
            "storage_path": "instances/zen-001/codex/foo.md",
            "construct_id": None,
            "is_system": True,
        }

        with patch.object(repo, "_fetch", return_value=[row]) as fetch:
            rows = repo.list_for_browser(
                user_id=None,
                is_admin=True,
                requested_path="instances/zen-001/codex",
            )

        self.assertEqual(rows, [row])
        sql, params = fetch.call_args.args
        self.assertIn("construct_id = %s", sql)
        self.assertIn("filename ILIKE %s", sql)
        self.assertIn("storage_path ILIKE %s", sql)
        self.assertEqual(
            params,
            ("zen-001", "instances/zen-001/codex/%", "instances/zen-001/codex/%"),
        )

    def test_non_admin_browser_listing_can_see_codex_system_thread_bank_only(self):
        fake_supabase = _NoSupabaseRuntime()
        codex_row = {
            "id": "codex-thread",
            "user_id": None,
            "filename": "instances/zen-001/codex/Build cross-platform transcript sync.md",
            "storage_path": "instances/zen-001/codex/Build cross-platform transcript sync.md",
            "construct_id": "zen-001",
            "file_type": "transcript",
            "metadata": {"sourceProduct": "codex", "codexThreadArchiveSchemaVersion": 1},
            "is_system": True,
            "created_at": "2026-05-16T00:00:00+00:00",
            "updated_at": "2026-05-17T00:00:00+00:00",
        }
        hidden_system_row = {
            "id": "hidden-config",
            "user_id": None,
            "filename": "system/configs/auth/service.json",
            "storage_path": "system/configs/auth/service.json",
            "construct_id": None,
            "file_type": "application/json",
            "metadata": {"service": "auth"},
            "is_system": True,
            "created_at": "2026-05-16T00:00:00+00:00",
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "list_for_browser",
            return_value=[codex_row, hidden_system_row],
        ) as list_rows:
            response = self.client.get(
                "/api/vault/files?path=instances/zen-001/codex",
                headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["files"][0]["id"], "codex-thread")
        self.assertEqual(payload["files"][0]["display_name"], "Build cross-platform transcript sync.md")
        list_rows.assert_called_once_with(
            user_id=USER_ID,
            is_admin=False,
            requested_path="instances/zen-001/codex",
        )
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_non_admin_repository_listing_includes_only_codex_system_rows_for_codex_path(self):
        repo = VVaultFileRepository.__new__(VVaultFileRepository)
        row = {
            "id": "codex-thread",
            "filename": "instances/zen-001/codex/foo.md",
            "storage_path": "instances/zen-001/codex/foo.md",
            "construct_id": "zen-001",
            "is_system": True,
            "metadata": {"sourceProduct": "codex"},
        }

        with patch.object(repo, "_fetch", return_value=[row]) as fetch:
            rows = repo.list_for_browser(
                user_id=USER_ID,
                is_admin=False,
                requested_path="instances/zen-001/codex",
            )

        self.assertEqual(rows, [row])
        sql, params = fetch.call_args.args
        self.assertIn("coalesce(is_system, false) = true", sql)
        self.assertIn("metadata->>'sourceProduct'", sql)
        self.assertIn("codexThreadArchiveSchemaVersion", sql)
        self.assertEqual(
            params,
            (
                "zen-001",
                "instances/zen-001/codex/%",
                "instances/zen-001/codex/%",
                USER_ID,
                "instances/zen-001/codex/%",
                "instances/zen-001/codex/%",
            ),
        )

    def test_non_admin_can_open_and_download_visible_codex_system_thread(self):
        fake_supabase = _NoSupabaseRuntime()
        storage_path = "instances/zen-001/codex/Build cross-platform transcript sync.md"
        row = {
            "id": "codex-thread",
            "user_id": "system-user-id",
            "filename": storage_path,
            "storage_path": storage_path,
            "construct_id": "zen-001",
            "file_type": "transcript",
            "content": "# Build cross-platform transcript sync\n\n## User\nhello\n",
            "metadata": {"sourceProduct": "codex", "codexThreadArchiveSchemaVersion": 1},
            "is_system": True,
            "created_at": "2026-05-16T00:00:00+00:00",
            "updated_at": "2026-05-17T00:00:00+00:00",
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row), patch.object(
            server.VAULT_FILE_REPOSITORY, "load_bytes", return_value=None
        ):
            detail_response = self.client.get("/api/vault/files/codex-thread", headers=self._auth_headers())
            download_response = self.client.get(
                "/api/vault/files/codex-thread/download",
                headers=self._auth_headers(),
            )

        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.get_json()
        self.assertTrue(detail_payload["success"])
        self.assertIn("Build cross-platform transcript sync", detail_payload["file"]["content"])
        self.assertEqual(download_response.status_code, 200)
        self.assertIn(b"Build cross-platform transcript sync", download_response.data)
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_non_admin_cannot_open_unrelated_system_file_by_id(self):
        row = {
            "id": "hidden-config",
            "user_id": None,
            "filename": "system/configs/auth/service.json",
            "storage_path": "system/configs/auth/service.json",
            "file_type": "application/json",
            "content": "{}",
            "metadata": {"service": "auth"},
            "is_system": True,
        }

        with patch.object(server, "db_get_session", return_value=self._session()), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_by_id", return_value=row
        ):
            detail_response = self.client.get("/api/vault/files/hidden-config", headers=self._auth_headers())
            download_response = self.client.get(
                "/api/vault/files/hidden-config/download",
                headers=self._auth_headers(),
            )

        self.assertEqual(detail_response.status_code, 403)
        self.assertEqual(download_response.status_code, 403)

    def test_system_file_outbox_replay_is_retired_noop(self):
        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"):
            response = self.client.post(
                "/api/vault/system-files/outbox/replay",
                headers=self._service_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["outbox_replay_receipt"]["action"], "retired")
        self.assertFalse(hasattr(server, "SUPABASE_WRITE_OUTBOX"))

    def test_memup_sync_uses_local_repository_only(self):
        fake_supabase = _NoSupabaseRuntime()

        def _sync(repository, construct_id, user_id):
            self.assertIs(repository, server.VAULT_FILE_REPOSITORY)
            return {"success": True, "construct_id": construct_id, "user_id": user_id}

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(memup_sync, "sync_construct_memup", side_effect=_sync) as sync:
            response = self.client.post(
                "/api/vault/memup/sync",
                headers=self._auth_headers(),
                json={"construct_id": "lin-001"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        sync.assert_called_once()
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_memup_materialize_uses_local_repository_only(self):
        fake_supabase = _NoSupabaseRuntime()
        materialized = {
            "capsule_data": {
                "capsule_version": "2.0.0",
                "summary": {
                    "total_sessions": 1,
                    "total_exchanges": 2,
                    "date_range": {},
                    "topics": ["Continuity"],
                },
            },
            "write_result": {"file_id": "capsule-row", "path": "instances/lin-001/memup/lin-001.materialized.capsule"},
            "original_capsule": {"file_id": "original-row"},
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(
            server, "_candidate_transcript_ids_for_construct", return_value=["tx-1"]
        ), patch.object(
            server, "_persist_capsule_from_candidate_transcripts", return_value=materialized
        ) as persist:
            response = self.client.post(
                "/api/vault/memup/materialize",
                headers=self._auth_headers(),
                json={"construct_id": "lin-001"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        persist.assert_called_once_with("lin-001", ["tx-1"], USER_ID)
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_memup_status_uses_local_rows_only(self):
        fake_supabase = _NoSupabaseRuntime()
        materialized = {
            "id": "capsule-row",
            "filename": "instances/lin-001/memup/lin-001.materialized.capsule",
            "storage_path": "instances/lin-001/memup/lin-001.materialized.capsule",
            "sha256": "sha",
            "file_type": "capsule",
            "metadata": {"last_synced_at": "2026-05-05T00:00:00+00:00", "total_sessions": 1},
        }

        with patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "db_get_session", return_value=self._session()
        ), patch.object(
            server, "_lookup_exact_vault_preview_row", side_effect=[None, materialized]
        ):
            response = self.client.get(
                "/api/vault/memup/status?construct_id=lin-001",
                headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["preferred_artifact"], "materialized")
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_identity_projection_routes_do_not_gate_on_supabase(self):
        fake_supabase = _NoSupabaseRuntime()

        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), patch.object(
            server, "VVAULT_SERVICE_TOKEN", "svc-token"
        ), patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server, "_read_identity_projection_snapshot", return_value={"success": True, "construct_id": "lin-001"}
        ):
            read_response = self.client.get(
                "/api/vault/constructs/lin-001/identity-projection",
                headers=self._service_headers(),
            )

        self.assertEqual(read_response.status_code, 200)
        self.assertTrue(read_response.get_json()["success"])
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_identity_projection_project_writes_local_projection_only(self):
        fake_supabase = _NoSupabaseRuntime()

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(server, "enforce_pocketverse_authority"), patch.object(
            server, "_project_identity_fields", return_value={"success": True, "construct_id": "lin-001", "results": {}}
        ) as project:
            response = self.client.post(
                "/api/vault/constructs/lin-001/identity-projection/project",
                headers=self._service_headers(),
                json={"fields": {"conditioning": "real body text"}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        project.assert_called_once()
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_service_configs_are_system_vault_file_artifacts(self):
        fake_supabase = _NoSupabaseRuntime()
        config_path = "system/configs/fxshinobi/default.json"
        stored_row = {
            "id": "config-row",
            "filename": config_path,
            "storage_path": config_path,
            "content": json.dumps({
                "service": "fxshinobi",
                "strategy_id": "default",
                "params": {"risk": "low"},
                "symbols": ["SPY"],
                "risk_limits": {"max": 1},
                "enabled": True,
                "version": 1,
                "updated_at": "2026-05-05T00:00:00+00:00",
            }),
            "is_system": True,
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(server.VAULT_FILE_REPOSITORY, "get_system_file", side_effect=[None, stored_row, stored_row]), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "config-row", "path": config_path},
        ) as upsert, patch.object(
            server.VAULT_FILE_REPOSITORY,
            "list_for_browser",
            return_value=[{"storage_path": config_path}],
        ):
            write_response = self.client.post(
                "/api/vault/configs/fxshinobi",
                headers=self._service_headers(),
                json={"strategy_id": "default", "params": {"risk": "low"}, "symbols": ["SPY"], "risk_limits": {"max": 1}},
            )
            read_response = self.client.get(
                "/api/vault/configs/fxshinobi",
                headers=self._service_headers(),
            )

        self.assertEqual(write_response.status_code, 200)
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(write_response.get_json()["storage_mode"], "vvault_body")
        self.assertEqual(read_response.get_json()["configs"][0]["strategy_id"], "default")
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0]["storage_path"], config_path)
        self.assertTrue(upsert.call_args.args[0]["is_system"])
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()

    def test_ledger_generate_and_read_use_local_repository(self):
        fake_supabase = _NoSupabaseRuntime()
        transcript = {
            "id": "tx-1",
            "filename": "instances/lin-001/chatty/chat_with_lin-001.md",
            "content": "You said:\nHello there\nChatGPT said:\nA continuity answer with enough body text. " * 3,
            "file_type": "transcript",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
        parser = SimpleNamespace(
            process_all_transcripts=lambda _rows: [{"filename": transcript["filename"], "exchange_count": 1, "estimated_date": "2026-05-05"}],
            generate_ledger_json=lambda entries, include_exchanges=False: entries,
        )
        ledger_row = {
            "content": json.dumps([{"filename": transcript["filename"], "exchange_count": 1}]),
            "metadata": {"total_sessions": 1, "total_exchanges": 1, "generated_at": "2026-05-05T00:00:00+00:00"},
        }

        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), patch.object(
            server, "VVAULT_SERVICE_TOKEN", "svc-token"
        ), patch.object(server, "legacy_remote_client", fake_supabase, create=True), patch.object(
            server.VAULT_FILE_REPOSITORY, "list_construct_file_rows", return_value=[transcript]
        ), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "ledger-row", "path": "lin-001_continuity_ledger.json"},
        ) as upsert, patch.object(
            server.VAULT_FILE_REPOSITORY, "find_exact", return_value=ledger_row
        ), patch.object(server, "ContinuityParser", return_value=parser):
            generate_response = self.client.post(
                "/api/chatty/construct/lin-001/ledger/generate",
                headers={
                    "X-Chatty-Key": "svc-token",
                    "X-Chatty-User": "devon@example.com",
                    "X-Chatty-User-Id": USER_ID,
                },
            )
            read_response = self.client.get(
                "/api/chatty/construct/lin-001/ledger",
                headers={
                    "X-Chatty-Key": "svc-token",
                    "X-Chatty-User": "devon@example.com",
                    "X-Chatty-User-Id": USER_ID,
                },
            )

        self.assertEqual(generate_response.status_code, 200)
        self.assertEqual(read_response.status_code, 200)
        self.assertTrue(read_response.get_json()["ledger_exists"])
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0]["construct_id"], "lin-001")
        self.assertEqual(upsert.call_args.args[0]["user_id"], USER_ID)
        fake_supabase.table.assert_not_called()
        fake_supabase.storage.from_.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
