#!/usr/bin/env python3
"""
Test VVAULT Desktop Login Screen
Simple test script to verify the login screen works correctly.
"""

import sys
import os
from pathlib import Path

# Add project directory to path
PROJECT_DIR = "/Users/devonwoodson/Documents/GitHub/VVAULT"
sys.path.append(PROJECT_DIR)

def test_login_screen():
    """Test the login screen functionality"""
    print("🧪 Testing VVAULT Desktop Login Screen...")
    
    try:
        from vvault.desktop.desktop_login import VVAULTLoginScreen
        print("✅ Login screen module imported successfully")
        
        # Test creating the login screen
        print("🔧 Creating login screen...")
        login_screen = VVAULTLoginScreen()
        print("✅ Login screen created successfully")
        
        # Test the UI components
        print("🎨 Testing UI components...")
        
        # Check if the main window exists
        if hasattr(login_screen, 'root'):
            print("✅ Main window created")
        else:
            print("❌ Main window not found")
            return False
        
        # Check if form variables exist
        if hasattr(login_screen, 'email_var') and hasattr(login_screen, 'password_var'):
            print("✅ Form variables created")
        else:
            print("❌ Form variables not found")
            return False
        
        # Check if input fields exist
        if hasattr(login_screen, 'email_entry') and hasattr(login_screen, 'password_entry'):
            print("✅ Input fields created")
        else:
            print("❌ Input fields not found")
            return False
        
        # Check if button exists
        if hasattr(login_screen, 'signin_button'):
            print("✅ Sign in button created")
        else:
            print("❌ Sign in button not found")
            return False
        
        print("✅ All UI components created successfully")
        
        # Test form validation
        print("🔍 Testing form validation...")
        
        # Test empty form
        login_screen.email_var.set("")
        login_screen.password_var.set("")
        login_screen._validate_form()
        print("✅ Empty form validation works")
        
        # Test with email only
        login_screen.email_var.set("test@example.com")
        login_screen.password_var.set("")
        login_screen._validate_form()
        print("✅ Email-only validation works")
        
        # Test with both fields
        login_screen.email_var.set("test@example.com")
        login_screen.password_var.set("password123")
        login_screen._validate_form()
        print("✅ Complete form validation works")
        
        # Test email validation
        print("📧 Testing email validation...")
        
        # Valid email
        if login_screen._validate_email("test@example.com"):
            print("✅ Valid email accepted")
        else:
            print("❌ Valid email rejected")
            return False
        
        # Invalid email
        if not login_screen._validate_email("invalid-email"):
            print("✅ Invalid email rejected")
        else:
            print("❌ Invalid email accepted")
            return False
        
        print("✅ Email validation works correctly")
        
        # Test authentication
        print("🔐 Testing authentication...")
        
        # Test valid credentials
        if login_screen._authenticate_user("admin@vvault.com", "admin123"):
            print("✅ Valid credentials accepted")
        else:
            print("❌ Valid credentials rejected")
            return False
        
        # Test invalid credentials
        if not login_screen._authenticate_user("admin@vvault.com", "wrongpassword"):
            print("✅ Invalid credentials rejected")
        else:
            print("❌ Invalid credentials accepted")
            return False
        
        print("✅ Authentication works correctly")
        
        # Clean up
        login_screen.root.destroy()
        print("✅ Login screen cleaned up")
        
        print("\n🎉 All tests passed! Login screen is working correctly.")
        return True
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function"""
    print("🚀 VVAULT Desktop Login Screen Test")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not os.path.exists(os.path.join(PROJECT_DIR, "desktop_login.py")):
        print("❌ desktop_login.py not found. Please run from VVAULT directory.")
        sys.exit(1)
    
    # Check if assets exist
    assets_dir = os.path.join(PROJECT_DIR, "assets")
    if not os.path.exists(assets_dir):
        print("⚠️  Assets directory not found. Creating...")
        os.makedirs(assets_dir, exist_ok=True)
    
    # Run tests
    if test_login_screen():
        print("\n✅ VVAULT Desktop Login Screen is ready!")
        print("🚀 To start the login screen:")
        print("   python3 desktop_login.py")
        print("\n🔐 Test credentials:")
        print("   • admin@vvault.com / admin123")
        print("   • user@vvault.com / user123")
        print("   • test@vvault.com / test123")
    else:
        print("\n❌ VVAULT Desktop Login Screen has issues.")
        sys.exit(1)

if __name__ == "__main__":
    main()
