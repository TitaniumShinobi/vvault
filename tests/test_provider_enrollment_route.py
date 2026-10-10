import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from vvault.server import vvault_auth_crypto
from vvault.server import vvault_web_server as server


class TestProviderEnrollmentRoute(unittest.TestCase):
    def test_consent_completes_signup_without_passkey_or_device_gate(self):
        pending = {
            "user_id": "owner-a",
            "session_id": "pending-a",
            "enrollment_session_kind": "PENDING_ENROLLMENT",
            "account_state": "PENDING_ENROLLMENT",
        }
        documents = [
            {"key": "terms", "version": "current", "sha256": "terms"},
            {"key": "privacy", "version": "current", "sha256": "privacy"},
            {"key": "eeccd", "version": "current", "sha256": "eeccd"},
        ]
        complete = Mock(return_value={
            "id": "normal-a",
            "user_id": "owner-a",
            "enrollment_session_kind": "NORMAL",
            "enrollment_device_id": None,
        })
        with (
            patch.object(server, "_enrollment_session_from_request", return_value=pending),
            patch.object(server, "_enrollment_documents", return_value=documents),
            patch.object(server, "_identity_hmac_key", return_value=b"test-key"),
            patch.object(server, "_runtime_is_production", return_value=False),
            patch.object(vvault_auth_crypto, "opaque_token", return_value="normal-token"),
            patch.object(vvault_auth_crypto, "keyed_digest", side_effect=lambda value, _key: f"digest:{value}"),
            patch.object(server.AUTH_REPOSITORY, "complete_provider_enrollment", complete),
            server.app.test_request_context(
                "/api/auth/enrollment/consents", method="POST",
                headers={"User-Agent": "test-browser"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            ),
        ):
            response = server.accept_canonical_enrollment_consents()

        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["enrollment_completed"])
        self.assertEqual(payload["account_state"], "ACTIVE")
        self.assertIn("vvault_session=normal-token", response.headers.get("Set-Cookie", ""))
        self.assertIn("vvault_enrollment_session=", response.headers.get("Set-Cookie", ""))
        kwargs = complete.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "owner-a")
        self.assertEqual(kwargs["pending_session_id"], "pending-a")
        self.assertEqual(kwargs["documents"], documents)
        self.assertIsInstance(kwargs["expires_at"], datetime)


if __name__ == "__main__":
    unittest.main(verbosity=2)
