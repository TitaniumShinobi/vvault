import React, { useState } from 'react';
import './LoginScreen.css';

const LoginScreen = ({ onLogin }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const validateEmail = (email) => {
    const pattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    return pattern.test(email);
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');

    if (!email || !password) {
      setError('Please fill in all fields.');
      return;
    }

    if (!validateEmail(email)) {
      setError('Please enter a valid email address.');
      return;
    }

    setIsLoading(true);

    try {
      // Call authentication API
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password, rememberMe }),
      });

      const data = await response.json();

      if (response.ok) {
        // Store user session
        localStorage.setItem('vvault_user', JSON.stringify(data.user));
        localStorage.setItem('vvault_token', data.token);
        
        // Call parent callback
        onLogin(data.user);
      } else {
        setError(data.error || 'Login failed. Please try again.');
      }
    } catch (err) {
      setError('Network error. Please check your connection.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-card">
        {/* VVAULT Logo */}
        <div className="login-logo">
          <img 
            src="/assets/vvault_glyph.png" 
            alt="VVAULT" 
            className="logo-image"
          />
        </div>
        
        {/* Title */}
        <h1 className="login-title">VVAULT</h1>
        <p className="login-subtitle">Secure your constructs. Remember forever.</p>

        {/* Login Form */}
        <form className="login-form" onSubmit={handleLogin}>
          <div className="form-group">
            <label className="form-label">Email Address</label>
            <input
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Enter your email address"
              disabled={isLoading}
              autoComplete="email"
            />
          </div>

          <div className="form-group">
            <label className="form-label">Password</label>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              disabled={isLoading}
              autoComplete="current-password"
            />
          </div>

          <div className="form-group remember-me-group">
            <label className="remember-me-label">
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                disabled={isLoading}
                className="remember-me-checkbox"
              />
              <span>Remember me for 90 days</span>
            </label>
          </div>

          <button
            type="submit"
            className="login-button"
            disabled={isLoading}
          >
            {isLoading ? 'Signing In...' : 'Sign In'}
          </button>

          {error && <div className="error-message">{error}</div>}
        </form>

        {/* Footer */}
        <div className="login-footer">
          <button className="create-account-link">
            Create Account
          </button>
        </div>
      </div>
    </div>
  );
};

export default LoginScreen;
