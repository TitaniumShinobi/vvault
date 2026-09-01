import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOGIN = (REPO_ROOT / "src" / "components" / "CinematicLogin.js").read_text(encoding="utf-8")
ENROLLMENT = (REPO_ROOT / "src" / "components" / "EnrollmentFlow.js").read_text(encoding="utf-8")
CREATE_CONSTRUCT = (REPO_ROOT / "src" / "components" / "CreateConstruct.js").read_text(encoding="utf-8")
LEGACY_LOGIN = (REPO_ROOT / "src" / "components" / "LoginScreen.js").read_text(encoding="utf-8")


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

    def test_protected_mutations_use_cookie_credentialed_fetch(self):
        self.assertIn("authFetch('/api/chatty/construct/create'", CREATE_CONSTRUCT)
        self.assertNotIn("Authorization", CREATE_CONSTRUCT)
        self.assertNotIn("localStorage", CREATE_CONSTRUCT)

    def test_legacy_login_export_cannot_restore_password_flow(self):
        self.assertIn("export { default } from './CinematicLogin'", LEGACY_LOGIN)
        self.assertNotIn('type="password"', LEGACY_LOGIN.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
