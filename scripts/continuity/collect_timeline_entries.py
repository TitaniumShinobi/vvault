import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ISO_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
MDY_RE = re.compile(r"\b([0-1]?\d)-([0-3]?\d)-(20\d{2})\b")
MDY_SLASH_RE = re.compile(r"\b([0-1]?\d)/([0-3]?\d)/(20\d{2})\b")
ISO_RANGE_RE = re.compile(r"\b(20\d{2})-([0-1]\d)-([0-3]\d)\s*[–-]\s*([0-3]\d)\b")
MONTH_RE = re.compile(
    r"/(20\d{2})/(January|February|March|April|May|June|July|August|September|October|November|December)/",
    re.IGNORECASE,
)
DAY_RE = re.compile(r"\b([0-2]?\d|3[01])\b")
MONTH_NAME_DAY_RE = re.compile(
    r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[_\\s-]*([0-3]?\d)"
)
MONTH_NAME_RE = re.compile(
    r"(?i)\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+([0-3]?\d)(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?\b"
)
MONTH_NAME_RANGE_RE = re.compile(
    r"(?i)\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+([0-3]?\d)\s*[–-]\s*([0-3]?\d)(?:,?\s*(20\d{2}))?\b"
)

KEYWORDS = [
    "hospital",
    "hospitalized",
    "admission",
    "discharge",
    "unit",
    "va",
    "hold",
    "b2n",
    "john dingell",
    "christmas",
    "day after christmas",
    "emergency",
]

MONTH_MAP = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def parse_line_for_dates(
    line: str,
    default_year: Optional[str] = None,
    default_month: Optional[str] = None,
) -> List[str]:
    match = ISO_RE.search(line)
    if match:
        return [match.group(1)]
    match = MDY_SLASH_RE.search(line)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        year = int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return [f"{year}-{month:02d}-{day:02d}"]
    match = MDY_RE.search(line)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        year = int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return [f"{year}-{month:02d}-{day:02d}"]
    match = ISO_RANGE_RE.search(line)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        start_day = int(match.group(3))
        end_day = int(match.group(4))
        if 1 <= month <= 12 and 1 <= start_day <= 31 and 1 <= end_day <= 31:
            return [f"{year}-{month:02d}-{day:02d}" for day in range(start_day, end_day + 1)]
    match = MONTH_NAME_RANGE_RE.search(line)
    if match:
        month_name = match.group(1).lower()
        month = MONTH_MAP.get(month_name)
        start_day = int(match.group(2))
        end_day = int(match.group(3))
        year = match.group(4) or default_year
        if month and year and 1 <= start_day <= 31 and 1 <= end_day <= 31:
            return [f"{year}-{month}-{day:02d}" for day in range(start_day, end_day + 1)]
    match = MONTH_NAME_RE.search(line)
    if match:
        month_name = match.group(1).lower()
        month = MONTH_MAP.get(month_name)
        day = int(match.group(2))
        year = match.group(3) or default_year
        if month and year and 1 <= day <= 31:
            return [f"{year}-{month}-{day:02d}"]
    if default_year and default_month:
        match = DAY_RE.search(line)
        if match:
            day = int(match.group(1))
            if 1 <= day <= 31:
                return [f"{default_year}-{default_month}-{day:02d}"]
    return []


def parse_year_month_from_path(path: Path) -> Tuple[Optional[str], Optional[str]]:
    match = MONTH_RE.search(str(path))
    if not match:
        return None, None
    year = match.group(1)
    month = MONTH_MAP.get(match.group(2).lower())
    if not month:
        return None, None
    return year, month


def parse_date_from_path(path: Path) -> Optional[str]:
    year, month = parse_year_month_from_path(path)
    if not year or not month:
        return None
    match = MONTH_RE.search(str(path))
    if not match:
        return None
    filename = path.name.lower()
    if "day after christmas" in filename:
        return f"{year}-{month}-26"
    if "christmas" in filename:
        return f"{year}-{month}-25"
    range_match = re.search(r"([0-3]?\d)[-_]([0-3]?\d)", filename)
    if range_match:
        day = int(range_match.group(1))
        return f"{year}-{month}-{day:02d}"
    month_day_match = MONTH_NAME_DAY_RE.search(filename)
    if month_day_match:
        day = int(month_day_match.group(2))
        return f"{year}-{month}-{day:02d}"
    day_match = DAY_RE.search(filename)
    if day_match:
        day = int(day_match.group(1))
        return f"{year}-{month}-{day:02d}"
    return f"{year}-{month}-01"


def in_range(date_str: str, start: Optional[str], end: Optional[str]) -> bool:
    if start and date_str < start:
        return False
    if end and date_str > end:
        return False
    return True


def should_reverse(path: Path) -> bool:
    return "character.ai" in (part.lower() for part in path.parts)


def extract_highlight(
    lines: List[Tuple[int, str]],
    keywords: List[str],
    window: int,
    limit: int,
) -> List[Tuple[int, str]]:
    lowered = [text.lower() for _, text in lines]
    for idx, text in enumerate(lowered):
        if any(keyword in text for keyword in keywords):
            start = max(0, idx - window)
            end = min(len(lines), idx + window + 1)
            return [(lineno, line) for lineno, line in lines[start:end] if line.strip()][:limit]
    return [(lineno, line) for lineno, line in lines if line.strip()][:limit]


def extract_correction_entries(
    path: Path,
    raw_lines: List[Tuple[int, str]],
    year_hint: Optional[str],
    start: Optional[str],
    end: Optional[str],
) -> List[dict]:
    entries: List[dict] = []
    in_block = False
    current_dates: List[str] = []
    current_notes: List[str] = []
    current_line = None

    def normalize_note_for_date(note_text: str, date_key: str) -> str:
        month_names = [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
        month_abbrs = [name[:3] for name in month_names]
        month_name = month_names[int(date_key[5:7]) - 1]
        month_abbr = month_abbrs[int(date_key[5:7]) - 1]
        range_pattern = rf"\\b(?:{month_name}|{month_abbr})\\w*\\s+\\d{{1,2}}\\s*[-–—]\\s*\\d{{1,2}}\\b"
        note_text = re.sub(range_pattern, date_key, note_text, flags=re.IGNORECASE)
        return note_text

    def flush():
        nonlocal current_dates, current_notes, current_line
        if not current_dates or not current_notes:
            current_dates = []
            current_notes = []
            current_line = None
            return
        note_text = " ".join(current_notes).strip()
        for date_key in current_dates:
            if not in_range(date_key, start, end):
                continue
            normalized = normalize_note_for_date(note_text, date_key)
            summary = normalized.replace("\t", " ")
            if len(summary) > 200:
                summary = summary[:197] + "..."
            entries.append(
                {
                    "Date": date_key,
                    "SessionTitle": f"Correction summary from {path.name}",
                    "Notes": normalized,
                    "Summary": summary,
                    "Source": str(path),
                    "Line": current_line,
                }
            )
        current_dates = []
        current_notes = []
        current_line = None

    for lineno, raw in raw_lines:
        line = raw.strip()
        if not line:
            if in_block:
                flush()
            continue
        if "chronological summary" in line.lower() or "summary of events" in line.lower():
            flush()
            in_block = True
            continue
        if not in_block:
            continue
        date_tags = parse_line_for_dates(line, default_year=year_hint)
        if date_tags:
            flush()
            current_dates = date_tags
            current_line = lineno
            current_notes = [line]
            continue
        if current_dates:
            current_notes.append(line)

    flush()
    return entries


def scan_file(
    path: Path,
    start: Optional[str],
    end: Optional[str],
    context_lines: int,
    max_lines_per_date: int,
    block_lines: int,
) -> List[dict]:
    entries = []
    year_hint, month_hint = parse_year_month_from_path(path)
    inferred_date = parse_date_from_path(path)
    inferred_used = False
    current_dates: List[str] = []
    current_notes: List[str] = []
    remaining_context = 0
    date_line_counts: Dict[str, int] = {}
    found_date_tag = False

    def flush_current():
        if not current_dates or not current_notes:
            return
        note_text = " ".join(current_notes).strip()
        summary = note_text.replace("\t", " ")
        if len(summary) > 200:
            summary = summary[:197] + "..."
        for date_key in current_dates:
            entries.append(
                {
                    "Date": date_key,
                    "SessionTitle": f"Context from {path.name}",
                    "Notes": note_text,
                    "Summary": summary,
                    "Source": str(path),
                    "Line": current_line,
                }
            )

    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        raw_lines = [(idx + 1, line.rstrip("\n")) for idx, line in enumerate(fh)]

    reverse = should_reverse(path)
    ordered_lines = list(reversed(raw_lines)) if reverse else raw_lines
    current_line = None

    entries.extend(extract_correction_entries(path, raw_lines, year_hint, start, end))

    for lineno, raw in ordered_lines:
        line = raw.strip()
        if not line:
            continue
        if line.lower() in {"skip to content.", "skip to content"}:
            continue

        date_tags = parse_line_for_dates(line, default_year=year_hint, default_month=month_hint)
        if date_tags:
            found_date_tag = True

        if not date_tags:
            if current_dates and remaining_context > 0:
                current_notes.append(line)
                remaining_context -= 1
            continue

        date_tags = [date_tag for date_tag in date_tags if in_range(date_tag, start, end)]
        if not date_tags:
            continue

        if current_dates and set(current_dates) != set(date_tags):
            flush_current()
            current_notes = []
            remaining_context = 0

        current_dates = date_tags
        current_line = lineno
        for date_tag in date_tags:
            date_line_counts.setdefault(date_tag, 0)
            if date_line_counts[date_tag] >= max_lines_per_date:
                continue

            current_notes.append(line)
            remaining_context = max(remaining_context, max(context_lines, block_lines))
            date_line_counts[date_tag] += 1

    flush_current()
    if not found_date_tag and inferred_date and not inferred_used and in_range(inferred_date, start, end):
        inferred_used = True
        excerpt = extract_highlight(raw_lines, KEYWORDS, window=3, limit=max_lines_per_date)
        notes = " ".join(line.strip() for _lineno, line in excerpt if line.strip())
        summary = notes.replace("\t", " ")
        if len(summary) > 200:
            summary = summary[:197] + "..."
        first_line = excerpt[0][0] if excerpt else None
        entries.append(
            {
                "Date": inferred_date,
                "SessionTitle": f"Context from {path.name}",
                "Notes": notes,
                "Summary": summary,
                "Source": str(path),
                "Line": first_line,
            }
        )
    return entries


def gather_entries(
    roots: Iterable[Path],
    start: Optional[str],
    end: Optional[str],
    per_file_limit: int,
    context_lines: int,
    max_lines_per_date: int,
    block_lines: int,
) -> List[dict]:
    results = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.txt"):
            entries = scan_file(path, start, end, context_lines, max_lines_per_date, block_lines)
            if per_file_limit and len(entries) > per_file_limit:
                entries = entries[:per_file_limit]
            results.extend(entries)
    return results


def write_entries(entries: List[dict], output: Path):
    output.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate timestamped timeline entries.")
    parser.add_argument(
        "--roots",
        nargs="+",
        required=True,
        help="Root directories to scan (accepts multiple, accepts globs).",
    )
    parser.add_argument("--start", help="Start date inclusive (YYYY-MM-DD).")
    parser.add_argument("--end", help="End date inclusive (YYYY-MM-DD).")
    parser.add_argument(
        "--limit-per-file",
        type=int,
        default=10,
        help="Maximum entries to capture from each file (prevents explosion).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("timeline_entries.json"),
        help="Where to write the aggregated ledger entries.",
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=3,
        help="Number of lines to capture after a dated line for context.",
    )
    parser.add_argument(
        "--block-lines",
        type=int,
        default=8,
        help="Additional lines to capture after a dated line for context.",
    )
    parser.add_argument(
        "--max-lines-per-date",
        type=int,
        default=15,
        help="Maximum lines to capture per date before truncating.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the aggregated entries to stdout instead of a file.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    roots = (Path(root) for root in args.roots)
    entries = gather_entries(
        roots,
        args.start,
        args.end,
        args.limit_per_file,
        args.context_lines,
        args.max_lines_per_date,
        args.block_lines,
    )
    if args.stdout:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    else:
        write_entries(entries, args.output)
        print(f"Collected {len(entries)} entries → {args.output}")


if __name__ == "__main__":
    main()
