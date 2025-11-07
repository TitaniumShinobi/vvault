#!/usr/bin/env python3
"""
VVAULT Capsule Viewer
Advanced capsule viewing and analysis component.

Features:
- JSON schema validation and preview
- Capsule integrity verification
- Memory analysis and visualization
- Personality trait visualization
- Export and sharing capabilities
- Security filtering for sensitive data

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import sys
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class CapsuleInfo:
    """Information about a capsule"""
    filepath: str
    filename: str
    size_bytes: int
    created_time: datetime
    modified_time: datetime
    is_valid: bool
    validation_errors: List[str]
    metadata: Dict[str, Any]

class CapsuleViewer:
    """Advanced capsule viewer and analyzer"""
    
    def __init__(self, parent_frame: tk.Widget, project_dir: str):
        self.parent_frame = parent_frame
        self.project_dir = project_dir
        self.capsules_dir = os.path.join(project_dir, "capsules")
        self.current_capsule: Optional[CapsuleInfo] = None
        self.capsule_data: Optional[Dict[str, Any]] = None
        
        # Security settings
        self.sensitive_data_masked = True
        self.mask_patterns = [
            r'\b[a-fA-F0-9]{64}\b',  # SHA-256 hashes
            r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b',  # UUIDs
            r'private[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?',  # Private keys
            r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9]{20,}["\']?',  # API keys
        ]
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the capsule viewer interface"""
        # Main container
        main_container = ttk.Frame(self.parent_frame)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Left panel - Capsule list
        left_panel = ttk.Frame(main_container)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        # Capsule list
        list_frame = ttk.LabelFrame(left_panel, text="Available Capsules", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        # Listbox with scrollbar
        list_container = ttk.Frame(list_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        self.capsule_listbox = tk.Listbox(
            list_container, 
            bg='#000000', 
            fg='#ffffff',
            selectmode=tk.SINGLE,
            font=('Consolas', 10)
        )
        scrollbar_list = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.capsule_listbox.yview)
        self.capsule_listbox.configure(yscrollcommand=scrollbar_list.set)
        
        self.capsule_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_list.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bind selection event
        self.capsule_listbox.bind('<<ListboxSelect>>', self.on_capsule_select)
        
        # Capsule actions
        action_frame = ttk.Frame(left_panel)
        action_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(action_frame, text="Refresh", command=self.refresh_capsules).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(action_frame, text="Verify", command=self.verify_selected_capsule).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(action_frame, text="Export", command=self.export_selected_capsule).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(action_frame, text="Analyze", command=self.analyze_selected_capsule).pack(side=tk.LEFT)
        
        # Right panel - Capsule details
        right_panel = ttk.Frame(main_container)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        # Create notebook for different views
        self.details_notebook = ttk.Notebook(right_panel)
        self.details_notebook.pack(fill=tk.BOTH, expand=True)
        
        # JSON View
        self.setup_json_view()
        
        # Analysis View
        self.setup_analysis_view()
        
        # Security View
        self.setup_security_view()
        
        # Load initial capsules
        self.refresh_capsules()
    
    def setup_json_view(self):
        """Setup JSON view tab"""
        json_frame = ttk.Frame(self.details_notebook)
        self.details_notebook.add(json_frame, text="JSON View")
        
        # JSON text area
        self.json_text = tk.Text(
            json_frame, 
            bg='#000000', 
            fg='#ffffff',
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        json_scrollbar = ttk.Scrollbar(json_frame, orient=tk.VERTICAL, command=self.json_text.yview)
        self.json_text.configure(yscrollcommand=json_scrollbar.set)
        
        self.json_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        json_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # JSON actions
        json_actions = ttk.Frame(json_frame)
        json_actions.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(json_actions, text="Format JSON", command=self.format_json).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(json_actions, text="Validate Schema", command=self.validate_schema).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(json_actions, text="Copy to Clipboard", command=self.copy_json).pack(side=tk.LEFT)
    
    def setup_analysis_view(self):
        """Setup analysis view tab"""
        analysis_frame = ttk.Frame(self.details_notebook)
        self.details_notebook.add(analysis_frame, text="Analysis")
        
        # Analysis content
        self.analysis_text = tk.Text(
            analysis_frame,
            bg='#000000',
            fg='#ffffff',
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        analysis_scrollbar = ttk.Scrollbar(analysis_frame, orient=tk.VERTICAL, command=self.analysis_text.yview)
        self.analysis_text.configure(yscrollcommand=analysis_scrollbar.set)
        
        self.analysis_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        analysis_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Analysis actions
        analysis_actions = ttk.Frame(analysis_frame)
        analysis_actions.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(analysis_actions, text="Generate Report", command=self.generate_analysis_report).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(analysis_actions, text="Visualize Traits", command=self.visualize_traits).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(analysis_actions, text="Memory Analysis", command=self.analyze_memories).pack(side=tk.LEFT)
    
    def setup_security_view(self):
        """Setup security view tab"""
        security_frame = ttk.Frame(self.details_notebook)
        self.details_notebook.add(security_frame, text="Security")
        
        # Security content
        self.security_text = tk.Text(
            security_frame,
            bg='#000000',
            fg='#ffffff',
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        security_scrollbar = ttk.Scrollbar(security_frame, orient=tk.VERTICAL, command=self.security_text.yview)
        self.security_text.configure(yscrollcommand=security_scrollbar.set)
        
        self.security_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        security_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Security actions
        security_actions = ttk.Frame(security_frame)
        security_actions.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(security_actions, text="Security Scan", command=self.security_scan).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(security_actions, text="Mask Sensitive Data", command=self.toggle_masking).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(security_actions, text="Export Safe Version", command=self.export_safe_version).pack(side=tk.LEFT)
    
    def refresh_capsules(self):
        """Refresh the list of available capsules"""
        self.capsule_listbox.delete(0, tk.END)
        
        if not os.path.exists(self.capsules_dir):
            logger.error(f"Capsules directory not found: {self.capsules_dir}")
            return
        
        try:
            capsule_files = []
            for root, dirs, files in os.walk(self.capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        capsule_path = os.path.join(root, file)
                        capsule_files.append(capsule_path)
            
            # Sort by modification time (newest first)
            capsule_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            
            for capsule_path in capsule_files:
                filename = os.path.basename(capsule_path)
                self.capsule_listbox.insert(tk.END, filename)
            
            logger.info(f"Found {len(capsule_files)} capsules")
            
        except Exception as e:
            logger.error(f"Error refreshing capsules: {e}")
            messagebox.showerror("Error", f"Failed to refresh capsules: {e}")
    
    def on_capsule_select(self, event):
        """Handle capsule selection"""
        selection = self.capsule_listbox.curselection()
        if not selection:
            return
        
        # Get selected capsule
        selected_index = selection[0]
        capsule_files = []
        for root, dirs, files in os.walk(self.capsules_dir):
            for file in files:
                if file.endswith('.capsule'):
                    capsule_files.append(os.path.join(root, file))
        
        capsule_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        selected_capsule = capsule_files[selected_index]
        
        # Load capsule
        self.load_capsule(selected_capsule)
    
    def load_capsule(self, capsule_path: str):
        """Load and display a capsule"""
        try:
            # Load capsule data
            with open(capsule_path, 'r', encoding='utf-8') as f:
                self.capsule_data = json.load(f)
            
            # Create capsule info
            stat = os.stat(capsule_path)
            self.current_capsule = CapsuleInfo(
                filepath=capsule_path,
                filename=os.path.basename(capsule_path),
                size_bytes=stat.st_size,
                created_time=datetime.fromtimestamp(stat.st_ctime),
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                is_valid=True,
                validation_errors=[],
                metadata=self.capsule_data.get('metadata', {})
            )
            
            # Validate capsule
            self.validate_capsule()
            
            # Update displays
            self.update_json_view()
            self.update_analysis_view()
            self.update_security_view()
            
            logger.info(f"Loaded capsule: {self.current_capsule.filename}")
            
        except Exception as e:
            logger.error(f"Error loading capsule: {e}")
            messagebox.showerror("Error", f"Failed to load capsule: {e}")
    
    def validate_capsule(self):
        """Validate the current capsule"""
        if not self.current_capsule or not self.capsule_data:
            return
        
        errors = []
        
        # Check required fields
        required_fields = ['metadata', 'traits', 'personality', 'memory', 'environment']
        for field in required_fields:
            if field not in self.capsule_data:
                errors.append(f"Missing required field: {field}")
        
        # Check metadata
        if 'metadata' in self.capsule_data:
            metadata = self.capsule_data['metadata']
            required_metadata = ['instance_name', 'uuid', 'timestamp', 'fingerprint_hash']
            for field in required_metadata:
                if field not in metadata:
                    errors.append(f"Missing metadata field: {field}")
        
        # Check integrity
        if 'metadata' in self.capsule_data and 'fingerprint_hash' in self.capsule_data['metadata']:
            stored_hash = self.capsule_data['metadata']['fingerprint_hash']
            calculated_hash = self.calculate_fingerprint()
            if stored_hash != calculated_hash:
                errors.append("Capsule integrity check failed")
        
        self.current_capsule.validation_errors = errors
        self.current_capsule.is_valid = len(errors) == 0
    
    def calculate_fingerprint(self) -> str:
        """Calculate fingerprint for the current capsule"""
        if not self.capsule_data:
            return ""
        
        try:
            # Create copy without fingerprint for calculation
            data_copy = self.capsule_data.copy()
            if 'metadata' in data_copy:
                data_copy['metadata'] = data_copy['metadata'].copy()
                data_copy['metadata'].pop('fingerprint_hash', None)
            
            # Calculate SHA-256 hash
            json_str = json.dumps(data_copy, sort_keys=True, default=str)
            return hashlib.sha256(json_str.encode('utf-8')).hexdigest()
            
        except Exception as e:
            logger.error(f"Error calculating fingerprint: {e}")
            return ""
    
    def update_json_view(self):
        """Update the JSON view"""
        if not self.capsule_data:
            self.json_text.delete(1.0, tk.END)
            return
        
        # Format JSON
        json_str = json.dumps(self.capsule_data, indent=2, default=str)
        
        # Mask sensitive data if enabled
        if self.sensitive_data_masked:
            json_str = self.mask_sensitive_data(json_str)
        
        self.json_text.delete(1.0, tk.END)
        self.json_text.insert(1.0, json_str)
    
    def update_analysis_view(self):
        """Update the analysis view"""
        if not self.current_capsule or not self.capsule_data:
            self.analysis_text.delete(1.0, tk.END)
            return
        
        analysis = self.generate_capsule_analysis()
        
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(1.0, analysis)
    
    def update_security_view(self):
        """Update the security view"""
        if not self.current_capsule:
            self.security_text.delete(1.0, tk.END)
            return
        
        security_info = self.generate_security_report()
        
        self.security_text.delete(1.0, tk.END)
        self.security_text.insert(1.0, security_info)
    
    def generate_capsule_analysis(self) -> str:
        """Generate analysis report for the current capsule"""
        if not self.capsule_data:
            return "No capsule data available"
        
        analysis = []
        analysis.append("=== CAPSULE ANALYSIS REPORT ===\n")
        
        # Basic info
        if self.current_capsule:
            analysis.append(f"Filename: {self.current_capsule.filename}")
            analysis.append(f"Size: {self.current_capsule.size_bytes:,} bytes")
            analysis.append(f"Created: {self.current_capsule.created_time}")
            analysis.append(f"Modified: {self.current_capsule.modified_time}")
            analysis.append(f"Valid: {'Yes' if self.current_capsule.is_valid else 'No'}")
            if self.current_capsule.validation_errors:
                analysis.append(f"Validation Errors: {len(self.current_capsule.validation_errors)}")
            analysis.append("")
        
        # Metadata analysis
        if 'metadata' in self.capsule_data:
            metadata = self.capsule_data['metadata']
            analysis.append("=== METADATA ===")
            for key, value in metadata.items():
                if key != 'fingerprint_hash':  # Skip hash in display
                    analysis.append(f"{key}: {value}")
            analysis.append("")
        
        # Traits analysis
        if 'traits' in self.capsule_data:
            traits = self.capsule_data['traits']
            analysis.append("=== PERSONALITY TRAITS ===")
            for trait, value in traits.items():
                analysis.append(f"{trait}: {value:.3f}")
            analysis.append("")
        
        # Memory analysis
        if 'memory' in self.capsule_data:
            memory = self.capsule_data['memory']
            analysis.append("=== MEMORY ANALYSIS ===")
            if 'short_term_memories' in memory:
                analysis.append(f"Short-term memories: {len(memory['short_term_memories'])}")
            if 'long_term_memories' in memory:
                analysis.append(f"Long-term memories: {len(memory['long_term_memories'])}")
            if 'emotional_memories' in memory:
                analysis.append(f"Emotional memories: {len(memory['emotional_memories'])}")
            if 'procedural_memories' in memory:
                analysis.append(f"Procedural memories: {len(memory['procedural_memories'])}")
            if 'episodic_memories' in memory:
                analysis.append(f"Episodic memories: {len(memory['episodic_memories'])}")
            analysis.append("")
        
        # Personality analysis
        if 'personality' in self.capsule_data:
            personality = self.capsule_data['personality']
            analysis.append("=== PERSONALITY PROFILE ===")
            if 'personality_type' in personality:
                analysis.append(f"MBTI Type: {personality['personality_type']}")
            if 'big_five_traits' in personality:
                analysis.append("Big Five Traits:")
                for trait, value in personality['big_five_traits'].items():
                    analysis.append(f"  {trait}: {value:.3f}")
            analysis.append("")
        
        return "\n".join(analysis)
    
    def generate_security_report(self) -> str:
        """Generate security report for the current capsule"""
        if not self.current_capsule:
            return "No capsule loaded"
        
        report = []
        report.append("=== SECURITY REPORT ===\n")
        
        # Basic security info
        report.append(f"File: {self.current_capsule.filename}")
        report.append(f"Size: {self.current_capsule.size_bytes:,} bytes")
        report.append(f"Integrity: {'Valid' if self.current_capsule.is_valid else 'Invalid'}")
        report.append("")
        
        # Validation errors
        if self.current_capsule.validation_errors:
            report.append("=== VALIDATION ERRORS ===")
            for error in self.current_capsule.validation_errors:
                report.append(f"❌ {error}")
            report.append("")
        else:
            report.append("✅ No validation errors found")
            report.append("")
        
        # Security checks
        report.append("=== SECURITY CHECKS ===")
        
        # Check for sensitive data
        if self.capsule_data:
            sensitive_found = self.check_sensitive_data()
            if sensitive_found:
                report.append("⚠️ Sensitive data detected:")
                for item in sensitive_found:
                    report.append(f"  - {item}")
            else:
                report.append("✅ No sensitive data detected")
        
        report.append("")
        report.append("=== RECOMMENDATIONS ===")
        report.append("• Keep capsule files in secure location")
        report.append("• Regularly verify capsule integrity")
        report.append("• Use encryption for sensitive capsules")
        report.append("• Monitor for unauthorized access")
        
        return "\n".join(report)
    
    def check_sensitive_data(self) -> List[str]:
        """Check for sensitive data in the capsule"""
        if not self.capsule_data:
            return []
        
        sensitive_found = []
        json_str = json.dumps(self.capsule_data, default=str)
        
        # Check for patterns
        import re
        for pattern in self.mask_patterns:
            matches = re.findall(pattern, json_str)
            if matches:
                sensitive_found.append(f"Pattern matches: {len(matches)}")
        
        return sensitive_found
    
    def mask_sensitive_data(self, text: str) -> str:
        """Mask sensitive data in text"""
        import re
        
        for pattern in self.mask_patterns:
            if 'hash' in pattern:
                text = re.sub(pattern, '***HASH***', text)
            elif 'uuid' in pattern:
                text = re.sub(pattern, '***UUID***', text)
            elif 'key' in pattern:
                text = re.sub(pattern, '***KEY***', text)
            else:
                text = re.sub(pattern, '***MASKED***', text)
        
        return text
    
    def format_json(self):
        """Format the JSON in the text area"""
        try:
            json_str = self.json_text.get(1.0, tk.END).strip()
            parsed = json.loads(json_str)
            formatted = json.dumps(parsed, indent=2, default=str)
            self.json_text.delete(1.0, tk.END)
            self.json_text.insert(1.0, formatted)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Invalid JSON: {e}")
    
    def validate_schema(self):
        """Validate the capsule schema"""
        if not self.capsule_data:
            messagebox.showwarning("No Data", "No capsule loaded")
            return
        
        # Basic schema validation
        required_fields = ['metadata', 'traits', 'personality', 'memory', 'environment']
        missing_fields = [field for field in required_fields if field not in self.capsule_data]
        
        if missing_fields:
            messagebox.showerror("Schema Error", f"Missing required fields: {', '.join(missing_fields)}")
        else:
            messagebox.showinfo("Schema Valid", "Capsule schema is valid")
    
    def copy_json(self):
        """Copy JSON to clipboard"""
        json_str = self.json_text.get(1.0, tk.END).strip()
        self.parent_frame.clipboard_clear()
        self.parent_frame.clipboard_append(json_str)
        messagebox.showinfo("Copied", "JSON copied to clipboard")
    
    def verify_selected_capsule(self):
        """Verify the selected capsule"""
        if not self.current_capsule:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        if self.current_capsule.is_valid:
            messagebox.showinfo("Verification", "Capsule verification passed")
        else:
            error_msg = "\n".join(self.current_capsule.validation_errors)
            messagebox.showerror("Verification Failed", f"Validation errors:\n{error_msg}")
    
    def export_selected_capsule(self):
        """Export the selected capsule"""
        if not self.current_capsule:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        filename = filedialog.asksaveasfilename(
            title="Export Capsule",
            defaultextension=".capsule",
            filetypes=[("Capsule files", "*.capsule"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                import shutil
                shutil.copy2(self.current_capsule.filepath, filename)
                messagebox.showinfo("Export", f"Capsule exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))
    
    def analyze_selected_capsule(self):
        """Analyze the selected capsule"""
        if not self.current_capsule:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        # Switch to analysis tab
        self.details_notebook.select(1)  # Analysis tab
    
    def generate_analysis_report(self):
        """Generate detailed analysis report"""
        if not self.current_capsule:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        # Generate and save report
        report = self.generate_capsule_analysis()
        
        filename = filedialog.asksaveasfilename(
            title="Save Analysis Report",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(report)
                messagebox.showinfo("Report", f"Analysis report saved to {filename}")
            except Exception as e:
                messagebox.showerror("Save Error", str(e))
    
    def visualize_traits(self):
        """Visualize personality traits"""
        if not self.capsule_data or 'traits' not in self.capsule_data:
            messagebox.showwarning("No Data", "No trait data available")
            return
        
        traits = self.capsule_data['traits']
        
        # Create visualization window
        viz_window = tk.Toplevel(self.parent_frame)
        viz_window.title("Personality Traits Visualization")
        viz_window.geometry("600x400")
        
        # Create matplotlib figure
        fig, ax = plt.subplots(figsize=(8, 6))
        
        # Prepare data
        trait_names = list(traits.keys())
        trait_values = list(traits.values())
        
        # Create bar chart
        bars = ax.bar(trait_names, trait_values, color='skyblue', alpha=0.7)
        ax.set_ylabel('Trait Value')
        ax.set_title('Personality Traits')
        ax.set_ylim(0, 1)
        
        # Rotate x-axis labels
        plt.xticks(rotation=45, ha='right')
        
        # Add value labels on bars
        for bar, value in zip(bars, trait_values):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                   f'{value:.3f}', ha='center', va='bottom')
        
        plt.tight_layout()
        
        # Embed in tkinter
        canvas = FigureCanvasTkAgg(fig, viz_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    
    def analyze_memories(self):
        """Analyze memory content"""
        if not self.capsule_data or 'memory' not in self.capsule_data:
            messagebox.showwarning("No Data", "No memory data available")
            return
        
        memory = self.capsule_data['memory']
        
        # Create memory analysis window
        mem_window = tk.Toplevel(self.parent_frame)
        mem_window.title("Memory Analysis")
        mem_window.geometry("800x600")
        
        # Memory statistics
        stats_text = tk.Text(mem_window, wrap=tk.WORD, font=('Consolas', 10))
        stats_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        stats = []
        stats.append("=== MEMORY ANALYSIS ===\n")
        
        for mem_type in ['short_term_memories', 'long_term_memories', 'emotional_memories', 
                        'procedural_memories', 'episodic_memories']:
            if mem_type in memory:
                memories = memory[mem_type]
                stats.append(f"{mem_type.replace('_', ' ').title()}: {len(memories)}")
                
                if memories:
                    # Show sample memories
                    stats.append("Sample memories:")
                    for i, mem in enumerate(memories[:3]):  # Show first 3
                        stats.append(f"  {i+1}. {mem[:100]}...")
                    stats.append("")
        
        stats_text.insert(1.0, "\n".join(stats))
        stats_text.config(state=tk.DISABLED)
    
    def security_scan(self):
        """Perform security scan"""
        if not self.current_capsule:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        # Switch to security tab
        self.details_notebook.select(2)  # Security tab
        
        # Update security view
        self.update_security_view()
    
    def toggle_masking(self):
        """Toggle sensitive data masking"""
        self.sensitive_data_masked = not self.sensitive_data_masked
        self.update_json_view()
        
        status = "enabled" if self.sensitive_data_masked else "disabled"
        messagebox.showinfo("Masking", f"Sensitive data masking {status}")
    
    def export_safe_version(self):
        """Export a version with sensitive data masked"""
        if not self.current_capsule or not self.capsule_data:
            messagebox.showwarning("No Selection", "No capsule selected")
            return
        
        # Create masked version
        masked_data = self.capsule_data.copy()
        json_str = json.dumps(masked_data, default=str)
        masked_str = self.mask_sensitive_data(json_str)
        masked_data = json.loads(masked_str)
        
        # Save masked version
        filename = filedialog.asksaveasfilename(
            title="Save Safe Version",
            defaultextension=".capsule",
            filetypes=[("Capsule files", "*.capsule"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(masked_data, f, indent=2, default=str)
                messagebox.showinfo("Export", f"Safe version saved to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))

def main():
    """Test the capsule viewer"""
    root = tk.Tk()
    root.title("Capsule Viewer Test")
    root.geometry("1000x700")
    
    project_dir = "/Users/devonwoodson/Documents/GitHub/VVAULT"
    viewer = CapsuleViewer(root, project_dir)
    
    root.mainloop()

if __name__ == "__main__":
    main()
