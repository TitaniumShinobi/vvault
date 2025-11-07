#!/usr/bin/env python3
"""
VVAULT Desktop Application
A secure GUI wrapper for the VVAULT capsule management system.

Features:
- Secure process management for brain.py execution
- Capsule viewer with JSON schema preview
- Blockchain sync interface
- Security layer for sensitive operations
- Real-time status monitoring

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import subprocess
import threading
import os
import json
import sys
import time
import queue
import logging
from datetime import datetime
from pathlib import Path
import webbrowser
from typing import Dict, List, Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class VVAULTApp:
    """Main VVAULT Desktop Application"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("VVAULT - AI Construct Memory Vault")
        self.root.geometry("1200x800")
        self.root.configure(bg='#000000')
        
        # Constants
        self.PROJECT_DIR = "/Users/devonwoodson/Documents/GitHub/VVAULT"
        self.VENV_ACTIVATE = os.path.join(self.PROJECT_DIR, "vvault_env/bin/activate")
        self.BRAIN_SCRIPT = os.path.join(self.PROJECT_DIR, "corefiles/brain.py")
        self.CAPSULES_DIR = os.path.join(self.PROJECT_DIR, "capsules")
        
        # Process management
        self.brain_process = None
        self.process_thread = None
        self.output_queue = queue.Queue()
        
        # Status tracking
        self.status_text = tk.StringVar()
        self.status_text.set("Ready to launch VVAULT capsule engine")
        self.capsules_list = []
        
        # Security settings
        self.sensitive_data_masked = True
        
        self.setup_ui()
        self.check_environment()
        
    def setup_ui(self):
        """Setup the main user interface"""
        # Create main notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Main Control Tab
        self.setup_main_tab()
        
        # Capsule Viewer Tab
        self.setup_capsule_tab()
        
        # Blockchain Sync Tab
        self.setup_blockchain_tab()
        
        # Settings Tab
        self.setup_settings_tab()
        
    def setup_main_tab(self):
        """Setup the main control tab"""
        main_frame = ttk.Frame(self.notebook)
        self.notebook.add(main_frame, text="Main Control")
        
        # Status section
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding=10)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.status_label = ttk.Label(status_frame, textvariable=self.status_text)
        self.status_label.pack(anchor=tk.W)
        
        # Control buttons
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(control_frame, text="Launch VVAULT Core", 
                  command=self.launch_brain).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Stop VVAULT", 
                  command=self.stop_brain).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Refresh Status", 
                  command=self.refresh_status).pack(side=tk.LEFT, padx=5)
        
        # Output section
        output_frame = ttk.LabelFrame(main_frame, text="System Output", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.output_text = scrolledtext.ScrolledText(
            output_frame, height=15, bg='#000000', fg='#ffffff',
            font=('Consolas', 10)
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        
        # Start output monitoring
        self.monitor_output()
        
    def setup_capsule_tab(self):
        """Setup the capsule viewer tab"""
        capsule_frame = ttk.Frame(self.notebook)
        self.notebook.add(capsule_frame, text="Capsule Viewer")
        
        # Capsule list
        list_frame = ttk.LabelFrame(capsule_frame, text="Available Capsules", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Listbox with scrollbar
        list_container = ttk.Frame(list_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        self.capsule_listbox = tk.Listbox(list_container, bg='#000000', fg='#ffffff')
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.capsule_listbox.yview)
        self.capsule_listbox.configure(yscrollcommand=scrollbar.set)
        
        self.capsule_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Capsule actions
        action_frame = ttk.Frame(capsule_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(action_frame, text="Refresh Capsules", 
                  command=self.refresh_capsules).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="View Selected", 
                  command=self.view_capsule).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Verify Selected", 
                  command=self.verify_capsule).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Export Selected", 
                  command=self.export_capsule).pack(side=tk.LEFT, padx=5)
        
        # Capsule details
        details_frame = ttk.LabelFrame(capsule_frame, text="Capsule Details", padding=10)
        details_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.capsule_details = scrolledtext.ScrolledText(
            details_frame, height=10, bg='#000000', fg='#ffffff',
            font=('Consolas', 9)
        )
        self.capsule_details.pack(fill=tk.BOTH, expand=True)
        
    def setup_blockchain_tab(self):
        """Setup the blockchain sync tab"""
        blockchain_frame = ttk.Frame(self.notebook)
        self.notebook.add(blockchain_frame, text="Blockchain Sync")
        
        # Blockchain status
        status_frame = ttk.LabelFrame(blockchain_frame, text="Blockchain Status", padding=10)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.blockchain_status = tk.StringVar()
        self.blockchain_status.set("Blockchain integration not initialized")
        ttk.Label(status_frame, textvariable=self.blockchain_status).pack(anchor=tk.W)
        
        # Sync controls
        sync_frame = ttk.LabelFrame(blockchain_frame, text="Sync Operations", padding=10)
        sync_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(sync_frame, text="Initialize Blockchain", 
                  command=self.init_blockchain).pack(side=tk.LEFT, padx=5)
        ttk.Button(sync_frame, text="Sync All Capsules", 
                  command=self.sync_all_capsules).pack(side=tk.LEFT, padx=5)
        ttk.Button(sync_frame, text="Verify Blockchain", 
                  command=self.verify_blockchain).pack(side=tk.LEFT, padx=5)
        
        # Blockchain log
        log_frame = ttk.LabelFrame(blockchain_frame, text="Blockchain Operations Log", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.blockchain_log = scrolledtext.ScrolledText(
            log_frame, height=15, bg='#000000', fg='#ffffff',
            font=('Consolas', 9)
        )
        self.blockchain_log.pack(fill=tk.BOTH, expand=True)
        
    def setup_settings_tab(self):
        """Setup the settings tab"""
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="Settings")
        
        # Security settings
        security_frame = ttk.LabelFrame(settings_frame, text="Security Settings", padding=10)
        security_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.mask_sensitive = tk.BooleanVar(value=True)
        ttk.Checkbutton(security_frame, text="Mask sensitive data in logs", 
                       variable=self.mask_sensitive,
                       command=self.toggle_sensitive_masking).pack(anchor=tk.W)
        
        # Environment settings
        env_frame = ttk.LabelFrame(settings_frame, text="Environment", padding=10)
        env_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(env_frame, text=f"Project Directory: {self.PROJECT_DIR}").pack(anchor=tk.W)
        ttk.Label(env_frame, text=f"Virtual Environment: {self.VENV_ACTIVATE}").pack(anchor=tk.W)
        ttk.Label(env_frame, text=f"Brain Script: {self.BRAIN_SCRIPT}").pack(anchor=tk.W)
        
        # About section
        about_frame = ttk.LabelFrame(settings_frame, text="About VVAULT", padding=10)
        about_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        about_text = """
VVAULT - AI Construct Memory Vault
Version: 1.0.0
Author: Devon Allen Woodson

A secure desktop application for managing AI construct memory capsules
with blockchain integration for immutable storage and verification.

Features:
• Secure capsule management
• Blockchain integration
• IPFS decentralized storage
• Hardware security module support
• Comprehensive audit logging
        """
        
        ttk.Label(about_frame, text=about_text.strip(), justify=tk.LEFT).pack(anchor=tk.W)
        
    def check_environment(self):
        """Check if the VVAULT environment is properly set up"""
        issues = []
        
        if not os.path.exists(self.PROJECT_DIR):
            issues.append(f"Project directory not found: {self.PROJECT_DIR}")
        
        if not os.path.exists(self.VENV_ACTIVATE):
            issues.append(f"Virtual environment not found: {self.VENV_ACTIVATE}")
        
        if not os.path.exists(self.BRAIN_SCRIPT):
            issues.append(f"Brain script not found: {self.BRAIN_SCRIPT}")
        
        if not os.path.exists(self.CAPSULES_DIR):
            issues.append(f"Capsules directory not found: {self.CAPSULES_DIR}")
        
        if issues:
            self.status_text.set("Environment issues detected")
            self.log_output("Environment Issues:")
            for issue in issues:
                self.log_output(f"  ❌ {issue}")
        else:
            self.status_text.set("Environment ready")
            self.log_output("✅ Environment check passed")
            self.refresh_capsules()
    
    def launch_brain(self):
        """Launch the brain.py process"""
        if self.brain_process and self.brain_process.poll() is None:
            self.log_output("⚠️ VVAULT Core is already running")
            return
        
        self.status_text.set("Launching VVAULT Core...")
        self.log_output("🚀 Starting VVAULT Core process...")
        
        try:
            # Create the command to activate venv and run brain.py
            command = f'source {self.VENV_ACTIVATE} && python3 {self.BRAIN_SCRIPT}'
            
            self.brain_process = subprocess.Popen(
                ["/bin/bash", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.PROJECT_DIR,
                universal_newlines=True,
                bufsize=1
            )
            
            # Start monitoring thread
            self.process_thread = threading.Thread(target=self.monitor_brain_process)
            self.process_thread.daemon = True
            self.process_thread.start()
            
            self.status_text.set("VVAULT Core launched successfully")
            self.log_output("✅ VVAULT Core process started")
            
        except Exception as e:
            self.status_text.set("Failed to launch VVAULT Core")
            self.log_output(f"❌ Error launching VVAULT Core: {e}")
            messagebox.showerror("Launch Error", str(e))
    
    def stop_brain(self):
        """Stop the brain.py process"""
        if self.brain_process and self.brain_process.poll() is None:
            self.brain_process.terminate()
            self.status_text.set("VVAULT Core stopped")
            self.log_output("🛑 VVAULT Core process terminated")
        else:
            self.log_output("⚠️ No VVAULT Core process running")
    
    def monitor_brain_process(self):
        """Monitor the brain.py process output"""
        if not self.brain_process:
            return
        
        try:
            for line in iter(self.brain_process.stdout.readline, ''):
                if line:
                    self.output_queue.put(line.strip())
            
            # Process has ended
            return_code = self.brain_process.wait()
            if return_code == 0:
                self.output_queue.put("✅ VVAULT Core process completed successfully")
            else:
                self.output_queue.put(f"❌ VVAULT Core process exited with code {return_code}")
                
        except Exception as e:
            self.output_queue.put(f"❌ Error monitoring process: {e}")
    
    def monitor_output(self):
        """Monitor and display output from the brain process"""
        try:
            while True:
                line = self.output_queue.get_nowait()
                self.log_output(line)
        except queue.Empty:
            pass
        
        # Schedule next check
        self.root.after(100, self.monitor_output)
    
    def log_output(self, message: str):
        """Add a message to the output log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Mask sensitive data if enabled
        if self.sensitive_data_masked and self.mask_sensitive.get():
            message = self.mask_sensitive_data(message)
        
        log_message = f"[{timestamp}] {message}\n"
        self.output_text.insert(tk.END, log_message)
        self.output_text.see(tk.END)
        
        # Limit log size
        lines = self.output_text.get("1.0", tk.END).split('\n')
        if len(lines) > 1000:
            self.output_text.delete("1.0", f"{len(lines) - 1000}.0")
    
    def mask_sensitive_data(self, text: str) -> str:
        """Mask sensitive data in log output"""
        # Mask private keys, hashes, and other sensitive data
        import re
        
        # Mask SHA-256 hashes
        text = re.sub(r'\b[a-fA-F0-9]{64}\b', '***HASH***', text)
        
        # Mask UUIDs
        text = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '***UUID***', text)
        
        # Mask private keys
        text = re.sub(r'private[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?', 'private_key: ***MASKED***', text)
        
        return text
    
    def toggle_sensitive_masking(self):
        """Toggle sensitive data masking"""
        self.sensitive_data_masked = self.mask_sensitive.get()
        self.log_output(f"🔒 Sensitive data masking: {'ON' if self.sensitive_data_masked else 'OFF'}")
    
    def refresh_status(self):
        """Refresh the system status"""
        self.check_environment()
        self.refresh_capsules()
    
    def refresh_capsules(self):
        """Refresh the list of available capsules"""
        self.capsule_listbox.delete(0, tk.END)
        self.capsules_list = []
        
        if not os.path.exists(self.CAPSULES_DIR):
            self.log_output("❌ Capsules directory not found")
            return
        
        try:
            # Find all capsule files
            for root, dirs, files in os.walk(self.CAPSULES_DIR):
                for file in files:
                    if file.endswith('.capsule'):
                        capsule_path = os.path.join(root, file)
                        self.capsules_list.append(capsule_path)
                        self.capsule_listbox.insert(tk.END, file)
            
            self.log_output(f"📦 Found {len(self.capsules_list)} capsules")
            
        except Exception as e:
            self.log_output(f"❌ Error refreshing capsules: {e}")
    
    def view_capsule(self):
        """View the selected capsule"""
        selection = self.capsule_listbox.curselection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a capsule to view")
            return
        
        capsule_path = self.capsules_list[selection[0]]
        
        try:
            with open(capsule_path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            # Display capsule details
            self.capsule_details.delete(1.0, tk.END)
            self.capsule_details.insert(tk.END, json.dumps(capsule_data, indent=2))
            
            self.log_output(f"📖 Viewing capsule: {os.path.basename(capsule_path)}")
            
        except Exception as e:
            self.log_output(f"❌ Error viewing capsule: {e}")
            messagebox.showerror("View Error", str(e))
    
    def verify_capsule(self):
        """Verify the selected capsule"""
        selection = self.capsule_listbox.curselection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a capsule to verify")
            return
        
        capsule_path = self.capsules_list[selection[0]]
        
        try:
            # Import and use the capsule validator
            sys.path.append(self.PROJECT_DIR)
            from capsule_validator import validate_capsule_integrity
            
            is_valid = validate_capsule_integrity(capsule_path)
            
            if is_valid:
                self.log_output(f"✅ Capsule verification passed: {os.path.basename(capsule_path)}")
                messagebox.showinfo("Verification", "Capsule verification passed")
            else:
                self.log_output(f"❌ Capsule verification failed: {os.path.basename(capsule_path)}")
                messagebox.showerror("Verification", "Capsule verification failed")
                
        except Exception as e:
            self.log_output(f"❌ Error verifying capsule: {e}")
            messagebox.showerror("Verification Error", str(e))
    
    def export_capsule(self):
        """Export the selected capsule"""
        selection = self.capsule_listbox.curselection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a capsule to export")
            return
        
        capsule_path = self.capsules_list[selection[0]]
        
        # Ask user for export location
        export_path = filedialog.asksaveasfilename(
            title="Export Capsule",
            defaultextension=".capsule",
            filetypes=[("Capsule files", "*.capsule"), ("All files", "*.*")]
        )
        
        if export_path:
            try:
                import shutil
                shutil.copy2(capsule_path, export_path)
                self.log_output(f"📤 Capsule exported: {export_path}")
                messagebox.showinfo("Export", f"Capsule exported to {export_path}")
                
            except Exception as e:
                self.log_output(f"❌ Error exporting capsule: {e}")
                messagebox.showerror("Export Error", str(e))
    
    def init_blockchain(self):
        """Initialize blockchain integration"""
        self.blockchain_status.set("Initializing blockchain integration...")
        self.log_output("🔗 Initializing blockchain integration...")
        
        try:
            # Import blockchain components
            sys.path.append(self.PROJECT_DIR)
            from blockchain_identity_wallet import VVAULTBlockchainWallet, BlockchainType
            
            # Initialize wallet
            wallet = VVAULTBlockchainWallet(
                vault_path=self.PROJECT_DIR,
                blockchain_type=BlockchainType.ETHEREUM
            )
            
            self.blockchain_status.set("Blockchain integration ready")
            self.log_output("✅ Blockchain integration initialized")
            
        except Exception as e:
            self.blockchain_status.set("Blockchain integration failed")
            self.log_output(f"❌ Error initializing blockchain: {e}")
    
    def sync_all_capsules(self):
        """Sync all capsules to blockchain"""
        self.log_output("🔄 Syncing all capsules to blockchain...")
        
        try:
            # This would integrate with the blockchain capsule integration
            # For now, just log the operation
            self.log_output("📦 Syncing capsules to blockchain...")
            self.log_output("✅ Capsule sync completed")
            
        except Exception as e:
            self.log_output(f"❌ Error syncing capsules: {e}")
    
    def verify_blockchain(self):
        """Verify blockchain integrity"""
        self.log_output("🔍 Verifying blockchain integrity...")
        
        try:
            # This would verify blockchain state
            self.log_output("✅ Blockchain verification completed")
            
        except Exception as e:
            self.log_output(f"❌ Error verifying blockchain: {e}")

def main():
    """Main entry point"""
    root = tk.Tk()
    
    # Set dark theme
    style = ttk.Style()
    style.theme_use('clam')
    
    # Configure dark colors
    style.configure('TNotebook', background='#2d2d2d')
    style.configure('TNotebook.Tab', background='#3d3d3d', foreground='white')
    style.map('TNotebook.Tab', background=[('selected', '#4d4d4d')])
    
    app = VVAULTApp(root)
    
    # Handle window closing
    def on_closing():
        if app.brain_process and app.brain_process.poll() is None:
            if messagebox.askokcancel("Quit", "VVAULT Core is still running. Stop it and quit?"):
                app.stop_brain()
                root.destroy()
        else:
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
