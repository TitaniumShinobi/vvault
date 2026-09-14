export async function submitCredentials(signIn, form, options = {}, timeoutMs = 15000) {
  const headers = { 'Content-Type': 'application/json' };
  const body = JSON.stringify(signIn
    ? { email: form.email, password: form.password }
    : { name: form.name, email: form.email, password: form.password,
        confirmPassword: form.confirmPassword, agreeToTerms: form.agreeToTerms === true,
        invitation: options.invitation || '', turnstileToken: options.turnstileToken || '' });
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(signIn ? '/api/auth/login' : '/api/auth/register', {
          method: 'POST', credentials: 'same-origin', headers, body, signal: controller.signal,
        });
        const result = await response.json();
        if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Invalid authentication response. Please retry.');
        if (!response.ok || result.success !== true) throw new Error(result.error || 'Authentication failed. Please retry.');
        return result;
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('Authentication request timed out. Please retry.'));
          controller.abort();
        }, timeoutMs);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

export function completeCredentialAuthentication(result, onLogin, navigate) {
  if (result.state === 'AUTHENTICATED' && result.user?.id) {
    onLogin(result.user);
    return;
  }
  if (result.state === 'ENROLLMENT_REQUIRED' || result.state === 'USER_DECISION_REQUIRED') {
    navigate('/?oauth_pending=1');
    return;
  }
  throw new Error(result.error || 'Authentication requires another step. Please retry or sign in again.');
}
