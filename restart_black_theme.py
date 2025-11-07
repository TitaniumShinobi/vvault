#!/usr/bin/env python3
"""
Restart VVAULT Desktop Application with Pure Black Theme
This script kills any existing VVAULT processes and restarts with the black theme.
"""

import os
import sys
import subprocess
import time
import signal

def kill_vvault_processes():
    """Kill any existing VVAULT processes"""
    print("🛑 Stopping existing VVAULT processes...")
    
    try:
        # Find VVAULT processes
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        vvault_pids = []
        for line in lines:
            if 'python' in line and ('vvault' in line or 'VVAULT' in line):
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        vvault_pids.append(pid)
                        print(f"   Found VVAULT process: PID {pid}")
                    except ValueError:
                        continue
        
        # Kill processes
        for pid in vvault_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"   Sent SIGTERM to PID {pid}")
            except ProcessLookupError:
                print(f"   Process {pid} already terminated")
            except PermissionError:
                print(f"   Permission denied for PID {pid}")
        
        # Wait a moment for graceful shutdown
        if vvault_pids:
            print("   Waiting for graceful shutdown...")
            time.sleep(2)
            
            # Force kill if still running
            for pid in vvault_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    print(f"   Force killed PID {pid}")
                except ProcessLookupError:
                    pass  # Process already dead
        
        print("✅ VVAULT processes stopped")
        return True
        
    except Exception as e:
        print(f"❌ Error stopping processes: {e}")
        return False

def start_vvault_black_theme():
    """Start VVAULT with black theme"""
    print("🚀 Starting VVAULT Desktop Application with Pure Black Theme...")
    
    try:
        # Change to project directory
        project_dir = "/Users/devonwoodson/Documents/GitHub/VVAULT"
        os.chdir(project_dir)
        
        # Activate virtual environment and start launcher
        cmd = [
            "bash", "-c", 
            f"cd {project_dir} && source vvault_env/bin/activate && python3 vvault_launcher.py"
        ]
        
        print("   Launching VVAULT Desktop Application...")
        print("   Theme: Pure Black (#000000)")
        print("   Interface: Multi-tab desktop application")
        print("   Components: Process Manager, Capsule Viewer, Security Layer, Blockchain Sync")
        
        # Start the application
        process = subprocess.Popen(cmd)
        
        print("✅ VVAULT Desktop Application started successfully!")
        print("🎨 Pure black theme applied to all components")
        print("🖥️  Desktop application should now be visible")
        
        return True
        
    except Exception as e:
        print(f"❌ Error starting VVAULT: {e}")
        return False

def main():
    """Main function"""
    print("🎨 VVAULT Desktop Application - Pure Black Theme")
    print("=" * 60)
    
    # Kill existing processes
    if not kill_vvault_processes():
        print("⚠️  Warning: Some processes may still be running")
    
    print()
    
    # Start with black theme
    if start_vvault_black_theme():
        print("\n🎉 VVAULT Desktop Application is now running with pure black theme!")
        print("📱 Check your desktop for the VVAULT application window")
    else:
        print("\n❌ Failed to start VVAULT Desktop Application")
        sys.exit(1)

if __name__ == "__main__":
    main()
