#!/usr/bin/env python3
"""Bounded, repeatable source-native history backfill and sync.

Only explicitly named files below explicitly named roots are considered.  The
default mode is a count-only dry run; database access requires both ``--apply``
and a canonical owner UUID.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import UUID


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from vvault.etl.codex_desktop_adapter import (  # noqa: E402
    CodexSessionRejected,
    build_thread_record,
    parse_segment,
)
from vvault.etl.source_export_adapter import adapt_source_export, evidence_metadata  # noqa: E402
from vvault.server.relying_party_scope import (  # noqa: E402
    set_authenticated_user_id,
    set_relying_party_id,
)
from vvault.server.source_native_ingestion_service import SourceNativeIngestionService  # noqa: E402


ACTOR = "vvault.sync_source_native_history"
CODEX_PROJECTION_CONTRACT = "life.vvault.provider-transcript.codex-desktop/v1"
EXPORT_PROJECTION_CONTRACT = "life.vvault.source-export/1"


def _owner_uuid(value: str | None) -> str | None:
    if not value:
        return None
    canonical = str(UUID(value))
    if value.lower() != canonical:
        raise ValueError("owner UUID must use canonical form")
    return canonical


def _parse_export(value: str) -> tuple[str, Path]:
    category, separator, path = value.partition("=")
    if not separator or not category.strip() or not path.strip():
        raise argparse.ArgumentTypeError("export files must use CATEGORY=PATH")
    return category.strip(), Path(path).expanduser()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True,
                        help="explicit allowed source root; repeatable")
    parser.add_argument("--codex-file", action="append", type=Path, default=[],
                        help="explicit Codex rollout JSONL; repeatable")
    parser.add_argument("--export-file", action="append", type=_parse_export, default=[],
                        help="explicit generic export as CATEGORY=PATH; repeatable")
    parser.add_argument("--apply", action="store_true", help="perform idempotent DB writes")
    parser.add_argument("--owner-user-id", help="canonical owner UUID; required with --apply")
    parser.add_argument("--relying-party-id", default="vvault", choices=("vvault", "chatty", "chatty-cli"))
    parser.add_argument("--failure-policy", choices=("stop", "continue"), default="stop",
                        help="stop at first rejected/error record or continue remaining records")
    args = parser.parse_args(argv)
    if not args.codex_file and not args.export_file:
        parser.error("at least one explicit --codex-file or --export-file is required")
    if args.apply and not args.owner_user_id:
        parser.error("--apply requires --owner-user-id")
    return args


def _bounded(path: Path, roots: list[Path]) -> tuple[Path, str]:
    candidate = path.resolve(strict=True)
    matches: list[tuple[int, Path]] = []
    for index, root in enumerate(roots, 1):
        canonical_root = root.resolve(strict=True)
        try:
            relative = candidate.relative_to(canonical_root)
        except ValueError:
            continue
        matches.append((index, relative))
    if not matches:
        raise ValueError("source file is outside every allowed root")
    index, relative = min(matches, key=lambda item: len(item[1].parts))
    if not relative.parts:
        raise ValueError("source must be a file or explicit RTFD package below a root")
    return candidate, f"root-{index:02d}/{relative.as_posix()}"


def _summary(*, mode: str, policy: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "failurePolicy": policy,
        "inputFiles": 0,
        "candidateRecords": 0,
        "acceptedRecords": 0,
        "appliedRecords": 0,
        "alreadyAppliedRecords": 0,
        "rejectedRecords": 0,
        "failedRecords": 0,
        "codexAccountPrivateRecords": 0,
        "legacyUnclassifiedRecords": 0,
        "stopped": False,
    }


def run(
    args: argparse.Namespace,
    *,
    service_factory: Callable[[], SourceNativeIngestionService] = SourceNativeIngestionService,
) -> dict[str, Any]:
    roots = [Path(root) for root in args.root]
    result = _summary(mode="apply" if args.apply else "dry-run", policy=args.failure_policy)
    owner = _owner_uuid(args.owner_user_id)

    codex_segments: dict[str, list[tuple[Any, str]]] = {}
    generic_records: list[tuple[Any, str]] = []

    def rejected() -> bool:
        result["rejectedRecords"] += 1
        if args.failure_policy == "stop":
            result["stopped"] = True
            return True
        return False

    for raw_path in args.codex_file:
        result["inputFiles"] += 1
        try:
            path, locator = _bounded(Path(raw_path), roots)
            segment = parse_segment(path)
            codex_segments.setdefault(segment.thread_id, []).append((segment, locator))
        except (OSError, ValueError, CodexSessionRejected):
            if rejected():
                return result

    for category, raw_path in args.export_file:
        result["inputFiles"] += 1
        try:
            path, locator = _bounded(Path(raw_path), roots)
            export = adapt_source_export(path, category=category)
            if export.derived_text is None:
                raise ValueError("source has no lossless text projection")
            generic_records.append((export, locator))
        except (OSError, UnicodeError, ValueError):
            if rejected():
                return result

    planned: list[dict[str, Any]] = []
    for thread_id in sorted(codex_segments):
        pairs = codex_segments[thread_id]
        record = build_thread_record([item[0] for item in pairs])
        locators = sorted(item[1] for item in pairs)
        planned.append({
            "kind": "codex",
            "provider": "codex",
            "source_kind": "jsonl",
            "source_collection": "codex-desktop-rollouts",
            "stable_source_id": thread_id,
            "source_locator": json.dumps(locators, separators=(",", ":")),
            "raw": base64.b64decode(record["sourceNative"]["bytesBase64"]),
            "projection": record["projection"]["content"],
            "metadata": record["envelope"],
            "projection_contract": CODEX_PROJECTION_CONTRACT,
            "explicit": bool(
                record["envelope"]["classificationEvidence"]
                ["authoritativeConstructBinding"]["verified"]
            ),
        })
    for export, locator in generic_records:
        planned.append({
            "kind": "generic",
            "provider": "legacy",
            "source_kind": export.source_kind,
            "source_collection": export.category,
            "stable_source_id": None,
            "source_locator": locator,
            "raw": export.raw_bytes,
            "projection": export.derived_text,
            "metadata": evidence_metadata(export) | {"source_path": locator},
            "projection_contract": EXPORT_PROJECTION_CONTRACT,
            "explicit": False,
        })

    result["candidateRecords"] = len(planned)
    result["acceptedRecords"] = len(planned)
    result["codexAccountPrivateRecords"] = sum(item["explicit"] for item in planned)
    result["legacyUnclassifiedRecords"] = sum(not item["explicit"] for item in planned)
    if not args.apply:
        return result

    # Scope is installed before constructing the service, so its first pooled
    # connection is configured with both verified contexts.
    set_relying_party_id(args.relying_party_id)
    set_authenticated_user_id(owner)
    service = service_factory()
    for item in planned:
        try:
            receipt = service.ingest_and_project_vault_file(
                owner_user_id=owner,
                relying_party_id=args.relying_party_id,
                provider=item["provider"],
                source_kind=item["source_kind"],
                source_collection=item["source_collection"],
                raw_envelope=item["raw"],
                projection_content=item["projection"],
                actor=ACTOR,
                stable_source_id=item["stable_source_id"],
                source_locator=item["source_locator"],
                source_metadata=item["metadata"],
                projection_contract=item["projection_contract"],
                projection_version="1",
                explicit_construct_evidence=item["explicit"],
                construct_id="zen-001" if item["explicit"] else None,
                content_type="text/markdown; charset=utf-8" if item["kind"] == "codex" else "text/plain; charset=utf-8",
                file_type="transcript" if item["kind"] == "codex" else "document",
            )
            key = "alreadyAppliedRecords" if receipt.get("result") == "already_applied" else "appliedRecords"
            result[key] += 1
        except Exception:
            result["failedRecords"] += 1
            if args.failure_policy == "stop":
                result["stopped"] = True
                break
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if result["failedRecords"] or result["rejectedRecords"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
