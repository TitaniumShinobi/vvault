import React, { useEffect, useState } from 'react';
import './CinematicLogin.css';
import wreckSymbol from '../../assets/WRECK_INVERTED.svg';

/** Canonical identity entry point; it never handles passwords or bearer tokens. */
const CinematicLogin = () => {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const token = params.get('magic_link');
    if (!token) return;
    // Remove the fragment before any request or rendering path can expose it.
    window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
    setIsLoading(true);
    fetch('/api/auth/email-magic-links/consume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),
    }).then((response) => {
      setStatus(response.ok ? 'Identity verified. Complete enrollment to open your vault.' : 'That secure link is no longer valid.');
    }).catch(() => setStatus('That secure link could not be verified.')).finally(() => setIsLoading(false));
  }, []);

  const beginProvider = (provider) => {
    // The server creates and binds the PKCE/state transaction. No client token
    // or caller-selected return URL is accepted here.
    window.location.assign(`/api/auth/oauth/${provider}`);
  };

  const requestMagicLink = async (event) => {
    event.preventDefault();
    setIsLoading(true);
    setStatus('');
    try {
      const response = await fetch('/api/auth/email-magic-links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      // Identical result for new and known emails prevents enumeration.
      if (!response.ok && response.status !== 202) throw new Error('request_failed');
      setStatus('If this address can receive sign-in mail, a secure link is on its way.');
    } catch (_) {
      setStatus('We could not start that request. Please try again shortly.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="cinematic-login-container vvault-sunrise-bg">
      <div className="login-content">
        <div className="welcome-section">
          <div className="welcome-content">
            <h1 className="welcome-title">Welcome<br /><span className="welcome-back">to VVAULT</span></h1>
            <p className="welcome-subtitle">Your identity is verified before a vault becomes available.</p>
            <p className="welcome-description">New identities begin enrollment; they never receive another person’s vault.</p>
            <a href="https://thewreck.org" target="_blank" rel="noopener noreferrer" className="social-icon wreck-symbol-container">
              <img src={wreckSymbol} alt="thewreck.org" className="wreck-symbol" />
            </a>
          </div>
        </div>
        <div className="form-section">
          <div className="login-form-container">
            <h2 className="form-title">Continue securely</h2>
            <div className="oauth-section">
              <div className="oauth-buttons">
                <button type="button" onClick={() => beginProvider('google')} className="btn-oauth" disabled={isLoading}>Continue with Google</button>
                <button type="button" onClick={() => beginProvider('github')} className="btn-oauth" disabled={isLoading}>Continue with GitHub</button>
              </div>
            </div>
            <form onSubmit={requestMagicLink}>
              <div className="form-group">
                <label htmlFor="email" className="form-label">Email magic link</label>
                <input type="email" id="email" name="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)} className="form-input"
                  placeholder="you@example.com" required disabled={isLoading} />
              </div>
              <button type="submit" className="btn-primary" disabled={isLoading}>
                {isLoading ? 'Sending…' : 'Email me a secure link'}
              </button>
            </form>
            {status && <p className="welcome-description" role="status">{status}</p>}
            <p className="welcome-description">A new email or provider creates a separate pending account unless you link it from an active account.</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CinematicLogin;
