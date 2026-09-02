import React, { useCallback, useEffect, useState } from 'react';
import './EnrollmentFlow.css';

const b64url = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const decode = (value) => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), (c) => c.charCodeAt(0));

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Request was denied');
  return payload;
}

const legalDocuments = [
  { name: 'Terms of Service', href: '/terms-of-service.html' },
  { name: 'Privacy Notice', href: '/privacy-notice.html' },
  { name: 'EECCD Disclosure', href: '/european-electronic-communications-code-disclosure.html' },
];

function LegalDocuments() {
  return <ul className="lifecycle-document-list" aria-label="Current legal documents">
    {legalDocuments.map((document) => <li key={document.href}><a href={document.href} target="_blank" rel="noopener noreferrer">{document.name}</a></li>)}
  </ul>;
}

function RecoveryCode({ onRecovered, run, working }) {
  const [recoveryCode, setRecoveryCode] = useState('');
  return <form onSubmit={(event) => {
    event.preventDefault();
    run(async () => { await api('/api/auth/devices/recover', { method: 'POST', body: JSON.stringify({ recovery_code: recoveryCode }) }); onRecovered(); });
  }}>
    <label htmlFor="recovery-code">Recovery code</label>
    <input id="recovery-code" autoComplete="one-time-code" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} required disabled={working} />
    <button type="submit" disabled={working || !recoveryCode.trim()}>Use recovery code</button>
  </form>;
}

export default function EnrollmentFlow({ requestedMode = 'enrollment' }) {
  const [status, setStatus] = useState(null);
  const [step, setStep] = useState('loading');
  const [codes, setCodes] = useState([]);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [transferCode, setTransferCode] = useState('');

  const reloadStatus = useCallback(async () => {
    try {
      const current = await api('/api/auth/enrollment/status');
      if (!current.pending) throw new Error('This secure checkpoint has expired. Sign in again to continue.');
      setStatus(current);
      setStep(current.session_kind === 'PENDING_DEVICE' ? 'device' : 'consent');
    } catch (failure) {
      setError(failure.message || 'This secure checkpoint is unavailable.');
      setStep('expired');
    }
  }, []);

  useEffect(() => { reloadStatus(); }, [reloadStatus]);
  const run = async (action) => { setWorking(true); setError(''); try { await action(); } catch (failure) { setError(failure.message); } finally { setWorking(false); } };
  const complete = () => window.location.assign('/');
  const mode = status?.session_kind === 'PENDING_DEVICE' ? 'device' : status?.session_kind === 'LEGACY' ? 'recertification' : requestedMode;

  const passkey = () => run(async () => {
    const challenge = await api('/api/auth/enrollment/webauthn/challenge', { method: 'POST', body: '{}' });
    const publicKey = challenge.publicKey; publicKey.challenge = decode(publicKey.challenge); publicKey.user.id = decode(publicKey.user.id);
    const credential = await navigator.credentials.create({ publicKey }); const response = credential.response;
    await api('/api/auth/enrollment/webauthn/register', { method: 'POST', body: JSON.stringify({ id: credential.id, rawId: b64url(credential.rawId), type: credential.type, response: { clientDataJSON: b64url(response.clientDataJSON), attestationObject: b64url(response.attestationObject), transports: response.getTransports?.() || [] } }) });
    setStep('recovery');
  });

  const assertPasskey = () => run(async () => {
    const challenge = await api('/api/auth/devices/webauthn/challenge', { method: 'POST', body: '{}' });
    const publicKey = challenge.publicKey; publicKey.challenge = decode(publicKey.challenge);
    if (publicKey.allowCredentials) publicKey.allowCredentials = publicKey.allowCredentials.map((credential) => ({ ...credential, id: decode(credential.id) }));
    const credential = await navigator.credentials.get({ publicKey }); const response = credential.response;
    await api('/api/auth/devices/webauthn/assert', { method: 'POST', body: JSON.stringify({ id: credential.id, rawId: b64url(credential.rawId), type: credential.type, response: { clientDataJSON: b64url(response.clientDataJSON), authenticatorData: b64url(response.authenticatorData), signature: b64url(response.signature), userHandle: response.userHandle ? b64url(response.userHandle) : null } }) });
    complete();
  });

  const startTransfer = () => run(async () => {
    const result = await api('/api/auth/devices/transfer/start', { method: 'POST', body: '{}' });
    setTransferCode(result.transfer_code || '');
  });

  const title = mode === 'device' ? 'Verify this device' : mode === 'recertification' ? 'Welcome back' : 'Secure enrollment';
  const subtitle = mode === 'device' ? 'This browser is not yet trusted. Verify it to continue to your existing VVAULT.' : mode === 'recertification' ? 'We’ve updated our legal documents. Accept the current versions to return to your existing VVAULT.' : 'Review the current documents before creating your personal VVAULT.';

  return <main className="lifecycle-container"><section className="lifecycle-card" aria-live="polite">
    <p className="lifecycle-eyebrow">VVAULT security checkpoint</p><h1>{title}</h1><p>{subtitle}</p>
    {step === 'loading' && <p>Checking this secure session…</p>}
    {step === 'expired' && <><p>This link or device-verification session is no longer active.</p><a className="lifecycle-link" href="/">Return to sign in</a></>}
    {step === 'consent' && <><LegalDocuments /><button disabled={working} onClick={() => run(async () => {
      const result = await api('/api/auth/enrollment/consents', { method: 'POST', body: '{}' });
      if (mode === 'recertification' || result.legacy_continuity) return complete();
      setStep('passkey');
    })}>Accept all current documents</button><p className="lifecycle-note">Acceptance updates legal receipts only. It does not change your owner identity or Vault.</p></>}
    {step === 'passkey' && <><p>Create a passkey for future sign-ins on your devices.</p><button disabled={working} onClick={passkey}>Create passkey</button></>}
    {step === 'recovery' && <><p>Keep recovery codes offline. They are displayed once.</p><button disabled={working} onClick={() => run(async () => { const result = await api('/api/auth/enrollment/recovery-codes', { method: 'POST', body: '{}' }); setCodes(result.recovery_codes || []); setStep('activate'); })}>Generate recovery codes</button></>}
    {codes.length > 0 && <pre aria-label="Recovery codes">{codes.join('\n')}</pre>}
    {step === 'activate' && <><p>Trust this device to finish enrollment.</p><button disabled={working} onClick={() => run(async () => { await api('/api/auth/enrollment/activate', { method: 'POST', body: '{}' }); complete(); })}>Trust this device</button></>}
    {step === 'device' && <div className="device-options"><p>Device verification is separate from legal-document acceptance.</p><button disabled={working} onClick={assertPasskey}>Use an existing passkey</button><RecoveryCode working={working} run={run} onRecovered={complete} /><div className="transfer-option"><button disabled={working} onClick={startTransfer}>Get approval code</button>{transferCode && <><p>On an authenticated trusted device, approve this new device with:</p><code>{transferCode}</code><p className="lifecycle-note">This code expires shortly and does not reveal identity or Vault data.</p></>}</div></div>}
    {error && <p className="error-message" role="alert">{error}</p>}
  </section></main>;
}
