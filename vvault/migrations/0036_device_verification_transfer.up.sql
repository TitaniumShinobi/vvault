-- Device verification evidence is owner-bound and contains only opaque digests.
CREATE TABLE IF NOT EXISTS ovvaults.enrollment_device_transfer_codes (
  code_digest TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  pending_session_id UUID NOT NULL REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (expires_at <= created_at + interval '10 minutes'),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE INDEX IF NOT EXISTS enrollment_device_transfer_live_idx
  ON ovvaults.enrollment_device_transfer_codes(user_id, expires_at)
  WHERE consumed_at IS NULL;
