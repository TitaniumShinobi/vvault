import React, { useState, useEffect } from 'react';
import './CinematicLogin.css';

const CinematicLogin = ({ onLogin }) => {
  const [isSignInMode, setIsSignInMode] = useState(true);
  const [isTrustedDevice, setIsTrustedDevice] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
    rememberMe: false,
    agreeToTerms: false
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [cloudflareVerified, setCloudflareVerified] = useState(false);

  // Turnstile state
  const [turnstileToken, setTurnstileToken] = useState('');
  const [turnstileWidgetId, setTurnstileWidgetId] = useState('');
  const [turnstileError, setTurnstileError] = useState('');
  const turnstileSiteKey = '0x4AAAAAAB9IaDdnFsA9yISn'; // Using same key as Chatty

  // Initialize Cloudflare Turnstile
  useEffect(() => {
    const loadTurnstile = () => {
      if (window.turnstile) {
        // Turnstile is already loaded
        return;
      }

      if (!turnstileSiteKey) {
        console.error('Turnstile site key is not configured.')
        setTurnstileError('Human verification is temporarily unavailable. Please contact support.')
        return;
      }
      
      const script = document.createElement('script');
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    };

    loadTurnstile();
  }, [turnstileSiteKey]);

  // Initialize Turnstile widget when in signup mode
  useEffect(() => {
    if (!isSignInMode && window.turnstile && !turnstileWidgetId) {
      if (!turnstileSiteKey) {
        return;
      }
      const widgetId = window.turnstile.render('#turnstile-widget', {
        sitekey: turnstileSiteKey,
        callback: (token) => {
          setTurnstileToken(token);
          setTurnstileError('');
        },
        'error-callback': (error) => {
          setTurnstileError('Human verification failed. Please try again.');
          setTurnstileToken('');
        },
        'expired-callback': () => {
          setTurnstileError('Verification expired. Please verify again.');
          setTurnstileToken('');
        },
        theme: 'auto',
        size: 'normal'
      });
      setTurnstileWidgetId(widgetId);
    }
  }, [isSignInMode, turnstileWidgetId, turnstileSiteKey]);

  // Cleanup Turnstile widget when switching modes
  useEffect(() => {
    if (isSignInMode && turnstileWidgetId && window.turnstile) {
      window.turnstile.remove(turnstileWidgetId);
      setTurnstileWidgetId('');
      setTurnstileToken('');
      setTurnstileError('');
    }
  }, [isSignInMode, turnstileWidgetId]);
  useEffect(() => {
    // In a real app, this would check IP, device fingerprint, localStorage, etc.
    const simulateTrustedDevice = () => {
      const savedDevice = localStorage.getItem('vvault_trusted_device');
      if (savedDevice) {
        setIsTrustedDevice(true);
      } else {
        // Randomly assign for demo purposes
        const isTrusted = Math.random() > 0.5;
        setIsTrustedDevice(isTrusted);
        if (isTrusted) {
          localStorage.setItem('vvault_trusted_device', 'true');
        }
      }
    };

    simulateTrustedDevice();
  }, []);

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
    setError(''); // Clear error on input change
  };

  const handleCloudflareVerification = () => {
    setCloudflareVerified(true);
  };

  const validateForm = () => {
    if (!formData.email || !formData.password) {
      setError('Email and password are required.');
      return false;
    }

    if (!isSignInMode) {
      if (!formData.name) {
        setError('Name is required.');
        return false;
      }
      if (formData.password !== formData.confirmPassword) {
        setError('Passwords do not match.');
        return false;
      }
      if (!formData.agreeToTerms) {
        setError('You must agree to the Terms of Service and Privacy Notice.');
        return false;
      }
      if (!cloudflareVerified) {
        setError('Please complete the human verification.');
        return false;
      }
    }

    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!validateForm()) {
      return;
    }

    // Validate Turnstile for signup
    if (!isSignInMode && !turnstileToken) {
      setError('Please complete human verification.');
      return;
    }

    setIsLoading(true);
    setError('');

    try {
      // Prepare request data
      const requestData = {
        email: formData.email,
        password: formData.password,
        ...(isSignInMode ? {} : {
          name: formData.name,
          confirmPassword: formData.confirmPassword,
          turnstileToken: turnstileToken
        })
      };

      // Make API call to backend
      const endpoint = isSignInMode ? '/api/auth/login' : '/api/auth/register';
      const response = await fetch(`http://localhost:8000${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestData)
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || 'Authentication failed');
      }

      // Store user session
      localStorage.setItem('vvault_user', JSON.stringify(result.user));
      localStorage.setItem('vvault_token', result.token);
      
      // Call parent callback
      onLogin(result.user);
    } catch (err) {
      setError(err.message || 'Authentication failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleOAuth = (provider) => {
    if (provider === 'Google') {
      window.location.href = '/api/auth/oauth/google';
    } else {
      console.log(`${provider} OAuth not yet implemented`);
    }
  };

  const backgroundImage = isSignInMode ? 'vvault_sunrise.png' : 'vvault_sunset.png';
  const backgroundTheme = isSignInMode ? 'Familiarity. Belonging. Trusted Return.' : 'Ceremony. Recognition of Arrival.';

  return (
    <div 
      className={`cinematic-login-container ${isSignInMode ? 'vvault-sunrise-bg' : 'vvault-sunset-bg'}`}
    >
      <div className="login-content">
        <div className="welcome-section">
          <div className="welcome-content">
            <h1 className="welcome-title">
              {isSignInMode ? (
                <>
                  Welcome<br />
                  <span className="welcome-back">Back</span>
                </>
              ) : (
                'Intelligent Memory. Guarded Sovereignty.'
              )}
            </h1>
            <div className="version-badge">v2.0.1 PROD</div>
            <p className="welcome-subtitle">
              {isSignInMode 
                ? 'VVAULT still protects what you trusted it with.'
                : 'Secure, immutable memory system that serves as a digital sanctuary for truth — preserving data, identity, and history with absolute integrity beyond manipulation or decay.'
              }
            </p>
            <p className="welcome-description">
              {isSignInMode 
                ? 'Click \'Remember Me\' to skip login on this device next time.'
                : ''
              }
            </p>
            
            <div className="social-links">
              <div className="social-icon">
                <svg viewBox="0 0 24 24" width="24" height="24">
                  <path fill="white" d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                </svg>
              </div>
              <div className="social-icon">
                <svg viewBox="0 0 24 24" width="24" height="24">
                  <path fill="white" d="M23.953 4.57a10 10 0 01-2.825.775 4.958 4.958 0 002.163-2.723c-.951.555-2.005.959-3.127 1.184a4.92 4.92 0 00-8.384 4.482C7.69 8.095 4.067 6.13 1.64 3.162a4.822 4.822 0 00-.666 2.475c0 1.71.87 3.213 2.188 4.096a4.904 4.904 0 01-2.228-.616v.06a4.923 4.923 0 003.946 4.827 4.996 4.996 0 01-2.212.085 4.936 4.936 0 004.604 3.417 9.867 9.867 0 01-6.102 2.105c-.39 0-.779-.023-1.17-.067a13.995 13.995 0 007.557 2.209c9.053 0 13.998-7.496 13.998-13.985 0-.21 0-.42-.015-.63A9.935 9.935 0 0024 4.59z"/>
                </svg>
              </div>
              <div className="social-icon">
                <svg viewBox="0 0 24 24" width="24" height="24">
                  <path fill="white" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
                </svg>
              </div>
              <div className="social-icon">
                <svg viewBox="0 0 24 24" width="24" height="24">
                  <path fill="white" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                </svg>
              </div>
            </div>
          </div>
        </div>

        <div className="form-section">
          <div className="login-form-container">
            <h2 className="form-title">
              {isSignInMode ? 'Sign in' : 'Create Account'}
            </h2>

            <form onSubmit={handleSubmit}>
              {!isSignInMode && (
                <div className="form-group">
                  <label htmlFor="name" className="form-label">Name</label>
                  <input
                    type="text"
                    id="name"
                    name="name"
                    value={formData.name}
                    onChange={handleInputChange}
                    className="form-input"
                    placeholder="Your name"
                    disabled={isLoading}
                  />
                </div>
              )}

              <div className="form-group">
                <label htmlFor="email" className="form-label">Email Address</label>
                <input
                  type="email"
                  id="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  className="form-input"
                  placeholder="Enter your email"
                  disabled={isLoading}
                />
              </div>

              <div className="form-group">
                <label htmlFor="password" className="form-label">Password</label>
                <input
                  type="password"
                  id="password"
                  name="password"
                  value={formData.password}
                  onChange={handleInputChange}
                  className="form-input"
                  placeholder="Enter your password"
                  disabled={isLoading}
                />
              </div>

              {!isSignInMode && (
                <div className="form-group">
                  <label htmlFor="confirmPassword" className="form-label">Confirm Password</label>
                  <input
                    type="password"
                    id="confirmPassword"
                    name="confirmPassword"
                    value={formData.confirmPassword}
                    onChange={handleInputChange}
                    className="form-input"
                    placeholder="Confirm your password"
                    disabled={isLoading}
                  />
                </div>
              )}

              {isSignInMode && (
                <div className="form-checkbox">
                  <input
                    type="checkbox"
                    name="rememberMe"
                    checked={formData.rememberMe}
                    onChange={handleInputChange}
                    disabled={isLoading}
                  />
                  <label htmlFor="rememberMe">Remember Me</label>
                </div>
              )}

              {!isSignInMode && (
                <div className="terms-checkbox">
                  <input
                    type="checkbox"
                    name="agreeToTerms"
                    checked={formData.agreeToTerms}
                    onChange={handleInputChange}
                    disabled={isLoading}
                  />
                  <label htmlFor="agreeToTerms">
                    By continuing, I confirm I understand and agree to the{' '}
                    <a href="/vvault-terms.html" target="_blank" className="terms-link">V²AULT Terms of Service</a> and the{' '}
                    <a href="/vvault-privacy.html" target="_blank" className="terms-link">V²AULT Privacy Notice</a>.{' '}
                    If I am in the EEA or UK, I have read and agree to the{' '}
                    <a href="/vvault-eeccd.html" target="_blank" className="terms-link">European Electronic Communications Code Disclosure</a>.
                  </label>
                </div>
              )}

              {!isSignInMode && (
                <div className="turnstile-verification">
                  <div id="turnstile-widget" className="flex justify-center"></div>
                  {turnstileError && (
                    <div className="turnstile-error">{turnstileError}</div>
                  )}
                </div>
              )}

              <button
                type="submit"
                className="btn-primary"
                disabled={isLoading}
              >
                {isLoading ? 'Processing...' : (isSignInMode ? 'Sign in now' : 'Create account')}
              </button>

              {isSignInMode && (
                <div className="password-link">
                  <a href="#" className="form-link">Lost your password?</a>
                </div>
              )}

              {error && <div className="error-message">{error}</div>}

              <div className="oauth-section">
                <div className="oauth-buttons">
                  <button
                    type="button"
                    onClick={() => handleOAuth('Google')}
                    className="btn-oauth"
                    disabled={isLoading}
                  >
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                    </svg>
                    Google
                  </button>
                  <button
                    type="button"
                    onClick={() => handleOAuth('Microsoft')}
                    className="btn-oauth"
                    disabled={isLoading}
                  >
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#F25022" d="M1 1h10v10H1z"/>
                      <path fill="#00A4EF" d="M13 1h10v10H13z"/>
                      <path fill="#7FBA00" d="M1 13h10v10H1z"/>
                      <path fill="#FFB900" d="M13 13h10v10H13z"/>
                    </svg>
                    Microsoft
                  </button>
                  <button
                    type="button"
                    onClick={() => handleOAuth('Apple')}
                    className="btn-oauth"
                    disabled={isLoading}
                  >
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#000000" d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                    </svg>
                    Apple
                  </button>
                  <button
                    type="button"
                    onClick={() => handleOAuth('GitHub')}
                    className="btn-oauth"
                    disabled={isLoading}
                  >
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#000000" d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
                    </svg>
                    GitHub
                  </button>
                </div>
              </div>

              <div className="form-links">
                {isSignInMode ? (
                  <div className="form-toggle">
                    <span className="form-toggle-text">
                      Don't have an account?{' '}
                      <button
                        type="button"
                        onClick={() => setIsSignInMode(false)}
                        className="form-link"
                      >
                        Create one
                      </button>
                    </span>
                  </div>
                ) : (
                  <div className="form-toggle">
                    <span className="form-toggle-text">
                      Already have an account?{' '}
                      <button
                        type="button"
                        onClick={() => setIsSignInMode(true)}
                        className="form-link"
                      >
                        Sign in
                      </button>
                    </span>
                  </div>
                )}
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CinematicLogin;
