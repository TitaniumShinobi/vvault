import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTH_FETCH_PATH = REPO_ROOT / "src" / "utils" / "authFetch.js"
VAULT_BROWSER_PATH = REPO_ROOT / "src" / "components" / "VaultBrowser.js"
APP_PATH = REPO_ROOT / "src" / "App.js"
CINEMATIC_LOGIN_PATH = REPO_ROOT / "src" / "components" / "CinematicLogin.js"


class TestFrontendSessionContractStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_fetch = AUTH_FETCH_PATH.read_text(encoding="utf-8")
        cls.vault_browser = VAULT_BROWSER_PATH.read_text(encoding="utf-8")
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.cinematic_login = CINEMATIC_LOGIN_PATH.read_text(encoding="utf-8")

    def test_authfetch_uses_httponly_cookie_not_browser_bearer_storage(self):
        self.assertIn("credentials: 'same-origin'", self.auth_fetch)
        self.assertNotIn("localStorage", self.auth_fetch)
        self.assertNotIn("Authorization", self.auth_fetch)

    def test_authfetch_dispatches_session_expiry_only_after_401(self):
        self.assertEqual(self.auth_fetch.count("if (response.status === 401)"), 2)
        self.assertEqual(self.auth_fetch.count("dispatchExpired();"), 2)
        self.assertIn("const SESSION_EXPIRED_EVENT = 'vvault-session-expired';", self.auth_fetch)

    def test_validate_session_uses_cookie_backed_auth_verification(self):
        self.assertIn("fetch('/api/auth/verify', { credentials: 'same-origin' })", self.auth_fetch)
        self.assertNotIn("Bearer", self.auth_fetch)

    def test_app_observes_session_expiration(self):
        self.assertIn("window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)", self.app)
        self.assertIn("window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)", self.app)
        self.assertIn("setUser(null);", self.app)

    def test_provider_oauth_uses_top_level_canonical_navigation(self):
        self.assertIn("window.location.assign(`/api/auth/oauth/${provider}`)", self.cinematic_login)
        self.assertNotIn("fetch('/api/auth/oauth/", self.cinematic_login)

    def test_vaultbrowser_uses_authfetch_for_protected_routes(self):
        for route in (
            "/api/chatty/constructs",
            "/api/vault/user-info",
            "/api/vault/files",
            "/api/vault/memup/sync",
        ):
            self.assertIn(f"authFetch('{route}", self.vault_browser)
        self.assertIn("authFetch('/api/vault/knowledge-files/upload'", self.vault_browser)
        self.assertNotIn("vvault_token", self.vault_browser)
        self.assertNotIn("vvault_user", self.vault_browser)

    def test_app_uses_current_health_and_status_routes(self):
        self.assertIn("fetch('/api/health')", self.app)
        self.assertIn("fetch('/api/status')", self.app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
