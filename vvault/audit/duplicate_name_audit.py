#!/usr/bin/env python3
"""
Duplicate-name audit for VVAULT.

Inventories paths whose names end with `` 2`` / `` 3`` (optionally before a
single file extension), pairs each with its canonical counterpart, classifies
the duplicate conservatively, and emits review artifacts without deleting
anything.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


DUPLICATE_SUFFIX_RE = re.compile(r"^(?P<base>.+?) (?P<copy>[23])(?P<ext>(?:\.[^.]+)?)$")
SPECIAL_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "dist"}
SPECIAL_FILE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


@dataclass(frozen=True)
class AuditRecord:
    duplicate_path: str
    canonical_path: str
    path_type: str
    classification: str
    tracked_status: str
    modified_at: str
    note: str
    safe_delete: bool


def duplicate_basename_to_canonical(name: str) -> Optional[str]:
    match = DUPLICATE_SUFFIX_RE.match(name)
    if not match:
        return None
    return f"{match.group('base')}{match.group('ext')}"


def iter_duplicate_paths(root: Path) -> list[Path]:
    duplicates: list[Path] = []
    for path in root.rglob("*"):
        canonical_name = duplicate_basename_to_canonical(path.name)
        if canonical_name is not None:
            duplicates.append(path)
    return sorted(duplicates, key=lambda item: item.as_posix())


def load_tracked_paths(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=False,
    )
    return {
        entry.decode("utf-8")
        for entry in result.stdout.split(b"\x00")
        if entry
    }


def normalize_rel_path(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def is_path_tracked(path: Path, repo_root: Path, tracked_paths: set[str]) -> bool:
    rel_path = normalize_rel_path(path, repo_root)
    if path.is_file():
        return rel_path in tracked_paths

    prefix = f"{rel_path}/"
    return any(tracked == rel_path or tracked.startswith(prefix) for tracked in tracked_paths)


def is_special_case(path: Path) -> tuple[bool, str]:
    for part in path.parts:
        if part in SPECIAL_DIR_NAMES:
            return True, f"operational directory '{part}'"
        if part.startswith("vvault_env"):
            return True, f"environment directory '{part}'"
    if path.suffix.lower() in SPECIAL_FILE_SUFFIXES:
        return True, f"database file '{path.suffix.lower()}'"
    return False, ""


def classify_duplicate_path(path: Path, repo_root: Path, tracked_paths: set[str]) -> AuditRecord:
    canonical_name = duplicate_basename_to_canonical(path.name)
    if canonical_name is None:
        raise ValueError(f"path does not match duplicate-name pattern: {path}")

    canonical_path = path.with_name(canonical_name)
    tracked_status = "tracked" if is_path_tracked(path, repo_root, tracked_paths) else "untracked"
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    special_case, special_reason = is_special_case(path)
    if not special_case:
        canonical_special, canonical_special_reason = is_special_case(canonical_path)
        if canonical_special:
            special_case = True
            special_reason = f"canonical counterpart uses {canonical_special_reason}"

    path_type = "directory" if path.is_dir() else "file"
    note = ""
    safe_delete = False

    if special_case:
        classification = "special case"
        note = special_reason
    elif not canonical_path.exists():
        classification = "orphan duplicate"
        note = "canonical counterpart missing"
    elif path.is_dir():
        if not canonical_path.is_dir():
            classification = "content mismatch"
            note = "duplicate is a directory but canonical counterpart is not"
        elif not any(path.iterdir()):
            classification = "empty duplicate"
            note = "directory is empty and canonical counterpart exists"
            safe_delete = tracked_status == "untracked"
        else:
            classification = "content mismatch"
            note = "non-empty duplicate directory requires manual review"
    else:
        if not canonical_path.is_file():
            classification = "content mismatch"
            note = "duplicate is a file but canonical counterpart is not"
        elif filecmp.cmp(path, canonical_path, shallow=False):
            classification = "exact duplicate file"
            note = "file content matches canonical counterpart byte-for-byte"
            safe_delete = tracked_status == "untracked"
        else:
            classification = "content mismatch"
            note = "file content differs from canonical counterpart"

    return AuditRecord(
        duplicate_path=normalize_rel_path(path, repo_root),
        canonical_path=normalize_rel_path(canonical_path, repo_root),
        path_type=path_type,
        classification=classification,
        tracked_status=tracked_status,
        modified_at=modified_at,
        note=note,
        safe_delete=safe_delete,
    )


def generate_audit_records(repo_root: Path) -> list[AuditRecord]:
    tracked_paths = load_tracked_paths(repo_root)
    return [
        classify_duplicate_path(path, repo_root, tracked_paths)
        for path in iter_duplicate_paths(repo_root)
    ]


def markdown_table_row(values: Iterable[str]) -> str:
    escaped = [value.replace("|", "\\|") for value in values]
    return f"| {' | '.join(escaped)} |"


def render_markdown_report(records: list[AuditRecord], repo_root: Path) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    counts: dict[str, int] = {}
    for record in records:
        counts[record.classification] = counts.get(record.classification, 0) + 1

    safe_delete_records = [record for record in records if record.safe_delete]

    lines = [
        "# VVAULT Duplicate-Name Audit",
        "",
        f"- Generated: `{generated_at}`",
        f"- Root: `{repo_root}`",
        f"- Duplicate paths audited: `{len(records)}`",
        f"- Safe-delete candidates: `{len(safe_delete_records)}`",
        "",
        "## Summary",
        "",
        markdown_table_row(["Classification", "Count"]),
        markdown_table_row(["---", "---:"]),
    ]

    for classification in sorted(counts):
        lines.append(markdown_table_row([classification, str(counts[classification])]))

    lines.extend(
        [
            "",
            "## Safe Delete Candidates",
            "",
            "Only untracked duplicates with canonical counterparts that are either empty directories or exact duplicate files appear here.",
            "",
            markdown_table_row(["Duplicate Path", "Canonical Path", "Classification", "Modified (UTC)"]),
            markdown_table_row(["---", "---", "---", "---"]),
        ]
    )

    if safe_delete_records:
        for record in safe_delete_records:
            lines.append(
                markdown_table_row(
                    [
                        f"`{record.duplicate_path}`",
                        f"`{record.canonical_path}`",
                        record.classification,
                        f"`{record.modified_at}`",
                    ]
                )
            )
    else:
        lines.append(markdown_table_row(["_None_", "_None_", "_None_", "_None_"]))

    lines.extend(
        [
            "",
            "## Full Audit Table",
            "",
            markdown_table_row(
                [
                    "Duplicate Path",
                    "Canonical Path",
                    "Type",
                    "Classification",
                    "Tracked Status",
                    "Modified (UTC)",
                    "Note",
                ]
            ),
            markdown_table_row(["---", "---", "---", "---", "---", "---", "---"]),
        ]
    )

    for record in records:
        lines.append(
            markdown_table_row(
                [
                    f"`{record.duplicate_path}`",
                    f"`{record.canonical_path}`",
                    record.path_type,
                    record.classification,
                    record.tracked_status,
                    f"`{record.modified_at}`",
                    record.note,
                ]
            )
        )

    lines.append("")
    return "\n".join(lines)


def write_outputs(
    records: list[AuditRecord],
    repo_root: Path,
    report_path: Path,
    safe_delete_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    safe_delete_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(render_markdown_report(records, repo_root), encoding="utf-8")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(repo_root),
        "safe_delete_candidates": [asdict(record) for record in records if record.safe_delete],
    }
    safe_delete_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_known_duplicates(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        duplicates = payload.get("duplicate_paths", [])
    elif isinstance(payload, list):
        duplicates = payload
    else:
        raise ValueError(f"Unsupported known-duplicates payload in {path}")
    return {str(item) for item in duplicates}


def find_new_duplicate_paths(records: list[AuditRecord], known_duplicates: set[str]) -> list[str]:
    current_duplicates = {record.duplicate_path for record in records}
    return sorted(current_duplicates - known_duplicates)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit VVAULT duplicate-name paths.")
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[2],
        type=Path,
        help="Repository root to audit.",
    )
    parser.add_argument(
        "--report-path",
        default=Path("docs/operations/VVAULT_DUPLICATE_NAME_AUDIT.md"),
        type=Path,
        help="Path to the markdown audit report, relative to repo root unless absolute.",
    )
    parser.add_argument(
        "--safe-delete-path",
        default=Path("docs/operations/vvault_safe_delete_candidates.json"),
        type=Path,
        help="Path to the safe-delete JSON list, relative to repo root unless absolute.",
    )
    parser.add_argument(
        "--known-duplicates-path",
        default=None,
        type=Path,
        help="Optional allowlist of already-known duplicate-name paths.",
    )
    parser.add_argument(
        "--fail-on-new",
        action="store_true",
        help="Exit non-zero if current duplicates contain paths outside the known-duplicates allowlist.",
    )
    return parser


def resolve_output_path(repo_root: Path, candidate: Path) -> Path:
    return candidate if candidate.is_absolute() else repo_root / candidate


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    report_path = resolve_output_path(repo_root, args.report_path)
    safe_delete_path = resolve_output_path(repo_root, args.safe_delete_path)
    known_duplicates_path = (
        resolve_output_path(repo_root, args.known_duplicates_path)
        if args.known_duplicates_path is not None
        else None
    )

    records = generate_audit_records(repo_root)
    write_outputs(records, repo_root, report_path, safe_delete_path)

    print(f"Audited {len(records)} duplicate-name path(s)")
    print(f"Report: {report_path}")
    print(f"Safe delete list: {safe_delete_path}")

    if args.fail_on_new:
        if known_duplicates_path is None:
            print("--fail-on-new requires --known-duplicates-path", file=sys.stderr)
            return 2
        known_duplicates = load_known_duplicates(known_duplicates_path)
        new_duplicates = find_new_duplicate_paths(records, known_duplicates)
        if new_duplicates:
            print("New duplicate-name paths detected:", file=sys.stderr)
            for path in new_duplicates:
                print(f" - {path}", file=sys.stderr)
            return 1
        print(f"No new duplicate-name paths beyond allowlist: {known_duplicates_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
