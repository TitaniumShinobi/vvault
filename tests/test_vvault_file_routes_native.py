import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vvault" / "server"))
import memup_sync  # noqa: E402


USER_ID = "7e34f6b8-e33a-48b5-8ddb-95b94d18e296"


class _NoSupabaseRuntime:
    def __init__(self):
        self.table = Mock(side_effect=AssertionError("Supabase table must not be used"))
        self.storage = Mock()
        self.storage.from_ = Mock(side_effect=AssertionError("Supabase Storage must not be used"))


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
