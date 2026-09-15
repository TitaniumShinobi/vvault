export function enrollmentCheckpoint(status) {
  if (!status || status.pending !== true) { const error = new Error('This secure checkpoint has expired. Sign in again to continue.'); error.status = 401; throw error; }
  if (status.session_kind === 'PENDING_DEVICE') return 'device';
  if (!['PENDING_ENROLLMENT', 'LEGACY'].includes(status.session_kind) || typeof status.legal_receipts_current !== 'boolean') throw new Error('Enrollment returned an invalid status. Please retry.');
  if (!status.legal_receipts_current) return 'consent';
  if (status.session_kind === 'LEGACY') return 'consent';
  if (typeof status.passkey_registered !== 'boolean' || typeof status.recovery_codes_ready !== 'boolean') throw new Error('Enrollment returned an incomplete status. Please retry.');
  if (!status.passkey_registered) return 'passkey';
  return status.recovery_codes_ready ? 'activate' : 'recovery';
}
