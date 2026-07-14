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

def _normalized_requirement_name(requirement: str) -> str:
    raw = str(requirement or "").strip().lower()
    return re.split(r"[\[<>=!~; ]", raw, maxsplit=1)[0]


class TestRuntimeBodyDatabaseContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_source = SERVER_PATH.read_text(encoding="utf-8")
        cls.pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        cls.requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        cls.package_json = json.loads(PACKAGE_JSON_PATH.read_text(encoding="utf-8"))
        cls.package_lock = json.loads(PACKAGE_LOCK_PATH.read_text(encoding="utf-8"))

    def test_runtime_server_uses_body_database_service(self):
        self.assertIn("import chatty_body_service", self.server_source)
        self.assertIn("chatty_body_service._connect()", self.server_source)
        self.assertIn("def _body_database_dependency_status", self.server_source)
        self.assertIn('authority": "vvault_body"', self.server_source)

    def test_body_database_environment_names_are_direct(self):
        self.assertIn('"VVAULT_BODY_DB', self.server_source)

    def test_python_runtime_dependencies_do_not_define_database_authority(self):
        runtime_deps = {
            _normalized_requirement_name(dep)
            for dep in self.pyproject["project"].get("dependencies", [])
        }
        requirement_deps = {
            _normalized_requirement_name(line)
            for line in self.requirements
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("psycopg", runtime_deps | requirement_deps)
        self.assertIn("flask", runtime_deps | requirement_deps)

    def test_frontend_runtime_dependencies_use_vvault_native_packages(self):
        package_deps = {
            **self.package_json.get("dependencies", {}),
            **self.package_json.get("devDependencies", {}),
        }
        self.assertIn("axios", package_deps)

        root_package = self.package_lock.get("packages", {}).get("", {})
        lock_deps = {
            **root_package.get("dependencies", {}),
            **root_package.get("devDependencies", {}),
        }
        self.assertIn("axios", lock_deps)


if __name__ == "__main__":
    unittest.main()
