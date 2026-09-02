import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOGIN = (REPO_ROOT / "src" / "components" / "CinematicLogin.js").read_text(encoding="utf-8")
ENROLLMENT = (REPO_ROOT / "src" / "components" / "EnrollmentFlow.js").read_text(encoding="utf-8")
CREATE_CONSTRUCT = (REPO_ROOT / "src" / "components" / "CreateConstruct.js").read_text(encoding="utf-8")
LEGACY_LOGIN = (REPO_ROOT / "src" / "components" / "LoginScreen.js").read_text(encoding="utf-8")
SERVER = (REPO_ROOT / "vvault" / "server" / "vvault_web_server.py").read_text(encoding="utf-8")
AUTH_REPOSITORY = (REPO_ROOT / "vvault" / "server" / "vvault_auth_repository.py").read_text(encoding="utf-8")


class TestFrontendEnrollmentContract(unittest.TestCase):
    def test_identity_entry_offers_provider_and_magic_link_without_password(self):
        self.assertIn("Continue with Google", LOGIN)
        self.assertIn("Continue with GitHub", LOGIN)
        self.assertIn("Email me a secure link", LOGIN)
        self.assertNotIn('type="password"', LOGIN.lower())

    def test_enrollment_orders_consent_passkey_recovery_then_activation(self):
        for endpoint in (
            "/api/auth/enrollment/consents",
            "/api/auth/enrollment/webauthn/challenge",
            "/api/auth/enrollment/webauthn/register",
            "/api/auth/enrollment/recovery-codes",
            "/api/auth/enrollment/activate",
        ):
            self.assertIn(endpoint, ENROLLMENT)
        self.assertIn("step === 'consent'", ENROLLMENT)
        self.assertIn("step === 'passkey'", ENROLLMENT)
        self.assertIn("step === 'recovery'", ENROLLMENT)
        self.assertIn("step === 'activate'", ENROLLMENT)

    def test_enrollment_status_is_a_native_api_route(self):
        self.assertIn("@app.route('/api/auth/enrollment/status', methods=['GET'])", SERVER)
        route = SERVER.split("def canonical_enrollment_status", 1)[1].split("def _chatty_pairing_callback", 1)[0]
        self.assertIn('"pending"', route)
        self.assertNotIn('"email"', route)

    def test_device_verification_routes_are_separate_from_legal_recertification(self):
        for endpoint in (
            "/api/auth/devices/status",
            "/api/auth/devices/webauthn/challenge",
            "/api/auth/devices/webauthn/assert",
            "/api/auth/devices/transfer/start",
        ):
            self.assertIn(endpoint, SERVER)
        self.assertIn('("vvault:eeccd", "VVAULT_EUROPEAN_ELECTRONIC_COMMNICATION_CODE_DISCLOSURE.md")', SERVER)
        self.assertIn("def issue_known_device_session", AUTH_REPOSITORY)

    def test_protected_mutations_use_cookie_credentialed_fetch(self):
        self.assertIn("authFetch('/api/chatty/construct/create'", CREATE_CONSTRUCT)
        self.assertNotIn("Authorization", CREATE_CONSTRUCT)
        self.assertNotIn("localStorage", CREATE_CONSTRUCT)

    def test_legacy_login_export_cannot_restore_password_flow(self):
        self.assertIn("export { default } from './CinematicLogin'", LEGACY_LOGIN)
        self.assertNotIn('type="password"', LEGACY_LOGIN.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
