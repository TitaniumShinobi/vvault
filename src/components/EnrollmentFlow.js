import React, { useState } from 'react';

const b64url = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const decode = (value) => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), (c) => c.charCodeAt(0));

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Request was denied');
  return payload;
}

export default function EnrollmentFlow({ returningOwner = false }) {
  const [step, setStep] = useState('consent');
  const [codes, setCodes] = useState([]);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const run = async (action) => { setWorking(true); setError(''); try { await action(); } catch (failure) { setError(failure.message); } finally { setWorking(false); } };
  const passkey = () => run(async () => {
    const challenge = await api('/api/auth/enrollment/webauthn/challenge', { method: 'POST', body: '{}' });
    const publicKey = challenge.publicKey; publicKey.challenge = decode(publicKey.challenge); publicKey.user.id = decode(publicKey.user.id);
    const credential = await navigator.credentials.create({ publicKey }); const response = credential.response;
    await api('/api/auth/enrollment/webauthn/register', { method: 'POST', body: JSON.stringify({ id: credential.id, rawId: b64url(credential.rawId), type: credential.type, response: { clientDataJSON: b64url(response.clientDataJSON), attestationObject: b64url(response.attestationObject), transports: response.getTransports?.() || [] } }) });
    setStep('recovery');
  });
  return <main className="login-container"><section className="login-card"><h1 className="login-title">{returningOwner ? 'Welcome back' : 'Secure enrollment'}</h1>
    {step === 'consent' && <><p>{returningOwner ? 'We have updated our Terms and Privacy Notice. Accept the current documents to renew your receipt and return to your existing VVAULT.' : 'Review and accept VVAULT’s Terms of Service and Privacy Notice before your personal vault is created.'}</p><button disabled={working} onClick={() => run(async () => { const result = await api('/api/auth/enrollment/consents', { method: 'POST', body: '{}' }); if (result.legacy_continuity) { window.location.assign('/'); return; } setStep('passkey'); })}>Accept Terms and Privacy</button></>}
    {step === 'passkey' && <button disabled={working} onClick={passkey}>Register a passkey</button>}
    {step === 'recovery' && <button disabled={working} onClick={() => run(async () => { const result = await api('/api/auth/enrollment/recovery-codes', { method: 'POST', body: '{}' }); setCodes(result.recovery_codes || []); setStep('activate'); })}>Generate recovery codes</button>}
    {codes.length > 0 && <><p>Save these codes offline. They are shown once.</p><pre>{codes.join('\n')}</pre></>}
    {step === 'activate' && <button disabled={working} onClick={() => run(async () => { await api('/api/auth/enrollment/activate', { method: 'POST', body: '{}' }); window.location.assign('/'); })}>Activate this trusted device</button>}
    {error && <p className="error-message">{error}</p>}
  </section></main>;
}
