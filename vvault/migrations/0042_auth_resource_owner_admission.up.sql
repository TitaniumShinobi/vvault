-- Explicit AUTH subject -> VVAULT owner bindings. No bindings are backfilled.
CREATE TABLE IF NOT EXISTS ovvaults.auth_resource_owner_bindings (
  binding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_issuer text NOT NULL,
  auth_subject text NOT NULL,
  owner_user_id uuid NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  audience text NOT NULL,
  binding_status text NOT NULL CHECK (binding_status IN ('ACTIVE','PENDING','REVOKED','DISABLED')),
  policy_version text NOT NULL,
  valid_until timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (auth_issuer, auth_subject),
  CHECK (length(auth_issuer) BETWEEN 1 AND 256),
  CHECK (length(auth_subject) BETWEEN 1 AND 256),
  CHECK (audience='https://vvault.thewreck.org')
);

REVOKE ALL ON ovvaults.auth_resource_owner_bindings FROM PUBLIC;
