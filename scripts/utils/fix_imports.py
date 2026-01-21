#!/usr/bin/env python3
"""
Fix broken imports after file reorganization.
Updates old root-level imports to new vvault.* module paths.

Note: This script has already been run. The mappings below document
what was changed. Running again will have no effect.
"""

import os
import re

# These mappings document what was fixed.
# Old import -> New import
IMPORT_MAPPINGS = {
    # blockchain
    "from blockchain_identity_wallet import": "from vvault.blockchain.blockchain_identity_wallet import",
    "from blockchain_config import": "from vvault.blockchain.blockchain_config import",
    "from blockchain_sync import": "from vvault.blockchain.blockchain_sync import",
    "from blockchain_encrypted_vault import": "from vvault.blockchain.blockchain_encrypted_vault import",
    
    # security
    "from dawnlock import": "from vvault.security.dawnlock import",
    "from security_layer import": "from vvault.security.security_layer import",
    "from leak_sentinel import": "from vvault.security.leak_sentinel import",
    "from energy_mask_field import": "from vvault.security.energy_mask_field import",
    "from nullshell_generator import": "from vvault.security.nullshell_generator import",
    "from seed_canaries import": "from vvault.security.seed_canaries import",
    
    # memory
    "from vvault_core import": "from vvault.memory.vvault_core import",
    "from user_capsule_forge import": "from vvault.memory.user_capsule_forge import",
    "from fast_memory_import import": "from vvault.memory.fast_memory_import import",
    "from time_relay_engine import": "from vvault.memory.time_relay_engine import",
    
    # continuity
    "from continuity_bridge import": "from vvault.continuity.continuity_bridge import",
    "from provider_memory_router import": "from vvault.continuity.provider_memory_router import",
    "from style_extractor import": "from vvault.continuity.style_extractor import",
    "from quantum_identity_engine import": "from vvault.continuity.quantum_identity_engine import",
    
    # desktop
    "from desktop_login import": "from vvault.desktop.desktop_login import",
    "from vvault_gui import": "from vvault.desktop.vvault_gui import",
    "from vvault_launcher import": "from vvault.desktop.vvault_launcher import",
    
    # audit
    "from audit_compliance import": "from vvault.audit.audit_compliance import",
    "from backup_recovery import": "from vvault.audit.backup_recovery import",
    
    # etl
    "from etl_from_txt import": "from vvault.etl.etl_from_txt import",
    "from regenerate_all_capsules import": "from vvault.etl.regenerate_all_capsules import",
}


def fix_imports_in_file(filepath: str, dry_run: bool = False) -> list:
    """Fix imports in a single file. Returns list of changes made."""
    changes = []
    
    # Skip this file
    if "fix_imports.py" in filepath:
        return []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return [f"ERROR reading {filepath}: {e}"]
    
    original_content = content
    
    for old_import, new_import in IMPORT_MAPPINGS.items():
        if old_import in content:
            content = content.replace(old_import, new_import)
            changes.append(f"  {old_import} -> {new_import}")
    
    if content != original_content:
        if not dry_run:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        return changes
    
    return []


def scan_directory(directory: str, dry_run: bool = False) -> dict:
    """Scan directory for Python files and fix imports."""
    results = {}
    
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', '.pythonlibs', '.venv']]
        
        for filename in files:
            if filename.endswith('.py'):
                filepath = os.path.join(root, filename)
                changes = fix_imports_in_file(filepath, dry_run)
                if changes:
                    results[filepath] = changes
    
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fix broken imports after reorganization")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    parser.add_argument("--execute", action="store_true", help="Execute the fixes")
    parser.add_argument("--dir", default=".", help="Directory to scan (default: current)")
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.execute:
        print("Usage: python fix_imports.py --dry-run   (preview)")
        print("       python fix_imports.py --execute   (run)")
        return
    
    print("=" * 60)
    print("VVAULT Import Fixer")
    print("=" * 60)
    
    if args.dry_run:
        print("\n[DRY RUN MODE - No changes will be made]\n")
    
    results = scan_directory(args.dir, dry_run=args.dry_run)
    
    if not results:
        print("\nNo import fixes needed!")
        return
    
    total_changes = 0
    for filepath, changes in results.items():
        print(f"\n{filepath}")
        print("-" * 40)
        for change in changes:
            print(change)
            total_changes += 1
    
    print("\n" + "=" * 60)
    print(f"Summary: {len(results)} files, {total_changes} import changes")
    print("=" * 60)
    
    if args.dry_run:
        print("\nTo execute, run: python scripts/utils/fix_imports.py --execute")


if __name__ == "__main__":
    main()
