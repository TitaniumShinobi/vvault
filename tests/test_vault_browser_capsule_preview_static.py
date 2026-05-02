import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_BROWSER_PATH = REPO_ROOT / "src" / "components" / "VaultBrowser.js"


class TestVaultBrowserCapsulePreviewStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = VAULT_BROWSER_PATH.read_text(encoding="utf-8")

    def test_capsule_preview_uses_derived_kind_not_raw_file_type(self):
        self.assertIn("const isCapsuleFile = (file) => getPreviewPath(file).toLowerCase().endsWith('.capsule');", self.source)
        self.assertIn("const serverPreviewKind = previewMeta?.preview_kind || null;", self.source)
        self.assertIn("serverPreviewKind === 'binary' && !looksReadableText(content)", self.source)
        self.assertIn("previewKind === 'binary'", self.source)
        self.assertIn("previewKind === 'unavailable'", self.source)
        self.assertNotIn("selectedFile.file_type === 'binary'", self.source)
        self.assertNotIn("if (!isPreviewableTextFile(file)) {", self.source)

    def test_capsule_preview_pretty_prints_json_when_possible(self):
        self.assertIn("JSON.stringify(JSON.parse(content), null, 2)", self.source)
        self.assertIn("if (serverPreviewKind === 'json' || isCapsuleFile(file) || isJsonFile(file))", self.source)
        self.assertIn("return { kind: 'text', content };", self.source)

    def test_capsule_preview_uses_unavailable_state_for_recovery_failures(self):
        self.assertIn("serverPreviewStatus === 'unavailable' && !content", self.source)
        self.assertIn("Preview unavailable -", self.source)
        self.assertIn("Content could not be recovered from storage.", self.source)
        self.assertNotIn("Capsule preview unavailable -", self.source)

    def test_capsule_preview_uses_fail_fast_fetch_timeout_and_logs_timing(self):
        self.assertIn("const PREVIEW_FETCH_TIMEOUT_MS = 2200;", self.source)
        self.assertIn("const PREVIEW_BODY_HYDRATE_TIMEOUT_MS = 30000;", self.source)
        self.assertIn("new AbortController()", self.source)
        self.assertIn("window.setTimeout(() => controller.abort(), PREVIEW_FETCH_TIMEOUT_MS)", self.source)
        self.assertIn("console.info('[VVAULT preview] fetch-complete'", self.source)
        self.assertIn("console.warn('[VVAULT preview] fetch-timeout'", self.source)
        self.assertIn("console.info('[VVAULT preview] body-hydrate-start'", self.source)
        self.assertIn("console.info('[VVAULT preview] body-hydrate-complete'", self.source)
        self.assertIn("candidate_transcript_ids", self.source)
        self.assertIn("candidateTranscriptIds", self.source)
        self.assertIn("await authFetch('/api/vault/files/preview'", self.source)
        self.assertIn("const isFastCapsulePreview = isCapsuleFile(file);", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
