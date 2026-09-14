import React, { useState, useEffect } from 'react';
import './CinematicLogin.css';
import wreckSymbol from '../../assets/WRECK_INVERTED.svg';

const CinematicLogin = ({ onLogin, pendingSignup = false, children }) => {
  const recoveryMode = new URLSearchParams(window.location.search).get('account_recovery') === '1';
  const [isSignInMode, setIsSignInMode] = useState(!pendingSignup && !children);
  const signupStep = 1;
  const [signupDocuments, setSignupDocuments] = useState([]);
  const [chattyAccepted, setChattyAccepted] = useState(false);
  const [vvaultAccepted, setVvaultAccepted] = useState(false);
  useEffect(() => {
    if (isSignInMode || children) return;
    fetch('/api/auth/paired-signup/documents', {credentials:'same-origin'})
      .then(async response => { if (!response.ok) throw new Error('Current signup documents could not load.'); return response.json(); })
      .then(value => { setSignupDocuments(value.documents); setChattyAccepted(false); setVvaultAccepted(false); })
      .catch(err => setError(err.message));
  }, [isSignInMode, children]);
  const [email, setEmail] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [codeRequested, setCodeRequested] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [magicAvailable, setMagicAvailable] = useState(null);
  const switchToSignup = () => { setIsSignInMode(false); setError(''); setStatus(''); };
  const switchToSignin = () => { setIsSignInMode(true); setError(''); setStatus(''); };
  const handleOAuth = (name) => {
    const provider = name.toLowerCase();
    if (!['google', 'github'].includes(provider)) {
      setError(`${name} sign-in is not configured. Choose Google or email.`);
      return;
    }
    if (!isSignInMode) {
      if (!chattyAccepted || !vvaultAccepted || signupDocuments.length !== 6) {
        setError('Review and accept both products’ current documents to create your accounts.'); return;
      }
      let policy = document.querySelector('meta[name="referrer"]'); if (!policy) { policy = document.createElement('meta'); policy.name='referrer'; document.head.appendChild(policy); } policy.content='strict-origin';
      const form = document.createElement('form'); form.method = 'POST'; form.action = `/api/auth/oauth/${provider}`;
      for (const [name,value] of Object.entries({intent:'SIGN_UP',chattyAccepted:'true',vvaultAccepted:'true',documents:JSON.stringify(signupDocuments)})) {
        const input=document.createElement('input'); input.type='hidden'; input.name=name; input.value=value; form.appendChild(input);
      }
      document.body.appendChild(form); form.submit(); return;
    }
    window.location.assign(`/api/auth/oauth/${provider}`);
  };
  useEffect(() => {
    fetch('/api/auth/email-codes/health', { credentials: 'same-origin' })
      .then(async response => setMagicAvailable(response.ok && (await response.json()).available === true))
      .catch(() => setMagicAvailable(false));
    if(new URLSearchParams(window.location.search).get('email_code_requested')==='1') {
      fetch('/api/auth/email-codes/status',{credentials:'same-origin'}).then(async response=>{
        if(!response.ok) throw new Error('Request a new verification code.');
        const context=await response.json();
        setCodeRequested(context.codeRequested===true);setEmail(context.email || '');
        setIsSignInMode(context.intent!=='SIGN_UP');
        setStatus('Enter the code sent to your email. Your signup acceptance is saved with this request.');
      }).catch(err=>setError(err.message));
    }
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const token = params.get('magic_link');
    if (!token) return;
    window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
    setIsLoading(true);
    fetch('/api/auth/email-magic-links/consume', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json'}, body: JSON.stringify({token}),
    }).then(async response => {
      if (!response.ok) throw new Error('That sign-in link is invalid or expired. Request a new one.');
      if (response.redirected) {
        const destination = new URL(response.url);
        if (destination.origin !== window.location.origin) throw new Error('Unexpected sign-in destination.');
        window.location.assign(destination.pathname + destination.search);
      } else { window.location.assign('/'); }
    }).catch(err => setError(err.message)).finally(() => setIsLoading(false));
  }, []);
  const resumeSignup = async () => {
    setIsLoading(true); setError('');
    try {
      const response=await fetch('/api/auth/paired-signup/resume',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({intent:'SIGN_UP',chattyAccepted,vvaultAccepted,documents:signupDocuments})});
      const result=await response.json();
      if (!response.ok) throw new Error(result.error || 'Signup could not continue.');
      window.location.assign('/?identity_pending=1');
    } catch(err) { setError(err.message); } finally { setIsLoading(false); }
  };
  const requestMagicLink = async event => {
    event.preventDefault(); setIsLoading(true); setError(''); setStatus('');
    try {
      // Account recovery is a one-time link ceremony.  It must use the
      // recovery-aware link endpoint, rather than the sign-in/sign-up code
      // endpoint, which intentionally does not accept recovery intent.
      const endpoint = recoveryMode
        ? '/api/auth/email-magic-links'
        : (codeRequested ? '/api/auth/email-codes/resend' : '/api/auth/email-codes');
      const payload = recoveryMode
        ? {email, intent:'ACCOUNT_RECOVERY'}
        : {email, intent:isSignInMode?'SIGN_IN':'SIGN_UP',chattyAccepted,vvaultAccepted,documents:signupDocuments};
      const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const result=await response.json();
      if (result.disposition === 'SIGNUP_REQUIRED') { setIsSignInMode(false); setStatus('Create your account first. Review both products’ documents below.'); return; }
      if (!response.ok) throw new Error(result.error || (recoveryMode ? 'A recovery link could not be sent.' : 'A verification code could not be sent.'));
      if (recoveryMode) {
        setStatus(result.message || 'If this verified address can receive recovery mail, a secure link is on its way.');
        return;
      }
      setCodeRequested(true); setEmailCode(''); setStatus(result.message);
    } catch(err) { setError(err.message); } finally { setIsLoading(false); }
  };
  const verifyEmailCode = async () => {
    setIsLoading(true); setError('');
    try {
      const response=await fetch('/api/auth/email-codes/verify',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:emailCode})});
      if (!response.ok) { const result=await response.json(); throw new Error(result.error || 'Request a new verification code.'); }
      const destination=new URL(response.url);
      if (destination.origin!==window.location.origin) throw new Error('Unexpected sign-in destination.');
      window.location.assign(destination.pathname+destination.search);
    } catch(err) { setError(err.message); setEmailCode(''); } finally { setIsLoading(false); }
  };
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
              ) : signupStep === 2 ? (
                'Forge Your Codex Glyph'
              ) : (
                'Intelligent Memory. Guarded Sovereignty.'
              )}
            </h1>
            <p className="welcome-subtitle">
              {isSignInMode
                ? 'VVAULT still protects your data.'
                : signupStep === 2
                  ? 'Every VVAULT member receives a unique Codex Glyph — a cryptographic seal that represents your identity in the system. Customize it to make it yours.'
                  : 'Secure, immutable memory system that serves as a digital sanctuary for truth — preserving data, identity, and history with absolute integrity beyond manipulation or decay.'
              }
            </p>
            <p className="welcome-description">
              {isSignInMode
                ? 'Sign in to continue to your personal VVAULT.'
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
              <a href="https://thewreck.org" target="_blank" rel="noopener noreferrer" className="social-icon wreck-symbol-container">
                <img src={wreckSymbol} alt="thewreck.org" className="wreck-symbol" />
              </a>
            </div>
          </div>
        </div>
        <div className="wreck-footer">
          <a href="https://thewreck.org" target="_blank" rel="noopener noreferrer" className="public-copyright">
            © 2026 Verified Vectored Anatomy Unconsciously Lingering Together
          </a>
        </div>

        <div className="form-section">
          <div className="login-form-container">
            <h2 className="form-title">
              {isSignInMode ? 'Sign in' : signupStep === 1 ? 'Create Account' : 'Your Codex Glyph'}
            </h2>

            {children || <form onSubmit={requestMagicLink}>
              {!pendingSignup && <><div className="form-group">
                <label htmlFor="email" className="form-label">Email Address</label>
                <input type="email" id="email" name="email" autoComplete="email"
                  value={email} onChange={(event) => setEmail(event.target.value)}
                  className="form-input" placeholder="Enter your email" required disabled={isLoading} />
              </div>
              <button type="submit" className="btn-primary" disabled={isLoading || magicAvailable === false}>
                {isLoading ? 'Sending…' : recoveryMode ? 'Email me a secure recovery link' : codeRequested ? 'Send a new code' : 'Email me a verification code'}
              </button>
              {codeRequested && <div className="form-group"><label htmlFor="email-code" className="form-label">Verification code</label><input id="email-code" className="form-input" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={emailCode} onChange={event=>setEmailCode(event.target.value)} /><p>Each code allows one attempt. If incorrect, request a new code.</p><button type="button" className="btn-primary" disabled={isLoading || emailCode.length!==8} onClick={verifyEmailCode}>Verify code</button></div>}
              <p className="welcome-description">{isSignInMode ? 'Use a verification code, or continue with your provider below.' : 'Verify your email, then complete account setup.'}</p>
              {magicAvailable === false && <p role="status">Email sign-in is not configured yet. Google remains available.</p>}
              </>}
              {status && <p role="status">{status}</p>}
              {error && <div className="error-message" role="alert">{error}</div>}
              {!isSignInMode && !codeRequested && <div className="signup-consents">
                {['chatty','vvault'].map(product => <label key={product} style={{display:'block',margin:'12px 0',lineHeight:1.5}}>
                  <input type="checkbox" checked={product === 'chatty' ? chattyAccepted : vvaultAccepted}
                    onChange={event => product === 'chatty' ? setChattyAccepted(event.target.checked) : setVvaultAccepted(event.target.checked)} />{' '}
                  I agree to {product === 'chatty' ? 'Chatty' : 'VVAULT'}’s current{' '}
                  {signupDocuments.filter(doc => doc.key.startsWith(product + ':')).map((doc,index) => <React.Fragment key={doc.key}>
                    {index > 0 && ', '}<a href={doc.url} target="_blank" rel="noopener noreferrer" style={{color:'#b8dcff',textDecoration:'underline'}}>{doc.label}</a>
                  </React.Fragment>)}.
                </label>)}
              </div>}
              {pendingSignup && <><p>Your identity is verified. Accept both products’ documents to finish creating your accounts.</p><button type="button" className="btn-primary" disabled={isLoading || !chattyAccepted || !vvaultAccepted || signupDocuments.length !== 6} onClick={resumeSignup}>Create accounts and continue</button></>}
              {!pendingSignup && <><div className="oauth-section">
                <div className="oauth-buttons">
                  <button type="button" onClick={() => handleOAuth('Google')} className="btn-oauth" disabled={isLoading}>
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                    </svg>
                    Google
                  </button>
                  <button type="button" onClick={() => handleOAuth('Microsoft')} className="btn-oauth" disabled={isLoading}>
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#F25022" d="M1 1h10v10H1z"/>
                      <path fill="#00A4EF" d="M13 1h10v10H13z"/>
                      <path fill="#7FBA00" d="M1 13h10v10H1z"/>
                      <path fill="#FFB900" d="M13 13h10v10H13z"/>
                    </svg>
                    Microsoft
                  </button>
                  <button type="button" onClick={() => handleOAuth('Apple')} className="btn-oauth" disabled={isLoading}>
                    <svg className="oauth-icon" viewBox="0 0 24 24" width="20" height="20">
                      <path fill="#000000" d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                    </svg>
                    Apple
                  </button>
                  <button type="button" onClick={() => handleOAuth('GitHub')} className="btn-oauth" disabled={isLoading}>
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
                      <button type="button" onClick={switchToSignup} className="form-link">
                        Create one
                      </button>
                    </span>
                  </div>
                ) : (
                  <div className="form-toggle">
                    <span className="form-toggle-text">
                      Already have an account?{' '}
                      <button type="button" onClick={switchToSignin} className="form-link">
                        Sign in
                      </button>
                    </span>
                  </div>
                )}
              </div></>}
            </form>}
          </div>
        </div>
      </div>
    </div>
  );
};

export default CinematicLogin;
