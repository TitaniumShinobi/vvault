import json
import re
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "vvault" / "server" / "vvault_web_server.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
PACKAGE_JSON_PATH = REPO_ROOT / "package.json"
PACKAGE_LOCK_PATH = REPO_ROOT / "package-lock.json"
UV_LOCK_PATH = REPO_ROOT / "uv.lock"

RUNTIME_SDK_IMPORT_RE = re.compile(r"^\s*(?:from\s+supabase\s+import|import\s+supabase\b)", re.MULTILINE)
CREATE_CLIENT_RE = re.compile(r"\bcreate_client\s*\(")
ALLOWED_LEGACY_SUPABASE_SDK_IMPORTS = {
    "scripts/migrate_to_supabase.py",
    "scripts/cleanup_duplicates.py",
    "scripts/continuity/run_katana_test.py",
    "scripts/continuity/run_capsule_v3_dry_run.py",
}


def _normalized_requirement_name(requirement: str) -> str:
    raw = str(requirement or "").strip().lower()
    return re.split(r"[\[<>=!~; ]", raw, maxsplit=1)[0]


class TestRuntimeSupabaseDependencyStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_source = SERVER_PATH.read_text(encoding="utf-8")
        cls.pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        cls.requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        cls.package_json = json.loads(PACKAGE_JSON_PATH.read_text(encoding="utf-8"))
        cls.package_lock = json.loads(PACKAGE_LOCK_PATH.read_text(encoding="utf-8"))
        cls.uv_lock = tomllib.loads(UV_LOCK_PATH.read_text(encoding="utf-8"))

    def test_runtime_server_does_not_import_or_create_supabase_sdk_client(self):
        self.assertIsNone(RUNTIME_SDK_IMPORT_RE.search(self.server_source))
        self.assertIsNone(CREATE_CLIENT_RE.search(self.server_source))
        self.assertNotIn("supabase_client", self.server_source)
        self.assertNotIn("SUPABASE_STEWARD", self.server_source)
        self.assertNotIn("SUPABASE_WRITE_OUTBOX", self.server_source)
        self.assertNotIn("SUPABASE_", self.server_source)
        self.assertNotIn("storage.from_", self.server_source)
        self.assertNotIn("/storage/v1", self.server_source)
        self.assertNotIn("/rest/v1", self.server_source)

    def test_python_runtime_dependencies_do_not_include_supabase_sdk(self):
        runtime_deps = {
            _normalized_requirement_name(dep)
            for dep in self.pyproject["project"].get("dependencies", [])
        }
        self.assertNotIn("supabase", runtime_deps)
        self.assertIn("psycopg", runtime_deps)

        requirement_deps = {
            _normalized_requirement_name(line)
            for line in self.requirements
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn("supabase", requirement_deps)
        self.assertIn("psycopg", requirement_deps)

    def test_supabase_sdk_is_only_optional_legacy_migration_dependency(self):
        optional = self.pyproject["project"].get("optional-dependencies", {})
        self.assertIn("legacy-supabase-migration", optional)
        legacy_deps = {_normalized_requirement_name(dep) for dep in optional["legacy-supabase-migration"]}
        self.assertIn("supabase", legacy_deps)

    def test_uv_lock_root_runtime_dependencies_do_not_include_supabase_sdk(self):
        root_package = next(
            package for package in self.uv_lock["package"] if package["name"] == self.pyproject["project"]["name"]
        )
        runtime_deps = {dep["name"] for dep in root_package.get("dependencies", [])}
        self.assertNotIn("supabase", runtime_deps)
        self.assertIn("psycopg", runtime_deps)

    def test_frontend_runtime_dependencies_do_not_include_supabase_js(self):
        package_deps = {
            **self.package_json.get("dependencies", {}),
            **self.package_json.get("devDependencies", {}),
        }
        self.assertNotIn("@supabase/supabase-js", package_deps)

        root_package = self.package_lock.get("packages", {}).get("", {})
        lock_deps = {
            **root_package.get("dependencies", {}),
            **root_package.get("devDependencies", {}),
        }
        self.assertNotIn("@supabase/supabase-js", lock_deps)

    def test_remaining_supabase_sdk_imports_are_legacy_scripts_only(self):
        offenders = []
        for root_name in ("packages", "scripts", "vvault"):
            for path in (REPO_ROOT / root_name).rglob("*.py"):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if any(part in {".venv", "venv", "__pycache__", "node_modules"} for part in path.parts):
                    continue
                source = path.read_text(encoding="utf-8", errors="ignore")
                if RUNTIME_SDK_IMPORT_RE.search(source) or CREATE_CLIENT_RE.search(source):
                    if rel not in ALLOWED_LEGACY_SUPABASE_SDK_IMPORTS:
                        offenders.append(rel)
        self.assertEqual([], sorted(offenders))


if __name__ == "__main__":
    unittest.main()
