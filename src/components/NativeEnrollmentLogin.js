import React from 'react';
import './CinematicLogin.css';

export default function NativeEnrollmentLogin() {
  return (
    <main className="login-container">
      <section className="login-card" aria-labelledby="vvault-sign-in-title">
        <h1 id="vvault-sign-in-title" className="login-title">Welcome to VVAULT</h1>
        <p>VVAULT is invitation-only. Continue with Google to begin secure enrollment.</p>
        <button type="button" onClick={() => { window.location.assign('/api/auth/google'); }}>
          Continue with Google
        </button>
        <p className="login-hint">New accounts require an invitation, Terms and Privacy acceptance, a security key, recovery codes, and device approval.</p>
      </section>
    </main>
  );
}
