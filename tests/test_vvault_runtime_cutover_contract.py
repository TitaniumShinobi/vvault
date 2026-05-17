import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "vvault" / "server"))
import memup_sync  # noqa: E402

SERVER_PATH = REPO_ROOT / "vvault" / "server" / "vvault_web_server.py"
LAUNCHER_PATH = REPO_ROOT / "scripts" / "open-vvault-standalone.sh"
PACKAGE_PATH = REPO_ROOT / "package.json"


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def _vvault_runtime_status(*, ready=True):
    return {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": ready,
        "connection_state": "connected" if ready else "degraded",
        "runtime": {
            "server_pid": 1234,
            "repo_root": str(REPO_ROOT),
            "started_at": "2026-05-05T00:00:00+00:00",
            "log_configured": False,
        },
        "body_database": {
            "required": True,
            "ready": ready,
            "status": "healthy" if ready else "unhealthy",
            "configured": True,
            "authority": "vvault_body",
            "storage_mode": "vvault_body",
            "canonical": ready,
            "connection_state": "connected" if ready else "degraded",
            "schema": "ovvaults",
            "source_database": "vvault_body_test",
            "checks": {
                "vault_files_readable": ready,
                "vault_files_runtime_columns": ready,
                "transcripts_readable": ready,
                "transcript_content_column": ready,
            },
        },
        "storage": {
            "required_for_readiness": False,
            "configured": False,
            "status": "unconfigured",
            "bucket_configured": False,
            "provider": "s3_compatible",
        },
        "auth": {
            "required_for_readiness": False,
            "status": "unconfigured",
            "ready": False,
            "authority": "vvault_auth",
            "storage_mode": "vvault_body",
            "canonical": False,
            "connection_state": "degraded",
            "identity_authority_available": False,
            "service_api": {"configured": False},
            "google_oauth": {
                "configured": False,
                "callback_route": "/api/auth/google/callback",
            },
        },
    }


class TestVvaultRuntimeCutoverStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PATH.read_text(encoding="utf-8")
        cls.launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        cls.package = PACKAGE_PATH.read_text(encoding="utf-8")

    def test_health_and_ready_use_vvault_body_runtime_authority(self):
        self.assertIn("@app.route('/api/ready')", self.server)
        self.assertIn("def _body_database_dependency_status", self.server)
        self.assertIn("chatty_body_service.database_url()", self.server)
        self.assertIn("SELECT 1 FROM vault_files LIMIT 1", self.server)
        self.assertIn("SELECT id, content, metadata, construct_id, storage_path, file_type", self.server)
        self.assertIn("SELECT 1 FROM transcripts LIMIT 1", self.server)
        self.assertIn("SELECT id, content FROM transcripts LIMIT 1", self.server)
        self.assertIn("runtime_status = _get_vvault_runtime_status()", self.server)
        self.assertIn("\"body_database\": runtime_status[\"body_database\"]", self.server)
        self.assertIn("\"authority\": runtime_status[\"authority\"]", self.server)
        self.assertIn("\"canonical\": runtime_status[\"canonical\"]", self.server)
        self.assertIn("\"connection_state\": runtime_status[\"connection_state\"]", self.server)
        health_route = self.server.split("@app.route('/api/health')", 1)[1].split("@app.route('/api/ready')", 1)[0]
        ready_route = self.server.split("@app.route('/api/ready')", 1)[1].split("USER_PATH_PATTERN", 1)[0]
        self.assertNotIn("SUPABASE_STEWARD.snapshot()", health_route)
        self.assertNotIn("SUPABASE_STEWARD.snapshot()", ready_route)
        self.assertNotIn("\"supabase\"", health_route)
        self.assertNotIn("\"supabase\"", ready_route)
        self.assertIn("200 if ready else 503", self.server)

    def test_supabase_sdk_client_is_quarantined_from_runtime_import(self):
        self.assertNotIn("from supabase import", self.server)
        self.assertNotIn("create_client(", self.server)
        self.assertNotIn("supabase_client", self.server)
        self.assertNotIn("SUPABASE_STEWARD", self.server)
        self.assertNotIn("SUPABASE_WRITE_OUTBOX", self.server)

    def test_launcher_uses_ready_without_defining_supabase_as_runtime_truth(self):
        self.assertIn("/api/ready", self.launcher)
        self.assertIn("backend_listener_count()", self.launcher)
        self.assertIn("ambiguous duplicate listeners", self.launcher)
        self.assertIn("start_frontend()", self.launcher)
        self.assertIn("start_devfull()", self.launcher)

    def test_frontend_script_survives_detached_launcher_stdin(self):
        self.assertIn("webpack-dev-server --mode development --no-watch-options-stdin", self.package)

    def test_dev_server_serves_public_html_documents_before_spa_fallback(self):
        self.assertIn("path.join(__dirname, 'html')", self.server + self.package + (REPO_ROOT / "webpack.config.js").read_text(encoding="utf-8"))
        webpack = (REPO_ROOT / "webpack.config.js").read_text(encoding="utf-8")
        html_static = webpack.split("path.join(__dirname, 'html')", 1)[1].split("]", 1)[0]
        self.assertIn("publicPath: '/'", html_static)


class TestVvaultRuntimeCutoverRoutes(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def test_ready_route_uses_body_database_not_legacy_supabase_steward(self):
        with patch.object(server, "_get_vvault_runtime_status", return_value=_vvault_runtime_status(ready=True)):
            connected = self.client.get("/api/ready")
        self.assertEqual(connected.status_code, 200)
        connected_payload = connected.get_json()
        self.assertTrue(connected_payload["ready"])
        self.assertEqual(connected_payload["authority"], "vvault_body")
        self.assertEqual(connected_payload["storage_mode"], "vvault_body")
        self.assertTrue(connected_payload["canonical"])
        self.assertEqual(connected_payload["connection_state"], "connected")
        self.assertEqual(connected_payload["body_database"]["status"], "healthy")
        self.assertTrue(connected_payload["body_database"]["canonical"])
        self.assertEqual(connected_payload["body_database"]["connection_state"], "connected")
        self.assertTrue(connected_payload["body_database"]["checks"]["vault_files_runtime_columns"])
        self.assertTrue(connected_payload["body_database"]["checks"]["transcript_content_column"])
        self.assertNotIn("supabase", connected_payload)

        with patch.object(server, "_get_vvault_runtime_status", return_value=_vvault_runtime_status(ready=False)):
            blocked = self.client.get("/api/ready")
        self.assertEqual(blocked.status_code, 503)
        blocked_payload = blocked.get_json()
        self.assertFalse(blocked_payload["ready"])
        self.assertEqual(blocked_payload["authority"], "vvault_body")
        self.assertFalse(blocked_payload["canonical"])
        self.assertEqual(blocked_payload["connection_state"], "degraded")
        self.assertEqual(blocked_payload["body_database"]["status"], "unhealthy")
        self.assertFalse(blocked_payload["body_database"]["checks"]["vault_files_runtime_columns"])
        self.assertFalse(blocked_payload["body_database"]["checks"]["transcript_content_column"])
        self.assertFalse(blocked_payload["storage"]["required_for_readiness"])
        self.assertFalse(blocked_payload["auth"]["required_for_readiness"])
        self.assertNotIn("supabase", blocked_payload)

    def test_health_reports_vvault_native_dependencies_without_supabase_authority(self):
        with patch.object(server, "_get_vvault_runtime_status", return_value=_vvault_runtime_status(ready=True)):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["body_database"]["status"], "healthy")
        self.assertTrue(payload["body_database"]["canonical"])
        self.assertEqual(payload["body_database"]["connection_state"], "connected")
        self.assertTrue(payload["body_database"]["checks"]["vault_files_runtime_columns"])
        self.assertTrue(payload["body_database"]["checks"]["transcript_content_column"])
        self.assertFalse(payload["storage"]["required_for_readiness"])
        self.assertFalse(payload["auth"]["required_for_readiness"])
        self.assertEqual(payload["auth"]["authority"], "vvault_auth")
        self.assertNotIn("supabase", payload)
        self.assertNotIn("supabase_mode", payload)

    def test_vault_service_health_reports_vvault_native_dependencies(self):
        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server, "_get_vvault_runtime_status", return_value=_vvault_runtime_status(ready=True)
        ):
            response = self.client.get("/api/vault/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["components"]["body_database"], "healthy")
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["storage_owner"], "ovvaults.vault_files")
        self.assertNotIn("supabase", payload)
        self.assertNotIn("supabase_mode", payload)

    def test_public_legal_documents_do_not_fall_through_to_spa_shell(self):
        expected = {
            "/vvault-terms.html": "VVAULT Terms of Service",
            "/terms-of-service.html": "VVAULT Terms of Service",
            "/vvault-privacy.html": "VVAULT Privacy Notice",
            "/privacy-notice.html": "VVAULT Privacy Notice",
            "/vvault-eeccd.html": "VVAULT European Electronic Communications Code Disclosure",
            "/european-electronic-communications-code-disclosure.html": "VVAULT European Electronic Communications Code Disclosure",
        }

        for path, title in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                body = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn(f"<title>{title}</title>", body)
                self.assertNotIn('id="root"', body)
                self.assertNotIn("Welcome Back", body)

    def test_service_credentials_are_encrypted_local_system_files(self):
        stored_rows = []

        def _upsert(record):
            stored_rows.append(record)
            return {"action": "created", "id": "credential-row", "path": record["storage_path"]}

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server, "_body_database_dependency_status", return_value=_vvault_runtime_status(ready=True)["body_database"]
        ), patch.object(server.VAULT_FILE_REPOSITORY, "get_system_file", return_value=None), patch.object(
            server.VAULT_FILE_REPOSITORY, "upsert", side_effect=_upsert
        ):
            response = self.client.post(
                "/api/vault/credentials",
                headers={"Authorization": "Bearer svc-token"},
                json={"service": "ollama", "key": "api-key", "value": "raw-secret", "metadata": {"scope": "local"}},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(stored_rows[0]["storage_path"], "system/credentials/ollama/api-key.json")
        self.assertTrue(stored_rows[0]["is_system"])
        self.assertNotIn("raw-secret", stored_rows[0]["content"])
        self.assertIn("encrypted_value", stored_rows[0]["content"])

    def test_local_credential_login_succeeds_without_supabase_call(self):
        with patch.object(server, "_auth_repository_ready", return_value=True), patch.object(
            server,
            "db_get_user",
            return_value={
                "email": "admin@vvault.com",
                "password": "admin123",
                "name": "Admin User",
                "role": "admin",
                "source": "vvault_auth",
            },
        ), patch.object(server, "db_create_session", return_value=True):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "admin@vvault.com", "password": "admin123", "rememberMe": True},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["user"]["email"], "admin@vvault.com")
        self.assertFalse(hasattr(server, "supabase_client"))

    def test_vault_files_use_local_repository_without_supabase_table(self):
        with patch.object(
            server,
            "db_get_session",
            return_value={
                "id": "7e34f6b8-e33a-48b5-8ddb-95b94d18e296",
                "email": "devon@example.com",
                "name": "Devon",
                "role": "user",
            },
        ), patch.object(server.VAULT_FILE_REPOSITORY, "list_for_browser", return_value=[]):
            response = self.client.get("/api/vault/files", headers=_auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertEqual(payload["storage_owner"], "ovvaults.vault_files")
        self.assertEqual(payload["files"], [])
        self.assertFalse(hasattr(server, "supabase_client"))

    def test_system_file_write_is_local_transaction_not_supabase_outbox(self):
        system_row = {"id": "system-file", "storage_path": "system/current.md", "content": "queued", "is_system": True}

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), patch.object(
            server.VAULT_FILE_REPOSITORY, "get_system_file", side_effect=[None, system_row]
        ), patch.object(
            server.VAULT_FILE_REPOSITORY,
            "upsert",
            return_value={"action": "created", "id": "system-file", "path": "system/current.md"},
        ):
            response = self.client.post(
                "/api/vault/system-files",
                headers={"Authorization": "Bearer svc-token"},
                json={"storage_path": "system/current.md", "content": "queued"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["storage_mode"], "vvault_body")
        self.assertFalse(hasattr(server, "SUPABASE_WRITE_OUTBOX"))

    def test_memup_sync_uses_local_repository_path_without_supabase_table(self):
        with patch.object(
            server,
            "db_get_session",
            return_value={
                "id": "7e34f6b8-e33a-48b5-8ddb-95b94d18e296",
                "email": "devon@example.com",
                "role": "user",
            },
        ), patch.object(
            memup_sync,
            "sync_construct_memup",
            return_value={"success": True, "construct_id": "zen-001", "storage_mode": "vvault_body"},
        ):
            response = self.client.post(
                "/api/vault/memup/sync",
                headers=_auth_headers(),
                json={"construct_id": "zen-001"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertFalse(hasattr(server, "supabase_client"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
