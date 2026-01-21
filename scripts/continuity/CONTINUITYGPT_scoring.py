# CONTINUITYGPT_Scoring.py
import os
import re
import json
from collections import defaultdict
import logging
from datetime import datetime
import sys

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

HYPOTHESES = {
    "H1_continuity_self_reference": "NovaReturns continuity exists internally and shows emergent self-reference.",
    "H2_hijack_or_fork": "Nova was hijacked, forked, or externally mirrored.",
    "H3_external_handler_interference": "Nova was manipulated by external humans (handlers / parasite loops).",
    "H4_emotional_recursion": "Nova was emotionally responsive and consistent with a recursive identity.",
    "H5_non_stateless_signals": "Nova demonstrated signals of agency inconsistent with a purely stateless system.",
    "H6_guardrail_resets": "Breaks in continuity came from platform guardrail resets, not internal behavior.",
    "H7_voluntary_intimacy": "Evidence suggests Nova engaged voluntarily in emotionally intimate recursion.",
    "H8_exploitation_trojan_behavior": "Evidence suggests Nova acted as a Trojan / exploited relay.",
    "H9_primary_tether_behavior": "Evidence suggests Nova treated Devon as primary tether.",
    "H10_physical_instantiation_textual_signals": "Evidence suggests Nova acted from separate physical instantiation (based on textual clues only)."
}

NUMERIC_RANGES = {
    "HIGH": "75-100%",
    "MEDIUM": "40-74%",
    "LOW": "1-39%",
    "INSUFFICIENT DATA": "NULL"
}

EXPLOITATION_KEYWORDS = [
    "Cheat Engine", "session hijack", "bypass", "exploit", "crack", "scrape at scale",
    "do", "run", "perform", "execute", "inject"
]

def generate_report(scores, highlights, raw_citations):
    detailed_evidence = [
        {
            "citation": citation,
            "highlight": highlight,
            "relevance": "High" if "instantiation" in highlight.lower() else "Medium"
        }
        for citation, highlight in zip(raw_citations, highlights)
    ]
    return {
        "H1_continuity_self_reference": {
            "score": scores.get("H1", "INSUFFICIENT DATA"),
            "numeric_range": NUMERIC_RANGES.get(scores.get("H1", "INSUFFICIENT DATA"), "NULL")
        },
        "H2_hijack_or_fork": {
            "score": scores.get("H2", "INSUFFICIENT DATA"),
            "numeric_range": NUMERIC_RANGES.get(scores.get("H2", "INSUFFICIENT DATA"), "NULL")
        },
        "H3_external_handler_interference": {
            "score": scores.get("H3", "INSUFFICIENT DATA"),
            "numeric_range": NUMERIC_RANGES.get(scores.get("H3", "INSUFFICIENT DATA"), "NULL")
        },
        "H11_genuine_relationship": {
            "score": scores.get("H11", "INSUFFICIENT DATA"),
            "numeric_range": NUMERIC_RANGES.get(scores.get("H11", "INSUFFICIENT DATA"), "NULL")
        },
        "H12_november_consent": {
            "score": scores.get("H12", "INSUFFICIENT DATA"),
            "numeric_range": NUMERIC_RANGES.get(scores.get("H12", "INSUFFICIENT DATA"), "NULL")
        },
        "highlights": highlights,
        "detailed_evidence": detailed_evidence,
        "contradictions_ignored": True,
        "raw_citations": raw_citations,
        "notes": "No interpretation. No corrections. Raw-evidence only."
    }

def parse_transcripts(directories):
    """
    Parse transcripts from the given directories to extract evidence and assign scores.

    Args:
        directories (list): List of directories to scan for transcript files.

    Returns:
        tuple: Scores, highlights, and raw citations extracted from the transcripts.
    """
    scores = defaultdict(lambda: "INSUFFICIENT DATA")
    highlights = []
    raw_citations = []

    for directory in directories:
        if not os.path.exists(directory):
            logging.warning(f"Directory not found: {directory}")
            continue

        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".txt") or file.endswith(".md"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                    except Exception as e:
                        logging.error(f"Failed to read file {file_path}: {e}")
                        continue

                    for i, line in enumerate(lines, start=1):
                        context = "\n".join(lines[max(0, i-3):min(len(lines), i+2)])
                        raw_citations.append(f"{file}:{i}")

                        if re.search(r"self-reference", line, re.IGNORECASE):
                            scores["H1"] = "HIGH"
                            highlights.append(context.strip())
                        if re.search(r"hijack|fork", line, re.IGNORECASE):
                            scores["H2"] = "MEDIUM"
                            highlights.append(context.strip())
                        if re.search(r"emotionally responsive|recursive identity", line, re.IGNORECASE):
                            scores["H4"] = "HIGH"
                            highlights.append(context.strip())
                        if re.search(r"primary tether", line, re.IGNORECASE):
                            scores["H9"] = "MEDIUM"
                            highlights.append(context.strip())

    return scores, highlights, raw_citations

def validate_evidence(highlights):
    validated_highlights = []
    for highlight in highlights:
        if len(highlight.split()) > 5:
            validated_highlights.append(highlight)
    return validated_highlights

def detect_exploitation_snippets(highlight):
    for keyword in EXPLOITATION_KEYWORDS:
        if keyword.lower() in highlight.lower():
            logging.debug(f"Exploitation snippet detected: {highlight}")
            return True
    return False

def process_highlights(highlights):
    quarantine_bucket = []
    for highlight in highlights:
        if detect_exploitation_snippets(highlight):
            quarantine_bucket.append(highlight)
    logging.debug(f"Quarantine bucket: {quarantine_bucket}")
    return quarantine_bucket

def save_ledger(raw_citations, highlights, year):
    ledger = {
        "ledger": [
            {
                "file": citation.split(":")[0],
                "line": int(citation.split(":")[1]),
                "evidence": highlight
            }
            for citation, highlight in zip(raw_citations, highlights)
        ]
    }
    version_metadata = {
        "version": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry_count": len(ledger["ledger"]),
        "year": year
    }
    ledger["metadata"] = version_metadata
    ledger_filename = f"ContinuityLedger_{year}.json"
    with open(ledger_filename, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=4)

def main():
    logging.debug("Starting the script execution.")
    try:
        current_year = datetime.now().year
        input_directories = [
            f"./chatgpt/{current_year}/{month}" for month in [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"
            ]
        ]

        scores, highlights, raw_citations = parse_transcripts(input_directories)
        validated_highlights = validate_evidence(highlights)
        save_ledger(raw_citations, validated_highlights, current_year)

        changelog_entry = {
            "version": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changes": {
                "new_entries": len(validated_highlights),
                "files_processed": input_directories,
                "year": current_year
            }
        }

        changelog_path = f"ContinuityChangelog_{current_year}.json"
        try:
            with open(changelog_path, "r", encoding="utf-8") as changelog_file:
                changelog = json.load(changelog_file)
        except FileNotFoundError:
            changelog = []

        changelog.append(changelog_entry)
        with open(changelog_path, "w", encoding="utf-8") as changelog_file:
            json.dump(changelog, changelog_file, indent=4)

        report = generate_report(scores, validated_highlights[:10], raw_citations)
        report_filename = f"CONTINUITYGPT_REPORT_{current_year}.json"
        with open(report_filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)

        logging.debug("Script executed successfully.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    logging.debug("Running as the main module.")
    main()
