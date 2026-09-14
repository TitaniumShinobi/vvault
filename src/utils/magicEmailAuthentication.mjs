// Email proof uses the same enrollment/device completion contract as credentials.
export function takeMagicLinkToken(location, history) {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (!fragment.has('magic_link')) return null;
  const token = fragment.get('magic_link') || '';
  fragment.delete('magic_link');
  const rest = fragment.toString();
  history.replaceState(null, '', `${location.pathname}${location.search}${rest ? `#${rest}` : ''}`);
  if (!token) throw new Error('This email link is incomplete. Request another link.');
  return token;
}

async function postMagicLink(path, body, timeoutMs = 15000) {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(path, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal,
        });
        const result = await response.json();
        if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Invalid email authentication response. Please retry.');
        if (!response.ok || result.success !== true) throw new Error(result.error || 'Email authentication failed. Request another link.');
        return result;
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('Email authentication timed out. Request another link.'));
          controller.abort();
        }, timeoutMs);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

export function requestMagicLink(email, invitation = '', intent = 'login', timeoutMs) {
  return postMagicLink('/api/auth/email-magic-links', { email: email.trim(), invitation: invitation.trim(), intent }, timeoutMs);
}
export function consumeMagicLink(token, timeoutMs) {
  return postMagicLink('/api/auth/email-magic-links/consume', { token }, timeoutMs);
}
