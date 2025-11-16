#!/usr/bin/env python3
"""
VVAULT Log Organization Script
Moves all log files to a new 'logs' folder and updates imports accordingly.
"""

import os
import shutil
import re
from pathlib import Path

def organize_vvault_logs():
    """Organize log files and update imports in VVAULT scripts."""
    
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Find all potential log files
    log_patterns = [
        "*.log",           # All .log files
        "*_log.txt",       # Log text files
        "*_results.json",  # Result files
        "*_records.json",  # Record files
        "*_ledger.json",   # Ledger files
        "*_registry.json", # Registry files
    ]

    # Specific log files to move
    specific_log_files = [
        "canary_detection_results.json",
        "canary_records.json", 
        "sample_compliant_records.json",
        "vvault_continuity_ledger.json",
        "construct_capsule_registry.json"
    ]

    # Collect all files to move
    files_to_move = []

    # Add files matching patterns
    for pattern in log_patterns:
        files_to_move.extend(Path(".").glob(pattern))
    
    # Add specific files that exist
    for log_file in specific_log_files:
        if Path(log_file).exists():
            files_to_move.append(Path(log_file))
    
    # Remove duplicates and filter out directories
    files_to_move = list(set([f for f in files_to_move if f.is_file()]))
    
    # Move files to logs directory
    moved_files = []
    for log_file in files_to_move:
        try:
            shutil.move(str(log_file), logs_dir / log_file.name)
            moved_files.append(log_file.name)
            print(f"✅ Moved {log_file.name} to logs/")
        except Exception as e:
            print(f"⚠️  Failed to move {log_file}: {e}")
    
    # Find Python files that might reference these logs
    python_files = list(Path(".").glob("*.py"))
    
    # Update imports and file paths in Python scripts
    for py_file in python_files:
        if py_file.name == "organize_logs.py":  # Skip this script
            continue
            
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # Update file path references for moved log files
            for log_file in moved_files:
                # Update direct file references
                content = re.sub(
                    rf'["\']?{re.escape(log_file)}["\']?',
                    f'"logs/{log_file}"',
                    content
                )
                
                # Update Path references
                content = re.sub(
                    rf'Path\(["\']?{re.escape(log_file)}["\']?\)',
                    f'Path("logs/{log_file}")',
                    content
                )
                
                # Update os.path.join references
                content = re.sub(
                    rf'os\.path\.join\([^,)]*,\s*["\']?{re.escape(log_file)}["\']?\)',
                    f'os.path.join("logs", "{log_file}")',
                    content
                )
            
            # Write back if changes were made
            if content != original_content:
                with open(py_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"📝 Updated imports in {py_file}")
                
        except Exception as e:
            print(f"⚠️  Error processing {py_file}: {e}")
    
    # Create a logs README for documentation
    logs_readme = logs_dir / "README.md"
    with open(logs_readme, 'w') as f:
        f.write("""# VVAULT Logs Directory

This directory contains all VVAULT log and data files for better organization.

## Files:
""")
        for log_file in moved_files:
            f.write(f"- `{log_file}` - Moved from root directory\n")
        
        f.write(f"""
## Organization Date:
- Moved on: {Path().cwd().name} reorganization
- Script used: `organize_logs.py`

## Purpose:
Centralizes all logging and data tracking files for easier maintenance and backup.
""")
    
    print(f"\n✨ Log organization complete!")
    print(f"📁 Created logs/ directory with {len(moved_files)} files")
    print(f"📄 Updated imports in {len(python_files)-1} Python files")
    print(f"📋 Created logs/README.md for documentation")

if __name__ == "__main__":
    organize_vvault_logs()