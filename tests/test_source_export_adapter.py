import hashlib
import zipfile

import pytest

from vvault.etl.source_export_adapter import (
    LEGACY_CLASSIFICATION,
    adapt_source_export,
    canonical_metadata_bytes,
    deterministic_rtfd_bytes,
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_plaintext_preserves_raw_bytes_and_hashes_bom_newlines_separately(tmp_path):
    source = tmp_path / "thread.txt"
    raw = b"\xef\xbb\xbfuser\r\nassistant\rnext"
    source.write_bytes(raw)

    result = adapt_source_export(source, category="cursor_export")

    assert result.raw_bytes == raw
    assert result.raw_sha256 == sha(raw)
    assert result.raw_size_bytes == len(raw)
    assert result.derived_text == "user\nassistant\nnext"
    assert result.normalized_text_sha256 == sha(b"user\nassistant\nnext")
    assert result.classification == LEGACY_CLASSIFICATION
    assert result.construct_id is None


def test_markdown_whitespace_is_not_normalized(tmp_path):
    source = tmp_path / "thread.md"
    source.write_bytes(b"# Title  \r\n\r\nBody  \n")

    result = adapt_source_export(source, category="codex_export")

    assert result.derived_text == "# Title  \n\nBody  \n"


def test_rtf_preserves_raw_and_builds_separate_projection(tmp_path):
    source = tmp_path / "thread.rtf"
    raw = br"{\rtf1\ansi Hello\par Caf\'e9}"
    source.write_bytes(raw)

    result = adapt_source_export(source, category="legacy_export")

    assert result.raw_bytes == raw
    assert result.raw_sha256 == sha(raw)
    assert "Hello\nCafé" in result.derived_text
    assert result.normalized_text_sha256 == sha(result.derived_text.encode("utf-8"))


def test_rtfd_representation_is_deterministic_and_includes_attachments(tmp_path):
    package = tmp_path / "Conversation.rtfd"
    package.mkdir()
    (package / "TXT.rtf").write_bytes(br"{\rtf1\ansi Preserved\par text}")
    attachments = package / "Attachments"
    attachments.mkdir()
    attachment = b"\x89PNG\r\n\x1a\nexact-image-bytes"
    (attachments / "image.png").write_bytes(attachment)

    first = deterministic_rtfd_bytes(package)
    second = deterministic_rtfd_bytes(package)
    result = adapt_source_export(package, category="cursor_export")

    assert first == second == result.raw_bytes
    with zipfile.ZipFile(package_bytes := __import__("io").BytesIO(first)) as archive:
        assert archive.namelist() == ["Attachments/image.png", "TXT.rtf"]
        assert archive.read("Attachments/image.png") == attachment
    assert "Preserved\ntext" in result.derived_text


def test_rtfd_symlinks_fail_closed(tmp_path):
    package = tmp_path / "Conversation.rtfd"
    package.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (package / "link.txt").symlink_to(target)

    with pytest.raises(ValueError, match="symbolic links"):
        adapt_source_export(package, category="legacy_export")


def test_metadata_is_deterministic_and_contains_no_content(tmp_path):
    source = tmp_path / "thread.txt"
    source.write_text("private transcript", encoding="utf-8")
    result = adapt_source_export(source, category="unclassified")

    first = canonical_metadata_bytes(result)
    second = canonical_metadata_bytes(result)

    assert first == second
    assert b"private transcript" not in first
    assert b'"construct_id":null' in first
    assert b'"classification":"LEGACY_UNCLASSIFIED"' in first


def test_unknown_files_and_non_rtfd_directories_are_rejected(tmp_path):
    unknown = tmp_path / "thread.json"
    unknown.write_text("{}", encoding="utf-8")
    directory = tmp_path / "folder"
    directory.mkdir()

    with pytest.raises(ValueError, match="unsupported"):
        adapt_source_export(unknown, category="legacy")
    with pytest.raises(ValueError, match="only explicit .rtfd"):
        adapt_source_export(directory, category="legacy")
