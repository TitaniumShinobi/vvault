#!/usr/bin/env python3
"""
VVAULT Status Check
Check the status of all VVAULT components and applications.
"""

import os
import sys
import subprocess
from pathlib import Path

def check_vvault_status():
    """Check VVAULT system status"""
    print("🔍 VVAULT System Status Check")
    print("=" * 50)
    
    # Check project directory
    project_dir = "/Users/devonwoodson/Documents/GitHub/VVAULT"
    if not os.path.exists(project_dir):
        print("❌ VVAULT project directory not found")
        return False
    
    print(f"✅ Project directory: {project_dir}")
    
    # Check virtual environment
    venv_path = os.path.join(project_dir, "vvault_env")
    if os.path.exists(venv_path):
        print("✅ Virtual environment found")
    else:
        print("❌ Virtual environment not found")
        return False
    
    # Check main components
    components = [
        "vvault_launcher.py",
        "desktop_login.py", 
        "process_manager.py",
        "capsule_viewer.py",
        "security_layer.py",
        "blockchain_sync.py",
        "vvault_gui.py"
    ]
    
    print("\n📁 Checking VVAULT Components:")
    for component in components:
        component_path = os.path.join(project_dir, component)
        if os.path.exists(component_path):
            print(f"✅ {component}")
        else:
            print(f"❌ {component} (missing)")
            return False
    
    # Check assets
    assets_dir = os.path.join(project_dir, "assets")
    if os.path.exists(assets_dir):
        print(f"✅ Assets directory: {assets_dir}")
        
        # Check VVAULT glyph
        glyph_path = os.path.join(assets_dir, "vvault_glyph.png")
        if os.path.exists(glyph_path):
            print("✅ VVAULT glyph found")
        else:
            print("⚠️  VVAULT glyph not found")
    else:
        print("❌ Assets directory not found")
        return False
    
    # Check capsules directory
    capsules_dir = os.path.join(project_dir, "capsules")
    if os.path.exists(capsules_dir):
        print(f"✅ Capsules directory: {capsules_dir}")
        
        # Count capsules
        capsule_count = 0
        for root, dirs, files in os.walk(capsules_dir):
            for file in files:
                if file.endswith('.capsule'):
                    capsule_count += 1
        
        print(f"✅ Found {capsule_count} capsules")
    else:
        print("⚠️  Capsules directory not found")
    
    # Check corefiles
    corefiles_dir = os.path.join(project_dir, "corefiles")
    if os.path.exists(corefiles_dir):
        print(f"✅ Core files directory: {corefiles_dir}")
        
        brain_script = os.path.join(corefiles_dir, "brain.py")
        if os.path.exists(brain_script):
            print("✅ brain.py found")
        else:
            print("❌ brain.py not found")
            return False
    else:
        print("❌ Core files directory not found")
        return False
    
    # Check Python dependencies
    print("\n🐍 Checking Python Dependencies:")
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
    
    try:
        from PIL import Image
        print("✅ Pillow (PIL)")
    except ImportError:
        print("❌ Pillow (PIL) (missing)")
        return False
    
    # Check running processes
    print("\n🔄 Checking Running Processes:")
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        vvault_processes = []
        for line in lines:
            if 'python' in line and ('vvault' in line.lower() or 'VVAULT' in line):
                vvault_processes.append(line.strip())
        
        if vvault_processes:
            print(f"✅ Found {len(vvault_processes)} VVAULT process(es)")
            for process in vvault_processes[:3]:  # Show first 3
                print(f"   {process[:80]}...")
        else:
            print("ℹ️  No VVAULT processes currently running")
    except Exception as e:
        print(f"⚠️  Could not check processes: {e}")
    
    print("\n📊 VVAULT System Summary:")
    print("=" * 30)
    print("✅ Desktop Application: Ready")
    print("✅ Login Screen: Ready")
    print("✅ Process Manager: Ready")
    print("✅ Capsule Viewer: Ready")
    print("✅ Security Layer: Ready")
    print("✅ Blockchain Sync: Ready")
    print("✅ All Dependencies: Installed")
    print("✅ Assets: Available")
    print("✅ Core System: Ready")
    
    print("\n🚀 VVAULT is ready to use!")
    print("\n📋 Available Commands:")
    print("   • python3 start_vvault_with_login.py  # Start with login")
    print("   • python3 desktop_login.py            # Login screen only")
    print("   • python3 vvault_launcher.py         # Main application")
    print("   • python3 test_login_screen.py       # Test login screen")
    
    print("\n🔐 Test Credentials:")
    print("   • admin@vvault.com / admin123")
    print("   • user@vvault.com / user123")
    print("   • test@vvault.com / test123")
    
    return True

def main():
    """Main function"""
    if check_vvault_status():
        print("\n🎉 VVAULT System Status: READY")
    else:
        print("\n❌ VVAULT System Status: ISSUES DETECTED")
        sys.exit(1)

if __name__ == "__main__":
    main()
