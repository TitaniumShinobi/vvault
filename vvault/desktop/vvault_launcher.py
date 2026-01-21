#!/usr/bin/env python3
"""
VVAULT Desktop Launcher
Comprehensive launcher for the VVAULT desktop application.

Features:
- Integrated GUI with all VVAULT components
- Secure process management
- Advanced capsule viewer
- Blockchain synchronization
- Security layer integration
- Real-time monitoring

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import logging
import threading
from datetime import datetime
from pathlib import Path

# Add project directory to path
PROJECT_DIR = "/Users/devonwoodson/Documents/GitHub/VVAULT"
sys.path.append(PROJECT_DIR)

# Import VVAULT components
from vvault_gui import VVAULTApp
from process_manager import VVAULTProcessManager, ProcessConfig
from capsule_viewer import CapsuleViewer
from security_layer import VVAULTSecurityLayer
from blockchain_sync import VVAULTBlockchainSync
from desktop_login import VVAULTLoginScreen

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_DIR, "logs/vvault_desktop.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class VVAULTDesktopLauncher:
    """Main launcher for VVAULT desktop application"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VVAULT Desktop - AI Construct Memory Vault")
        self.root.geometry("1400x900")
        self.root.configure(bg='#000000')
        
        # Set pure black theme
        style = ttk.Style()
        style.theme_use('clam')
        
        # Configure pure black colors
        style.configure('TNotebook', background='#000000')
        style.configure('TNotebook.Tab', background='#000000', foreground='white')
        style.map('TNotebook.Tab', background=[('selected', '#1a1a1a')])
        
        # Configure other widgets for black theme
        style.configure('TLabel', background='#000000', foreground='white')
        style.configure('TFrame', background='#000000')
        style.configure('TLabelFrame', background='#000000', foreground='white')
        style.configure('TButton', background='#1a1a1a', foreground='white')
        style.configure('TEntry', background='#1a1a1a', foreground='white')
        style.configure('TCombobox', background='#1a1a1a', foreground='white')
        
        # Project configuration
        self.project_dir = PROJECT_DIR
        self.capsules_dir = os.path.join(self.project_dir, "capsules")
        self.brain_script = os.path.join(self.project_dir, "corefiles/brain.py")
        self.venv_activate = os.path.join(self.project_dir, "vvault_env/bin/activate")
        
        # Component instances
        self.process_manager: Optional[VVAULTProcessManager] = None
        self.security_layer: Optional[VVAULTSecurityLayer] = None
        self.main_app: Optional[VVAULTApp] = None
        
        # Initialize components
        self._initialize_components()
        self._setup_ui()
        self._start_monitoring()
        
        logger.info("VVAULT Desktop Launcher initialized")
    
    def _initialize_components(self):
        """Initialize all VVAULT components"""
        try:
            # Initialize process manager
            config = ProcessConfig(
                project_dir=self.project_dir,
                venv_activate=self.venv_activate,
                brain_script=self.brain_script,
                working_directory=self.project_dir,
                environment_vars=os.environ.copy()
            )
            self.process_manager = VVAULTProcessManager(config)
            
            # Initialize security layer
            self.security_layer = VVAULTSecurityLayer(self.project_dir)
            
            logger.info("VVAULT components initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            messagebox.showerror("Initialization Error", f"Failed to initialize VVAULT components: {e}")
    
    def _setup_ui(self):
        """Setup the main user interface"""
        # Create main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Main Control Tab
        self._setup_main_control_tab()
        
        # Capsule Management Tab
        self._setup_capsule_management_tab()
        
        # Blockchain Sync Tab
        self._setup_blockchain_sync_tab()
        
        # Security Tab
        self._setup_security_tab()
        
        # System Status Tab
        self._setup_system_status_tab()
        
        # About Tab
        self._setup_about_tab()
    
    def _setup_main_control_tab(self):
        """Setup the main control tab"""
        main_frame = ttk.Frame(self.notebook)
        self.notebook.add(main_frame, text="Main Control")
        
        # System status
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding=10)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.system_status = tk.StringVar()
        self.system_status.set("VVAULT Desktop Ready")
        ttk.Label(status_frame, textvariable=self.system_status).pack(anchor=tk.W)
        
        # Control buttons
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(control_frame, text="Launch VVAULT Core", 
                  command=self.launch_vvault_core).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Stop VVAULT Core", 
                  command=self.stop_vvault_core).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Restart VVAULT Core", 
                  command=self.restart_vvault_core).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="System Check", 
                  command=self.system_check).pack(side=tk.LEFT, padx=5)
        
        # Output display
        output_frame = ttk.LabelFrame(main_frame, text="System Output", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.output_text = tk.Text(
            output_frame, height=15, bg='#000000', fg='#ffffff',
            font=('Consolas', 10)
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        
        # Output controls
        output_controls = ttk.Frame(output_frame)
        output_controls.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(output_controls, text="Clear Output", 
                  command=self.clear_output).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(output_controls, text="Export Logs", 
                  command=self.export_logs).pack(side=tk.LEFT)
    
    def _setup_capsule_management_tab(self):
        """Setup the capsule management tab"""
        capsule_frame = ttk.Frame(self.notebook)
        self.notebook.add(capsule_frame, text="Capsule Management")
        
        # Create capsule viewer
        self.capsule_viewer = CapsuleViewer(capsule_frame, self.project_dir)
    
    def _setup_blockchain_sync_tab(self):
        """Setup the blockchain sync tab"""
        blockchain_frame = ttk.Frame(self.notebook)
        self.notebook.add(blockchain_frame, text="Blockchain Sync")
        
        # Create blockchain sync interface
        self.blockchain_sync = VVAULTBlockchainSync(blockchain_frame, self.project_dir)
    
    def _setup_security_tab(self):
        """Setup the security tab"""
        security_frame = ttk.Frame(self.notebook)
        self.notebook.add(security_frame, text="Security")
        
        # Security status
        sec_status_frame = ttk.LabelFrame(security_frame, text="Security Status", padding=10)
        sec_status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.security_status = tk.StringVar()
        self.security_status.set("Security layer active")
        ttk.Label(sec_status_frame, textvariable=self.security_status).pack(anchor=tk.W)
        
        # Security controls
        sec_controls = ttk.Frame(security_frame)
        sec_controls.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(sec_controls, text="Security Scan", 
                  command=self.security_scan).pack(side=tk.LEFT, padx=5)
        ttk.Button(sec_controls, text="View Security Report", 
                  command=self.view_security_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(sec_controls, text="Export Security Log", 
                  command=self.export_security_log).pack(side=tk.LEFT, padx=5)
        
        # Security log
        sec_log_frame = ttk.LabelFrame(security_frame, text="Security Log", padding=10)
        sec_log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.security_log = tk.Text(
            sec_log_frame, height=10, bg='#000000', fg='#ffffff',
            font=('Consolas', 9)
        )
        self.security_log.pack(fill=tk.BOTH, expand=True)
    
    def _setup_system_status_tab(self):
        """Setup the system status tab"""
        status_frame = ttk.Frame(self.notebook)
        self.notebook.add(status_frame, text="System Status")
        
        # System information
        sys_info_frame = ttk.LabelFrame(status_frame, text="System Information", padding=10)
        sys_info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.system_info_text = tk.Text(
            sys_info_frame, height=8, bg='#000000', fg='#ffffff',
            font=('Consolas', 10)
        )
        self.system_info_text.pack(fill=tk.X)
        
        # Process status
        proc_status_frame = ttk.LabelFrame(status_frame, text="Process Status", padding=10)
        proc_status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.process_status_text = tk.Text(
            proc_status_frame, height=6, bg='#000000', fg='#ffffff',
            font=('Consolas', 10)
        )
        self.process_status_text.pack(fill=tk.X)
        
        # Update system information
        self.update_system_info()
    
    def _setup_about_tab(self):
        """Setup the about tab"""
        about_frame = ttk.Frame(self.notebook)
        self.notebook.add(about_frame, text="About")
        
        # About content
        about_text = """
VVAULT Desktop Application
Version: 1.0.0
Author: Devon Allen Woodson

A comprehensive desktop application for managing AI construct memory capsules
with blockchain integration for immutable storage and verification.

Features:
• Secure capsule management and storage
• Advanced capsule viewer with JSON schema validation
• Blockchain synchronization with multiple networks
• Comprehensive security layer with threat detection
• Real-time process monitoring and management
• IPFS integration for decentralized storage
• Hardware security module support

Components:
• VVAULT Core: Main capsule management system
• Process Manager: Secure process execution and monitoring
• Capsule Viewer: Advanced capsule analysis and visualization
• Blockchain Sync: Multi-blockchain synchronization interface
• Security Layer: Comprehensive security and threat detection

System Requirements:
• Python 3.7+
• Virtual environment with required dependencies
• Internet connection for blockchain operations
• Sufficient disk space for capsule storage

For support and documentation, visit the VVAULT project repository.
        """
        
        about_display = tk.Text(
            about_frame, bg='#000000', fg='#ffffff',
            font=('Consolas', 10), wrap=tk.WORD
        )
        about_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        about_display.insert(1.0, about_text.strip())
        about_display.config(state=tk.DISABLED)
    
    def launch_vvault_core(self):
        """Launch the VVAULT core process"""
        if not self.process_manager:
            messagebox.showerror("Error", "Process manager not initialized")
            return
        
        try:
            success = self.process_manager.start_process()
            if success:
                self.system_status.set("VVAULT Core launched successfully")
                self.log_output("✅ VVAULT Core process started")
            else:
                self.system_status.set("Failed to launch VVAULT Core")
                self.log_output("❌ Failed to start VVAULT Core process")
                
        except Exception as e:
            self.log_output(f"❌ Error launching VVAULT Core: {e}")
            messagebox.showerror("Launch Error", str(e))
    
    def stop_vvault_core(self):
        """Stop the VVAULT core process"""
        if not self.process_manager:
            messagebox.showerror("Error", "Process manager not initialized")
            return
        
        try:
            success = self.process_manager.stop_process()
            if success:
                self.system_status.set("VVAULT Core stopped")
                self.log_output("🛑 VVAULT Core process stopped")
            else:
                self.log_output("⚠️ Failed to stop VVAULT Core process")
                
        except Exception as e:
            self.log_output(f"❌ Error stopping VVAULT Core: {e}")
            messagebox.showerror("Stop Error", str(e))
    
    def restart_vvault_core(self):
        """Restart the VVAULT core process"""
        if not self.process_manager:
            messagebox.showerror("Error", "Process manager not initialized")
            return
        
        try:
            success = self.process_manager.restart_process()
            if success:
                self.system_status.set("VVAULT Core restarted")
                self.log_output("🔄 VVAULT Core process restarted")
            else:
                self.system_status.set("Failed to restart VVAULT Core")
                self.log_output("❌ Failed to restart VVAULT Core process")
                
        except Exception as e:
            self.log_output(f"❌ Error restarting VVAULT Core: {e}")
            messagebox.showerror("Restart Error", str(e))
    
    def system_check(self):
        """Perform system check"""
        self.log_output("🔍 Performing system check...")
        
        # Check project directory
        if os.path.exists(self.project_dir):
            self.log_output("✅ Project directory exists")
        else:
            self.log_output("❌ Project directory not found")
        
        # Check virtual environment
        if os.path.exists(self.venv_activate):
            self.log_output("✅ Virtual environment exists")
        else:
            self.log_output("❌ Virtual environment not found")
        
        # Check brain script
        if os.path.exists(self.brain_script):
            self.log_output("✅ Brain script exists")
        else:
            self.log_output("❌ Brain script not found")
        
        # Check capsules directory
        if os.path.exists(self.capsules_dir):
            self.log_output("✅ Capsules directory exists")
        else:
            self.log_output("❌ Capsules directory not found")
        
        # Check process manager
        if self.process_manager:
            self.log_output("✅ Process manager initialized")
        else:
            self.log_output("❌ Process manager not initialized")
        
        # Check security layer
        if self.security_layer:
            self.log_output("✅ Security layer initialized")
        else:
            self.log_output("❌ Security layer not initialized")
        
        self.log_output("🔍 System check completed")
    
    def security_scan(self):
        """Perform security scan"""
        if not self.security_layer:
            messagebox.showerror("Error", "Security layer not initialized")
            return
        
        self.log_output("🔒 Performing security scan...")
        
        try:
            # Get security report
            report = self.security_layer.get_security_report()
            
            # Update security log
            self.security_log.delete(1.0, tk.END)
            security_info = []
            security_info.append("=== SECURITY SCAN REPORT ===")
            security_info.append(f"Active Sessions: {report['active_sessions']}")
            security_info.append(f"Access Controls: {report['access_controls']}")
            security_info.append(f"Security Policies: {report['security_policies']}")
            security_info.append(f"Recent Events: {report['recent_events']}")
            security_info.append(f"Threat Detectors: {report['threat_detectors']}")
            security_info.append(f"Monitoring Active: {report['monitoring_active']}")
            
            self.security_log.insert(1.0, "\n".join(security_info))
            self.log_output("✅ Security scan completed")
            
        except Exception as e:
            self.log_output(f"❌ Security scan error: {e}")
            messagebox.showerror("Security Scan Error", str(e))
    
    def view_security_report(self):
        """View detailed security report"""
        if not self.security_layer:
            messagebox.showerror("Error", "Security layer not initialized")
            return
        
        # Create security report window
        report_window = tk.Toplevel(self.root)
        report_window.title("Security Report")
        report_window.geometry("800x600")
        
        report_text = tk.Text(
            report_window, bg='#000000', fg='#ffffff',
            font=('Consolas', 10)
        )
        report_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Generate detailed report
        report_content = self._generate_security_report()
        report_text.insert(1.0, report_content)
        report_text.config(state=tk.DISABLED)
    
    def export_security_log(self):
        """Export security log"""
        from tkinter import filedialog
        
        filename = filedialog.asksaveasfilename(
            title="Export Security Log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.security_log.get(1.0, tk.END))
                self.log_output(f"📤 Security log exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))
    
    def _generate_security_report(self) -> str:
        """Generate detailed security report"""
        report = []
        report.append("=== VVAULT SECURITY REPORT ===")
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append("")
        
        if self.security_layer:
            security_report = self.security_layer.get_security_report()
            report.append("=== SECURITY STATUS ===")
            for key, value in security_report.items():
                report.append(f"{key}: {value}")
            report.append("")
        
        if self.process_manager:
            process_status = self.process_manager.get_status()
            report.append("=== PROCESS STATUS ===")
            report.append(f"Running: {process_status.is_running}")
            report.append(f"PID: {process_status.pid}")
            report.append(f"CPU Usage: {process_status.cpu_usage}%")
            report.append(f"Memory Usage: {process_status.memory_usage}%")
            report.append("")
        
        report.append("=== SYSTEM INFORMATION ===")
        report.append(f"Project Directory: {self.project_dir}")
        report.append(f"Capsules Directory: {self.capsules_dir}")
        report.append(f"Brain Script: {self.brain_script}")
        report.append(f"Virtual Environment: {self.venv_activate}")
        
        return "\n".join(report)
    
    def update_system_info(self):
        """Update system information display"""
        # System information
        sys_info = []
        sys_info.append("=== SYSTEM INFORMATION ===")
        sys_info.append(f"Project Directory: {self.project_dir}")
        sys_info.append(f"Capsules Directory: {self.capsules_dir}")
        sys_info.append(f"Brain Script: {self.brain_script}")
        sys_info.append(f"Virtual Environment: {self.venv_activate}")
        sys_info.append(f"Python Version: {sys.version}")
        sys_info.append(f"Platform: {sys.platform}")
        
        self.system_info_text.delete(1.0, tk.END)
        self.system_info_text.insert(1.0, "\n".join(sys_info))
        
        # Process status
        proc_info = []
        proc_info.append("=== PROCESS STATUS ===")
        
        if self.process_manager:
            status = self.process_manager.get_status()
            proc_info.append(f"Running: {status.is_running}")
            proc_info.append(f"PID: {status.pid}")
            proc_info.append(f"Start Time: {status.start_time}")
            proc_info.append(f"CPU Usage: {status.cpu_usage}%")
            proc_info.append(f"Memory Usage: {status.memory_usage}%")
            proc_info.append(f"Restart Count: {status.restart_count}")
        else:
            proc_info.append("Process manager not initialized")
        
        self.process_status_text.delete(1.0, tk.END)
        self.process_status_text.insert(1.0, "\n".join(proc_info))
    
    def log_output(self, message: str):
        """Add a message to the output log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        
        self.output_text.insert(tk.END, log_message)
        self.output_text.see(tk.END)
        
        # Limit log size
        lines = self.output_text.get("1.0", tk.END).split('\n')
        if len(lines) > 1000:
            self.output_text.delete("1.0", f"{len(lines) - 1000}.0")
    
    def clear_output(self):
        """Clear the output log"""
        self.output_text.delete(1.0, tk.END)
        self.log_output("Output cleared")
    
    def export_logs(self):
        """Export logs to file"""
        from tkinter import filedialog
        
        filename = filedialog.asksaveasfilename(
            title="Export Logs",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.output_text.get(1.0, tk.END))
                self.log_output(f"📤 Logs exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))
    
    def _start_monitoring(self):
        """Start monitoring thread"""
        def monitor_thread():
            while True:
                try:
                    # Update system information
                    self.update_system_info()
                    
                    # Update process status
                    if self.process_manager:
                        status = self.process_manager.get_status()
                        if status.is_running:
                            self.system_status.set("VVAULT Core running")
                        else:
                            self.system_status.set("VVAULT Core stopped")
                    
                    # Sleep between updates
                    import time
                    time.sleep(5)
                    
                except Exception as e:
                    logger.error(f"Monitoring error: {e}")
                    import time
                    time.sleep(10)  # Wait longer on error
        
        threading.Thread(target=monitor_thread, daemon=True).start()
    
    def run(self):
        """Run the VVAULT desktop application"""
        try:
            # Show login screen first
            self.log_output("🔐 VVAULT Login Required")
            login_screen = VVAULTLoginScreen(self.root)
            login_success, credentials = login_screen.show()
            
            if not login_success:
                self.log_output("❌ Login cancelled or failed")
                self.root.destroy()
                return
            
            self.log_output(f"✅ Login successful for: {credentials['email']}")
            self.log_output("🚀 VVAULT Desktop Application starting...")
            self.log_output("✅ All components initialized")
            self.log_output("🎯 Ready for operations")
            
            # Handle window closing
            def on_closing():
                if self.process_manager and self.process_manager.is_running():
                    if messagebox.askokcancel("Quit", "VVAULT Core is still running. Stop it and quit?"):
                        self.process_manager.stop_process()
                        self.root.destroy()
                else:
                    self.root.destroy()
            
            self.root.protocol("WM_DELETE_WINDOW", on_closing)
            self.root.mainloop()
            
        except Exception as e:
            logger.error(f"Application error: {e}")
            messagebox.showerror("Application Error", str(e))
        finally:
            # Cleanup
            if self.process_manager:
                self.process_manager.stop_process()
            if self.security_layer:
                self.security_layer.shutdown()
            logger.info("VVAULT Desktop Application shutdown complete")

def main():
    """Main entry point"""
    try:
        # Create and run the launcher
        launcher = VVAULTDesktopLauncher()
        launcher.run()
        
    except Exception as e:
        logger.error(f"Launcher error: {e}")
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
