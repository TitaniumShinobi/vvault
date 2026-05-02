import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTH_FETCH_PATH = REPO_ROOT / "src" / "utils" / "authFetch.js"
VAULT_BROWSER_PATH = REPO_ROOT / "src" / "components" / "VaultBrowser.js"
APP_PATH = REPO_ROOT / "src" / "App.js"
CINEMATIC_LOGIN_PATH = REPO_ROOT / "src" / "components" / "CinematicLogin.js"


class TestFrontendOutageContractStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_fetch = AUTH_FETCH_PATH.read_text(encoding="utf-8")
        cls.vault_browser = VAULT_BROWSER_PATH.read_text(encoding="utf-8")
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.cinematic_login = CINEMATIC_LOGIN_PATH.read_text(encoding="utf-8")

    def test_authfetch_clears_session_only_on_401_paths(self):
        self.assertIn("if (response.status === 401)", self.auth_fetch)
        self.assertIn("clearSession();", self.auth_fetch)
        self.assertEqual(self.auth_fetch.count("clearSession();"), 3)
        self.assertIn("if (response.status === 401) {", self.auth_fetch)
        self.assertIn("isAuthenticatedApiRequest(url) && (!token || sessionExpired)", self.auth_fetch)

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
            r"if \(sessionExpired\) return;\s*setRefreshing\(true\);\s*setError\(null\);\s*try \{\s*"
            r"const userInfoOk = await fetchUserInfo\(\);\s*if \(!userInfoOk\) return;\s*"
            r"await fetchFiles\(true\);\s*await fetchConstructs\(\);",
            re.S,
        )
        self.assertRegex(self.vault_browser, pattern)

    def test_vaultbrowser_listens_for_outage_event(self):
        self.assertIn("window.addEventListener(SUPABASE_OUTAGE_EVENT, onOutage)", self.vault_browser)
        self.assertIn("window.removeEventListener(SUPABASE_OUTAGE_EVENT, onOutage)", self.vault_browser)

    def test_vaultbrowser_preserves_strict_write_failure_payload(self):
        pattern = re.compile(
            r"const triggerMemupSync = async \(constructId\) => \{\s*"
            r"setSyncingConstruct\(constructId\);\s*setSyncResult\(null\);.*?"
            r"const response = await authFetch\('/api/vault/memup/sync'.*?"
            r"const data = await response\.json\(\);\s*setSyncResult\(data\);\s*"
            r"if \(data\.success\) \{\s*fetchFiles\(\);",
            re.S,
        )
        self.assertRegex(self.vault_browser, pattern)

    def test_authfetch_uses_backend_ready_as_connection_contract(self):
        self.assertIn("SUPABASE_CONNECTION_EVENT", self.auth_fetch)
        self.assertIn("refreshSupabaseConnectionState", self.auth_fetch)
        self.assertIn("fetchWithOptionalTimeout('/api/ready'", self.auth_fetch)
        self.assertIn("connection_state === 'connected'", self.auth_fetch)

    def test_authfetch_blocks_mutating_writes_without_connected_supabase(self):
        self.assertIn("isMutatingRequest(options)", self.auth_fetch)
        self.assertIn("supabaseWriteBlockedResponse(url)", self.auth_fetch)
        self.assertIn("canonical: false", self.auth_fetch)
        self.assertIn("storage_mode: 'none'", self.auth_fetch)

    def test_status_indicator_uses_supabase_connection_state_not_shallow_health(self):
        self.assertIn("refreshSupabaseConnectionState", self.app)
        self.assertIn("SUPABASE_CONNECTION_EVENT", self.app)
        self.assertIn("Supabase connected", self.app)
        self.assertNotIn("fetch('/api/health')", self.app)

    def test_status_indicator_uses_slower_poll_while_degraded(self):
        self.assertIn("SUPABASE_STATUS_CONNECTED_POLL_MS = 15000", self.app)
        self.assertIn("SUPABASE_STATUS_DEGRADED_POLL_MS = 60000", self.app)
        self.assertIn("nextDelay = connection.connected ? SUPABASE_STATUS_CONNECTED_POLL_MS : SUPABASE_STATUS_DEGRADED_POLL_MS", self.app)
        self.assertIn("timeoutId = setTimeout(checkStatus, nextDelay)", self.app)
        self.assertNotIn("setInterval(checkStatus, 15000)", self.app)

    def test_startup_loading_gate_is_bounded_during_supabase_degradation(self):
        self.assertIn("STARTUP_AUTH_TIMEOUT_MS = 2500", self.app)
        self.assertIn("STARTUP_STATUS_TIMEOUT_MS = 2500", self.app)
        self.assertIn("validateSession({ timeoutMs: STARTUP_AUTH_TIMEOUT_MS })", self.app)
        self.assertIn("finalizeAuthServiceLogin({ readyTimeoutMs: STARTUP_AUTH_TIMEOUT_MS })", self.app)
        self.assertIn("fetchJsonWithTimeout('/api/status', STARTUP_STATUS_TIMEOUT_MS)", self.app)
        self.assertIn("new AbortController()", self.app)
        self.assertIn("window.setTimeout(() => controller.abort(), timeoutMs)", self.app)

    def test_auth_ready_probe_timeout_is_call_site_scoped(self):
        self.assertIn("fetchWithOptionalTimeout('/api/ready'", self.auth_fetch)
        self.assertIn("options.timeoutMs", self.auth_fetch)
        self.assertIn("refreshSupabaseConnectionState({ timeoutMs: options.readyTimeoutMs })", self.auth_fetch)
        self.assertIn("fetchWithOptionalTimeout('/api/vault/user-info'", self.auth_fetch)
        self.assertIn("const connection = await refreshSupabaseConnectionState();", self.auth_fetch)

    def test_session_expired_dispatch_is_deduped_and_local_authenticated_fetches_stop(self):
        self.assertIn("let sessionExpired = false;", self.auth_fetch)
        self.assertIn("if (hasDispatchedSessionExpired) return;", self.auth_fetch)
        self.assertIn("sessionExpired = true;", self.auth_fetch)
        self.assertIn("sessionExpired = false;", self.auth_fetch)
        self.assertIn("return localSessionExpiredResponse(url);", self.auth_fetch)
        self.assertIn("error_code: 'SESSION_EXPIRED'", self.auth_fetch)

    def test_vaultbrowser_stops_fetch_loop_after_session_expiration(self):
        self.assertIn("SESSION_EXPIRED_EVENT", self.vault_browser)
        self.assertIn("const [sessionExpired, setSessionExpired] = useState(false);", self.vault_browser)
        self.assertIn("const showSessionExpiredState = useCallback(() => {", self.vault_browser)
        self.assertIn("if (sessionExpired) return false;", self.vault_browser)
        self.assertIn("response.status === 401 || data?.error_code === 'SESSION_EXPIRED'", self.vault_browser)
        self.assertIn("if (cancelled || !userInfoOk) return;", self.vault_browser)
        self.assertIn("window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)", self.vault_browser)

    def test_google_login_blocks_before_redirect_when_identity_authority_unavailable(self):
        self.assertIn("fetch('/api/auth/google/health')", self.cinematic_login)
        self.assertIn("!health.supabase_identity_authority_available", self.cinematic_login)
        self.assertIn("Sign-in is blocked to protect immutable LIFE identity", self.cinematic_login)

        health_check = self.cinematic_login.index("!health.supabase_identity_authority_available")
        generic_health_error = self.cinematic_login.index("!healthResponse.ok")
        redirect = self.cinematic_login.index("window.location.href = '/api/auth/google'")

        self.assertLess(health_check, generic_health_error)
        self.assertLess(generic_health_error, redirect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
