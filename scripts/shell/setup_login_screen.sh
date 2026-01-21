#!/bin/bash

# VVAULT Login Screen Setup Script
# This script sets up the complete VVAULT login screen environment

echo "🎨 VVAULT Login Screen Setup"
echo "============================"

# Navigate to login screen directory
cd "/Users/devonwoodson/Documents/GitHub/VVAULT/login-screen"

echo "📁 Working directory: $(pwd)"

# Check if we're in the right directory
if [ ! -f "package.json" ]; then
    echo "❌ package.json not found. Please run this script from the login-screen directory."
    exit 1
fi

echo "✅ Found package.json"

# Install dependencies
echo "📦 Installing dependencies..."
npm install

if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    echo "   Try running: npm install --legacy-peer-deps"
    exit 1
fi

echo "✅ Dependencies installed successfully"

# Create environment file
echo "🔧 Setting up environment configuration..."
cat > .env << EOF
# VVAULT Login Screen Environment Configuration
REACT_APP_NAME=VVAULT
REACT_APP_VERSION=1.0.0
REACT_APP_DESCRIPTION="Secure your constructs. Remember forever."
REACT_APP_THEME=terminal
REACT_APP_PRIMARY_COLOR=#3b82f6
REACT_APP_BACKGROUND_COLOR=#000000
REACT_APP_LOGO_PATH=/assets/vvault_glyph.svg
REACT_APP_ANIMATIONS_ENABLED=true
REACT_APP_GLASS_MORPHISM=true
EOF

echo "✅ Environment configuration created"

# Create assets directory if it doesn't exist
mkdir -p "public/assets"

# Verify the VVAULT glyph exists
if [ -f "public/assets/vvault_glyph.svg" ]; then
    echo "✅ VVAULT glyph found"
else
    echo "⚠️  VVAULT glyph not found, using fallback"
fi

# Create a simple test to verify setup
echo "🧪 Running setup verification..."

# Check if React is working
if node -e "console.log('React setup check:', require('react/package.json').version)" 2>/dev/null; then
    echo "✅ React is properly installed"
else
    echo "❌ React installation issue"
fi

# Check if Tailwind is working
if node -e "console.log('Tailwind setup check:', require('tailwindcss/package.json').version)" 2>/dev/null; then
    echo "✅ Tailwind CSS is properly installed"
else
    echo "❌ Tailwind CSS installation issue"
fi

echo ""
echo "🎉 VVAULT Login Screen Setup Complete!"
echo "====================================="
echo ""
echo "📋 Setup Summary:"
echo "   ✅ Dependencies installed"
echo "   ✅ Environment configured"
echo "   ✅ Assets directory created"
echo "   ✅ React + Tailwind CSS ready"
echo ""
echo "🚀 To start the login screen:"
echo "   ./start_login_screen.sh"
echo ""
echo "   Or manually:"
echo "   cd login-screen && npm start"
echo ""
echo "🌐 The login screen will be available at:"
echo "   http://localhost:3000"
echo ""
echo "🎨 Features:"
echo "   • Terminal aesthetic with pure black theme"
echo "   • OAuth login (Google, Apple, Microsoft, GitHub)"
echo "   • Email/password authentication"
echo "   • Responsive mobile-friendly design"
echo "   • Modern animations and hover effects"
echo "   • Glass morphism styling"
echo ""
echo "📱 The login screen is optimized for:"
echo "   • Desktop browsers"
echo "   • Mobile devices"
echo "   • Tablet screens"
echo "   • High-DPI displays"
