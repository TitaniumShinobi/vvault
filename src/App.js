import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Capsules from './components/Capsules';
import VaultBrowser from './components/VaultBrowser';
import Blockchain from './components/Blockchain';
import Settings from './components/Settings';
import CinematicLogin from './components/CinematicLogin';
import './App.css';

// Navigation component
const Navigation = ({ user, onLogout }) => {
  const location = useLocation();
  
  const navItems = [
    { path: '/', label: 'Dashboard', icon: '🏠' },
    { path: '/vault', label: 'Vault', icon: '🔒' },
    { path: '/capsules', label: 'Capsules', icon: '📦' },
    { path: '/blockchain', label: 'Blockchain', icon: '⛓️' },
    { path: '/settings', label: 'Settings', icon: '⚙️' }
  ];
  
  const handleLogout = () => {
    // Clear user session
    localStorage.removeItem('vvault_user');
    localStorage.removeItem('vvault_token');
    
    // Call parent logout callback
    onLogout();
  };
  
  return (
    <nav className="navbar">
      <div className="nav-brand">
        <img 
          src="/assets/vvault_glyph.png" 
          alt="VVAULT" 
          className="nav-logo"
          style={{ width: '32px', height: '32px' }}
        />
        <span className="nav-title">VVAULT</span>
        <span className="nav-subtitle">AI Construct Memory Vault</span>
      </div>
      
      <div className="nav-links">
        {navItems.map(item => (
          <Link
            key={item.path}
            to={item.path}
            className={`nav-link ${location.pathname === item.path ? 'active' : ''}`}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
          </Link>
        ))}
      </div>
      
      <div className="nav-user-section">
        <div className="nav-user-info">
          <span className="nav-user-email">{user?.email}</span>
        </div>
        <button className="nav-logout-button" onClick={handleLogout}>
          <span className="nav-icon">🚪</span>
          <span className="nav-label">Logout</span>
        </button>
        <div className="nav-status">
          <StatusIndicator />
        </div>
      </div>
    </nav>
  );
};

// Status indicator component
const StatusIndicator = () => {
  const [status, setStatus] = useState({ online: false, loading: true });
  
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const response = await fetch('/api/health');
        const data = await response.json();
        setStatus({ online: response.ok, loading: false, data });
      } catch (error) {
        setStatus({ online: false, loading: false, error: error.message });
      }
    };
    
    checkStatus();
    const interval = setInterval(checkStatus, 30000); // Check every 30 seconds
    
    return () => clearInterval(interval);
  }, []);
  
  if (status.loading) {
    return (
      <div className="status-indicator status-info">
        <div className="spinner"></div>
        <span>Connecting...</span>
      </div>
    );
  }
  
  return (
    <div className={`status-indicator ${status.online ? 'status-success' : 'status-error'}`}>
      <span className="status-dot"></span>
      <span>{status.online ? 'Online' : 'Offline'}</span>
    </div>
  );
};

// Main App component
function App() {
  const [user, setUser] = useState(null);
  const [systemInfo, setSystemInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    // Check for OAuth callback params in URL
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    const email = urlParams.get('email');
    const name = urlParams.get('name');
    
    if (token && email) {
      // OAuth successful - save user session
      const userData = {
        email: decodeURIComponent(email),
        name: name ? decodeURIComponent(name) : email.split('@')[0],
        token: token
      };
      localStorage.setItem('vvault_user', JSON.stringify(userData));
      localStorage.setItem('vvault_token', token);
      setUser(userData);
      
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname);
      console.log('OAuth login successful:', userData.email);
    } else {
      // Check for existing user session
      const savedUser = localStorage.getItem('vvault_user');
      if (savedUser) {
        try {
          setUser(JSON.parse(savedUser));
        } catch (error) {
          console.error('Failed to parse saved user:', error);
          localStorage.removeItem('vvault_user');
          localStorage.removeItem('vvault_token');
        }
      }
    }
    
    // Load system info
    const loadSystemInfo = async () => {
      try {
        const response = await fetch('/api/status');
        const data = await response.json();
        setSystemInfo(data);
      } catch (error) {
        console.error('Failed to load system info:', error);
      } finally {
        setLoading(false);
      }
    };
    
    loadSystemInfo();
  }, []);
  
  const handleLogin = (userData) => {
    setUser(userData);
  };
  
  const handleLogout = () => {
    setUser(null);
  };
  
  if (loading) {
    return (
      <div className="app-loading">
        <div className="loading-content">
          <div className="loading-spinner"></div>
          <h2>Loading VVAULT...</h2>
          <p>Initializing AI construct memory vault...</p>
        </div>
      </div>
    );
  }
  
  // Show cinematic login screen if user is not authenticated
  if (!user) {
    return <CinematicLogin onLogin={handleLogin} />;
  }
  
  return (
    <Router>
      <div className="app">
        <Navigation user={user} onLogout={handleLogout} />
        
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard systemInfo={systemInfo} user={user} />} />
            <Route path="/vault" element={<VaultBrowser user={user} />} />
            <Route path="/capsules" element={<Capsules user={user} />} />
            <Route path="/blockchain" element={<Blockchain user={user} />} />
            <Route path="/settings" element={<Settings systemInfo={systemInfo} user={user} />} />
          </Routes>
        </main>
        
        <footer className="footer">
          <div className="footer-content">
            <div className="footer-section">
              <span>© 2025 VVAULT - AI Construct Memory Vault</span>
            </div>
            <div className="footer-section">
              <span>Backend: localhost:8000</span>
              <span>Frontend: localhost:7784</span>
            </div>
            <div className="footer-section">
              <span>Version 1.0.0</span>
            </div>
          </div>
        </footer>
      </div>
    </Router>
  );
}

export default App;