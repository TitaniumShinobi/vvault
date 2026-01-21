#!/usr/bin/env python3
"""
VVAULT Desktop Startup Script
Simple startup script for the VVAULT desktop application.

Usage:
    python3 start_vvault_desktop.py

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    """Start the VVAULT desktop application"""
    # Get the project directory
    project_dir = Path(__file__).parent.absolute()
    
    print("🚀 Starting VVAULT Desktop Application...")
    print(f"📁 Project Directory: {project_dir}")
    
    # Check if we're in the right directory
    if not (project_dir / "vvault_launcher.py").exists():
        print("❌ Error: vvault_launcher.py not found in current directory")
        print("   Please run this script from the VVAULT project directory")
        sys.exit(1)
    
    # Check for virtual environment
    venv_path = project_dir / "vvault_env"
    if not venv_path.exists():
        print("⚠️  Warning: Virtual environment not found")
        print("   Please create a virtual environment first:")
        print("   python3 -m venv vvault_env")
        print("   source vvault_env/bin/activate")
        print("   pip install -r requirements.txt")
    
    # Check for required files
    required_files = [
        "vvault_launcher.py",
        "vvault_gui.py",
        "process_manager.py",
        "capsule_viewer.py",
        "security_layer.py",
        "blockchain_sync.py"
    ]
    
    missing_files = []
    for file in required_files:
        if not (project_dir / file).exists():
            missing_files.append(file)
    
    if missing_files:
        print("❌ Error: Missing required files:")
        for file in missing_files:
            print(f"   - {file}")
        sys.exit(1)
    
    # Check for brain.py
    brain_script = project_dir / "corefiles" / "brain.py"
    if not brain_script.exists():
        print("⚠️  Warning: brain.py not found at corefiles/brain.py")
        print("   The VVAULT core system may not be available")
    
    # Check for capsules directory
    capsules_dir = project_dir / "capsules"
    if not capsules_dir.exists():
        print("📁 Creating capsules directory...")
        capsules_dir.mkdir(exist_ok=True)
    
    print("✅ All checks passed")
    print("🎯 Launching VVAULT Desktop Application...")
    
    try:
        # Launch the desktop application
        launcher_script = project_dir / "vvault_launcher.py"
        subprocess.run([sys.executable, str(launcher_script)], check=True)
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error launching VVAULT Desktop: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n🛑 VVAULT Desktop Application stopped by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
