import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "vvault" / "server" / "vvault_web_server.py"
STEWARD_PATH = REPO_ROOT / "vvault" / "server" / "supabase_connection_steward.py"
LAUNCHER_PATH = REPO_ROOT / "scripts" / "open-vvault-standalone.sh"
PACKAGE_PATH = REPO_ROOT / "package.json"

TIMEOUT_ERROR = Exception("{'message': 'JSON could not be generated', 'code': 522}")


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def _fake_users_query(*, side_effect=None, data=None):
    query = Mock()
    query.select.return_value = query
    query.eq.return_value = query
    query.limit.return_value = query
    if side_effect is not None:
        query.execute.side_effect = side_effect
    else:
        result = Mock()
        result.data = data if data is not None else []
        query.execute.return_value = result
    return query


def _steward_snapshot(**overrides):
    snapshot = {
        "connection_state": "connected",
        "storage_mode": "supabase",
        "outage_id": "outage-test",
        "latency_ms": 42,
        "recovery_proven_at": "2026-04-30T00:00:00+00:00",
        "last_error_code": None,
    }
    snapshot.update(overrides)
    return snapshot


class TestSupabaseTimeoutContractStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PATH.read_text(encoding="utf-8")
        cls.steward = STEWARD_PATH.read_text(encoding="utf-8")
        cls.launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        cls.package = PACKAGE_PATH.read_text(encoding="utf-8")

    def test_health_exposes_connection_steward_and_ready_is_strict(self):
        self.assertIn("SUPABASE_STEWARD = SupabaseConnectionSteward", self.server)
        self.assertIn("@app.route('/api/ready')", self.server)
        self.assertIn("ready = connection.get(\"connection_state\") == \"connected\"", self.server)
        self.assertIn("200 if ready else 503", self.server)
        self.assertIn("server_pid", self.server)
        self.assertIn("repo_root", self.server)

    def test_writes_are_blocked_without_connected_state(self):
        self.assertIn("def _gate_supabase_canonical_writes()", self.server)
        self.assertIn("SUPABASE_STEWARD.allow_write()", self.server)
        self.assertIn("def _supabase_write_block_response", self.server)
        self.assertIn("canonical\": False", self.server)
        self.assertIn("storage_mode\": \"none\"", self.server)

    def test_reads_return_labeled_noncanonical_degraded_contract(self):
        self.assertIn("def _supabase_read_block_response", self.server)
        self.assertIn("supabase_available", self.server)
        self.assertIn("connection_state", self.server)
        self.assertIn("outage_id", self.server)
        self.assertIn("canonical\": False", self.server)

    def test_steward_tracks_recovery_and_metadata_only_proof(self):
        self.assertIn("recovery_successes_required", self.steward)
        self.assertIn("probe_timeout_seconds: float = 8.0", self.steward)
        self.assertIn('os.environ.get("VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS", "8")', self.server)
        self.assertIn("client.table(\"users\").select(\"id\").limit(1).execute()", self.steward)
        self.assertIn("client.table(\"vault_files\").select(\"id\").limit(1).execute()", self.steward)
        self.assertIn("SUPABASE_CONNECTION_STEWARD", self.steward)
        self.assertIn("connection_state", self.steward)
        self.assertIn("pending_outbox_count", self.steward)
        self.assertIn("reconciliation_status", self.steward)
        self.assertIn("blocked_replay_count", self.steward)
        self.assertIn("def update_outbox_visibility", self.steward)

    def test_steward_blocks_identity_minting_and_stale_replay_overwrites(self):
        self.assertIn("def resolve_life_identity", self.steward)
        self.assertIn("\"should_mint\": False", self.steward)
        self.assertIn("IDENTITY_AUTHORITY_UNAVAILABLE", self.steward)
        self.assertIn("IDENTITY_CONFLICT", self.steward)
        self.assertIn("def plan_replay", self.steward)
        self.assertIn("SCHEMA_DOWNGRADE_REJECTED", self.steward)
        self.assertIn("STALE_REPLAY_REJECTED", self.steward)
        self.assertIn("merged = dict(remote)", self.steward)

    def test_launcher_uses_ready_and_blocks_duplicate_backend_listeners(self):
        self.assertIn("/api/ready", self.launcher)
        self.assertIn("backend_listener_count()", self.launcher)
        self.assertIn("ambiguous duplicate listeners", self.launcher)
        self.assertIn('if [[ "$backend_state" == "ambiguous" ]]', self.launcher)
        self.assertIn("start_frontend()", self.launcher)
        self.assertIn("start_devfull()", self.launcher)
        self.assertIn("start_backend", self.launcher)
        self.assertIn('VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS="${VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS:-8}"', self.launcher)
        self.assertIn('VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS="$VVAULT_SUPABASE_PROBE_TIMEOUT_SECONDS"', self.launcher)
        self.assertIn("./node_modules/.bin/webpack-dev-server --mode development --no-watch-options-stdin", self.launcher)
        self.assertIn('if [[ "$backend_state" == "dead" ]]; then', self.launcher)
        self.assertIn('if [[ "$frontend_state" == "dead" ]]; then', self.launcher)

    def test_frontend_script_survives_detached_launcher_stdin(self):
        self.assertIn("webpack-dev-server --mode development --no-watch-options-stdin", self.package)


class TestSupabaseTimeoutRouteContract(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def _route_context(self, *, read_allowed=True, write_allowed=True, snapshot=None):
        state = snapshot or _steward_snapshot()
        return patch.multiple(
            server.SUPABASE_STEWARD,
            allow_read=Mock(return_value=(read_allowed, state)),
            allow_write=Mock(return_value=(write_allowed, state)),
            snapshot=Mock(return_value=state),
        )

    def test_ready_route_is_strict_at_runtime(self):
        with self._route_context(snapshot=_steward_snapshot(connection_state="warming")):
            warming = self.client.get("/api/ready")
        self.assertEqual(warming.status_code, 503)
        warming_payload = warming.get_json()
        self.assertFalse(warming_payload["ready"])
        self.assertEqual(warming_payload["status"], "not_ready")

        with self._route_context(snapshot=_steward_snapshot(connection_state="connected")):
            connected = self.client.get("/api/ready")
        self.assertEqual(connected.status_code, 200)
        connected_payload = connected.get_json()
        self.assertTrue(connected_payload["ready"])
        self.assertEqual(connected_payload["status"], "ready")

    def assert_soft_degrade_522(self, response, *, collection_key=None):
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["storage_mode"], "supabase")
        self.assertEqual(payload["connection_state"], "connected")
        if collection_key:
            self.assertEqual(payload[collection_key], [])
            self.assertEqual(payload["count"], 0)
        return payload

    def assert_strict_503_522(self, response):
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertNotIn("success", payload)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["storage_mode"], "supabase")
        self.assertEqual(payload["connection_state"], "connected")
        return payload

    def assert_soft_degrade_read_block(self, response, *, collection_key=None):
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["storage_mode"], "none")
        self.assertEqual(payload["connection_state"], "blocked")
        if collection_key:
            self.assertEqual(payload[collection_key], [])
            self.assertEqual(payload["count"], 0)
        return payload

    def test_vault_files_timeout_returns_soft_degrade_contract(self):
        users_query = _fake_users_query(data=[{"id": "user-123", "name": "Devon"}])
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with self._route_context(), patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ), patch.object(server, "_fetch_all_rows", side_effect=TIMEOUT_ERROR):
            response = self.client.get("/api/vault/files", headers=_auth_headers())

        payload = self.assert_soft_degrade_522(response, collection_key="files")
        self.assertEqual(payload["user_root"], "Vault")

    def test_vault_user_info_timeout_returns_soft_degrade_contract_with_schema(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with self._route_context(), patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.get("/api/vault/user-info", headers=_auth_headers())

        payload = self.assert_soft_degrade_522(response)
        self.assertEqual(payload["display_name"], "Devon")
        self.assertFalse(payload["is_admin"])
        self.assertEqual(payload["root_label"], "Devon")
        self.assertIsNone(payload["user_id"])

    def test_chatty_constructs_timeout_returns_soft_degrade_contract(self):
        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), self._route_context(), patch.object(
            server, "supabase_client", object()
        ), patch.object(
            server,
            "_get_authenticated_user_id",
            return_value="7e34f6b8-e33a-48b5-8ddb-95b94d18e296",
        ), patch.object(server, "_fetch_all_rows", side_effect=TIMEOUT_ERROR):
            response = self.client.get(
                "/api/chatty/constructs",
                headers={"X-Chatty-Key": "svc-token", "X-Chatty-User": "devon@example.com"},
            )

        self.assert_soft_degrade_522(response, collection_key="constructs")

    def test_vault_files_read_block_returns_soft_degrade_contract(self):
        blocked = _steward_snapshot(
            connection_state="blocked",
            storage_mode="none",
            last_error_code="SUPABASE_TIMEOUT_522",
            recovery_proven_at=None,
        )

        with self._route_context(read_allowed=False, snapshot=blocked), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.get("/api/vault/files", headers=_auth_headers())

        payload = self.assert_soft_degrade_read_block(response, collection_key="files")
        self.assertEqual(payload["user_root"], "Vault")

    def test_vault_user_info_read_block_returns_soft_degrade_schema(self):
        blocked = _steward_snapshot(
            connection_state="blocked",
            storage_mode="none",
            last_error_code="SUPABASE_TIMEOUT_522",
            recovery_proven_at=None,
        )

        with self._route_context(read_allowed=False, snapshot=blocked), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.get("/api/vault/user-info", headers=_auth_headers())

        payload = self.assert_soft_degrade_read_block(response)
        self.assertEqual(payload["display_name"], "Devon")
        self.assertEqual(payload["root_label"], "Devon")
        self.assertIsNone(payload["user_id"])

    def test_chatty_constructs_read_block_returns_soft_degrade_contract(self):
        blocked = _steward_snapshot(
            connection_state="blocked",
            storage_mode="none",
            last_error_code="SUPABASE_TIMEOUT_522",
            recovery_proven_at=None,
        )

        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), self._route_context(
            read_allowed=False,
            snapshot=blocked,
        ):
            response = self.client.get(
                "/api/chatty/constructs",
                headers={"X-Chatty-Key": "svc-token", "X-Chatty-User": "devon@example.com"},
            )

        self.assert_soft_degrade_read_block(response, collection_key="constructs")

    def test_mutating_route_write_block_returns_strict_503_contract_before_route_work(self):
        blocked = _steward_snapshot(
            connection_state="blocked",
            storage_mode="none",
            last_error_code="SUPABASE_TIMEOUT_522",
            recovery_proven_at=None,
        )

        with self._route_context(write_allowed=False, snapshot=blocked), patch.object(
            server, "supabase_client", Mock()
        ) as fake_client:
            response = self.client.post(
                "/api/vault/memup/sync",
                headers=_auth_headers(),
                json={"construct_id": "zen-001"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["storage_mode"], "none")
        self.assertEqual(payload["connection_state"], "blocked")
        fake_client.table.assert_not_called()

    def test_system_file_write_can_queue_durable_receipt_when_supabase_is_blocked(self):
        blocked = _steward_snapshot(
            connection_state="blocked",
            storage_mode="none",
            last_error_code="SUPABASE_TIMEOUT_522",
            recovery_proven_at=None,
        )
        receipt = {
            "ok": True,
            "action": "queued",
            "outbox_id": "outbox-test",
            "operation": "vault_file_upsert",
            "table": "vault_files",
            "idempotency_key": "vault_files:system_file:system/current.md:abc123",
        }

        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), self._route_context(
            write_allowed=False,
            snapshot=blocked,
        ), patch.object(server, "supabase_client", None), patch.object(
            server.SUPABASE_WRITE_OUTBOX, "queue_write", return_value=receipt
        ) as queue_write:
            response = self.client.post(
                "/api/vault/system-files",
                headers={"Authorization": "Bearer svc-token"},
                json={"storage_path": "system/current.md", "content": "queued"},
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["queued"])
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["storage_mode"], "outbox")
        self.assertEqual(payload["outbox_receipt"]["outbox_id"], "outbox-test")
        queue_write.assert_called_once()
        self.assertEqual(queue_write.call_args.kwargs["operation"], "vault_file_upsert")
        self.assertEqual(queue_write.call_args.kwargs["table"], "vault_files")
        self.assertEqual(queue_write.call_args.kwargs["identity_fields"], ["storage_path", "is_system", "user_id"])

    def test_memup_sync_timeout_returns_strict_503_contract(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with self._route_context(), patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.post(
                "/api/vault/memup/sync",
                headers=_auth_headers(),
                json={"construct_id": "zen-001"},
            )

        self.assert_strict_503_522(response)

    def test_simdrive_write_timeout_returns_strict_503_contract(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with self._route_context(), patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.post(
                "/api/vault/simdrive/write",
                headers=_auth_headers(),
                json={"construct_id": "zen-001", "filename": "continuity.json", "content": "{}"},
            )

        self.assert_strict_503_522(response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
