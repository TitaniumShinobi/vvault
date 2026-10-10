"""Pure, conservative adapter for preserved text-export evidence.

The adapter performs no database or filesystem writes.  It preserves exact
source bytes, derives a separately hashed text projection when possible, and
never infers a construct identity from a filename or directory name.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CANONICALIZATION_VERSION = "life.vvault.source-export/1"
LEGACY_CLASSIFICATION = "LEGACY_UNCLASSIFIED"


@dataclass(frozen=True)
class SourceExport:
    source_path: str
    source_kind: str
    category: str
    raw_bytes: bytes
    raw_sha256: str
    raw_size_bytes: int
    derived_text: str | None
    normalized_text_sha256: str | None
    classification: str = LEGACY_CLASSIFICATION
    construct_id: None = None
    canonicalization_version: str = CANONICALIZATION_VERSION


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_text(value: str) -> str:
    """Normalize only BOM and newline representation; preserve all other text."""
    if value.startswith("\ufeff"):
        value = value[1:]
    return value.replace("\r\n", "\n").replace("\r", "\n")


def decode_text_bytes(raw: bytes) -> str:
    """Decode explicit Unicode BOMs, otherwise require valid UTF-8."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def _rtf_to_text(raw: bytes) -> str:
    """Create a conservative readable projection without changing RTF evidence."""
    source = raw.decode("latin-1")

    def hex_escape(match: re.Match[str]) -> str:
        return bytes.fromhex(match.group(1)).decode("cp1252", errors="replace")

    source = re.sub(r"\\'([0-9a-fA-F]{2})", hex_escape, source)
    source = re.sub(r"\\(?:par|line) ?", "\n", source)
    source = re.sub(r"\\tab ?", "\t", source)
    source = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), source)
    source = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", source)
    source = source.replace(r"\{", "{").replace(r"\}", "}").replace(r"\\", "\\")
    source = source.replace("{", "").replace("}", "")
    return normalize_text(source)


def _package_members(root: Path) -> Iterable[tuple[str, bytes]]:
    for child in sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if child.is_symlink():
            raise ValueError("RTFD packages may not contain symbolic links")
        if child.is_dir():
            for relative, content in _package_members(child):
                yield f"{child.name}/{relative}", content
        elif child.is_file():
            yield child.name, child.read_bytes()


def deterministic_rtfd_bytes(root: Path) -> bytes:
    """Represent an RTFD directory as a stable ZIP containing every file byte."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative, content in _package_members(root):
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def _rtfd_projection(root: Path) -> str | None:
    candidates = [item for item in _package_members(root) if item[0].lower().endswith(".rtf")]
    if not candidates:
        return None
    # TextEdit convention is TXT.rtf; deterministic path ordering is fallback.
    candidates.sort(key=lambda item: (item[0].lower() != "txt.rtf", item[0].encode("utf-8")))
    return _rtf_to_text(candidates[0][1])


def adapt_source_export(path: str | Path, *, category: str) -> SourceExport:
    """Read one explicit source file/package into a mutation-free evidence record."""
    source = Path(path)
    if source.is_dir():
        if source.suffix.lower() != ".rtfd":
            raise ValueError("only explicit .rtfd directory packages are supported")
        raw = deterministic_rtfd_bytes(source)
        kind = "rtfd"
        derived = _rtfd_projection(source)
    elif source.is_file():
        raw = source.read_bytes()
        suffix = source.suffix.lower()
        if suffix == ".rtf":
            kind = "rtf"
            derived = _rtf_to_text(raw)
        elif suffix in {".txt", ".md", ".markdown"}:
            kind = suffix.lstrip(".")
            derived = normalize_text(decode_text_bytes(raw))
        else:
            raise ValueError(f"unsupported source export type: {suffix or '<none>'}")
    else:
        raise FileNotFoundError(source)

    normalized_hash = _sha256(derived.encode("utf-8")) if derived is not None else None
    return SourceExport(
        source_path=str(source),
        source_kind=kind,
        category=str(category),
        raw_bytes=raw,
        raw_sha256=_sha256(raw),
        raw_size_bytes=len(raw),
        derived_text=derived,
        normalized_text_sha256=normalized_hash,
    )


def evidence_metadata(export: SourceExport) -> dict[str, object]:
    """Return JSON-safe metadata without embedding content or inferring identity."""
    return {
        "canonicalization_version": export.canonicalization_version,
        "source_path": export.source_path,
        "source_kind": export.source_kind,
        "category": export.category,
        "raw_sha256": export.raw_sha256,
        "raw_size_bytes": export.raw_size_bytes,
        "normalized_text_sha256": export.normalized_text_sha256,
        "classification": export.classification,
        "construct_id": export.construct_id,
    }


def canonical_metadata_bytes(export: SourceExport) -> bytes:
    return json.dumps(
        evidence_metadata(export), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
