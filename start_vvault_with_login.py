#!/usr/bin/env python3
"""
VVAULT Desktop Application with Login Screen
Complete startup script for VVAULT with integrated login authentication.

Features:
- Desktop login screen with VVAULT branding
- Pure black terminal aesthetic
- Email/password authentication
- Integrated with main VVAULT application
- Secure login flow

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import logging
from pathlib import Path

# Add project directory to path
PROJECT_DIR = "/Users/devonwoodson/Documents/GitHub/VVAULT"
sys.path.append(PROJECT_DIR)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Check if the environment is properly set up"""
    print("🔍 Checking VVAULT Environment...")
    
    # Check if we're in the right directory
    if not os.path.exists(os.path.join(PROJECT_DIR, "vvault_launcher.py")):
        print("❌ VVAULT launcher not found. Please run from VVAULT directory.")
        return False
    
    # Check if virtual environment exists
    venv_path = os.path.join(PROJECT_DIR, "vvault_env")
    if not os.path.exists(venv_path):
        print("❌ Virtual environment not found. Please run setup first.")
        return False
    
    # Check if assets exist
    assets_dir = os.path.join(PROJECT_DIR, "assets")
    if not os.path.exists(assets_dir):
        print("⚠️  Assets directory not found. Creating...")
        os.makedirs(assets_dir, exist_ok=True)
    
    # Check if VVAULT glyph exists
    glyph_path = os.path.join(assets_dir, "vvault_glyph.png")
    if not os.path.exists(glyph_path):
        print("⚠️  VVAULT glyph not found. Creating...")
        try:
            from create_vvault_glyph import create_vvault_glyph
            glyph = create_vvault_glyph()
            glyph.save(glyph_path, "PNG")
            print("✅ VVAULT glyph created")
        except Exception as e:
            print(f"⚠️  Could not create glyph: {e}")
    
    print("✅ Environment check complete")
    return True

def main():
    """Main function"""
    print("🚀 VVAULT Desktop Application with Login")
    print("=" * 50)
    
    # Check environment
    if not check_environment():
        print("❌ Environment check failed")
        sys.exit(1)
    
    print("🔐 Starting VVAULT with Login Screen...")
    print("   Theme: Pure Black Terminal Aesthetic")
    print("   Features: Email/Password Authentication")
    print("   Integration: Full VVAULT Desktop Application")
    print()
    
    try:
        # Import and run the launcher with login
        from vvault_launcher import VVAULTDesktopLauncher
        
        print("🎯 Initializing VVAULT Desktop Launcher...")
        launcher = VVAULTDesktopLauncher()
        
        print("✅ VVAULT Desktop Application ready")
        print("🖥️  Login screen will appear first")
        print("   Valid test credentials:")
        print("   • admin@vvault.com / admin123")
        print("   • user@vvault.com / user123")
        print("   • test@vvault.com / test123")
        print()
        
        # Run the application
        launcher.run()
        
    except KeyboardInterrupt:
        print("\n🛑 VVAULT Desktop Application stopped by user")
    except Exception as e:
        print(f"❌ Error starting VVAULT: {e}")
        logger.error(f"Application error: {e}")
        sys.exit(1)
    
    print("👋 VVAULT Desktop Application closed")

if __name__ == "__main__":
    main()
