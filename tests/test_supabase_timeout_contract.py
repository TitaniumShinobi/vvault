import os
import unittest
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server


TIMEOUT_ERROR = Exception("{'message': 'JSON could not be generated', 'code': 522}")


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def _fake_users_query(*, side_effect=None, data=None):
    query = Mock()
    query.select.return_value = query
    query.eq.return_value = query
    if side_effect is not None:
        query.execute.side_effect = side_effect
    else:
        result = Mock()
        result.data = data if data is not None else []
        query.execute.return_value = result
    return query


class TestSupabaseTimeoutContract(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def test_vault_files_timeout_returns_soft_degrade_contract(self):
        users_query = _fake_users_query(data=[{"id": "user-123", "name": "Devon"}])
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ), patch.object(server, "_fetch_all_rows", side_effect=TIMEOUT_ERROR):
            response = self.client.get("/api/vault/files", headers=_auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["files"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["user_root"], "Vault")

    def test_vault_user_info_timeout_returns_soft_degrade_contract_with_schema(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.get("/api/vault/user-info", headers=_auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertIn("display_name", payload)
        self.assertIn("is_admin", payload)
        self.assertIn("root_label", payload)
        self.assertIn("user_id", payload)

    def test_chatty_constructs_timeout_returns_soft_degrade_contract(self):
        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), patch.object(
            server, "supabase_client", object()
        ), patch.object(server, "_get_authenticated_user_id", return_value="7e34f6b8-e33a-48b5-8ddb-95b94d18e296"), patch.object(
            server, "_fetch_all_rows", side_effect=TIMEOUT_ERROR
        ):
            response = self.client.get(
                "/api/chatty/constructs",
                headers={"X-Chatty-Key": "svc-token", "X-Chatty-User": "devon@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")
        self.assertEqual(payload["constructs"], [])
        self.assertEqual(payload["count"], 0)

    def test_memup_sync_timeout_returns_strict_503_contract(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.post(
                "/api/vault/memup/sync",
                headers=_auth_headers(),
                json={"construct_id": "zen-001"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertNotIn("success", payload)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")

    def test_simdrive_write_timeout_returns_strict_503_contract(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "db_get_session", return_value={"email": "devon@example.com", "role": "user"}
        ):
            response = self.client.post(
                "/api/vault/simdrive/write",
                headers=_auth_headers(),
                json={"construct_id": "zen-001", "filename": "continuity.json", "content": "{}"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertNotIn("success", payload)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")

    def test_service_credentials_timeout_returns_strict_503_contract(self):
        users_query = _fake_users_query(side_effect=TIMEOUT_ERROR)
        fake_supabase = Mock()
        fake_supabase.table.return_value = users_query

        with patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "VVAULT_SERVICE_TOKEN", "svc-token"
        ):
            response = self.client.post(
                "/api/vault/credentials",
                headers={"X-Service-Token": "svc-token"},
                json={"key": "alpha", "service": "fxshinobi", "value": "secret"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertNotIn("success", payload)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")

    def test_ledger_generate_timeout_returns_strict_503_contract(self):
        with patch.dict(os.environ, {"VVAULT_SERVICE_TOKEN": "svc-token"}), patch.object(
            server, "supabase_client", object()
        ), patch.object(server, "_get_transcript_files", side_effect=TIMEOUT_ERROR):
            response = self.client.post(
                "/api/chatty/construct/zen-001/ledger/generate",
                headers={"X-Chatty-Key": "svc-token", "X-Chatty-User": "devon@example.com"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertNotIn("success", payload)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["supabase_available"])
        self.assertEqual(payload["error_code"], "SUPABASE_TIMEOUT_522")


if __name__ == "__main__":
    unittest.main(verbosity=2)
