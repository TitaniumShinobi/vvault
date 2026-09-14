-- OVVAULTS migration: 0036_enrollment_device_transfer_codes
-- owner: VVAULT authentication; transaction: required
-- Repairs the approved-device transfer path without changing users, sessions,
-- vault data, or existing recovery codes. Transfer codes are keyed digests
-- only; plaintext approval codes never enter the database.

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_device_transfer_codes (
  code_digest TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE CASCADE,
  pending_session_id UUID NOT NULL REFERENCES ovvaults.sessions(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE INDEX IF NOT EXISTS enrollment_device_transfer_codes_live_user_expiry_idx
  ON ovvaults.enrollment_device_transfer_codes (user_id, expires_at)
  WHERE consumed_at IS NULL;
