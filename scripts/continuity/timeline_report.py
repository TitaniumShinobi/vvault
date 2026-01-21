import argparse
import json
import os
import re
import sys
from datetime import timedelta
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def load_ledger(path: Optional[Path]) -> List[Dict]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("entries") or data.get("ledger") or []
    return data


def group_by_date(entries: Iterable[Dict]) -> Dict[str, List[Dict]]:
    grouped = defaultdict(list)
    for entry in entries:
        date_str = entry.get("Date") or entry.get("date")
        if not date_str:
            continue
        # normalize YYYY-MM-DD
        match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
        if match:
            date_str = match.group(1)
        grouped[date_str].append(entry)
    return dict(sorted(grouped.items()))


def normalize_summary_for_date(summary: str, date_key: str) -> str:
    if not summary:
        return summary
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
    range_pattern = rf"(?:{month_name}|{month_abbr})\w*\s+\d{{1,2}}\s*[-–—‑]\s*\d{{1,2}}"
    replaced = re.sub(range_pattern, date_key, summary, flags=re.IGNORECASE)
    replaced = replaced.replace(f"{date_key}, {date_key[:4]}", date_key)
    return replaced


def short_sentence(entry: Dict, date_key: Optional[str] = None, prefer_corrections: bool = False) -> Optional[str]:
    summary = entry.get("Summary")
    if summary:
        if prefer_corrections and date_key and is_range_entry(entry):
            summary = normalize_summary_for_date(summary, date_key)
        source = Path(entry["Source"]).name if entry.get("Source") else "unknown"
        line = entry.get("Line")
        suffix = f" (source: {source}:{line})." if line else f" (source: {source})."
        summary = summary if summary.endswith(".") else summary + "."
        return summary + suffix
    title = entry.get("SessionTitle") or entry.get("title") or entry.get("Session")
    if title:
        return f"{title}."
    notes = entry.get("Notes")
    if notes:
        summary = notes.strip().split("\n")[0]
        return summary if summary.endswith(".") else summary + "."
    return None


def source_priority(source: Optional[str]) -> int:
    if not source:
        return 0
    source_lower = source.lower()
    if "file upload summary" in source_lower:
        return 5
    if "character.ai" in source_lower:
        return 4
    if "chatgpt" in source_lower:
        return 3
    if "github_copilot" in source_lower:
        return 2
    return 1


def text_signature(entry: Dict) -> str:
    text = entry.get("Summary") or entry.get("Notes") or ""
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text[:160]


def is_day_specific(entry: Dict, date_key: str) -> bool:
    try:
        month_num = int(date_key[5:7])
        day_num = int(date_key[8:10])
    except ValueError:
        return False
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
    month_name = month_names[month_num - 1]
    month_abbrs = [name[:3] for name in month_names]
    month_abbr = month_abbrs[month_num - 1]
    text = (entry.get("Summary") or entry.get("Notes") or "").lower()
    if date_key in text:
        return True
    if f"{month_name} {day_num}" in text or f"{month_abbr} {day_num}" in text:
        return not is_range_entry(entry)
    return False


def is_range_entry(entry: Dict) -> bool:
    text = (entry.get("Summary") or entry.get("Notes") or "").lower()
    if re.search(r"\d{1,2}\s*(?:-|–|—|‑|to|through)\s*\d{1,2}", text):
        return True
    if re.search(r"\bfrom\b.*\bto\b", text) and re.search(r"\b\d{1,2}\b", text):
        return True
    if re.search(r"\b\d{1,2}\b.*\bto\b.*\b\d{1,2}\b", text):
        return True
    return False


def score_entry(entry: Dict, signature_counts: Dict[str, int], date_key: str) -> Tuple[int, int]:
    base = source_priority(entry.get("Source"))
    sig = text_signature(entry)
    recurrence = signature_counts.get(sig, 1)
    text = (entry.get("Summary") or entry.get("Notes") or "").lower()
    date_bonus = 0
    if date_key in text:
        date_bonus += 3
    month_day = date_key[5:]
    if month_day in text:
        date_bonus += 2
    try:
        month_num = int(date_key[5:7])
        day_num = int(date_key[8:10])
    except ValueError:
        month_num = None
        day_num = None
    if month_num and day_num:
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
        month_name = month_names[month_num - 1]
        if re.search(rf"\\b{month_name}\\b", text) and re.search(rf"\\b{day_num}\\b", text):
            date_bonus += 2
            date_bonus += 2  # prefer day-specific lines
    if "chronological summary" in text or "summary of events" in text or "correction summary" in (entry.get("SessionTitle") or "").lower():
        date_bonus += 2
    if (entry.get("SessionTitle") or "").lower().startswith("correction summary"):
        date_bonus += 3
    if "if you want, i can now reconstruct" in text:
        date_bonus -= 3
    if "summary of events" in text and not (month_num and day_num and str(day_num) in text):
        date_bonus -= 2
    if "pdf" in text:
        date_bonus -= 3
    if text.startswith("####") or text.startswith("###") or text.startswith("##"):
        date_bonus -= 2
    if any(keyword in text for keyword in ["hospital", "hospitalized", "john dingell", "providence", "b2n", "va medical"]):
        date_bonus += 3
    if any(keyword in text for keyword in ["law enforcement", "noise complaint"]):
        date_bonus += 3
    if "risperidone" in text:
        date_bonus += 5
    if month_num and day_num:
        if f"{month_name} {day_num}" in text:
            date_bonus += 3
        span_match = re.search(rf"\\b{month_name}\\b\\s+(\\d{{1,2}})\\s*[-–—]\\s*(\\d{{1,2}})", text)
        if span_match:
            start_day = int(span_match.group(1))
            end_day = int(span_match.group(2))
            span = max(1, end_day - start_day + 1)
            date_bonus += max(0, 4 - span)
    return base + min(recurrence - 1, 3) + date_bonus, recurrence


def long_summary(entry: Dict) -> str:
    lines = []
    if entry.get("State"):
        lines.append(f"State: {entry['State']}")
    if entry.get("Notes"):
        lines.append(f"Notes: {entry['Notes']}")
    if entry.get("KeyTopics"):
        topics = ", ".join(entry["KeyTopics"])
        lines.append(f"Topics: {topics}")
    return " ".join(lines)


def timeline_report(
    ledger_path: Optional[Path],
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Optional[int] = None,
    include_long: bool = False,
    prefer_corrections: bool = False,
    inline_ledger: Optional[List[Dict]] = None,
) -> List[str]:
    ledger = inline_ledger if inline_ledger is not None else load_ledger(ledger_path)
    grouped = group_by_date(ledger)

    def in_range(date_key: str) -> bool:
        if start and date_key < start:
            return False
        if end and date_key > end:
            return False
        return True

    lines = []
    day_count = 0

    def emit(date_key: str, items: List[Dict]):
        sentence = None
        if items:
            correction_items = [
                entry
                for entry in items
                if (entry.get("SessionTitle") or "").lower().startswith("correction summary")
            ]
            candidates = correction_items if correction_items else items
            if prefer_corrections and correction_items:
                day_specific = [entry for entry in correction_items if is_day_specific(entry, date_key)]
                if day_specific:
                    candidates = day_specific
                else:
                    candidates = correction_items
            if correction_items and candidates == correction_items:
                medical_candidates = []
                for entry in correction_items:
                    text = (entry.get("Summary") or entry.get("Notes") or "").lower()
                    if any(keyword in text for keyword in ["risperidone", "john dingell", "providence", "b2n", "va medical"]):
                        medical_candidates.append(entry)
                if medical_candidates:
                    candidates = medical_candidates
                try:
                    month_num = int(date_key[5:7])
                    day_num = int(date_key[8:10])
                except ValueError:
                    month_num = None
                    day_num = None
                if month_num and day_num:
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
                    month_name = month_names[month_num - 1]
                    day_specific = []
                    for entry in correction_items:
                        text = (entry.get("Summary") or entry.get("Notes") or "").lower()
                        has_day = f"{month_name} {day_num}" in text
                        has_range = re.search(r"\\d{1,2}\\s*[-–—]\\s*\\d{1,2}", text)
                        if has_day and not has_range:
                            day_specific.append(entry)
                    if day_specific:
                        candidates = day_specific
            if prefer_corrections and correction_items and candidates:
                longest = max(
                    candidates,
                    key=lambda entry: len((entry.get("Summary") or entry.get("Notes") or "")),
                )
                sentence = short_sentence(longest, date_key=date_key, prefer_corrections=prefer_corrections)
                if sentence:
                    line = f"{date_key}: {sentence}"
                    if include_long:
                        line += " " + " ".join(long_summary(item) for item in candidates if long_summary(item))
                    lines.append(line)
                    return

            signatures = [text_signature(entry) for entry in candidates]
            signature_counts = defaultdict(int)
            for sig in signatures:
                if sig:
                    signature_counts[sig] += 1
            scored = []
            for entry in candidates:
                score, _recurrence = score_entry(entry, signature_counts, date_key)
                scored.append((score, entry))
            scored.sort(key=lambda item: item[0], reverse=True)
            for _score, entry in scored:
                sentence = short_sentence(entry, date_key=date_key, prefer_corrections=prefer_corrections)
                if sentence:
                    break
        if not sentence:
            sentence = "No dated entry found in scanned sources (source: none)."
        line = f"{date_key}: {sentence}"
        if include_long:
            line += " " + " ".join(long_summary(item) for item in items if long_summary(item))
        lines.append(line)

    if start and end:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        current = start_dt
        while current <= end_dt:
            date_key = current.strftime("%Y-%m-%d")
            if in_range(date_key):
                emit(date_key, grouped.get(date_key, []))
                day_count += 1
                if limit and day_count >= limit:
                    break
            current = current + timedelta(days=1)
        return lines

    for date_key, items in grouped.items():
        if not in_range(date_key):
            continue
        emit(date_key, items)
        day_count += 1
        if limit and day_count >= limit:
            break
    return lines


def parse_args():
    parser = argparse.ArgumentParser(description="Generate timeline summaries from a continuity ledger.")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("continuity_ledger.json"),
        help="Path to a ContinuityGPT ledger file (default=continuity_ledger.json)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read ledger data from stdin (JSON list) instead of a file.",
    )
    parser.add_argument("--start", help="Inclusive start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="Inclusive end date (YYYY-MM-DD).")
    parser.add_argument("--limit", type=int, help="Limit number of days in the timeline.")
    parser.add_argument(
        "--long",
        action="store_true",
        help="Include longer contextual text (Notes/Topics) after every day.",
    )
    parser.add_argument(
        "--prefer-corrections",
        action="store_true",
        help="Prefer correction summary entries, using day-specific lines when available.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional file to write the timeline to (defaults to stdout).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ledger_data = None
    if args.stdin:
        ledger_data = json.load(sys.stdin)
    lines = timeline_report(
        args.ledger if not args.stdin else None,
        start=args.start,
        end=args.end,
        limit=args.limit,
        include_long=args.long,
        prefer_corrections=args.prefer_corrections,
        inline_ledger=ledger_data,
    )
    if args.output:
        args.output.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {len(lines)} timeline lines to {args.output}")
    else:
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()
