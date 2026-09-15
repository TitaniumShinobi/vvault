import test from 'node:test';
import assert from 'node:assert/strict';
import { oauthProviders, checkOAuthProvider } from '../src/utils/oauthEntryPoints.mjs';

test('all four provider choices remain available and unavailable responses retain recovery text', async () => {
  assert.deepEqual(oauthProviders.map(p => p.id), ['google', 'github', 'microsoft', 'apple']);
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ available: false, error: 'Provider not configured. Choose another method.' }) });
    for (const provider of oauthProviders) {
      await assert.rejects(checkOAuthProvider(provider.id), /not configured/);
    }
  } finally { globalThis.fetch = original; }
});

test('configured Google preflight preserves same-origin authentication and allows continuation', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/auth/providers/google/health');
      assert.equal(options.credentials, 'same-origin');
      return { ok: true, json: async () => ({ available: true }) };
    };
    await checkOAuthProvider('google');
  } finally { globalThis.fetch = original; }
});

test('transport and malformed-response errors end preflight without navigation', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error('offline'); };
    await assert.rejects(checkOAuthProvider('google'), /offline/);
    globalThis.fetch = async () => ({ ok: true, json: async () => { throw new Error('Invalid response'); } });
    await assert.rejects(checkOAuthProvider('google'), /Invalid response/);
  } finally { globalThis.fetch = original; }
});

test('stalled provider lookup aborts with a recoverable timeout', async () => {
  const originalFetch = globalThis.fetch;
  const originalTimer = globalThis.setTimeout;
  try {
    globalThis.setTimeout = callback => originalTimer(callback, 1);
    globalThis.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    });
    await assert.rejects(checkOAuthProvider('google'), /timed out/);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalTimer;
  }
});
