import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTH_FETCH_PATH = REPO_ROOT / "src" / "utils" / "authFetch.js"
VAULT_BROWSER_PATH = REPO_ROOT / "src" / "components" / "VaultBrowser.js"


class TestFrontendOutageContractStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_fetch = AUTH_FETCH_PATH.read_text(encoding="utf-8")
        cls.vault_browser = VAULT_BROWSER_PATH.read_text(encoding="utf-8")

    def test_authfetch_clears_session_only_on_401_paths(self):
        self.assertIn("if (response.status === 401)", self.auth_fetch)
        self.assertIn("clearSession();", self.auth_fetch)
        self.assertEqual(self.auth_fetch.count("clearSession();"), 2)
        self.assertIn("if (response.status === 401) {", self.auth_fetch)

    def test_authfetch_treats_degraded_503_as_outage_contract(self):
        self.assertIn("response.status !== 200 && response.status !== 503", self.auth_fetch)
        self.assertIn("payload?.degraded === true", self.auth_fetch)
        self.assertIn("payload?.supabase_available === false", self.auth_fetch)
        self.assertIn("SUPABASE_TIMEOUT_522", self.auth_fetch)

    def test_authfetch_sanitizes_cloudflare_html_into_degraded_object(self):
        self.assertIn("isSupabaseOutageText", self.auth_fetch)
        self.assertIn("supabase_available: false", self.auth_fetch)
        self.assertIn("degraded: true", self.auth_fetch)
        self.assertIn("error_code: 'SUPABASE_TIMEOUT_522'", self.auth_fetch)
        self.assertIn("lowered.includes('<!doctype html')", self.auth_fetch)
        self.assertIn("lowered.includes('cloudflare')", self.auth_fetch)

    def test_authfetch_has_outage_dedupe_window(self):
        self.assertIn("OUTAGE_DEDUPE_WINDOW_MS = 4000", self.auth_fetch)
        self.assertIn("outageEmitTimestamps = new Map()", self.auth_fetch)
        self.assertIn("shouldSuppressOutageEmit(signature)", self.auth_fetch)

    def test_vaultbrowser_renders_single_persistent_outage_banner(self):
        self.assertEqual(self.vault_browser.count("vault-notice vault-notice-outage"), 1)
        self.assertIn("notice && !degraded.active", self.vault_browser)

    def test_vaultbrowser_retry_refetches_read_triplet(self):
        pattern = re.compile(
            r"const handleRefresh = async \(\) => \{\s*"
            r"setRefreshing\(true\);\s*setError\(null\);\s*try \{\s*"
            r"await fetchUserInfo\(\);\s*await fetchFiles\(true\);\s*await fetchConstructs\(\);",
            re.S,
        )
        self.assertRegex(self.vault_browser, pattern)

    def test_vaultbrowser_listens_for_outage_event(self):
        self.assertIn("window.addEventListener(SUPABASE_OUTAGE_EVENT, onOutage)", self.vault_browser)
        self.assertIn("window.removeEventListener(SUPABASE_OUTAGE_EVENT, onOutage)", self.vault_browser)


if __name__ == "__main__":
    unittest.main(verbosity=2)
