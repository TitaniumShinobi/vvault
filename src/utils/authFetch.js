const SESSION_EXPIRED_EVENT = 'vvault-session-expired';
const SUPABASE_OUTAGE_EVENT = 'vvault-supabase-outage';
const SUPABASE_CONNECTION_EVENT = 'vvault-supabase-connection';
const OUTAGE_DEDUPE_WINDOW_MS = 4000;
let hasDispatchedSessionExpired = false;
let sessionExpired = false;
const outageEmitTimestamps = new Map();
let supabaseConnectionState = {
  connected: false,
  connection_state: 'unknown',
  canonical: false,
  storage_mode: 'none',
  recovery_proven_at: null,
};

async function fetchWithOptionalTimeout(url, options = {}, timeoutMs = null) {
  if (!timeoutMs) {
    return fetch(url, options);
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

function sanitizeErrorText(text, response, fallback = 'Request failed.') {
  const trimmed = String(text || '').trim();
  if (!trimmed) {
    return response?.ok ? fallback : `${fallback} (${response?.status || 'unknown status'})`;
  }
  const lowered = trimmed.toLowerCase();
  if (trimmed.startsWith('Error occurred while trying to proxy')) {
    return 'VVAULT backend is still starting. Please wait a moment and try again.';
  }
  if (lowered.includes('<!doctype html') || lowered.includes('<html')) {
    return 'Upstream service returned a non-JSON error payload. Please retry in a moment.';
  }
  if (lowered.includes('error code 522') || lowered.includes('"code": 522') || lowered.includes("'code': 522")) {
    return 'Supabase is temporarily unreachable (522 timeout). Please retry in a few minutes.';
  }
  return trimmed.length > 220 ? `${trimmed.slice(0, 217)}...` : trimmed;
}

function normalizeNonJsonError(text, response, fallback = 'Request failed.') {
  return sanitizeErrorText(text, response, fallback);
}

function isSupabaseOutageText(text) {
  const lowered = String(text || '').toLowerCase();
  if (!lowered) return false;
  return (
    lowered.includes('error code 522') ||
    lowered.includes('"code": 522') ||
    lowered.includes("'code': 522") ||
    lowered.includes('cloudflare') ||
    lowered.includes('supabase.co') ||
    lowered.includes('connection timed out') ||
    lowered.includes('request timeout')
  );
}

function shouldSuppressOutageEmit(signature) {
  const now = Date.now();
  const last = outageEmitTimestamps.get(signature);
  if (last && now - last < OUTAGE_DEDUPE_WINDOW_MS) {
    return true;
  }
  outageEmitTimestamps.set(signature, now);
  return false;
}

async function maybeDispatchSupabaseOutage(response, url) {
  try {
    if (!response || (response.status !== 200 && response.status !== 503)) return;
    const payload = await readResponsePayload(response.clone(), '');
    const isOutage = payload?.degraded === true || payload?.supabase_available === false || payload?.error_code === 'SUPABASE_TIMEOUT_522';
    if (!isOutage) return;
    const message = sanitizeErrorText(payload?.message || payload?.error || 'Supabase is temporarily unavailable.', response, 'Supabase is temporarily unavailable.');
    const errorCode = payload?.error_code || 'SUPABASE_TIMEOUT_522';
    const signature = `${errorCode}|${url}|${response.status}|${message}`;
    if (shouldSuppressOutageEmit(signature)) return;
    window.dispatchEvent(new CustomEvent(SUPABASE_OUTAGE_EVENT, {
      detail: {
        url,
        status: response.status,
        error_code: errorCode,
        message,
      },
    }));
  } catch (e) {
    // Swallow notification parsing errors; caller still handles the response.
  }
}

function updateSupabaseConnectionState(payload = {}) {
  const connection = payload?.supabase?.connection || payload?.supabase_connection || payload?.connection || {};
  supabaseConnectionState = {
    connected: payload?.ready === true || connection.connection_state === 'connected',
    connection_state: connection.connection_state || payload?.status || 'unknown',
    canonical: connection.canonical === true,
    storage_mode: connection.storage_mode || 'none',
    recovery_proven_at: connection.recovery_proven_at || null,
    error_code: connection.last_error_code || payload?.error_code || null,
  };
  window.dispatchEvent(new CustomEvent(SUPABASE_CONNECTION_EVENT, {
    detail: { ...supabaseConnectionState },
  }));
  return supabaseConnectionState;
}

export async function refreshSupabaseConnectionState(options = {}) {
  try {
    const response = await fetchWithOptionalTimeout('/api/ready', { credentials: 'include' }, options.timeoutMs);
    const payload = await readResponsePayload(response, 'Could not verify Supabase readiness.');
    return updateSupabaseConnectionState(payload);
  } catch (error) {
    return updateSupabaseConnectionState({
      ready: false,
      supabase: {
        connection: {
          connection_state: 'blocked',
          canonical: false,
          storage_mode: 'none',
          last_error_code: 'SUPABASE_READY_CHECK_FAILED',
        },
      },
    });
  }
}

function isMutatingRequest(options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  return !['GET', 'HEAD', 'OPTIONS'].includes(method);
}

function isAuthenticatedApiRequest(url) {
  const path = String(url || '');
  return path.startsWith('/api/vault/') || path.startsWith('/api/chatty/');
}

function localSessionExpiredResponse(url) {
  return new Response(JSON.stringify({
    success: false,
    error: 'Local VVAULT session expired. Sign in again after Supabase identity authority recovers.',
    error_code: 'SESSION_EXPIRED',
    url,
  }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });
}

function supabaseWriteBlockedResponse(url) {
  return new Response(JSON.stringify({
    success: false,
    supabase_available: false,
    degraded: true,
    canonical: false,
    storage_mode: 'none',
    connection_state: supabaseConnectionState.connection_state,
    error_code: supabaseConnectionState.error_code || 'SUPABASE_NOT_CONNECTED',
    message: 'Supabase is not currently connected. Canonical writes are blocked until recovery is proven.',
    url,
  }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

export async function readResponsePayload(response, fallback = 'Request failed.') {
  const text = await response.text();
  if (!text) return {};
  try {
    const payload = JSON.parse(text);
    if (payload && typeof payload.error === 'string') {
      payload.error = sanitizeErrorText(payload.error, response, fallback);
    }
    if (payload && typeof payload.message === 'string' && payload.message.length > 240) {
      payload.message = sanitizeErrorText(payload.message, response, fallback);
    }
    return payload;
  } catch (error) {
    const normalizedMessage = normalizeNonJsonError(text, response, fallback);
    if (isSupabaseOutageText(text)) {
      return {
        supabase_available: false,
        degraded: true,
        error_code: 'SUPABASE_TIMEOUT_522',
        message: normalizedMessage,
      };
    }
    return {
      success: false,
      error: normalizedMessage,
      rawText: text,
      parseError: error instanceof Error ? error.message : 'Invalid JSON response',
    };
  }
}

export function getResponseErrorMessage(response, payload, fallback = 'Request failed.') {
  if (payload && typeof payload.error === 'string' && payload.error.trim()) {
    return sanitizeErrorText(payload.error.trim(), response, fallback);
  }
  if (payload && typeof payload.message === 'string' && payload.message.trim() && response?.status >= 400) {
    return sanitizeErrorText(payload.message.trim(), response, fallback);
  }
  return response?.ok ? fallback : `${fallback} (${response?.status || 'unknown status'})`;
}

/**
 * After standalone @auth login/OAuth (HttpOnly cookie), mint a Flask vault Bearer token.
 * @returns {Promise<object|null>} user object or null
 */
export async function finalizeAuthServiceLogin(options = {}) {
  const connection = await refreshSupabaseConnectionState({ timeoutMs: options.readyTimeoutMs });
  if (!connection.connected) {
    return null;
  }
  const response = await fetch('/api/vault/session-bridge', {
    method: 'POST',
    credentials: 'include',
  });
  const data = await readResponsePayload(response, 'Could not start vault session.');
  if (!response.ok || !data.success || !data.token || !data.user) {
    return null;
  }
  localStorage.setItem('vvault_user', JSON.stringify(data.user));
  localStorage.setItem('vvault_token', data.token);
  markSessionActive();
  return data.user;
}

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

export function markSessionActive() {
  hasDispatchedSessionExpired = false;
  sessionExpired = false;
}

function dispatchExpired() {
  if (hasDispatchedSessionExpired) return;
  hasDispatchedSessionExpired = true;
  sessionExpired = true;
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

export async function authFetch(url, options = {}) {
  const token = getToken();
  if (isAuthenticatedApiRequest(url) && (!token || sessionExpired)) {
    clearSession();
    dispatchExpired();
    return localSessionExpiredResponse(url);
  }

  if (isMutatingRequest(options) && url !== '/api/ready') {
    const connection = await refreshSupabaseConnectionState();
    if (!connection.connected) {
      return supabaseWriteBlockedResponse(url);
    }
  }

  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });
  void maybeDispatchSupabaseOutage(response, url);

  if (response.status === 401) {
    clearSession();
    dispatchExpired();
  } else if (response.ok && token) {
    markSessionActive();
  }

  return response;
}

export async function validateSession(options = {}) {
  const token = getToken();
  if (!token) return false;

  try {
    const response = await fetchWithOptionalTimeout('/api/vault/user-info', {
      headers: { 'Authorization': `Bearer ${token}` }
    }, options.timeoutMs);
    if (response.status === 401) {
      clearSession();
      dispatchExpired();
      return false;
    }
    if (response.ok) {
      markSessionActive();
      return true;
    }
    // Do not auto-logout on backend faults (5xx) or transient upstream issues.
    // We only hard-expire local auth state on a confirmed 401.
    return true;
  } catch (e) {
    return true;
  }
}

export { SESSION_EXPIRED_EVENT, SUPABASE_OUTAGE_EVENT, SUPABASE_CONNECTION_EVENT };
