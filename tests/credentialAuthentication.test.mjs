import test from 'node:test';
import assert from 'node:assert/strict';
import { submitCredentials, completeCredentialAuthentication } from '../src/utils/credentialAuthentication.mjs';

test('login and signup POST distinct credential payloads without bypassing enrollment', async () => {
  const original = globalThis.fetch;
  const requests = [];
  const form = { email: 'person@example.test', name: 'Person', password: 'long-password', confirmPassword: 'long-password', agreeToTerms: true };
  try {
    globalThis.fetch = async (path, options) => {
      requests.push({ path, options });
      return { ok: true, json: async () => ({ success: true, state: 'ENROLLMENT_REQUIRED' }) };
    };
    const login = await submitCredentials(true, form);
    assert.equal(requests[0].path, '/api/auth/login');
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(requests[0].options.credentials, 'same-origin');
    assert.deepEqual(JSON.parse(requests[0].options.body), { email: form.email, password: form.password });
    await submitCredentials(false, form, { invitation: 'invite', turnstileToken: 'verified' });
    assert.equal(requests[1].path, '/api/auth/register');
    assert.deepEqual(JSON.parse(requests[1].options.body), { ...form, invitation: 'invite', turnstileToken: 'verified' });
    let destination;
    completeCredentialAuthentication(login, () => assert.fail('pending user cannot authenticate'), path => { destination = path; });
    assert.equal(destination, '/?oauth_pending=1');
  } finally { globalThis.fetch = original; }
});

test('unknown states and failed requests surface recoverable errors', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ success: false, error: 'Invalid credentials' }) });
    await assert.rejects(submitCredentials(true, { email: 'a', password: 'b' }), /Invalid credentials/);
    globalThis.fetch = async () => ({ ok: true, json: async () => null });
    await assert.rejects(submitCredentials(true, {}), /Invalid authentication response/);
    assert.throws(() => completeCredentialAuthentication({ state: 'UNKNOWN' }, () => {}, () => {}), /another step/);
    assert.throws(() => completeCredentialAuthentication({ state: 'AUTHENTICATED' }, () => {}, () => {}), /another step/);
    globalThis.fetch = () => new Promise(() => {});
    await assert.rejects(submitCredentials(true, {}, {}, 5), /timed out/);
  } finally { globalThis.fetch = original; }
});
