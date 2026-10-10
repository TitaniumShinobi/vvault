-- Lossless, append-only source capture and canonical projection provenance.
-- Source bytes remain evidence; vault_files/transcripts remain projections.

CREATE TABLE IF NOT EXISTS ovvaults.source_native_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL REFERENCES ovvaults.users(id),
  relying_party_id text NOT NULL
    CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault')),
  provider text NOT NULL CHECK (provider ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  source_kind text NOT NULL CHECK (source_kind ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  source_collection text NOT NULL,
  stable_source_id text,
  source_locator text,
  observed_at timestamptz,
  raw_envelope bytea NOT NULL,
  raw_envelope_sha256 text NOT NULL CHECK (raw_envelope_sha256 ~ '^[a-f0-9]{64}$'),
  raw_envelope_bytes bigint NOT NULL CHECK (raw_envelope_bytes >= 0),
  payload bytea,
  payload_sha256 text CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[a-f0-9]{64}$'),
  payload_bytes bigint CHECK (payload_bytes IS NULL OR payload_bytes >= 0),
  media_type text NOT NULL DEFAULT 'application/octet-stream',
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_metadata) = 'object'),
  classification text NOT NULL DEFAULT 'LEGACY_UNCLASSIFIED'
    CHECK (classification = 'LEGACY_UNCLASSIFIED'),
  classification_construct text NOT NULL DEFAULT 'legacy-unassigned'
    CHECK (classification_construct = 'legacy-unassigned'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (octet_length(raw_envelope) = raw_envelope_bytes),
  CHECK ((payload IS NULL) = (payload_sha256 IS NULL)),
  CHECK ((payload IS NULL) = (payload_bytes IS NULL)),
  CHECK (payload IS NULL OR octet_length(payload) = payload_bytes)
);

CREATE UNIQUE INDEX IF NOT EXISTS source_native_artifacts_idempotency_idx
  ON ovvaults.source_native_artifacts
  (owner_user_id, relying_party_id, provider, source_kind, source_collection,
   coalesce(stable_source_id, ''), raw_envelope_sha256);

CREATE TABLE IF NOT EXISTS ovvaults.source_ingest_receipts (
  operation_id text PRIMARY KEY CHECK (operation_id ~ '^[a-f0-9]{64}$'),
  source_artifact_id uuid NOT NULL REFERENCES ovvaults.source_native_artifacts(id),
  owner_user_id uuid NOT NULL REFERENCES ovvaults.users(id),
  relying_party_id text NOT NULL CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault')),
  contract_version text NOT NULL,
  actor text NOT NULL,
  result text NOT NULL CHECK (result IN ('applied', 'already_applied')),
  receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
  receipt_sha256 text NOT NULL CHECK (receipt_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ovvaults.canonical_source_projections (
  operation_id text PRIMARY KEY CHECK (operation_id ~ '^[a-f0-9]{64}$'),
  source_artifact_id uuid NOT NULL REFERENCES ovvaults.source_native_artifacts(id),
  owner_user_id uuid NOT NULL REFERENCES ovvaults.users(id),
  relying_party_id text NOT NULL CHECK (relying_party_id IN ('chatty', 'chatty-cli', 'vvault')),
  destination_table text NOT NULL CHECK (destination_table IN ('vault_files', 'transcripts')),
  destination_record_id uuid NOT NULL,
  projection_contract text NOT NULL,
  projection_version text NOT NULL,
  transform_sha256 text NOT NULL CHECK (transform_sha256 ~ '^[a-f0-9]{64}$'),
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
  destination_sha256 text NOT NULL CHECK (destination_sha256 ~ '^[a-f0-9]{64}$'),
  receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
  receipt_sha256 text NOT NULL CHECK (receipt_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_artifact_id, destination_table, destination_record_id,
          projection_contract, projection_version)
);

CREATE OR REPLACE FUNCTION ovvaults.reject_source_provenance_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'source-native provenance is append-only';
END;
$$;

CREATE TRIGGER source_native_artifacts_append_only
  BEFORE UPDATE OR DELETE ON ovvaults.source_native_artifacts
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_source_provenance_mutation();
CREATE TRIGGER source_ingest_receipts_append_only
  BEFORE UPDATE OR DELETE ON ovvaults.source_ingest_receipts
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_source_provenance_mutation();
CREATE TRIGGER canonical_source_projections_append_only
  BEFORE UPDATE OR DELETE ON ovvaults.canonical_source_projections
  FOR EACH ROW EXECUTE FUNCTION ovvaults.reject_source_provenance_mutation();

ALTER TABLE ovvaults.source_native_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.source_ingest_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.canonical_source_projections ENABLE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.source_native_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.source_ingest_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE ovvaults.canonical_source_projections FORCE ROW LEVEL SECURITY;

CREATE POLICY source_native_artifacts_owner_scope ON ovvaults.source_native_artifacts
  USING (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
         AND relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
              AND relying_party_id = current_setting('app.vvault_relying_party_id', true));
CREATE POLICY source_ingest_receipts_owner_scope ON ovvaults.source_ingest_receipts
  USING (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
         AND relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
              AND relying_party_id = current_setting('app.vvault_relying_party_id', true));
CREATE POLICY canonical_source_projections_owner_scope ON ovvaults.canonical_source_projections
  USING (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
         AND relying_party_id = current_setting('app.vvault_relying_party_id', true))
  WITH CHECK (owner_user_id::text = current_setting('app.vvault_authenticated_user_id', true)
              AND relying_party_id = current_setting('app.vvault_relying_party_id', true));

REVOKE UPDATE, DELETE ON ovvaults.source_native_artifacts FROM PUBLIC;
REVOKE UPDATE, DELETE ON ovvaults.source_ingest_receipts FROM PUBLIC;
REVOKE UPDATE, DELETE ON ovvaults.canonical_source_projections FROM PUBLIC;
