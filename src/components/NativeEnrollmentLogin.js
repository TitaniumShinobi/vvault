import React from 'react';
import './CinematicLogin.css';

export default function NativeEnrollmentLogin() {
  return (
    <main className="login-container">
      <section className="login-card" aria-labelledby="vvault-sign-in-title">
        <h1 id="vvault-sign-in-title" className="login-title">Welcome to VVAULT</h1>
        <p>VVAULT is invitation-only. Enter your invitation, then continue with Google to begin secure enrollment.</p>
        <form method="post" action="/api/auth/google">
          <label htmlFor="vvault-invitation">Invitation code</label>
          <input
            id="vvault-invitation"
            name="invitation"
            type="password"
            autoComplete="one-time-code"
            required
          />
          <button type="submit">Continue with Google</button>
        </form>
        <p className="login-hint">New accounts require an invitation, Terms and Privacy acceptance, a security key, recovery codes, and device approval.</p>
      </section>
    </main>
  );
}
