#!/usr/bin/env python3
"""
Verify that all log file imports are working correctly after reorganization.
"""

import os
import ast
import re
from pathlib import Path

def verify_imports():
    """Check that all log file references are valid."""
    
    log_files = [
        "logs/canary_detection_results.json",
        "logs/canary_records.json", 
        "logs/sample_compliant_records.json",
        "logs/vvault_continuity_ledger.json",
        "logs/construct_capsule_registry.json"
    ]
    
    # Verify log files exist
    print("🔍 Checking log file existence:")
    for log_file in log_files:
        if Path(log_file).exists():
            print(f"✅ {log_file}")
        else:
            print(f"❌ Missing: {log_file}")
    
    # Check Python files for correct references
    print("\n🔍 Checking Python file references:")
    python_files = list(Path(".").glob("*.py"))
    
    for py_file in python_files:
        if py_file.name in ["organize_logs.py", "verify_log_imports.py"]:
            continue
            
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Look for old references that weren't updated
            old_refs = []
            for log_name in ["logs/canary_detection_results.json", "logs/canary_records.json", 
                           "logs/sample_compliant_records.json", "logs/vvault_continuity_ledger.json",
                           "logs/construct_capsule_registry.json"]:
                if f'"{log_name}"' in content and f'"logs/{log_name}"' not in content:
                    old_refs.append(log_name)
            
            if old_refs:
                print(f"⚠️  {py_file}: Still has old references to {old_refs}")
            else:
                print(f"✅ {py_file}: Import references look good")
                
        except Exception as e:
            print(f"❌ Error checking {py_file}: {e}")

if __name__ == "__main__":
    verify_imports()