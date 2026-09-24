-- Milestone 1: registered resource admission plus opaque owner workspaces.
-- No owner workspace is created or backfilled by this migration.

ALTER TABLE ovvaults.vault_files DROP CONSTRAINT IF EXISTS vault_files_relying_party_id_check;
ALTER TABLE ovvaults.vault_files ADD CONSTRAINT vault_files_relying_party_id_check
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault', 'grid'));
ALTER TABLE ovvaults.transcripts DROP CONSTRAINT IF EXISTS transcripts_relying_party_id_check;
ALTER TABLE ovvaults.transcripts ADD CONSTRAINT transcripts_relying_party_id_check
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault', 'grid'));
ALTER TABLE ovvaults.vault_drive_nodes DROP CONSTRAINT IF EXISTS vault_drive_nodes_relying_party_id_check;
ALTER TABLE ovvaults.vault_drive_nodes ADD CONSTRAINT vault_drive_nodes_relying_party_id_check
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault', 'grid'));
ALTER TABLE ovvaults.vault_drive_operation_receipts DROP CONSTRAINT IF EXISTS vault_drive_operation_receipts_relying_party_id_check;
ALTER TABLE ovvaults.vault_drive_operation_receipts ADD CONSTRAINT vault_drive_operation_receipts_relying_party_id_check
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault', 'grid'));

CREATE OR REPLACE FUNCTION ovvaults.bind_relying_party_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE scope text;
BEGIN
  scope := current_setting('app.vvault_relying_party_id', true);
  IF scope IS NULL OR scope NOT IN ('chatty', 'chatty-cli', 'vvault', 'grid') THEN
    RAISE EXCEPTION 'verified relying-party scope is required';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.relying_party_id := scope;
  ELSIF NEW.relying_party_id IS DISTINCT FROM scope THEN
    RAISE EXCEPTION 'cross-relying-party mutation is not permitted';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS ovvaults.resource_application_admissions (
  client_id text NOT NULL,
  application_id text NOT NULL,
  relying_party_id text NOT NULL,
  contract_version text NOT NULL,
  capabilities text[] NOT NULL,
  enabled boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, application_id),
  CHECK (relying_party_id=application_id),
  CHECK (cardinality(capabilities) > 0)
);

INSERT INTO ovvaults.resource_application_admissions
  (client_id, application_id, relying_party_id, contract_version, capabilities, enabled)
VALUES
  ('grid-windows', 'grid', 'grid', 'life.vvault.resource-workspace/v1', ARRAY['workspace:resolve']::text[], false)
ON CONFLICT (client_id, application_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS ovvaults.owner_workspaces (
  workspace_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL UNIQUE REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  lifecycle_status text NOT NULL CHECK (lifecycle_status IN ('ACTIVE', 'SUSPENDED')),
  capabilities text[] NOT NULL DEFAULT ARRAY['workspace:resolve']::text[],
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(capabilities) > 0)
);

ALTER TABLE ovvaults.owner_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.owner_workspaces FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_workspaces_owner_isolation ON ovvaults.owner_workspaces
  USING (owner_user_id::text=current_setting('app.vvault_authenticated_user_id', true))
  WITH CHECK (owner_user_id::text=current_setting('app.vvault_authenticated_user_id', true));

REVOKE INSERT, UPDATE, DELETE ON ovvaults.owner_workspaces FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE ON ovvaults.resource_application_admissions FROM PUBLIC;
