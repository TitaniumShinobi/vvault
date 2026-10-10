import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';

const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
const fail = code => {
  process.stderr.write(`${code}\n`);
  process.exit(2);
};

const contract = await readJson('.auth-kit/contract.json');

if (contract.state === 'ESTABLISHED') {
  const verified = spawnSync(process.execPath, ['.auth-kit/verify.mjs', 'verify'], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: process.env,
  });
  process.stdout.write(verified.stdout || '');
  process.stderr.write(verified.stderr || '');
  process.exit(verified.status ?? 2);
}

if (contract.state !== 'UNESTABLISHED' || contract.sequence !== 0 || contract.head !== null) {
  fail('PREBASELINE_CONTRACT_STATE_INVALID');
}
const [history, baseline] = await Promise.all([
  readdir('.auth-kit/history'),
  readdir('.auth-kit/baseline'),
]);
if (history.some(name => name !== '.gitkeep') || baseline.some(name => name !== '.gitkeep')) {
  fail('PREBASELINE_HISTORY_MUST_BE_EMPTY');
}

const verified = spawnSync(process.execPath, ['.auth-kit/verify.mjs', 'verify'], {
  cwd: process.cwd(),
  encoding: 'utf8',
  env: process.env,
  maxBuffer: 32 * 1024 * 1024,
});
if (!verified.stdout) fail('AUTH_VERIFY_REPORT_MISSING');
let report;
try {
  report = JSON.parse(verified.stdout);
} catch {
  fail('AUTH_VERIFY_REPORT_INVALID');
}
if (report.verification === 'FAIL' || report.profile_verification === 'FAIL') {
  fail('AUTH_VERIFY_FAILED');
}
if ((report.regressions || []).length) fail('AUTH_REGRESSION_PRESENT');

const matrix = await readJson('.auth-kit/product/candidate-obligations.json');
const expected = matrix.candidates.map(candidate => candidate.id).sort();
const observed = (report.productCandidates || []).map(candidate => candidate.id).sort();
if (expected.length !== 16 || JSON.stringify(expected) !== JSON.stringify(observed)) {
  fail('PRODUCT_CANDIDATE_SET_MISMATCH');
}
for (const candidate of report.productCandidates) {
  if (candidate.applicability !== 'TRUE') fail('PRODUCT_CANDIDATE_APPLICABILITY_UNRESOLVED');
  if (candidate.establishment === 'REALIZATION_GAP' || candidate.verification === 'FAIL') {
    fail('PRODUCT_REALIZATION_GAP');
  }
}

const migration = await readFile('vvault/migrations/0039_returning_owner_session_without_device_gate.up.sql');
const migrationDigest = createHash('sha256').update(migration).digest('hex');
if (migrationDigest !== 'a2fa93bae20a083e346fa68b49b82166d2b6ce7019099d9e6c3fd4948efc349a') {
  fail('MIGRATION_0039_DIGEST_MISMATCH');
}

const tests = [
  'tests/test_auth_crypto_transaction_key.py',
  'tests/test_auth_session_lifecycle.py',
  'tests/test_body_database_connection_bounds.py',
  'tests/test_deployment_migration_contract.py',
  'tests/test_enrollment_resume.py',
  'tests/test_frontend_enrollment_contract_static.py',
  'tests/test_google_entry_compatibility.py',
  'tests/test_identity_directory_postgres.py',
  'tests/test_oauth_callback_recovery.py',
  'tests/test_provider_availability.py',
  'tests/test_returning_owner_signin.py',
  'tests/test_vvault_ready_body_database_contract.py',
  'tests/test_vvault_runtime_cutover_contract.py',
];
if (process.env.VVAULT_AUTH_REQUIRE_GITHUB_CHECK === '1') {
  const revision = report.binding?.revision;
  if (!/^[a-f0-9]{40}$/.test(revision || '')) fail('CANDIDATE_REVISION_INVALID');
  const endpoint = `https://api.github.com/repos/TitaniumShinobi/vvault/commits/${revision}/check-runs`;
  const deadline = Date.now() + 150_000;
  let accepted = false;
  do {
    let response;
    try {
      response = await fetch(endpoint, {
        headers: {
          Accept: 'application/vnd.github+json',
          'User-Agent': 'vvault-prebaseline-release-gate',
          'X-GitHub-Api-Version': '2022-11-28',
        },
      });
    } catch {
      fail('REQUIRED_CI_STATUS_UNAVAILABLE');
    }
    if (!response.ok) fail('REQUIRED_CI_STATUS_UNAVAILABLE');
    const payload = await response.json();
    accepted = (payload.check_runs || []).some(check =>
      check.name === 'auth-product-contract' && check.status === 'completed' && check.conclusion === 'success');
    if (!accepted) await new Promise(resolve => setTimeout(resolve, 5_000));
  } while (!accepted && Date.now() < deadline);
  if (!accepted) fail('REQUIRED_CI_STATUS_NOT_SUCCESSFUL');
} else {
  const python = process.env.VVAULT_AUTH_TEST_PYTHON || 'python3';
  const tested = spawnSync(python, ['-m', 'pytest', '-q', ...tests], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    maxBuffer: 32 * 1024 * 1024,
  });
  process.stdout.write(tested.stdout || '');
  process.stderr.write(tested.stderr || '');
  if (tested.status !== 0) fail('PREBASELINE_FOCUSED_TESTS_FAILED');
  if (/\b(?:skipped|xfailed|xpassed)\b/i.test(tested.stdout || '')) {
    fail('PREBASELINE_FOCUSED_TESTS_INCOMPLETE');
  }
}

process.stdout.write(JSON.stringify({
  release_admission: 'PASS',
  baseline_state: 'UNESTABLISHED',
  auth_verify: report.verification,
  profile_verification: report.profile_verification,
  candidates_visible: observed.length,
  deterministic_tests: process.env.VVAULT_AUTH_REQUIRE_GITHUB_CHECK === '1' ? 'REQUIRED_CI_SUCCESS' : 'PASS',
  migration_0039_sha256: migrationDigest,
}) + '\n');
