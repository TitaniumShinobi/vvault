import React, { useEffect, useState } from 'react';

const decodeBase64Url = (value) => {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
};

const encodeBase64Url = (value) => {
  const bytes = new Uint8Array(value);
  let binary = '';
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

const requestJson = async (path, options = {}) => {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Enrollment request failed');
  return payload;
};

export default function EnrollmentFlow({ onComplete }) {
  const [status, setStatus] = useState(null);
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [recoveryCode, setRecoveryCode] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const refresh = async () => {
    const next = await requestJson('/api/auth/enrollment/status');
    setStatus(next);
    return next;
  };
  useEffect(() => { refresh().catch((failure) => setError(failure.message)); }, []);
  const run = async (operation) => {
    setWorking(true); setError('');
    try { await operation(); await refresh(); } catch (failure) { setError(failure.message); } finally { setWorking(false); }
  };
  const registerSecurityKey = () => run(async () => {
    if (!window.PublicKeyCredential || !navigator.credentials) throw new Error('This browser does not support WebAuthn');
    const challenge = await requestJson('/api/auth/enrollment/webauthn/challenge', { method: 'POST', body: '{}' });
    const publicKey = challenge.publicKey;
    publicKey.challenge = decodeBase64Url(publicKey.challenge);
    publicKey.user.id = decodeBase64Url(publicKey.user.id);
    const credential = await navigator.credentials.create({ publicKey });
    const response = credential.response;
    await requestJson('/api/auth/enrollment/webauthn/register', {
      method: 'POST',
      body: JSON.stringify({ id: credential.id, rawId: encodeBase64Url(credential.rawId), type: credential.type,
        response: { clientDataJSON: encodeBase64Url(response.clientDataJSON), attestationObject: encodeBase64Url(response.attestationObject),
          transports: typeof response.getTransports === 'function' ? response.getTransports() : [] } }),
    });
  });
  const recoverDevice = () => run(async () => {
    await requestJson(`/api/auth/devices/${status.device_id}/recover`, { method: 'POST', body: JSON.stringify({ recovery_code: recoveryCode }) });
    const verified = await requestJson('/api/auth/verify');
    window.history.replaceState({}, document.title, window.location.pathname);
    onComplete(verified.user);
  });
  const activate = () => run(async () => {
    await requestJson(`/api/auth/enrollment/devices/${status.device_id}/approve`, { method: 'POST', body: '{}' });
    const verified = await requestJson('/api/auth/verify');
    window.history.replaceState({}, document.title, window.location.pathname);
    onComplete(verified.user);
  });
  if (!status) return <main className="login-container"><div className="login-card">Loading secure enrollment…</div></main>;
  const ready = status.consents_complete && status.webauthn_complete && status.recovery_complete;
  return <main className="login-container"><section className="login-card"><h1 className="login-title">Secure VVAULT enrollment</h1>
    {!status.consents_complete && <button disabled={working} onClick={() => run(() => requestJson('/api/auth/enrollment/consents', { method: 'POST', body: '{}' }))}>Accept current Terms and Privacy</button>}
    {status.consents_complete && !status.webauthn_complete && <button disabled={working} onClick={registerSecurityKey}>Register security key</button>}
    {status.webauthn_complete && !status.recovery_complete && <button disabled={working} onClick={() => run(async () => { const result = await requestJson('/api/auth/enrollment/recovery-codes', { method: 'POST', body: '{}' }); setRecoveryCodes(result.recovery_codes || []); })}>Create recovery codes</button>}
    {recoveryCodes.length > 0 && <pre>{recoveryCodes.join('\n')}</pre>}
    {ready && status.device_status !== 'TRUSTED' && <><p>Your device needs approval from an existing trusted VVAULT session. You may instead use one recovery code.</p><input type="password" autoComplete="one-time-code" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} placeholder="One-time recovery code"/><button disabled={working || !recoveryCode} onClick={recoverDevice}>Recover this device</button></>}
    {status.device_status === 'TRUSTED' && <button disabled={working} onClick={activate}>Activate this approved device</button>}
    {error && <div className="error-message">{error}</div>}
  </section></main>;
}
