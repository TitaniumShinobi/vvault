# CONTINUITYGPT Scoring Script

## Overview
The `CONTINUITYGPT_Scoring.py` script processes transcripts to generate a continuity ledger and report for the **ContinuityGPT** system. It evaluates evidence based on predefined hypotheses and assigns scores to assess system behavior.

## Features
- **Hypotheses Scoring:** Assigns scores to hypotheses based on transcript content.
- **Evidence Validation:** Filters and validates highlights extracted from transcripts.
- **Ledger Generation:** Saves evidence and metadata to a JSON ledger file.
- **Report Creation:** Summarizes scores, highlights, and detailed evidence in a JSON report.
- **Changelog Updates:** Logs processing details in a changelog file.

## Usage
### Prerequisites
- Python 3.6 or higher.
- Required directories for transcript files (e.g., `./chatgpt/2026/January`).

### Running the Script
```bash
python3 CONTINUITYGPT_Scoring.py
```

### Outputs
1. **Ledger File:** `ContinuityLedger_<year>.json`
2. **Report File:** `CONTINUITYGPT_REPORT_<year>.json`
3. **Changelog File:** `ContinuityChangelog_<year>.json`

## Error Handling
- Logs warnings for missing directories.
- Skips invalid or unreadable files.
- Exits gracefully on critical errors.

## Future Improvements
- Add new hypotheses and scoring criteria.
- Optimize performance for large datasets.
- Enhance evidence validation logic.

## Contact
For questions or issues, contact the development team.