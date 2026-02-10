const SESSION_EXPIRED_EVENT = 'vvault-session-expired';

function getToken() {
  try {
    const savedUser = localStorage.getItem('vvault_user');
    if (savedUser) {
      const parsed = JSON.parse(savedUser);
      if (parsed.token) return parsed.token;
    }
  } catch (e) {}
  return localStorage.getItem('vvault_token') || null;
}

function clearSession() {
  localStorage.removeItem('vvault_user');
  localStorage.removeItem('vvault_token');
}

function dispatchExpired() {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

export async function authFetch(url, options = {}) {
  const token = getToken();
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    clearSession();
    dispatchExpired();
  }

  return response;
}

export async function validateSession() {
  const token = getToken();
  if (!token) return false;

  try {
    const response = await fetch('/api/vault/user-info', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
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
