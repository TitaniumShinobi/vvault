import test from 'node:test';
import assert from 'node:assert/strict';
import { requestEnrollmentJson } from '../src/utils/enrollmentRequest.mjs';

test('enrollment request recovers from network, server, and malformed responses', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error('Network offline'); };
    await assert.rejects(requestEnrollmentJson('/status'), /Network offline/);
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: 'Session expired' }) });
    await assert.rejects(requestEnrollmentJson('/status'), /Session expired/);
    globalThis.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError('bad JSON'); } });
    await assert.rejects(requestEnrollmentJson('/status'), /invalid response/);
    for (const payload of [null, false, []]) {
      globalThis.fetch = async () => ({ ok: true, json: async () => payload });
      await assert.rejects(requestEnrollmentJson('/status'), /invalid response/);
    }
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ device_status: 'TRUSTED' }) });
    assert.equal((await requestEnrollmentJson('/status')).device_status, 'TRUSTED');
  } finally { globalThis.fetch = original; }
});

test('enrollment request has a bounded terminal error even when fetch never settles', async () => {
  const original = globalThis.fetch;
  try {
    let signal;
    globalThis.fetch = (_path, options) => { signal = options.signal; return new Promise(() => {}); };
    await assert.rejects(requestEnrollmentJson('/status', {}, 5), /timed out/);
    assert.equal(signal.aborted, true);
  } finally { globalThis.fetch = original; }
});

test('failed initial enrollment renders a retry and retry reloads status', async () => {
  const { createRequire } = await import('node:module');
  const { readFileSync } = await import('node:fs');
  const require = createRequire(new URL('../package.json', import.meta.url));
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { transformSync } = require('@babel/core');
  const source = readFileSync(new URL('../src/components/EnrollmentFlow.js', import.meta.url), 'utf8');
  const compiled = transformSync(source, { babelrc: false, configFile: false,
    presets: [require.resolve('@babel/preset-react'), [require.resolve('@babel/preset-env'), { targets: { node: 'current' } }]] }).code;
  const initial = [null, 'error', [], 'Network offline', false, ''];
  const updates = [];
  let index = 0;
  const hooks = { ...React, useCallback: fn => fn, useEffect() {}, useState() {
    const slot = index++;
    return [initial[slot], value => updates.push([slot, value])];
  } };
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(name => name === 'react' ? hooks : { requestEnrollmentJson, enrollmentCheckpoint: status => status.recovery_codes_ready ? 'activate' : 'consent' }, module, module.exports);
  const tree = module.exports.default({ onComplete() {} });
  const markup = renderToStaticMarkup(tree);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /Network offline/);
  assert.match(markup, /Retry enrollment/);
  assert.match(markup, /Return to sign in/);
  assert.doesNotMatch(markup, /Loading secure enrollment/);
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
  const retry = nodes(tree).find(child => child.type === 'button' && child.props.children === 'Retry enrollment');
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ pending: true, session_kind: 'PENDING_ENROLLMENT', legal_receipts_current: true, passkey_registered: true, recovery_codes_ready: true, device_status: 'TRUSTED' }) });
    await retry.props.onClick();
    assert.ok(updates.some(([slot, value]) => slot === 0 && value.device_status === 'TRUSTED'));
  } finally { globalThis.fetch = original; }
});
