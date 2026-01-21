#!/bin/bash

# VVAULT Login Screen Startup Script
# This script sets up and runs the VVAULT login screen

echo "🎨 VVAULT Login Screen Startup"
echo "================================"

# Navigate to login screen directory
cd "/Users/devonwoodson/Documents/GitHub/VVAULT/login-screen"

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js 16+ first."
    echo "   Visit: https://nodejs.org/"
    exit 1
fi

# Check Node.js version
NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 16 ]; then
    echo "❌ Node.js version 16+ is required. Current version: $(node -v)"
    echo "   Please upgrade Node.js"
    exit 1
fi

echo "✅ Node.js version: $(node -v)"

# Check if npm is available
if ! command -v npm &> /dev/null; then
    echo "❌ npm is not available"
    exit 1
fi

echo "✅ npm version: $(npm -v)"

# Install dependencies if node_modules doesn't exist
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
    
    if [ $? -ne 0 ]; then
        echo "❌ Failed to install dependencies"
        exit 1
    fi
    
    echo "✅ Dependencies installed successfully"
else
    echo "✅ Dependencies already installed"
fi

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "🔧 Creating environment configuration..."
    cat > .env << EOF
# VVAULT Login Screen Environment Configuration
REACT_APP_NAME=VVAULT
REACT_APP_VERSION=1.0.0
REACT_APP_DESCRIPTION="Secure your constructs. Remember forever."
REACT_APP_THEME=terminal
REACT_APP_PRIMARY_COLOR=#3b82f6
REACT_APP_BACKGROUND_COLOR=#000000
EOF
    echo "✅ Environment configuration created"
fi

echo ""
echo "🚀 Starting VVAULT Login Screen..."
echo "   Theme: Terminal Aesthetic"
echo "   Background: Pure Black (#000000)"
echo "   Accent Color: Blue (#3b82f6)"
echo "   Features: OAuth, Email/Password, Responsive"
echo ""
echo "🌐 The login screen will open at: http://localhost:3000"
echo "   Press Ctrl+C to stop the server"
echo ""

# Start the development server
npm start
