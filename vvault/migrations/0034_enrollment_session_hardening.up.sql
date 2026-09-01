-- Enrollment and device-bound sessions built on 0033_identity_directory.
--
-- This migration deliberately uses enrollment_* names rather than reusing the
-- retired 0032 tables.  It is additive, forward-only, and leaves pre-0034
-- sessions as LEGACY until a route explicitly rotates them into an enrollment
-- managed session.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_devices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  device_secret_digest TEXT NOT NULL UNIQUE,
  label TEXT,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'TRUSTED', 'REVOKED')),
  approved_by_user_id UUID REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  approved_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  ip_hash TEXT,
  user_agent_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (status = 'PENDING' AND approved_by_user_id IS NULL AND approved_at IS NULL AND revoked_at IS NULL)
    OR (status = 'TRUSTED' AND approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL AND revoked_at IS NULL)
    OR (status = 'REVOKED' AND revoked_at IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS enrollment_devices_user_status_idx
  ON ovvaults.enrollment_devices(user_id, status);

ALTER TABLE ovvaults.sessions
  ADD COLUMN IF NOT EXISTS enrollment_session_kind TEXT NOT NULL DEFAULT 'LEGACY',
  ADD COLUMN IF NOT EXISTS enrollment_device_id UUID REFERENCES ovvaults.enrollment_devices(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS rotated_from_session_id UUID REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT;
ALTER TABLE ovvaults.sessions
  DROP CONSTRAINT IF EXISTS sessions_enrollment_kind_check;
ALTER TABLE ovvaults.sessions
  ADD CONSTRAINT sessions_enrollment_kind_check CHECK (
    enrollment_session_kind IN ('LEGACY', 'PENDING_ENROLLMENT', 'PENDING_DEVICE', 'NORMAL')
  ) NOT VALID;

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  document_key TEXT NOT NULL,
  document_version TEXT NOT NULL,
  document_sha256 TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip_hash TEXT,
  user_agent_hash TEXT,
  UNIQUE(user_id, document_key, document_version, document_sha256)
);

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_webauthn_challenges (
  challenge_digest TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  session_id UUID NOT NULL REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT,
  rp_id TEXT NOT NULL,
  allowed_origin TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_webauthn_credentials (
  credential_id TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  public_key BYTEA NOT NULL,
  sign_count BIGINT NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
  transports JSONB NOT NULL DEFAULT '[]'::jsonb,
  user_verified_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS enrollment_webauthn_credentials_user_idx
  ON ovvaults.enrollment_webauthn_credentials(user_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_recovery_codes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  code_digest TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS enrollment_recovery_codes_user_live_idx
  ON ovvaults.enrollment_recovery_codes(user_id) WHERE used_at IS NULL;

CREATE OR REPLACE FUNCTION ovvaults.reject_immutable_enrollment_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
END;
$$;
DROP TRIGGER IF EXISTS enrollment_consents_immutable ON ovvaults.enrollment_consents;
CREATE TRIGGER enrollment_consents_immutable
  BEFORE UPDATE OR DELETE ON ovvaults.enrollment_consents
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_immutable_enrollment_evidence();

CREATE OR REPLACE FUNCTION ovvaults.validate_enrollment_session()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  device_owner UUID;
  device_status TEXT;
  account_state_value TEXT;
BEGIN
  IF NEW.enrollment_session_kind = 'LEGACY' THEN
    RETURN NEW;
  END IF;
  IF NEW.enrollment_device_id IS NULL THEN
    RAISE EXCEPTION 'enrollment session requires a bound device';
  END IF;
  SELECT user_id, status INTO device_owner, device_status
  FROM ovvaults.enrollment_devices WHERE id = NEW.enrollment_device_id;
  IF device_owner IS NULL OR device_owner <> NEW.user_id THEN
    RAISE EXCEPTION 'enrollment session device owner mismatch';
  END IF;
  SELECT account_state INTO account_state_value FROM ovvaults.users WHERE id = NEW.user_id;
  IF NEW.enrollment_session_kind = 'PENDING_ENROLLMENT'
     AND (device_status <> 'PENDING' OR account_state_value <> 'PENDING_ENROLLMENT') THEN
    RAISE EXCEPTION 'pending enrollment session requires pending account and device';
  END IF;
  IF NEW.enrollment_session_kind = 'PENDING_DEVICE'
     AND (device_status <> 'PENDING' OR account_state_value <> 'ACTIVE') THEN
    RAISE EXCEPTION 'pending device session requires active account and pending device';
  END IF;
  IF NEW.enrollment_session_kind = 'NORMAL'
     AND (device_status <> 'TRUSTED' OR account_state_value <> 'ACTIVE') THEN
    RAISE EXCEPTION 'normal session requires active account and trusted device';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS sessions_validate_enrollment_device ON ovvaults.sessions;
CREATE TRIGGER sessions_validate_enrollment_device
  BEFORE INSERT OR UPDATE OF user_id, enrollment_session_kind, enrollment_device_id ON ovvaults.sessions
  FOR EACH ROW EXECUTE FUNCTION ovvaults.validate_enrollment_session();
