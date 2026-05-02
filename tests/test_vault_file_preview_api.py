import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vvault.server import vvault_web_server as server


class _FakeVaultFileQuery:
    def __init__(self, row):
        self._row = row

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._row)


class _FakeSupabaseClient:
    def __init__(self, row):
        self._row = row

    def table(self, _name):
        return _FakeVaultFileQuery(self._row)


class TestVaultFilePreviewApi(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def _auth_headers(self):
        return {"Authorization": "Bearer test-session-token"}

    def test_capsule_detail_route_keeps_inline_json_as_available_preview(self):
        row = {
            "id": "file-inline",
            "filename": "instances/val-001/memup/val-001.capsule",
            "storage_path": "instances/val-001/memup/val-001.capsule",
            "file_type": "binary",
            "content": '{\n  "construct_id": "val-001"\n}',
            "user_id": "user-1",
            "is_system": False,
        }

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_load_vault_file_text") as load_text, patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-inline", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["file_type"], "binary")
        self.assertEqual(payload["file"]["preview_kind"], "json")
        self.assertEqual(payload["file"]["preview_status"], "inline")
        self.assertEqual(payload["file"]["preview_source"], "inline")
        self.assertFalse(payload["file"]["preview_timed_out"])
        load_text.assert_not_called()

    def test_capsule_detail_route_recovers_json_preview_despite_binary_file_type(self):
        row = {
            "id": "file-recovered-json",
            "filename": "instances/nova-001/memup/nova-001.capsule",
            "storage_path": "instances/nova-001/memup/nova-001.capsule",
            "file_type": "binary",
            "content": None,
            "construct_id": "nova-001",
            "user_id": "user-1",
            "is_system": False,
        }
        recovered = '{\n  "construct_id": "nova-001",\n  "summary": {"total_sessions": 3}\n}'

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_reconstruct_capsule_preview_text", return_value=recovered), patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-recovered-json", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["file_type"], "binary")
        self.assertEqual(payload["file"]["content"], recovered)
        self.assertEqual(payload["file"]["preview_kind"], "json")
        self.assertEqual(payload["file"]["preview_status"], "recovered")
        self.assertEqual(payload["file"]["preview_source"], "memup")

    def test_capsule_detail_route_keeps_recovered_non_json_capsule_as_text(self):
        row = {
            "id": "file-recovered-text",
            "filename": "instances/sera-001/memup/sera-001.capsule",
            "storage_path": "instances/sera-001/memup/sera-001.capsule",
            "file_type": "binary",
            "content": None,
            "construct_id": "sera-001",
            "user_id": "user-1",
            "is_system": False,
        }
        recovered = "capsule notes\n- first line\n- second line"

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_reconstruct_capsule_preview_text", return_value=recovered), patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-recovered-text", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["file_type"], "binary")
        self.assertEqual(payload["file"]["content"], recovered)
        self.assertEqual(payload["file"]["preview_kind"], "text")
        self.assertEqual(payload["file"]["preview_status"], "malformed_text")
        self.assertEqual(payload["file"]["preview_source"], "memup")

    def test_json_path_with_stale_binary_file_type_stays_json(self):
        row = {
            "id": "file-json",
            "filename": "instances/nova-001/profile.json",
            "storage_path": "instances/nova-001/profile.json",
            "file_type": "binary",
            "content": '{"name": "Nova", "active": true}',
            "user_id": "user-1",
            "is_system": False,
        }

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_load_vault_file_text") as load_text, patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-json", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["file_type"], "binary")
        self.assertEqual(payload["file"]["preview_kind"], "json")
        self.assertEqual(payload["file"]["preview_status"], "inline")
        self.assertEqual(payload["file"]["preview_source"], "inline")
        load_text.assert_not_called()

    def test_text_file_type_stays_text_preview(self):
        row = {
            "id": "file-text",
            "filename": "instances/nova-001/notes/readme.md",
            "storage_path": "instances/nova-001/notes/readme.md",
            "file_type": "text/markdown",
            "content": "# Notes\n\nReadable markdown is not JSON.",
            "user_id": "user-1",
            "is_system": False,
        }

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_load_vault_file_text") as load_text, patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-text", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["preview_kind"], "text")
        self.assertEqual(payload["file"]["preview_status"], "inline")
        self.assertEqual(payload["file"]["preview_source"], "inline")
        load_text.assert_not_called()

    def test_unknown_custom_file_with_inline_readable_text_stays_previewable(self):
        row = {
            "id": "file-custom",
            "filename": "instances/zen-001/memup/zen-001.memorymap",
            "storage_path": "instances/zen-001/memup/zen-001.memorymap",
            "file_type": "binary",
            "content": "memory map\n- anchor\n- witness",
            "user_id": "user-1",
            "is_system": False,
        }

        with patch.object(server, "supabase_client", _FakeSupabaseClient(row)), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}
        ), patch.object(server, "_load_vault_file_text") as load_text, patch.object(
            server, "_lookup_materialized_capsule_backing_row", return_value=None
        ):
            response = self.client.get("/api/vault/files/file-custom", headers=self._auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file"]["file_type"], "binary")
        self.assertEqual(payload["file"]["preview_kind"], "text")
        self.assertEqual(payload["file"]["preview_status"], "inline")
        self.assertEqual(payload["file"]["preview_source"], "inline")
        self.assertFalse(payload["file"]["preview_timed_out"])
        load_text.assert_not_called()

    def test_fast_capsule_preview_route_is_read_only_and_not_write_gated(self):
        payload = {
            "id": "file-fast",
            "filename": "instances/nova-001/memup/nova-001.capsule",
            "storage_path": "instances/nova-001/memup/nova-001.capsule",
            "file_type": "binary",
            "construct_id": "nova-001",
            "user_id": "user-1",
            "metadata": {"folder": "memup"},
        }
        inline_capsule = '{\n  "construct_id": "nova-001",\n  "summary": {"total_sessions": 4}\n}'

        with patch.object(server.SUPABASE_STEWARD, "allow_write", side_effect=AssertionError("preview is read-only")), patch.object(
            server, "supabase_client", object()
        ), patch.object(server, "db_get_session", return_value={"email": "devon@example.com", "role": "admin"}), patch.object(
            server,
            "_lookup_exact_vault_preview_row",
            return_value={
                "id": "db-row-1",
                "filename": payload["filename"],
                "storage_path": payload["storage_path"],
                "file_type": "binary",
                "construct_id": payload["construct_id"],
                "user_id": payload["user_id"],
                "metadata": payload["metadata"],
                "content": inline_capsule,
            },
        ), patch.object(server, "_lookup_materialized_capsule_backing_row", return_value=None), patch.object(
            server,
            "_derive_vault_preview_payload",
            return_value={
                "filename": payload["filename"],
                "storage_path": payload["storage_path"],
                "file_type": payload["file_type"],
                "construct_id": payload["construct_id"],
                "user_id": payload["user_id"],
                "metadata": payload["metadata"],
                "content": inline_capsule,
                "preview_kind": "json",
                "preview_status": "inline",
                "preview_source": "inline",
                "preview_timed_out": False,
                "preview_elapsed_ms": 4,
                "preview_budget_ms": 0,
                "preview_storage_elapsed_ms": 0,
                "preview_reconstruct_elapsed_ms": 0,
            },
        ) as derive_preview:
            response = self.client.post("/api/vault/files/preview", headers=self._auth_headers(), json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["file"]["preview_kind"], "json")
        self.assertEqual(body["file"]["preview_status"], "inline")
        self.assertEqual(body["file"]["preview_source"], "inline")
        self.assertEqual(body["file"]["content"], inline_capsule)
        derive_preview.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
