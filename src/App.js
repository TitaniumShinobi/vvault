import React, { useState, useEffect, useCallback } from 'react';
import { BrowserRouter as Router, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Capsules from './components/Capsules';
import VaultBrowser from './components/VaultBrowser';
import Settings from './components/Settings';
import CinematicLogin from './components/CinematicLogin';
import EnrollmentFlow from './components/EnrollmentFlow';
import { SESSION_EXPIRED_EVENT } from './utils/authFetch';
import vvaultLogo from '../assets/vvaultlogo_inverted.svg';
import './App.css';

// Navigation component
const Navigation = ({ user, onLogout }) => {
  const location = useLocation();
  
  const navItems = [
    { path: '/vault', label: 'Vault' },
    { path: '/capsules', label: 'Capsules' },
    { path: '/settings', label: 'Settings' }
  ];
  
  const handleLogout = () => {
    onLogout();
  };
  
  return (
    <nav className="navbar">
      <div className="nav-brand">
        <Link
          to="/"
          className={`nav-logo-link ${location.pathname === '/' ? 'active' : ''}`}
          aria-label="Open dashboard"
        >
          <img 
            src={vvaultLogo}
            alt="" 
            className="nav-logo"
          />
        </Link>
      </div>
      
      <div className="nav-links">
        {navItems.map(item => (
          <Link
            key={item.path}
            to={item.path}
            className={`nav-link ${location.pathname === item.path ? 'active' : ''}`}
          >
            <span className="nav-label">{item.label}</span>
          </Link>
        ))}
      </div>
      
      <div className="nav-user-section">
        <div className="nav-user-info">
          <span className="nav-user-email">{user?.email}</span>
        </div>
        <button className="nav-logout-button" onClick={handleLogout}>
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
    // Canonical sessions are HttpOnly cookies. No identity or bearer token is
    // accepted from query parameters or browser storage.
    fetch('/api/auth/verify', { credentials: 'same-origin' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => { if (payload?.user) setUser(payload.user); })
      .catch(() => setUser(null));
    
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
  
  const handleLogout = useCallback(() => {
    fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
    setUser(null);
  }, []);

  useEffect(() => {
    const onSessionExpired = () => {
      console.warn('Session expired — redirecting to login');
      setUser(null);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, []);
  
  if (loading) {
    return (
      <div className="app-loading">
        <div className="loading-content">
          <div className="loading-spinner"></div>
          <h2>Loading VVAULT...</h2>
          <p>Opening your vault...</p>
        </div>
      </div>
    );
  }
  
  // Show cinematic login screen if user is not authenticated
  if (!user) {
    const authState = new URLSearchParams(window.location.search);
    return authState.get('identity_pending') === '1'
      ? <EnrollmentFlow returningOwner={authState.get('terms_update') === '1'} />
      : <CinematicLogin onLogin={handleLogin} />;
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
            <Route path="/settings" element={<Settings systemInfo={systemInfo} user={user} />} />
            <Route path="/blockchain" element={<Navigate to="/vault" replace />} />
            <Route path="/create" element={<Navigate to="/vault" replace />} />
            <Route path="*" element={<Navigate to="/vault" replace />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
