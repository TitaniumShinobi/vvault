import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SERVER = (ROOT / "vvault" / "server" / "vvault_web_server.py").read_text(encoding="utf-8")
REPOSITORY = (ROOT / "vvault" / "server" / "vvault_auth_repository.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "vvault" / "migrations" / "0035_chatty_pairing_intents.up.sql").read_text(encoding="utf-8")


class TestChattyPairingIntentContract(unittest.TestCase):
    def test_database_intent_is_opaque_expiring_and_single_use(self):
        self.assertIn("code_digest TEXT PRIMARY KEY", MIGRATION)
        self.assertIn("audience = 'chatty-developer-local'", MIGRATION)
        self.assertIn("expires_at <= created_at + interval '60 seconds'", MIGRATION)
        self.assertIn("consumed_at", MIGRATION)
        self.assertIn("chatty_account_id UUID", MIGRATION)
        self.assertIn("link_id UUID", MIGRATION)

    def test_creation_requires_active_trusted_normal_session(self):
        method = REPOSITORY.split("def create_chatty_pairing_intent", 1)[1].split("def consume_chatty_pairing_intent", 1)[0]
        for requirement in ("enrollment_session_kind='NORMAL'", "users.account_state='ACTIVE'", "enrollment_devices.status='TRUSTED'", "sessions.revoked_at IS NULL"):
            self.assertIn(requirement, method)

    def test_browser_route_does_not_return_identity_or_session_material(self):
        route = SERVER.split("def create_chatty_pairing_intent", 1)[1].split("def _chatty_pairing_client_authenticated", 1)[0]
        self.assertIn("/api/auth/pairing-intents/chatty", SERVER)
        self.assertIn("expires_in\": 60", route)
        self.assertIn("Cache-Control", route)
        self.assertNotIn('"email"', route)
        self.assertNotIn('"provider"', route)
        self.assertNotIn("vvault_session", route)

    def test_callback_is_configuration_bound_not_client_supplied(self):
        route = SERVER.split("def create_chatty_pairing_intent", 1)[1].split("def _chatty_pairing_client_authenticated", 1)[0]
        self.assertIn("CHATTY_PAIRING_CALLBACK_URL", SERVER)
        self.assertNotIn("request.get_json", route)

    def test_redemption_is_server_authenticated_and_returns_only_opaque_link(self):
        route = SERVER.split("def redeem_chatty_pairing_intent", 1)[1].split("@app.route('/api/auth/enrollment/consents'", 1)[0]
        self.assertIn("X-Chatty-Client-Id", SERVER)
        self.assertIn("CHATTY_PAIRING_CLIENT_SECRET", SERVER)
        self.assertIn("hmac.compare_digest", SERVER)
        self.assertIn("consume_chatty_pairing_intent", route)
        self.assertIn('"link_id"', route)
        self.assertNotIn('"email"', route)
        self.assertNotIn('"provider"', route)
        self.assertNotIn('"user_id"', route)


if __name__ == "__main__":
    unittest.main(verbosity=2)
