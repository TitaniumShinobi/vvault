#!/usr/bin/env python3
"""
VVAULT Desktop Application Status Check
Check if the desktop application is running and show status.
"""

import os
import sys
import subprocess
from pathlib import Path

def check_processes():
    """Check for running VVAULT processes"""
    print("🔍 Checking for VVAULT processes...")
    
    try:
        # Check for Python processes running VVAULT
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        vvault_processes = []
        for line in lines:
            if 'python' in line and ('vvault' in line or 'VVAULT' in line):
                vvault_processes.append(line.strip())
        
        if vvault_processes:
            print(f"✅ Found {len(vvault_processes)} VVAULT process(es):")
            for process in vvault_processes:
                print(f"   {process}")
        else:
            print("❌ No VVAULT processes found")
        
        return len(vvault_processes) > 0
        
    except Exception as e:
        print(f"❌ Error checking processes: {e}")
        return False

def check_files():
    """Check if required files exist"""
    print("🔍 Checking required files...")
    
    project_dir = Path(__file__).parent.absolute()
    required_files = [
        "vvault_launcher.py",
        "process_manager.py", 
        "capsule_viewer.py",
        "security_layer.py",
        "blockchain_sync.py",
        "corefiles/brain.py",
        "vvault_env/bin/activate"
    ]
    
    missing_files = []
    for file in required_files:
        file_path = project_dir / file
        if file_path.exists():
            print(f"✅ {file}")
        else:
            print(f"❌ {file} (missing)")
            missing_files.append(file)
    
    return len(missing_files) == 0

def check_dependencies():
    """Check if dependencies are installed"""
    print("🔍 Checking dependencies...")
    
    try:
        import psutil
        print("✅ psutil")
    except ImportError:
        print("❌ psutil (missing)")
        return False
    
    try:
        import cryptography
        print("✅ cryptography")
    except ImportError:
        print("❌ cryptography (missing)")
        return False
    
    try:
        import matplotlib
        print("✅ matplotlib")
    except ImportError:
        print("❌ matplotlib (missing)")
        return False
    
    try:
        import numpy
        print("✅ numpy")
    except ImportError:
        print("❌ numpy (missing)")
        return False
    
    try:
        import pandas
        print("✅ pandas")
    except ImportError:
        print("❌ pandas (missing)")
        return False
    
    try:
        import web3
        print("✅ web3")
    except ImportError:
        print("❌ web3 (missing)")
        return False
    
    return True

def main():
    """Main status check"""
    print("🔍 VVAULT Desktop Application Status Check")
    print("=" * 50)
    
    # Check files
    files_ok = check_files()
    print()
    
    # Check dependencies
    deps_ok = check_dependencies()
    print()
    
    # Check processes
    processes_ok = check_processes()
    print()
    
    # Summary
    print("📊 STATUS SUMMARY")
    print("=" * 20)
    print(f"Files: {'✅ OK' if files_ok else '❌ Missing'}")
    print(f"Dependencies: {'✅ OK' if deps_ok else '❌ Missing'}")
    print(f"Processes: {'✅ Running' if processes_ok else '❌ Not Running'}")
    
    if files_ok and deps_ok:
        print("\n🎉 VVAULT Desktop Application is ready!")
        if not processes_ok:
            print("💡 To start the application, run:")
            print("   python3 vvault_launcher.py")
    else:
        print("\n⚠️ VVAULT Desktop Application has issues.")
        if not files_ok:
            print("   - Missing required files")
        if not deps_ok:
            print("   - Missing dependencies")

if __name__ == "__main__":
    main()
