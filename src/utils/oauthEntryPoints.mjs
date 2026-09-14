export const oauthProviders = Object.freeze([
  { id: 'google', label: 'Google' },
  { id: 'github', label: 'GitHub' },
  { id: 'microsoft', label: 'Microsoft' },
  { id: 'apple', label: 'Apple' },
]);

export async function checkOAuthProvider(provider) {
  if (!oauthProviders.some(entry => entry.id === provider)) throw new Error('Unknown sign-in provider.');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/auth/providers/${provider}/health`, { credentials: 'same-origin', signal: controller.signal });
    const result = await response.json();
    if (!response.ok || !result.available) {
      throw new Error(result.error || 'This provider is temporarily unavailable. Choose another sign-in method.');
    }
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Sign-in availability check timed out. Try again or choose another method.');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
