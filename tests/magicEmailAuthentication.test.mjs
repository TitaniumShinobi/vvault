import test from 'node:test';
import assert from 'node:assert/strict';
import { takeMagicLinkToken, requestMagicLink, consumeMagicLink } from '../src/utils/magicEmailAuthentication.mjs';
import { completeCredentialAuthentication } from '../src/utils/credentialAuthentication.mjs';

test('fragment bearer token is removed before redemption request and never enters request URL', async () => {
  const events = [];
  const token = takeMagicLinkToken({ hash: '#magic_link=secret-token', pathname: '/', search: '?mode=signin' },
    { replaceState: (...args) => events.push(['scrub', args[2]]) });
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    events.push(['post', url]);
    assert.equal(url, '/api/auth/email-magic-links/consume');
    assert.deepEqual(JSON.parse(options.body), { token: 'secret-token' });
    assert.equal(options.credentials, 'same-origin');
    return { ok: true, json: async () => ({ success: true, state: 'USER_DECISION_REQUIRED' }) };
  };
  try {
    const result = await consumeMagicLink(token);
    let target;
    completeCredentialAuthentication(result, () => assert.fail('Pending email identity must not log in'), (path) => { target = path; });
    assert.equal(target, '/?oauth_pending=1');
    assert.deepEqual(events, [['scrub', '/?mode=signin'], ['post', '/api/auth/email-magic-links/consume']]);
  } finally { globalThis.fetch = original; }
});

test('missing fragment leaves normal credentials and provider login untouched', () => {
  assert.equal(takeMagicLinkToken({ hash: '', pathname: '/', search: '' },
    { replaceState: () => assert.fail('No fragment must not rewrite location') }), null);
});

test('empty link is scrubbed and explicitly rejected', () => {
  let scrubbed = false;
  assert.throws(() => takeMagicLinkToken({ hash: '#magic_link=', pathname: '/', search: '' },
    { replaceState: () => { scrubbed = true; } }), /incomplete/);
  assert.equal(scrubbed, true);
});

test('request carries email and optional invitation without password or mode replacement', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/auth/email-magic-links');
    assert.deepEqual(JSON.parse(options.body), { email: 'new@example.test', invitation: 'invitation', intent: 'signup' });
    return { ok: true, json: async () => ({ success: true }) };
  };
  try { await requestMagicLink(' new@example.test ', ' invitation ', 'signup'); }
  finally { globalThis.fetch = original; }
});

test('expired or replayed link yields an explicit recoverable error', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ success: false, error: 'Link expired or already used' }) });
  try { await assert.rejects(consumeMagicLink('used'), /expired or already used/); }
  finally { globalThis.fetch = original; }
});

test('stalled delivery ends in a retryable timeout', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = () => new Promise(() => {});
  try { await assert.rejects(requestMagicLink('user@example.test', '', 'login', 5), /timed out/); }
  finally { globalThis.fetch = original; }
});


test('fragment is redeemed at most once after location scrubbing', () => {
  const location = { hash: '#magic_link=once', pathname: '/', search: '' };
  const history = { replaceState: () => { location.hash = ''; } };
  assert.equal(takeMagicLinkToken(location, history), 'once');
  assert.equal(takeMagicLinkToken(location, history), null);
});
