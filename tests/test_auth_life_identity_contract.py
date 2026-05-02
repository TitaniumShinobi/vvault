import unittest
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server


def _degraded_state(**overrides):
    state = {
        "connection_state": "degraded",
        "canonical": False,
        "storage_mode": "none",
        "last_error_code": "SUPABASE_PROBE_TIMEOUT",
        "outage_id": "oauth-test-outage",
    }
    state.update(overrides)
    return state


class TestAuthLifeIdentityContract(unittest.TestCase):
    def test_auth_identity_resolution_blocks_replacement_life_id_when_authority_missing(self):
        receipt = server._resolve_auth_life_identity(
            email="devon@example.com",
            proposed_life_id="devon_woodson_1774390416168",
        )

        self.assertFalse(receipt["ok"])
        self.assertFalse(receipt["should_mint"])
        self.assertIn(receipt["error_code"], {"IDENTITY_AUTHORITY_UNAVAILABLE", "LIFE_ID_NOT_FOUND"})
        self.assertEqual(receipt["proposed_life_id"], "devon_woodson_1774390416168")

    def test_auth_identity_resolution_fails_closed_on_trusted_life_id_conflict(self):
        receipt = server._resolve_auth_life_identity(
            email="devon@example.com",
            fallback_user={"life_user_id": "devon_woodson_1762969514958"},
            supabase_user={"life_user_id": "devon_woodson_1774390416168"},
        )

        self.assertFalse(receipt["ok"])
        self.assertFalse(receipt["canonical"])
        self.assertFalse(receipt["should_mint"])
        self.assertEqual(receipt["error_code"], "IDENTITY_CONFLICT")
        self.assertEqual(
            receipt["conflict_life_ids"],
            ["devon_woodson_1762969514958", "devon_woodson_1774390416168"],
        )

    def test_oauth_callback_uses_identity_receipt_before_timestamp_id_upsert(self):
        source = server.Path(server.__file__).read_text(encoding="utf-8")
        proposed = source.index("proposed_user_id = f\"{safe_name}_{ts}\"")
        resolve = source.index("_resolve_auth_life_identity", proposed)
        upsert = source.index("_upsert_supabase_user_record(user_id, users_email, users_name, resolved_role)", resolve)

        self.assertLess(proposed, resolve)
        self.assertLess(resolve, upsert)
        self.assertIn("return _auth_identity_failure_response(identity_receipt)", source[resolve:upsert])

    def test_oauth_callback_checks_steward_before_supabase_lookup(self):
        source = server.Path(server.__file__).read_text(encoding="utf-8")
        callback = source.index("def google_oauth_callback")
        authority = source.index("_oauth_identity_authority_available()", callback)
        lookup = source.index("supabase_client.table('users').select('*').eq('email', users_email).execute()", callback)
        proposed = source.index("proposed_user_id = f\"{safe_name}_{ts}\"", callback)

        self.assertLess(authority, lookup)
        self.assertLess(authority, proposed)

    def test_oauth_callback_degraded_supabase_redirects_without_lookup_or_mint(self):
        server.app.config["TESTING"] = True
        server.app.secret_key = "oauth-test-secret"
        client = server.app.test_client()
        degraded = _degraded_state()
        supabase = Mock()

        with client.session_transaction() as flask_session:
            flask_session["oauth_callback_url"] = "http://localhost:8000/api/auth/google/callback"
            flask_session["oauth_frontend_url"] = "http://localhost:7784"

        with patch.object(server, "google_client", Mock()), patch.object(
            server, "GOOGLE_CLIENT_ID", "google-client-id"
        ), patch.object(server, "GOOGLE_CLIENT_SECRET", "google-client-secret"), patch.object(
            server.SUPABASE_STEWARD, "allow_write", return_value=(False, degraded)
        ), patch.object(server, "supabase_client", supabase), patch.object(
            server, "_upsert_supabase_user_record", side_effect=AssertionError("must not mint or upsert")
        ), patch.object(server.requests, "get", side_effect=AssertionError("must not call Google/Supabase during degraded guard")), patch.object(
            server.requests, "post", side_effect=AssertionError("must not exchange token during degraded guard")
        ):
            response = client.get("/api/auth/google/callback?code=test-code")

        self.assertEqual(response.status_code, 302)
        self.assertIn("oauth_error=", response.headers["Location"])
        self.assertIn("Supabase%20identity%20authority%20is%20unavailable", response.headers["Location"])
        supabase.table.assert_not_called()

    def test_oauth_login_degraded_supabase_redirects_without_google_discovery(self):
        server.app.config["TESTING"] = True
        server.app.secret_key = "oauth-test-secret"
        client = server.app.test_client()
        degraded = _degraded_state()

        with patch.object(server, "google_client", Mock()), patch.object(
            server, "GOOGLE_CLIENT_ID", "google-client-id"
        ), patch.object(server, "GOOGLE_CLIENT_SECRET", "google-client-secret"), patch.object(
            server.SUPABASE_STEWARD, "allow_write", return_value=(False, degraded)
        ), patch.object(
            server.requests, "get", side_effect=AssertionError("must not call Google discovery during degraded guard")
        ):
            response = client.get(
                "/api/auth/google",
                headers={"Referer": "http://localhost:7784/"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("oauth_error=", response.headers["Location"])
        self.assertIn("Supabase%20identity%20authority%20is%20unavailable", response.headers["Location"])

    def test_oauth_identity_authority_requires_canonical_connected_write_state(self):
        connected_noncanonical = _degraded_state(connection_state="connected", canonical=False)
        with patch.object(server.SUPABASE_STEWARD, "allow_write", return_value=(True, connected_noncanonical)):
            available, state = server._oauth_identity_authority_available()

        self.assertFalse(available)
        self.assertFalse(state["canonical"])

        connected_canonical = _degraded_state(connection_state="connected", canonical=True, storage_mode="supabase")
        with patch.object(server.SUPABASE_STEWARD, "allow_write", return_value=(True, connected_canonical)):
            available, state = server._oauth_identity_authority_available()

        self.assertTrue(available)
        self.assertTrue(state["canonical"])

    def test_google_oauth_health_reports_degraded_identity_authority_from_steward(self):
        server.app.config["TESTING"] = True
        client = server.app.test_client()
        degraded = _degraded_state(latency_ms=8003)

        with patch.object(server, "GOOGLE_CLIENT_ID", "google-client-id"), patch.object(
            server, "GOOGLE_CLIENT_SECRET", "google-client-secret"
        ), patch.object(server.SUPABASE_STEWARD, "allow_write", return_value=(False, degraded)):
            response = client.get("/api/auth/google/health")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertTrue(payload["oauth_configured"])
        self.assertFalse(payload["supabase_identity_authority_available"])
        self.assertEqual(payload["supabase_mode"], "degraded")
        self.assertEqual(payload["connection_state"], "degraded")
        self.assertFalse(payload["canonical"])
        self.assertEqual(payload["storage_mode"], "none")
        self.assertEqual(payload["last_error_code"], "SUPABASE_PROBE_TIMEOUT")
        self.assertEqual(payload["latency_ms"], 8003)
        self.assertIn("immutable LIFE identity", payload["error"])
        self.assertNotEqual(payload["supabase_mode"], "healthy")

    def test_google_oauth_health_reports_available_only_for_connected_canonical_authority(self):
        server.app.config["TESTING"] = True
        client = server.app.test_client()
        connected = _degraded_state(
            connection_state="connected",
            canonical=True,
            storage_mode="supabase",
            last_error_code=None,
            latency_ms=42,
        )

        with patch.object(server, "GOOGLE_CLIENT_ID", "google-client-id"), patch.object(
            server, "GOOGLE_CLIENT_SECRET", "google-client-secret"
        ), patch.object(server.SUPABASE_STEWARD, "allow_write", return_value=(True, connected)):
            response = client.get("/api/auth/google/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["oauth_configured"])
        self.assertTrue(payload["supabase_identity_authority_available"])
        self.assertEqual(payload["supabase_mode"], "connected")
        self.assertEqual(payload["connection_state"], "connected")
        self.assertTrue(payload["canonical"])
        self.assertEqual(payload["storage_mode"], "supabase")
        self.assertIsNone(payload["last_error_code"])
        self.assertEqual(payload["latency_ms"], 42)
        self.assertIsNone(payload["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
