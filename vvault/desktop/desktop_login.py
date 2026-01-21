#!/usr/bin/env python3
"""
VVAULT Desktop Login Screen
Integrated login UI for the VVAULT desktop application.

Features:
- Pure black terminal aesthetic
- VVAULT logo and branding
- Email/password authentication
- Input validation
- Glowing blue hover effects
- Desktop-optimized interface

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import threading
import time
from pathlib import Path

class VVAULTLoginScreen:
    """Desktop login screen for VVAULT"""
    
    def __init__(self, parent=None):
        self.parent = parent
        self.login_successful = False
        self.user_credentials = None
        
        # Create login window
        self.root = tk.Toplevel() if parent else tk.Tk()
        self.root.title("VVAULT - Secure Login")
        self.root.geometry("500x600")
        self.root.configure(bg='#000000')
        self.root.resizable(False, False)
        
        # Center the window
        self._center_window()
        
        # Login form variables
        self.email_var = tk.StringVar()
        self.password_var = tk.StringVar()
        
        # Create UI
        self._create_ui()
        
        # Bind events
        self._bind_events()
        
        # Focus on email field
        self.email_entry.focus()
    
    def _center_window(self):
        """Center the login window on screen"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
    
    def _create_ui(self):
        """Create the login UI"""
        # Main container
        main_frame = tk.Frame(self.root, bg='#000000')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=40)
        
        # Logo section
        self._create_logo_section(main_frame)
        
        # Title section
        self._create_title_section(main_frame)
        
        # Login form
        self._create_login_form(main_frame)
        
        # Footer
        self._create_footer(main_frame)
    
    def _create_logo_section(self, parent):
        """Create logo section"""
        logo_frame = tk.Frame(parent, bg='#000000')
        logo_frame.pack(pady=(0, 20))
        
        # Try to load the VVAULT glyph
        logo_path = Path(__file__).parent / "assets" / "vvault_glyph.png"
        
        if logo_path.exists():
            try:
                # Load and display the logo
                from PIL import Image, ImageTk
                img = Image.open(logo_path)
                img = img.resize((150, 150), Image.Resampling.LANCZOS)
                self.logo_photo = ImageTk.PhotoImage(img)
                
                logo_label = tk.Label(
                    logo_frame, 
                    image=self.logo_photo, 
                    bg='#000000'
                )
                logo_label.pack()
                
            except ImportError:
                # Fallback if PIL not available
                self._create_fallback_logo(logo_frame)
            except Exception as e:
                print(f"Error loading logo: {e}")
                self._create_fallback_logo(logo_frame)
        else:
            # Create fallback logo
            self._create_fallback_logo(logo_frame)
    
    def _create_fallback_logo(self, parent):
        """Create fallback logo using text"""
        logo_label = tk.Label(
            parent,
            text="V",
            font=('Arial', 72, 'bold'),
            fg='#3b82f6',
            bg='#000000'
        )
        logo_label.pack()
        
        # Add a subtle border
        border_frame = tk.Frame(
            parent,
            bg='#3b82f6',
            height=2,
            width=150
        )
        border_frame.pack(pady=(10, 0))
    
    def _create_title_section(self, parent):
        """Create title section"""
        title_frame = tk.Frame(parent, bg='#000000')
        title_frame.pack(pady=(0, 30))
        
        # Main title
        title_label = tk.Label(
            title_frame,
            text="VVAULT",
            font=('Arial', 32, 'bold'),
            fg='#ffffff',
            bg='#000000'
        )
        title_label.pack()
        
        # Subtitle
        subtitle_label = tk.Label(
            title_frame,
            text="Secure your constructs. Remember forever.",
            font=('Arial', 14),
            fg='#6b7280',
            bg='#000000'
        )
        subtitle_label.pack(pady=(5, 0))
    
    def _create_login_form(self, parent):
        """Create login form"""
        form_frame = tk.Frame(parent, bg='#000000')
        form_frame.pack(fill=tk.X, pady=(0, 20))
        
        # Email field
        email_frame = tk.Frame(form_frame, bg='#000000')
        email_frame.pack(fill=tk.X, pady=(0, 15))
        
        email_label = tk.Label(
            email_frame,
            text="Email Address",
            font=('Arial', 12, 'bold'),
            fg='#ffffff',
            bg='#000000',
            anchor='w'
        )
        email_label.pack(fill=tk.X, pady=(0, 5))
        
        self.email_entry = tk.Entry(
            email_frame,
            textvariable=self.email_var,
            font=('Arial', 12),
            bg='#1a1a1a',
            fg='#ffffff',
            insertbackground='#ffffff',
            relief='flat',
            bd=0,
            highlightthickness=1,
            highlightcolor='#3b82f6',
            highlightbackground='#374151'
        )
        self.email_entry.pack(fill=tk.X, ipady=12)
        
        # Password field
        password_frame = tk.Frame(form_frame, bg='#000000')
        password_frame.pack(fill=tk.X, pady=(0, 20))
        
        password_label = tk.Label(
            password_frame,
            text="Password",
            font=('Arial', 12, 'bold'),
            fg='#ffffff',
            bg='#000000',
            anchor='w'
        )
        password_label.pack(fill=tk.X, pady=(0, 5))
        
        self.password_entry = tk.Entry(
            password_frame,
            textvariable=self.password_var,
            font=('Arial', 12),
            bg='#1a1a1a',
            fg='#ffffff',
            insertbackground='#ffffff',
            relief='flat',
            bd=0,
            show='*',
            highlightthickness=1,
            highlightcolor='#3b82f6',
            highlightbackground='#374151'
        )
        self.password_entry.pack(fill=tk.X, ipady=12)
        
        # Sign In button
        self.signin_button = tk.Button(
            form_frame,
            text="Sign In",
            font=('Arial', 14, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a',
            activebackground='#3b82f6',
            activeforeground='#ffffff',
            relief='flat',
            bd=0,
            cursor='hand2',
            command=self._handle_login
        )
        self.signin_button.pack(fill=tk.X, ipady=15, pady=(0, 15))
        
        # Create account link
        create_account_label = tk.Label(
            form_frame,
            text="Don't have an account? Create one",
            font=('Arial', 12),
            fg='#3b82f6',
            bg='#000000',
            cursor='hand2'
        )
        create_account_label.pack()
        create_account_label.bind('<Button-1>', self._handle_create_account)
    
    def _create_footer(self, parent):
        """Create footer section"""
        footer_frame = tk.Frame(parent, bg='#000000')
        footer_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))
        
        # Footer text
        footer_label = tk.Label(
            footer_frame,
            text="© 2025 VVAULT. Secure AI Construct Memory Vault.",
            font=('Arial', 10),
            fg='#6b7280',
            bg='#000000'
        )
        footer_label.pack()
    
    def _bind_events(self):
        """Bind keyboard and mouse events"""
        # Enter key to submit
        self.root.bind('<Return>', lambda e: self._handle_login())
        
        # Tab navigation
        self.email_entry.bind('<Tab>', lambda e: self.password_entry.focus())
        self.password_entry.bind('<Tab>', lambda e: self.email_entry.focus())
        
        # Input validation
        self.email_var.trace_add('write', self._validate_form)
        self.password_var.trace_add('write', self._validate_form)
        
        # Button hover effects
        self.signin_button.bind('<Enter>', self._on_button_hover)
        self.signin_button.bind('<Leave>', self._on_button_leave)
    
    def _validate_form(self, *args):
        """Validate form and enable/disable submit button"""
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        
        if email and password:
            self.signin_button.config(
                bg='#3b82f6',
                activebackground='#2563eb'
            )
            self.signin_button.config(state='normal')
        else:
            self.signin_button.config(
                bg='#1a1a1a',
                activebackground='#374151'
            )
            self.signin_button.config(state='normal')
    
    def _on_button_hover(self, event):
        """Button hover effect"""
        if self.signin_button['state'] == 'normal':
            self.signin_button.config(bg='#2563eb')
    
    def _on_button_leave(self, event):
        """Button leave effect"""
        if self.signin_button['state'] == 'normal':
            email = self.email_var.get().strip()
            password = self.password_var.get().strip()
            if email and password:
                self.signin_button.config(bg='#3b82f6')
            else:
                self.signin_button.config(bg='#1a1a1a')
    
    def _handle_login(self):
        """Handle login attempt"""
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        
        if not email or not password:
            messagebox.showerror("Error", "Please fill in all fields.")
            return
        
        if not self._validate_email(email):
            messagebox.showerror("Error", "Please enter a valid email address.")
            return
        
        # Disable button during login
        self.signin_button.config(state='disabled', text="Signing In...")
        self.root.update()
        
        # Simulate login process
        threading.Thread(target=self._perform_login, args=(email, password), daemon=True).start()
    
    def _validate_email(self, email):
        """Basic email validation"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    def _perform_login(self, email, password):
        """Perform login authentication"""
        try:
            # Simulate login delay
            time.sleep(1.5)
            
            # Mock authentication logic
            # In a real implementation, this would call your authentication service
            if self._authenticate_user(email, password):
                self.user_credentials = {'email': email, 'password': password}
                self.login_successful = True
                
                # Close login window
                self.root.after(0, self._login_success)
            else:
                self.root.after(0, self._login_failed)
                
        except Exception as e:
            self.root.after(0, lambda: self._login_error(str(e)))
    
    def _authenticate_user(self, email, password):
        """Mock authentication - replace with real authentication logic"""
        # Simple mock authentication
        # In production, this would connect to your authentication service
        valid_users = {
            'admin@vvault.com': 'admin123',
            'user@vvault.com': 'user123',
            'test@vvault.com': 'test123'
        }
        
        return valid_users.get(email) == password
    
    def _login_success(self):
        """Handle successful login"""
        self.signin_button.config(state='normal', text="Sign In")
        messagebox.showinfo("Success", "Login successful!")
        self.root.destroy()
    
    def _login_failed(self):
        """Handle failed login"""
        self.signin_button.config(state='normal', text="Sign In")
        messagebox.showerror("Error", "Invalid email or password.")
        self.password_var.set("")  # Clear password field
    
    def _login_error(self, error_msg):
        """Handle login error"""
        self.signin_button.config(state='normal', text="Sign In")
        messagebox.showerror("Error", f"Login failed: {error_msg}")
    
    def _handle_create_account(self, event):
        """Handle create account link click"""
        messagebox.showinfo("Create Account", "Account creation feature coming soon!")
    
    def show(self):
        """Show the login screen"""
        if self.parent:
            self.root.transient(self.parent)
            self.root.grab_set()
        
        self.root.mainloop()
        return self.login_successful, self.user_credentials

def main():
    """Main function for testing"""
    # Create assets directory if it doesn't exist
    assets_dir = Path(__file__).parent / "assets"
    assets_dir.mkdir(exist_ok=True)
    
    # Create a simple VVAULT glyph if it doesn't exist
    glyph_path = assets_dir / "vvault_glyph.png"
    if not glyph_path.exists():
        print("Creating fallback VVAULT glyph...")
        # The fallback logo will be used automatically
    
    # Create and show login screen
    login_screen = VVAULTLoginScreen()
    success, credentials = login_screen.show()
    
    if success:
        print(f"Login successful for: {credentials['email']}")
    else:
        print("Login cancelled or failed")

if __name__ == "__main__":
    main()
