import React, { useState, useEffect, useCallback } from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Capsules from './components/Capsules';
import VaultBrowser from './components/VaultBrowser';
import Blockchain from './components/Blockchain';
import Settings from './components/Settings';
import CreateConstruct from './components/CreateConstruct';
import CinematicLogin from './components/CinematicLogin';
import {
  validateSession,
  SESSION_EXPIRED_EVENT,
  VVAULT_READY_EVENT,
  clearStoredPostgrestJwt,
  finalizeAuthServiceLogin,
  markSessionActive,
  refreshPostgrestJwt,
  refreshVvaultRuntimeState
} from './utils/authFetch';
import './App.css';

const STARTUP_AUTH_TIMEOUT_MS = 2500;
const STARTUP_STATUS_TIMEOUT_MS = 2500;
const VVAULT_STATUS_READY_POLL_MS = 15000;
const VVAULT_STATUS_DEGRADED_POLL_MS = 60000;

async function fetchJsonWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    return response.json();
  } finally {
    window.clearTimeout(timer);
  }
}

// Navigation component
const Navigation = ({ user, onLogout }) => {
  const location = useLocation();
  
  const navItems = [
    { path: '/', label: 'Dashboard', icon: '🏠' },
    { path: '/vault', label: 'Vault', icon: '🔒' },
    { path: '/capsules', label: 'Capsules', icon: '📦' },
    { path: '/blockchain', label: 'Blockchain', icon: '⛓️' },
    { path: '/create', label: 'Create', icon: '✦' },
    { path: '/settings', label: 'Settings', icon: '⚙️' }
  ];
  
  const handleLogout = () => {
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
  const [status, setStatus] = useState({ online: false, loading: true, status: 'unknown' });
  
  useEffect(() => {
    let timeoutId = null;
    const checkStatus = async () => {
      let nextDelay = VVAULT_STATUS_DEGRADED_POLL_MS;
      try {
        const runtime = await refreshVvaultRuntimeState();
        setStatus({ online: runtime.ready, loading: false, ...runtime });
        nextDelay = runtime.ready ? VVAULT_STATUS_READY_POLL_MS : VVAULT_STATUS_DEGRADED_POLL_MS;
      } catch (error) {
        setStatus({ online: false, loading: false, status: 'not_ready', error: error.message });
      } finally {
        timeoutId = setTimeout(checkStatus, nextDelay);
      }
    };
    const onRuntimeReady = (event) => {
      const runtime = event.detail || {};
      setStatus({ online: runtime.ready === true, loading: false, ...runtime });
    };

    window.addEventListener(VVAULT_READY_EVENT, onRuntimeReady);
    checkStatus();

    return () => {
      window.removeEventListener(VVAULT_READY_EVENT, onRuntimeReady);
      clearTimeout(timeoutId);
    };
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
      <span>{status.online ? 'VVAULT ready' : `VVAULT ${status.status || 'not_ready'}`}</span>
    </div>
  );
};

// Main App component
function App() {
  const [user, setUser] = useState(null);
  const [systemInfo, setSystemInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authResolved, setAuthResolved] = useState(false);
  const [authError, setAuthError] = useState('');
  
  useEffect(() => {
    let mounted = true;

    const resolveAuth = () => {
      if (mounted) {
        setAuthResolved(true);
      }
    };

    // Check for OAuth callback params in URL
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    const email = urlParams.get('email');
    const name = urlParams.get('name');
    const oauthError = urlParams.get('oauth_error');
    
    if (token && email) {
      // OAuth successful - save user session
      const userData = {
        email: decodeURIComponent(email),
        name: name ? decodeURIComponent(name) : email.split('@')[0],
        token: token
      };
      localStorage.setItem('vvault_user', JSON.stringify(userData));
      localStorage.setItem('vvault_token', token);
      markSessionActive();
      if (mounted) {
        setUser(userData);
      }
      
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname);
      console.log('OAuth login successful:', userData.email);
      resolveAuth();
    } else if (oauthError) {
      if (mounted) {
        setAuthError(decodeURIComponent(oauthError));
      }
      window.history.replaceState({}, document.title, window.location.pathname);
      resolveAuth();
    } else {
      const flowError = urlParams.get('error');
      if (flowError) {
        if (mounted) {
          setAuthError(decodeURIComponent(flowError));
        }
        window.history.replaceState({}, document.title, window.location.pathname);
      }
      (async () => {
        try {
          const existingToken = localStorage.getItem('vvault_token');
          if (existingToken) {
            const savedUser = localStorage.getItem('vvault_user');
            if (savedUser) {
              try {
                const parsed = JSON.parse(savedUser);
                const valid = await validateSession({ timeoutMs: STARTUP_AUTH_TIMEOUT_MS });
                if (valid) {
                  markSessionActive();
                  await refreshPostgrestJwt({ timeoutMs: STARTUP_AUTH_TIMEOUT_MS });
                  if (mounted) {
                    setUser(parsed);
                  }
                } else {
                  console.warn('Stored session is no longer valid — clearing');
                  localStorage.removeItem('vvault_user');
                  localStorage.removeItem('vvault_token');
                  if (mounted) {
                    setUser(null);
                  }
                }
              } catch (error) {
                console.error('Failed to parse saved user:', error);
                localStorage.removeItem('vvault_user');
                localStorage.removeItem('vvault_token');
              }
            }
          } else {
            const bridged = await finalizeAuthServiceLogin({ readyTimeoutMs: STARTUP_AUTH_TIMEOUT_MS });
            if (bridged && mounted) {
              setUser(bridged);
            }
          }
        } finally {
          resolveAuth();
        }
      })();
    }
    
    // Load system info
    const loadSystemInfo = async () => {
      try {
        const data = await fetchJsonWithTimeout('/api/status', STARTUP_STATUS_TIMEOUT_MS);
        if (mounted) {
          setSystemInfo(data);
        }
      } catch (error) {
        console.error('Failed to load system info:', error);
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };
    
    loadSystemInfo();
    return () => {
      mounted = false;
    };
  }, []);
  
  const handleLogin = (userData) => {
    setUser(userData);
  };
  
  const handleLogout = useCallback(() => {
    localStorage.removeItem('vvault_user');
    localStorage.removeItem('vvault_token');
    clearStoredPostgrestJwt();
    setUser(null);
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
  }, []);

  useEffect(() => {
    const onSessionExpired = () => {
      console.warn('Session expired — redirecting to login');
      setUser(null);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, []);
  
  if (loading || !authResolved) {
    return (
      <div className="app-loading">
        <div className="loading-content">
          <div className="loading-spinner"></div>
          <h2>Loading VVAULT...</h2>
          <p>Initializing vectored anatomy vault...</p>
        </div>
      </div>
    );
  }
  
  // Show cinematic login screen if user is not authenticated
  if (!user) {
    return <CinematicLogin onLogin={handleLogin} initialError={authError} />;
  }
  
  return (
    <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <div className="app">
        <Navigation user={user} onLogout={handleLogout} />
        
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard systemInfo={systemInfo} user={user} />} />
            <Route path="/vault" element={<VaultBrowser user={user} />} />
            <Route path="/capsules" element={<Capsules user={user} />} />
            <Route path="/blockchain" element={<Blockchain user={user} />} />
            <Route path="/create" element={<CreateConstruct user={user} />} />
            <Route path="/settings" element={<Settings systemInfo={systemInfo} user={user} />} />
          </Routes>
        </main>
        
        <footer className="footer">
          <div className="footer-content">
            <div className="footer-section">
              <span>© 2025 VVAULT - Vectored Anatomy Vault</span>
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
