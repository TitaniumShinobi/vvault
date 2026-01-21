#!/usr/bin/env python3
"""
VVAULT Project Organizer
Moves floating scripts from root into organized subdirectories.
Run with --dry-run first to preview changes.
"""

import os
import shutil
import argparse
from pathlib import Path

ORGANIZATION_PLAN = {
    "vvault/blockchain": [
        "blockchain_config.py",
        "blockchain_encrypted_vault.py", 
        "blockchain_identity_wallet.py",
        "blockchain_sync.py",
        "wallet_cli.py",
    ],
    "vvault/security": [
        "dawnlock.py",
        "dawnlock_cli.py",
        "dawnlock_integration.py",
        "security_layer.py",
        "leak_sentinel.py",
        "seed_canaries.py",
        "energy_mask_field.py",
        "nullshell_generator.py",
    ],
    "vvault/memory": [
        "vvault_core.py",
        "user_capsule_forge.py",
        "fast_memory_import.py",
        "time_relay_engine.py",
    ],
    "vvault/continuity": [
        "continuity_bridge.py",
        "provider_memory_router.py",
        "style_extractor.py",
        "quantum_identity_engine.py",
    ],
    "vvault/desktop": [
        "vvault_gui.py",
        "vvault_launcher.py",
        "desktop_login.py",
        "start_vvault_desktop.py",
        "start_vvault_with_login.py",
        "restart_black_theme.py",
    ],
    "vvault/etl": [
        "etl_from_txt.py",
        "regenerate_all_capsules.py",
    ],
    "vvault/audit": [
        "audit_compliance.py",
        "backup_recovery.py",
        "rag_eval_harness.py",
    ],
    "vvault/server": [
        "vvault_web_server.py",
    ],
    "scripts/utils": [
        "organize_logs.py",
        "create_vvault_glyph.py",
        "process_manager.py",
    ],
    "scripts/shell": [
        "setup_login_screen.sh",
        "start_login_screen.sh",
        "test_canary_detection.sh",
    ],
    "docs": [
        "CONTINUITY_GPT_PROMPT.md",
        "KATANA_RESURRECTION_PROTOCOL.md",
        "FINAL_STATUS.md",
        "continuity.md",
    ],
}

KEEP_IN_ROOT = [
    "main.py",
    "__init__.py",
    "requirements.txt",
    "requirements_blockchain_capsules.txt",
    "requirements_desktop.txt",
    "requirements_web.txt",
    "package.json",
    "package-lock.json",
    "webpack.config.js",
    "pyproject.toml",
    "uv.lock",
    ".babelrc",
    ".gitignore",
    ".gitattributes",
    "README.md",
    "replit.md",
    "env.example",
    "users.json",
    "security.db",
    "organize_vvault.py",
]


def ensure_directory(path: str, dry_run: bool = False) -> None:
    if not os.path.exists(path):
        if dry_run:
            print(f"  [CREATE DIR] {path}/")
        else:
            os.makedirs(path, exist_ok=True)
            print(f"  [CREATED] {path}/")


def move_file(src: str, dest_dir: str, dry_run: bool = False) -> bool:
    if not os.path.exists(src):
        print(f"  [SKIP] {src} (not found)")
        return False
    
    dest = os.path.join(dest_dir, os.path.basename(src))
    
    if os.path.exists(dest):
        print(f"  [SKIP] {src} -> {dest} (already exists)")
        return False
    
    if dry_run:
        print(f"  [MOVE] {src} -> {dest}")
    else:
        shutil.move(src, dest)
        print(f"  [MOVED] {src} -> {dest}")
    return True


def create_init_files(dry_run: bool = False) -> None:
    init_dirs = [
        "vvault/blockchain",
        "vvault/security", 
        "vvault/memory",
        "vvault/continuity",
        "vvault/desktop",
        "vvault/etl",
        "vvault/audit",
        "vvault/server",
        "scripts/utils",
    ]
    
    for dir_path in init_dirs:
        init_file = os.path.join(dir_path, "__init__.py")
        if not os.path.exists(init_file):
            if dry_run:
                print(f"  [CREATE] {init_file}")
            else:
                Path(init_file).touch()
                print(f"  [CREATED] {init_file}")


def organize(dry_run: bool = False) -> None:
    print("\n" + "=" * 60)
    print("VVAULT Project Organizer")
    print("=" * 60)
    
    if dry_run:
        print("\n[DRY RUN MODE - No changes will be made]\n")
    
    moved_count = 0
    skipped_count = 0
    
    for dest_dir, files in ORGANIZATION_PLAN.items():
        print(f"\n{dest_dir}/")
        print("-" * 40)
        ensure_directory(dest_dir, dry_run)
        
        for filename in files:
            if move_file(filename, dest_dir, dry_run):
                moved_count += 1
            else:
                skipped_count += 1
    
    print("\n\nCreating __init__.py files...")
    print("-" * 40)
    create_init_files(dry_run)
    
    print("\n" + "=" * 60)
    print(f"Summary: {moved_count} files to move, {skipped_count} skipped")
    print("=" * 60)
    
    if dry_run:
        print("\nTo execute, run: python organize_vvault.py --execute")
    else:
        print("\nOrganization complete!")
        print("\nNOTE: You may need to update imports in files that reference moved modules.")


def main():
    parser = argparse.ArgumentParser(description="Organize VVAULT project files")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    parser.add_argument("--execute", action="store_true", help="Execute the organization")
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.execute:
        print("Usage: python organize_vvault.py --dry-run   (preview)")
        print("       python organize_vvault.py --execute   (run)")
        return
    
    organize(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
