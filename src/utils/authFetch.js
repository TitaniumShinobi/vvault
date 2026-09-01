// Browser authentication is carried only by the HttpOnly VVAULT session
// cookie. Never recover bearer credentials from browser storage, query strings,
// or application state: those locations are readable by injected browser code.
const SESSION_EXPIRED_EVENT = 'vvault-session-expired';

function dispatchExpired() {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

export async function authFetch(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: 'same-origin' });

  if (response.status === 401) {
    dispatchExpired();
  }

  return response;
}

export async function validateSession() {
  try {
    const response = await fetch('/api/auth/verify', { credentials: 'same-origin' });
    if (response.status === 401) {
      dispatchExpired();
      return false;
    }
    return response.ok;
  } catch (_) {
    return false;
  }
}

export { SESSION_EXPIRED_EVENT };
