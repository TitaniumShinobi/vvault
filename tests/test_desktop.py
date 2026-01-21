#!/usr/bin/env python3
"""
Test script for VVAULT Desktop Application
Simple test to verify all components are working.
"""

import sys
import os
import tkinter as tk
from pathlib import Path

def test_imports():
    """Test if all required modules can be imported"""
    print("🧪 Testing imports...")
    
    try:
        import psutil
        print("✅ psutil imported successfully")
    except ImportError as e:
        print(f"❌ psutil import failed: {e}")
        return False
    
    try:
        import cryptography
        print("✅ cryptography imported successfully")
    except ImportError as e:
        print(f"❌ cryptography import failed: {e}")
        return False
    
    try:
        import matplotlib
        print("✅ matplotlib imported successfully")
    except ImportError as e:
        print(f"❌ matplotlib import failed: {e}")
        return False
    
    try:
        import numpy
        print("✅ numpy imported successfully")
    except ImportError as e:
        print(f"❌ numpy import failed: {e}")
        return False
    
    try:
        import pandas
        print("✅ pandas imported successfully")
    except ImportError as e:
        print(f"❌ pandas import failed: {e}")
        return False
    
    try:
        import web3
        print("✅ web3 imported successfully")
    except ImportError as e:
        print(f"❌ web3 import failed: {e}")
        return False
    
    return True

def test_tkinter():
    """Test if tkinter is working"""
    print("🧪 Testing tkinter...")
    
    try:
        root = tk.Tk()
        root.title("VVAULT Test")
        root.geometry("300x200")
        
        label = tk.Label(root, text="VVAULT Desktop Test", font=("Arial", 16))
        label.pack(pady=50)
        
        button = tk.Button(root, text="Close", command=root.destroy)
        button.pack(pady=20)
        
        print("✅ tkinter GUI created successfully")
        print("   (Close the window to continue)")
        
        root.mainloop()
        return True
        
    except Exception as e:
        print(f"❌ tkinter test failed: {e}")
        return False

def test_vvault_components():
    """Test VVAULT components"""
    print("🧪 Testing VVAULT components...")
    
    try:
        # Test process manager
        from process_manager import VVAULTProcessManager, ProcessConfig
        print("✅ process_manager imported successfully")
        
        # Test capsule viewer
        from capsule_viewer import CapsuleViewer
        print("✅ capsule_viewer imported successfully")
        
        # Test security layer
        from vvault.security.security_layer import VVAULTSecurityLayer
        print("✅ security_layer imported successfully")
        
        # Test blockchain sync
        from vvault.blockchain.blockchain_sync import VVAULTBlockchainSync
        print("✅ blockchain_sync imported successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ VVAULT components test failed: {e}")
        return False

def test_brain_script():
    """Test brain.py script"""
    print("🧪 Testing brain.py script...")
    
    brain_script = Path("corefiles/brain.py")
    if brain_script.exists():
        print("✅ brain.py script exists")
        return True
    else:
        print("❌ brain.py script not found")
        return False

def main():
    """Main test function"""
    print("🚀 VVAULT Desktop Application Test")
    print("=" * 50)
    
    # Get project directory
    project_dir = Path(__file__).parent.absolute()
    print(f"📁 Project Directory: {project_dir}")
    
    # Change to project directory
    os.chdir(project_dir)
    
    # Run tests
    tests = [
        ("Import Test", test_imports),
        ("Tkinter Test", test_tkinter),
        ("VVAULT Components Test", test_vvault_components),
        ("Brain Script Test", test_brain_script)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    # Print results
    print(f"\n{'='*50}")
    print("📊 TEST RESULTS")
    print(f"{'='*50}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {test_name}")
        if result:
            passed += 1
    
    print(f"\n📈 Summary: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! VVAULT Desktop Application is ready.")
        return True
    else:
        print("⚠️ Some tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
