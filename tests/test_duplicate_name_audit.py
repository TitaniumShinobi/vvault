import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vvault.audit.duplicate_name_audit import (
    classify_duplicate_path,
    duplicate_basename_to_canonical,
    find_new_duplicate_paths,
    AuditRecord,
)


class TestDuplicateBasenameToCanonical(unittest.TestCase):
    def test_converts_supported_duplicate_names(self):
        self.assertEqual(duplicate_basename_to_canonical("README 2.md"), "README.md")
        self.assertEqual(duplicate_basename_to_canonical(".env 2"), ".env")
        self.assertEqual(duplicate_basename_to_canonical(".env 2.example"), ".env.example")
        self.assertEqual(duplicate_basename_to_canonical("src 2"), "src")
        self.assertIsNone(duplicate_basename_to_canonical("README copy.md"))


class TestDuplicateClassification(unittest.TestCase):
    def test_classify_empty_duplicate_directory(self):
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".git").mkdir()
            (repo_root / "docs").mkdir()
            (repo_root / "docs 2").mkdir()

            record = classify_duplicate_path(repo_root / "docs 2", repo_root, tracked_paths=set())

            self.assertEqual(record.classification, "empty duplicate")
            self.assertEqual(record.tracked_status, "untracked")
            self.assertTrue(record.safe_delete)
            self.assertEqual(record.canonical_path, "docs")

    def test_classify_exact_duplicate_file(self):
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".git").mkdir()
            (repo_root / "README.md").write_text("same", encoding="utf-8")
            (repo_root / "README 2.md").write_text("same", encoding="utf-8")

            record = classify_duplicate_path(repo_root / "README 2.md", repo_root, tracked_paths=set())

            self.assertEqual(record.classification, "exact duplicate file")
            self.assertTrue(record.safe_delete)

    def test_classify_content_mismatch_file(self):
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".git").mkdir()
            (repo_root / "README.md").write_text("canonical", encoding="utf-8")
            (repo_root / "README 2.md").write_text("different", encoding="utf-8")

            record = classify_duplicate_path(repo_root / "README 2.md", repo_root, tracked_paths=set())

            self.assertEqual(record.classification, "content mismatch")
            self.assertFalse(record.safe_delete)

    def test_classify_orphan_duplicate(self):
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".git").mkdir()
            (repo_root / "README 2.md").write_text("orphan", encoding="utf-8")

            record = classify_duplicate_path(repo_root / "README 2.md", repo_root, tracked_paths=set())

            self.assertEqual(record.classification, "orphan duplicate")
            self.assertFalse(record.safe_delete)

    def test_classify_special_case_for_env_and_databases(self):
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".git").mkdir()
            (repo_root / "venv").mkdir()
            (repo_root / "venv 2").mkdir()
            (repo_root / "security.db").write_text("a", encoding="utf-8")
            (repo_root / "security 2.db").write_text("a", encoding="utf-8")

            env_record = classify_duplicate_path(repo_root / "venv 2", repo_root, tracked_paths=set())
            db_record = classify_duplicate_path(repo_root / "security 2.db", repo_root, tracked_paths=set())

            self.assertEqual(env_record.classification, "special case")
            self.assertFalse(env_record.safe_delete)
            self.assertEqual(db_record.classification, "special case")
            self.assertFalse(db_record.safe_delete)


class TestDuplicateAllowlist(unittest.TestCase):
    def test_find_new_duplicate_paths_returns_only_non_baselined_entries(self):
        records = [
            AuditRecord(
                duplicate_path="docs 2",
                canonical_path="docs",
                path_type="directory",
                classification="empty duplicate",
                tracked_status="untracked",
                modified_at="2026-03-29T00:00:00+00:00",
                note="directory is empty and canonical counterpart exists",
                safe_delete=True,
            ),
            AuditRecord(
                duplicate_path="newfile 2.txt",
                canonical_path="newfile.txt",
                path_type="file",
                classification="content mismatch",
                tracked_status="untracked",
                modified_at="2026-03-29T00:00:00+00:00",
                note="file content differs from canonical counterpart",
                safe_delete=False,
            ),
        ]

        new_duplicates = find_new_duplicate_paths(records, {"docs 2"})

        self.assertEqual(new_duplicates, ["newfile 2.txt"])


if __name__ == "__main__":
    unittest.main()
