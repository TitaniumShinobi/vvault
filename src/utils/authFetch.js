const SESSION_EXPIRED_EVENT = 'vvault-session-expired';
const VVAULT_DEPENDENCY_EVENT = 'vvault-dependency-degraded';
const VVAULT_READY_EVENT = 'vvault-runtime-ready';
const POSTGREST_JWT_KEY = 'vvault_postgrest_jwt';
const POSTGREST_JWT_EXP_KEY = 'vvault_postgrest_jwt_exp';
const OUTAGE_DEDUPE_WINDOW_MS = 4000;
let hasDispatchedSessionExpired = false;
let sessionExpired = false;
const outageEmitTimestamps = new Map();
let vvaultRuntimeState = {
  ready: false,
  status: 'unknown',
  body_database: {},
  storage: {},
  auth: {},
  canonical: false,
  error_code: null,
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
    return 'VVAULT dependency is temporarily unreachable. Please retry in a few minutes.';
  }
  return trimmed.length > 220 ? `${trimmed.slice(0, 217)}...` : trimmed;
}

function normalizeNonJsonError(text, response, fallback = 'Request failed.') {
  return sanitizeErrorText(text, response, fallback);
}

function isVvaultDependencyText(text) {
  const lowered = String(text || '').toLowerCase();
  if (!lowered) return false;
  return (
    lowered.includes('error code 522') ||
    lowered.includes('"code": 522') ||
    lowered.includes("'code': 522") ||
    lowered.includes('cloudflare') ||
    lowered.includes('connection timed out') ||
    lowered.includes('request timeout')
  );
}

function normalizeDependencyErrorCode(errorCode) {
  const code = String(errorCode || '').trim();
  if (!code) return 'VVAULT_DEPENDENCY_UNAVAILABLE';
  return code;
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

async function maybeDispatchVvaultDependencyEvent(response, url) {
  try {
    if (!response || (response.status !== 200 && response.status !== 503)) return;
    const payload = await readResponsePayload(response.clone(), '');
    const isOutage = (
      payload?.degraded === true ||
      payload?.vvault_available === false ||
      payload?.body_database?.ready === false
    );
    if (!isOutage) return;
    const message = sanitizeErrorText(
      payload?.message || payload?.error || 'VVAULT local dependency is temporarily unavailable.',
      response,
      'VVAULT local dependency is temporarily unavailable.'
    );
    const errorCode = normalizeDependencyErrorCode(payload?.error_code || payload?.body_database?.error_code);
    const signature = `${errorCode}|${url}|${response.status}|${message}`;
    if (shouldSuppressOutageEmit(signature)) return;
    window.dispatchEvent(new CustomEvent(VVAULT_DEPENDENCY_EVENT, {
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

function updateVvaultRuntimeState(payload = {}) {
  const bodyDatabase = payload?.body_database || {};
  const storage = payload?.storage || {};
  const auth = payload?.auth || {};
  const ready = payload?.ready === true && bodyDatabase?.ready !== false;
  vvaultRuntimeState = {
    ready,
    status: payload?.status || bodyDatabase?.status || (ready ? 'ready' : 'not_ready'),
    body_database: bodyDatabase,
    storage,
    auth,
    canonical: ready && bodyDatabase?.status !== 'unhealthy',
    error_code: bodyDatabase?.error_code || payload?.error_code || null,
  };
  window.dispatchEvent(new CustomEvent(VVAULT_READY_EVENT, {
    detail: { ...vvaultRuntimeState },
  }));
  return vvaultRuntimeState;
}

export async function refreshVvaultRuntimeState(options = {}) {
  try {
    const response = await fetchWithOptionalTimeout('/api/ready', { credentials: 'include' }, options.timeoutMs);
    const payload = await readResponsePayload(response, 'Could not verify VVAULT readiness.');
    return updateVvaultRuntimeState(payload);
  } catch (error) {
    return updateVvaultRuntimeState({
      ready: false,
      status: 'not_ready',
      body_database: {
        ready: false,
        status: 'unavailable',
        error_code: 'VVAULT_READY_CHECK_FAILED',
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
    error: 'Local VVAULT session expired. Sign in again after VVAULT auth storage is available.',
    error_code: 'SESSION_EXPIRED',
    url,
  }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });
}

function vvaultWriteBlockedResponse(url) {
  return new Response(JSON.stringify({
    success: false,
    vvault_available: false,
    degraded: true,
    canonical: false,
    storage_mode: 'vvault_body',
    status: vvaultRuntimeState.status,
    body_database: vvaultRuntimeState.body_database,
    error_code: vvaultRuntimeState.error_code || 'VVAULT_NOT_READY',
    message: 'VVAULT local persistence is unavailable. Canonical writes are blocked until local storage is healthy.',
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
    if (isVvaultDependencyText(text)) {
      return {
        vvault_available: false,
        degraded: true,
        error_code: 'VVAULT_DEPENDENCY_UNAVAILABLE',
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
  const runtime = await refreshVvaultRuntimeState({ timeoutMs: options.readyTimeoutMs });
  if (!runtime.ready) {
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
  await refreshPostgrestJwt({ timeoutMs: options.jwtTimeoutMs || options.readyTimeoutMs });
  markSessionActive();
  return data.user;
}

export function clearStoredPostgrestJwt() {
  localStorage.removeItem(POSTGREST_JWT_KEY);
  localStorage.removeItem(POSTGREST_JWT_EXP_KEY);
}

export function getStoredPostgrestJwt() {
  const token = localStorage.getItem(POSTGREST_JWT_KEY);
  const exp = Number.parseInt(localStorage.getItem(POSTGREST_JWT_EXP_KEY) || '', 10);
  if (!token || !Number.isFinite(exp)) return null;
  const now = Math.floor(Date.now() / 1000);
  if (exp <= now + 30) {
    clearStoredPostgrestJwt();
    return null;
  }
  return token;
}

export async function refreshPostgrestJwt(options = {}) {
  try {
    const response = await fetchWithOptionalTimeout('/api/auth/postgrest-token', {
      method: 'POST',
      credentials: 'include',
    }, options.timeoutMs);
    const data = await readResponsePayload(response, 'Could not issue direct API token.');
    if (!response.ok || !data.ok || !data.token || !data.exp) {
      clearStoredPostgrestJwt();
      return null;
    }
    localStorage.setItem(POSTGREST_JWT_KEY, data.token);
    localStorage.setItem(POSTGREST_JWT_EXP_KEY, String(data.exp));
    return data.token;
  } catch (e) {
    clearStoredPostgrestJwt();
    return null;
  }
}

export async function postgrestFetch(url, options = {}) {
  const token = getStoredPostgrestJwt() || await refreshPostgrestJwt();
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers });
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
  clearStoredPostgrestJwt();
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
    const runtime = await refreshVvaultRuntimeState();
    if (!runtime.ready) {
      return vvaultWriteBlockedResponse(url);
    }
  }

  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });
  void maybeDispatchVvaultDependencyEvent(response, url);

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

export { SESSION_EXPIRED_EVENT, VVAULT_DEPENDENCY_EVENT, VVAULT_READY_EVENT };
