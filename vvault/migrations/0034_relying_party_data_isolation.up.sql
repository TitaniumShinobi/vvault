-- Additive VVAULT consumer partition. Legacy consumer data is Chatty data.
-- Runtime sets app.vvault_relying_party_id only after signature/session verification.
ALTER TABLE ovvaults.vault_files
  ADD COLUMN IF NOT EXISTS relying_party_id text NOT NULL DEFAULT 'chatty'
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault'));
ALTER TABLE ovvaults.transcripts
  ADD COLUMN IF NOT EXISTS relying_party_id text NOT NULL DEFAULT 'chatty'
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault'));
ALTER TABLE ovvaults.vault_drive_nodes
  ADD COLUMN IF NOT EXISTS relying_party_id text NOT NULL DEFAULT 'chatty'
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault'));
ALTER TABLE ovvaults.vault_drive_operation_receipts
  ADD COLUMN IF NOT EXISTS relying_party_id text NOT NULL DEFAULT 'chatty'
  CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault'));

CREATE OR REPLACE FUNCTION ovvaults.bind_relying_party_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE scope text;
BEGIN
  scope := current_setting('app.vvault_relying_party_id', true);
  IF scope IS NULL OR scope NOT IN ('chatty', 'chatty-cli', 'vvault') THEN
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

CREATE TRIGGER vault_files_bind_relying_party_scope
  BEFORE INSERT OR UPDATE ON ovvaults.vault_files
  FOR EACH ROW EXECUTE FUNCTION ovvaults.bind_relying_party_scope();
CREATE TRIGGER transcripts_bind_relying_party_scope
  BEFORE INSERT OR UPDATE ON ovvaults.transcripts
  FOR EACH ROW EXECUTE FUNCTION ovvaults.bind_relying_party_scope();
CREATE TRIGGER vault_drive_nodes_bind_relying_party_scope
  BEFORE INSERT OR UPDATE ON ovvaults.vault_drive_nodes
  FOR EACH ROW EXECUTE FUNCTION ovvaults.bind_relying_party_scope();
CREATE TRIGGER vault_drive_receipts_bind_relying_party_scope
  BEFORE INSERT OR UPDATE ON ovvaults.vault_drive_operation_receipts
  FOR EACH ROW EXECUTE FUNCTION ovvaults.bind_relying_party_scope();

CREATE INDEX IF NOT EXISTS vault_files_relying_party_owner_construct_idx
  ON ovvaults.vault_files (relying_party_id, user_id, construct_id);
CREATE INDEX IF NOT EXISTS transcripts_relying_party_owner_construct_idx
  ON ovvaults.transcripts (relying_party_id, user_id, anatomy_id);
CREATE INDEX IF NOT EXISTS vault_drive_nodes_relying_party_owner_construct_idx
  ON ovvaults.vault_drive_nodes (relying_party_id, owner_user_id, construct_id);

-- Legacy uniqueness omitted the consumer partition, so a Chatty CLI node at
-- the same logical path as a Chatty node would collide before RLS could apply.
-- Build the scoped replacements first, then retire only those incompatible
-- index definitions.  No rows are copied, reclassified, or deleted.
CREATE UNIQUE INDEX IF NOT EXISTS vault_drive_nodes_relying_party_active_path_idx
  ON ovvaults.vault_drive_nodes
     (relying_party_id, owner_user_id, construct_id, logical_path)
  WHERE trashed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS vault_drive_nodes_relying_party_active_sibling_idx
  ON ovvaults.vault_drive_nodes
     (relying_party_id, owner_user_id, construct_id,
      COALESCE(parent_node_id, '00000000-0000-0000-0000-000000000000'::uuid),
      normalized_name)
  WHERE trashed_at IS NULL;
DROP INDEX IF EXISTS ovvaults.vault_drive_nodes_active_path_idx;
DROP INDEX IF EXISTS ovvaults.vault_drive_nodes_active_sibling_idx;

ALTER TABLE ovvaults.vault_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.vault_drive_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.vault_drive_operation_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.vault_files FORCE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.transcripts FORCE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.vault_drive_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.vault_drive_operation_receipts FORCE ROW LEVEL SECURITY;

CREATE POLICY vault_files_relying_party_isolation ON ovvaults.vault_files
  USING (relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (relying_party_id = current_setting('app.vvault_relying_party_id', true));
CREATE POLICY transcripts_relying_party_isolation ON ovvaults.transcripts
  USING (relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (relying_party_id = current_setting('app.vvault_relying_party_id', true));
CREATE POLICY vault_drive_nodes_relying_party_isolation ON ovvaults.vault_drive_nodes
  USING (relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (relying_party_id = current_setting('app.vvault_relying_party_id', true));
CREATE POLICY vault_drive_receipts_relying_party_isolation ON ovvaults.vault_drive_operation_receipts
  USING (relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (relying_party_id = current_setting('app.vvault_relying_party_id', true));
