-- Canonical provider identities and verified contact records.
-- This is intentionally independent of trusted devices and enrollment gates;
-- those arrive in the following enrollment migration.
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE ovvaults.users
  ADD COLUMN IF NOT EXISTS account_state TEXT NOT NULL DEFAULT 'LEGACY';
ALTER TABLE ovvaults.users
  DROP CONSTRAINT IF EXISTS users_account_state_check;
ALTER TABLE ovvaults.users
  ADD CONSTRAINT users_account_state_check CHECK (account_state IN (
    'LEGACY', 'PENDING_ENROLLMENT', 'ACTIVE', 'SUSPENDED', 'REJECTED'
  ));

-- `users.email` was a legacy login key.  It is now only denormalized contact
-- data: provider subjects, not email addresses, decide account identity.
DO $$
DECLARE constraint_name TEXT;
BEGIN
  FOR constraint_name IN
    SELECT c.conname
    FROM pg_constraint c
    WHERE c.conrelid = 'ovvaults.users'::regclass
      AND c.contype = 'u'
      AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute
                             WHERE attrelid = 'ovvaults.users'::regclass
                               AND attname = 'email')]
  LOOP
    EXECUTE format('ALTER TABLE ovvaults.users DROP CONSTRAINT %I', constraint_name);
  END LOOP;
END $$;
ALTER TABLE ovvaults.users ALTER COLUMN email DROP NOT NULL;

CREATE TABLE IF NOT EXISTS ovvaults.external_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  provider_subject TEXT NOT NULL,
  issuer TEXT,
  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  CHECK (length(provider) BETWEEN 1 AND 64),
  CHECK (length(provider_subject) BETWEEN 1 AND 1024)
);
ALTER TABLE ovvaults.external_identities ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE ovvaults.external_identities ADD COLUMN IF NOT EXISTS provider_subject TEXT;
ALTER TABLE ovvaults.external_identities ADD COLUMN IF NOT EXISTS issuer TEXT;
ALTER TABLE ovvaults.external_identities ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE ovvaults.external_identities ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
DO $$
DECLARE has_subject BOOLEAN;
DECLARE has_last_verified BOOLEAN;
BEGIN
  SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='ovvaults.external_identities'::regclass AND attname='subject' AND NOT attisdropped) INTO has_subject;
  SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='ovvaults.external_identities'::regclass AND attname='last_verified_at' AND NOT attisdropped) INTO has_last_verified;
  IF has_subject THEN
    EXECUTE format(
      'UPDATE ovvaults.external_identities
       SET provider = CASE WHEN issuer IN (''accounts.google.com'', ''https://accounts.google.com'') THEN ''google'' ELSE COALESCE(provider, ''legacy'') END,
           provider_subject = COALESCE(provider_subject, subject),
           verified_at = COALESCE(verified_at, %s, created_at)
       WHERE provider IS NULL OR provider_subject IS NULL OR verified_at IS NULL',
      CASE WHEN has_last_verified THEN 'last_verified_at' ELSE 'created_at' END
    );
  END IF;
END $$;
ALTER TABLE ovvaults.external_identities ALTER COLUMN provider SET NOT NULL;
ALTER TABLE ovvaults.external_identities ALTER COLUMN provider_subject SET NOT NULL;
ALTER TABLE ovvaults.external_identities ALTER COLUMN verified_at SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS external_identities_active_provider_subject_key
  ON ovvaults.external_identities(provider, provider_subject) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS external_identities_user_provider_subject_key
  ON ovvaults.external_identities(user_id, provider, provider_subject) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS ovvaults.managed_emails (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  normalized_email CITEXT NOT NULL,
  identity_id UUID REFERENCES ovvaults.external_identities(id) ON DELETE RESTRICT,
  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  UNIQUE(user_id, normalized_email)
);
CREATE OR REPLACE FUNCTION ovvaults.reject_managed_email_reassignment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.user_id <> OLD.user_id OR NEW.normalized_email <> OLD.normalized_email THEN
    RAISE EXCEPTION 'managed email identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS managed_emails_no_reassignment ON ovvaults.managed_emails;
CREATE TRIGGER managed_emails_no_reassignment BEFORE UPDATE ON ovvaults.managed_emails
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_managed_email_reassignment();

CREATE TABLE IF NOT EXISTS ovvaults.identity_oauth_transactions (
  state_digest TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('signin', 'reauth', 'link')),
  nonce_digest TEXT,
  nonce_ciphertext BYTEA,
  pkce_verifier_digest TEXT NOT NULL,
  pkce_verifier_ciphertext BYTEA NOT NULL,
  redirect_uri TEXT NOT NULL,
  frontend_origin TEXT NOT NULL,
  initiating_user_id UUID REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  initiating_session_id UUID REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK ((purpose = 'signin' AND initiating_user_id IS NULL AND initiating_session_id IS NULL)
      OR (purpose IN ('reauth', 'link') AND initiating_user_id IS NOT NULL AND initiating_session_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS ovvaults.email_magic_link_challenges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token_digest TEXT NOT NULL UNIQUE,
  normalized_email CITEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('signin', 'link')),
  redirect_uri TEXT NOT NULL,
  initiating_user_id UUID REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  initiating_session_id UUID REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK ((purpose = 'signin' AND initiating_user_id IS NULL AND initiating_session_id IS NULL)
      OR (purpose = 'link' AND initiating_user_id IS NOT NULL AND initiating_session_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS email_magic_link_challenges_live_email_idx
  ON ovvaults.email_magic_link_challenges(normalized_email, expires_at) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS ovvaults.auth_rate_limit_buckets (
  scope TEXT NOT NULL,
  bucket_digest TEXT NOT NULL,
  window_started_at TIMESTAMPTZ NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  PRIMARY KEY(scope, bucket_digest, window_started_at)
);

CREATE TABLE IF NOT EXISTS ovvaults.auth_session_reauth (
  session_id UUID PRIMARY KEY REFERENCES ovvaults.sessions(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  reauthenticated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
