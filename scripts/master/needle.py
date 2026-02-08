#!/usr/bin/env python3
"""
needle.py - Fast "find the receipt" search helper for this vault.

Default behavior:
  - Uses ripgrep (rg) if available (fast).
  - Searches common transcript roots (chatgpt/, github_copilot/, character.ai/, etc.).
  - Limits to text-like files by default (*.txt, *.md).

Examples:
  python3 scripts/master/needle.py "Casa Madrigal"
  python3 scripts/master/needle.py "LIN ORCHESTRATION:" --around 3
  python3 scripts/master/needle.py "FEAD-06 (Sera)" --paths chatgpt github_copilot character.ai --all-files
  python3 scripts/master/needle.py "zen-001_chat_with_zen-001" --max 50

Notes:
  - Set NEEDLE_ROOT to explicitly control the repo root.
  - If rg isn't installed, a pure-Python fallback is used (slower, but works).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    text: str


DEFAULT_GLOBS = ["*.txt", "*.md"]
DEFAULT_PATHS = [
    "chatgpt",
    "github_copilot",
    "character.ai",
    "cursor_conversations",
    "codex_conversations",
    "chatgpt/codex_transcripts",
]


def _repo_root() -> Path:
    """
    Determine the repo root regardless of whether needle.py lives under:
      - scripts/master/
      - vvault_scripts/master/
      - identity/
    """
    env_root = os.environ.get("NEEDLE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    here = Path(__file__).resolve()
    repo_markers = (".git", "pyproject.toml", "package.json")

    for p in (here,) + tuple(here.parents):
        if any((p / m).exists() for m in repo_markers):
            return p

    # Heuristics if no markers were found (e.g., copied into a vault without git).
    parent = here.parent
    if parent.name == "identity":
        return parent.parent
    if parent.name == "master" and parent.parent.name in ("scripts", "vvault_scripts"):
        return parent.parent.parent

    return here.parent.parent


def _existing_paths(root: Path, rel_paths: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    root_resolved = root.resolve()
    for rel in rel_paths:
        p = (root / rel).resolve()
        try:
            p.relative_to(root_resolved)
        except Exception:
            continue
        if p.exists():
            out.append(p)
    return out


def _parse_rg_line(line: str) -> Optional[Match]:
    # rg -n --no-heading output: path:line:matchtext
    parts = line.rstrip("\n").split(":", 2)
    if len(parts) < 3:
        return None
    path, line_s, text = parts
    try:
        line_i = int(line_s)
    except ValueError:
        return None
    return Match(path=path, line=line_i, text=text)


def _run_rg(
    needle: str,
    paths: Sequence[Path],
    globs: Sequence[str],
    fixed: bool,
    case_sensitive: bool,
    max_hits: Optional[int],
) -> List[Match]:
    cmd = ["rg", "-n", "--no-heading", "-S"]
    if fixed:
        cmd.append("-F")
    if case_sensitive:
        cmd.append("--case-sensitive")
    for g in globs:
        cmd.extend(["-g", g])
    cmd.append(needle)
    cmd.extend([str(p) for p in paths])

    proc = subprocess.run(
        cmd,
        cwd=str(_repo_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Exit code 1 means "no matches" for rg.
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or f"rg failed with code {proc.returncode}")

    matches: List[Match] = []
    for raw in proc.stdout.splitlines():
        m = _parse_rg_line(raw)
        if not m:
            continue
        matches.append(m)
        if max_hits is not None and len(matches) >= max_hits:
            break
    return matches


def _iter_files(root: Path, paths: Sequence[Path], globs: Sequence[str]) -> Iterable[Path]:
    for base in paths:
        if base.is_file():
            yield base
            continue
        for g in globs:
            yield from base.rglob(g)


def _run_python_fallback(
    needle: str,
    paths: Sequence[Path],
    globs: Sequence[str],
    case_sensitive: bool,
    max_hits: Optional[int],
) -> List[Match]:
    root = _repo_root()
    matches: List[Match] = []
    needle_cmp = needle if case_sensitive else needle.lower()

    for f in _iter_files(root, paths, globs):
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fp:
                for i, line in enumerate(fp, start=1):
                    hay = line if case_sensitive else line.lower()
                    if needle_cmp in hay:
                        rel = str(f.resolve().relative_to(root.resolve()))
                        matches.append(Match(path=rel, line=i, text=line.rstrip("\n")))
                        if max_hits is not None and len(matches) >= max_hits:
                            return matches
        except (OSError, UnicodeError):
            continue
    return matches


def _read_context_window(path: Path, line_no: int, around: int) -> List[Tuple[int, str]]:
    start = max(1, line_no - around)
    end = line_no + around
    out: List[Tuple[int, str]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            for i, line in enumerate(fp, start=1):
                if i < start:
                    continue
                if i > end:
                    break
                out.append((i, line.rstrip("\n")))
    except OSError:
        return out
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="needle.py", add_help=True)
    p.add_argument("needle", help="Exact phrase to search (use --regex to treat as regex via rg).")
    p.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Paths (relative to repo root) to search. Default: common transcript roots.",
    )
    p.add_argument(
        "--glob",
        action="append",
        default=[],
        help="File glob(s) to include (repeatable). Default: *.txt, *.md (unless --all-files).",
    )
    p.add_argument("--all-files", action="store_true", help="Do not restrict by glob (search all files).")
    p.add_argument("--regex", action="store_true", help="Treat needle as a regex (rg only).")
    p.add_argument("--case-sensitive", action="store_true", help="Case sensitive search.")
    p.add_argument("--max", type=int, default=200, help="Max hits to print (default: 200). Use 0 for unlimited.")
    p.add_argument("--around", type=int, default=0, help="Print N lines of context around each match.")
    p.add_argument("--json", action="store_true", help="Output matches as JSON.")

    args = p.parse_args(list(argv) if argv is not None else None)

    root = _repo_root()
    rel_paths = args.paths if args.paths is not None and len(args.paths) > 0 else DEFAULT_PATHS
    paths = _existing_paths(root, rel_paths)
    if not paths:
        # If the caller explicitly provided paths, treat missing paths as an error.
        # If using defaults, fall back to searching the repo root so the tool remains usable
        # even when transcript roots aren't present.
        if args.paths is not None:
            print("No valid search paths found. Try: --paths chatgpt github_copilot", file=sys.stderr)
            return 2
        paths = [root]

    if args.all_files:
        globs = ["*"]
    else:
        globs = args.glob if args.glob else list(DEFAULT_GLOBS)

    max_hits = None if args.max == 0 else max(1, args.max)

    t0 = time.time()
    rg_ok = shutil.which("rg") is not None
    try:
        if rg_ok:
            matches = _run_rg(
                needle=args.needle,
                paths=paths,
                globs=globs,
                fixed=not args.regex,
                case_sensitive=args.case_sensitive,
                max_hits=max_hits,
            )
        else:
            if args.regex:
                print("rg not found; --regex not supported in fallback mode.", file=sys.stderr)
                return 2
            matches = _run_python_fallback(
                needle=args.needle,
                paths=paths,
                globs=globs,
                case_sensitive=args.case_sensitive,
                max_hits=max_hits,
            )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    dt_ms = int((time.time() - t0) * 1000)

    if args.json:
        payload = {
            "needle": args.needle,
            "paths": [str(p.resolve().relative_to(root.resolve())) for p in paths],
            "globs": globs,
            "case_sensitive": bool(args.case_sensitive),
            "regex": bool(args.regex),
            "count": len(matches),
            "elapsed_ms": dt_ms,
            "matches": [asdict(m) for m in matches],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if not matches:
        print(f"No matches for: {args.needle!r} (elapsed {dt_ms}ms)")
        return 0

    print(f"{len(matches)} match(es) for {args.needle!r} (elapsed {dt_ms}ms):")
    for idx, m in enumerate(matches, start=1):
        print(f"{idx:>3}. {m.path}:{m.line}: {m.text}")
        if args.around > 0:
            ctx_path = (root / m.path)
            window = _read_context_window(ctx_path, m.line, args.around)
            for ln, txt in window:
                prefix = ">>" if ln == m.line else "  "
                print(f"     {prefix} {ln:>6} | {txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
