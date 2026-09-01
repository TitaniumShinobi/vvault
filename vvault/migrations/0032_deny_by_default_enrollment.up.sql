-- VVAULT deny-by-default enrollment.  This is additive: it does not backfill
-- or alter existing user data.  Deploy the owner bootstrap invitation before
-- enabling the ACTIVE-session gate to avoid operator lockout.
CREATE EXTENSION IF NOT EXISTS citext;

ALTER TABLE ovvaults.users
  ADD COLUMN IF NOT EXISTS enrollment_status TEXT NOT NULL DEFAULT 'LEGACY_PENDING',
  ADD COLUMN IF NOT EXISTS enrollment_completed_at TIMESTAMPTZ;

ALTER TABLE ovvaults.users
  DROP CONSTRAINT IF EXISTS users_enrollment_status_check;
ALTER TABLE ovvaults.users
  ADD CONSTRAINT users_enrollment_status_check CHECK (enrollment_status IN (
    'LEGACY_PENDING', 'PENDING_ENROLLMENT', 'ACTIVE', 'SUSPENDED', 'REJECTED'
  ));

CREATE TABLE IF NOT EXISTS ovvaults.external_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  issuer TEXT NOT NULL,
  subject TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (issuer, subject)
);

CREATE TABLE IF NOT EXISTS ovvaults.oauth_transactions (
  state_digest TEXT PRIMARY KEY,
  nonce_digest TEXT NOT NULL,
  nonce_ciphertext BYTEA NOT NULL,
  pkce_verifier_digest TEXT NOT NULL,
  pkce_verifier_ciphertext BYTEA NOT NULL,
  redirect_uri TEXT NOT NULL,
  invitation_digest TEXT,
  frontend_origin TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE ovvaults.oauth_transactions
  ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT 'signin',
  ADD COLUMN IF NOT EXISTS link_user_id UUID REFERENCES ovvaults.users(id),
  ADD COLUMN IF NOT EXISTS link_session_id UUID REFERENCES ovvaults.sessions(id);
ALTER TABLE ovvaults.oauth_transactions DROP CONSTRAINT IF EXISTS oauth_transactions_operation_check;
ALTER TABLE ovvaults.oauth_transactions ADD CONSTRAINT oauth_transactions_operation_check
  CHECK (operation IN ('signin', 'link')) NOT VALID;
ALTER TABLE ovvaults.oauth_transactions DROP CONSTRAINT IF EXISTS oauth_transactions_link_context_check;
ALTER TABLE ovvaults.oauth_transactions ADD CONSTRAINT oauth_transactions_link_context_check
  CHECK (
    (operation='signin' AND link_user_id IS NULL AND link_session_id IS NULL)
    OR (operation='link' AND link_user_id IS NOT NULL AND link_session_id IS NOT NULL)
  ) NOT VALID;
ALTER TABLE ovvaults.oauth_transactions DROP CONSTRAINT IF EXISTS oauth_transactions_lifecycle_check;
ALTER TABLE ovvaults.oauth_transactions ADD CONSTRAINT oauth_transactions_lifecycle_check
  CHECK (expires_at > created_at AND (consumed_at IS NULL OR consumed_at >= created_at)) NOT VALID;

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_admission_grants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  grant_type TEXT NOT NULL CHECK (grant_type IN ('invitation', 'allowlist', 'owner_bootstrap')),
  email CITEXT NOT NULL,
  token_digest TEXT UNIQUE,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  consumed_by_user_id UUID REFERENCES ovvaults.users(id),
  created_by_user_id UUID REFERENCES ovvaults.users(id),
  target_user_id UUID REFERENCES ovvaults.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((grant_type = 'allowlist' AND token_digest IS NULL) OR (grant_type <> 'allowlist' AND token_digest IS NOT NULL))
);
ALTER TABLE ovvaults.enrollment_admission_grants
  ADD COLUMN IF NOT EXISTS target_user_id UUID REFERENCES ovvaults.users(id);
CREATE INDEX IF NOT EXISTS enrollment_admission_grants_email_idx
  ON ovvaults.enrollment_admission_grants (email, expires_at) WHERE consumed_at IS NULL;
ALTER TABLE ovvaults.enrollment_admission_grants DROP CONSTRAINT IF EXISTS enrollment_admission_grants_consumption_check;
ALTER TABLE ovvaults.enrollment_admission_grants ADD CONSTRAINT enrollment_admission_grants_consumption_check
  CHECK (
    expires_at > created_at
    AND ((consumed_at IS NULL AND consumed_by_user_id IS NULL)
      OR (consumed_at IS NOT NULL AND consumed_by_user_id IS NOT NULL AND consumed_at >= created_at))
  ) NOT VALID;

CREATE TABLE IF NOT EXISTS ovvaults.enrollment_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  document_key TEXT NOT NULL,
  document_version TEXT NOT NULL,
  document_sha256 TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip_hash TEXT,
  user_agent_hash TEXT,
  UNIQUE (user_id, document_key, document_version, document_sha256)
);

CREATE TABLE IF NOT EXISTS ovvaults.webauthn_challenges (
  challenge_digest TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE ovvaults.webauthn_challenges
  ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES ovvaults.sessions(id),
  ADD COLUMN IF NOT EXISTS purpose TEXT,
  ADD COLUMN IF NOT EXISTS rp_id TEXT,
  ADD COLUMN IF NOT EXISTS allowed_origin TEXT,
  ADD COLUMN IF NOT EXISTS user_verification TEXT;
ALTER TABLE ovvaults.webauthn_challenges DROP CONSTRAINT IF EXISTS webauthn_challenges_binding_check;
ALTER TABLE ovvaults.webauthn_challenges ADD CONSTRAINT webauthn_challenges_binding_check
  CHECK (
    session_id IS NOT NULL AND purpose='registration'
    AND length(rp_id) > 0 AND length(allowed_origin) > 0
    AND user_verification='required' AND expires_at > created_at
    AND (consumed_at IS NULL OR consumed_at >= created_at)
  ) NOT VALID;
CREATE TABLE IF NOT EXISTS ovvaults.webauthn_credentials (
  credential_id TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  public_key BYTEA NOT NULL,
  sign_count BIGINT NOT NULL DEFAULT 0,
  transports JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);
ALTER TABLE ovvaults.webauthn_credentials
  ADD COLUMN IF NOT EXISTS user_verified_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS ovvaults.recovery_codes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  code_digest TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  used_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS ovvaults.trusted_devices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id),
  device_secret_digest TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'TRUSTED', 'REVOKED')),
  approved_by_user_id UUID REFERENCES ovvaults.users(id),
  approved_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  ip_hash TEXT,
  user_agent_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE ovvaults.trusted_devices DROP CONSTRAINT IF EXISTS trusted_devices_state_check;
ALTER TABLE ovvaults.trusted_devices ADD CONSTRAINT trusted_devices_state_check CHECK (
  (status='PENDING' AND approved_by_user_id IS NULL AND approved_at IS NULL AND revoked_at IS NULL)
  OR (status='TRUSTED' AND approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL AND revoked_at IS NULL)
  OR (status='REVOKED' AND revoked_at IS NOT NULL)
) NOT VALID;

ALTER TABLE ovvaults.sessions
  ADD COLUMN IF NOT EXISTS session_kind TEXT NOT NULL DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS device_id UUID REFERENCES ovvaults.trusted_devices(id),
  ADD COLUMN IF NOT EXISTS rotated_from_session_id UUID REFERENCES ovvaults.sessions(id);
ALTER TABLE ovvaults.sessions DROP CONSTRAINT IF EXISTS sessions_kind_check;
ALTER TABLE ovvaults.sessions ADD CONSTRAINT sessions_kind_check
  CHECK (session_kind IN ('pending', 'device_pending', 'normal')) NOT VALID;
ALTER TABLE ovvaults.sessions DROP CONSTRAINT IF EXISTS sessions_device_required_check;
ALTER TABLE ovvaults.sessions ADD CONSTRAINT sessions_device_required_check
  CHECK (device_id IS NOT NULL) NOT VALID;

CREATE TABLE IF NOT EXISTS ovvaults.auth_security_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES ovvaults.users(id),
  event_type TEXT NOT NULL,
  outcome TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  ip_hash TEXT,
  user_agent_hash TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

REVOKE UPDATE, DELETE ON ovvaults.enrollment_consents FROM PUBLIC;
REVOKE UPDATE, DELETE ON ovvaults.auth_security_events FROM PUBLIC;

CREATE OR REPLACE FUNCTION ovvaults.reject_immutable_security_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS enrollment_consents_immutable ON ovvaults.enrollment_consents;
CREATE TRIGGER enrollment_consents_immutable
  BEFORE UPDATE OR DELETE ON ovvaults.enrollment_consents
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_immutable_security_evidence();
DROP TRIGGER IF EXISTS auth_security_events_immutable ON ovvaults.auth_security_events;
CREATE TRIGGER auth_security_events_immutable
  BEFORE UPDATE OR DELETE ON ovvaults.auth_security_events
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_immutable_security_evidence();

CREATE OR REPLACE FUNCTION ovvaults.validate_vvault_session_device()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  bound_user UUID;
  bound_status TEXT;
  account_status TEXT;
BEGIN
  SELECT user_id, status INTO bound_user, bound_status
  FROM ovvaults.trusted_devices WHERE id=NEW.device_id;
  IF bound_user IS NULL OR bound_user <> NEW.user_id THEN
    RAISE EXCEPTION 'session device owner mismatch';
  END IF;
  SELECT enrollment_status INTO account_status
  FROM ovvaults.users WHERE id=NEW.user_id;
  IF NEW.session_kind='normal' AND (bound_status <> 'TRUSTED' OR account_status <> 'ACTIVE') THEN
    RAISE EXCEPTION 'normal session requires active account and trusted device';
  END IF;
  IF NEW.session_kind='pending' AND (bound_status <> 'PENDING' OR account_status <> 'PENDING_ENROLLMENT') THEN
    RAISE EXCEPTION 'pending session requires pending device';
  END IF;
  IF NEW.session_kind='device_pending' AND (bound_status <> 'PENDING' OR account_status <> 'ACTIVE') THEN
    RAISE EXCEPTION 'device-pending session requires active account and pending device';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS sessions_validate_device ON ovvaults.sessions;
CREATE TRIGGER sessions_validate_device
  BEFORE INSERT OR UPDATE OF user_id, session_kind, device_id ON ovvaults.sessions
  FOR EACH ROW EXECUTE FUNCTION ovvaults.validate_vvault_session_device();
