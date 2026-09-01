const SESSION_EXPIRED_EVENT = 'vvault-session-expired';

function clearSession() {
  // Native VVAULT sessions are HttpOnly cookies; no browser token is retained.
}

function dispatchExpired() {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

export async function authFetch(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: 'same-origin' });

  if (response.status === 401) {
    clearSession();
    dispatchExpired();
  }

  return response;
}

export async function validateSession() {
  try {
    const response = await fetch('/api/auth/verify', { credentials: 'same-origin' });
    if (response.status === 401) {
      clearSession();
      dispatchExpired();
      return false;
    }
    return response.ok;
  } catch (e) {
    return true;
  }
}

export { SESSION_EXPIRED_EVENT };
