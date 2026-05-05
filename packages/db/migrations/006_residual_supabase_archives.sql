CREATE SCHEMA IF NOT EXISTS ovvaults;
SET search_path TO ovvaults, public;

CREATE TABLE IF NOT EXISTS supabase_residual_archives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_table TEXT NOT NULL,
    source_row_id TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_class TEXT NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_table, source_row_id)
);

CREATE TABLE IF NOT EXISTS supabase_residual_retirement_manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_row_count INTEGER NOT NULL DEFAULT 0 CHECK (source_row_count >= 0),
    target_owner TEXT NOT NULL,
    target_store TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('imported', 'archived', 'discarded', 'blocked')),
    imported_count INTEGER NOT NULL DEFAULT 0 CHECK (imported_count >= 0),
    archived_count INTEGER NOT NULL DEFAULT 0 CHECK (archived_count >= 0),
    discarded_count INTEGER NOT NULL DEFAULT 0 CHECK (discarded_count >= 0),
    aggregate_checksum_sha256 TEXT,
    blocker TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, source_table)
);

CREATE INDEX IF NOT EXISTS idx_supabase_residual_archives_table
    ON supabase_residual_archives(source_table);
CREATE INDEX IF NOT EXISTS idx_supabase_residual_archives_class
    ON supabase_residual_archives(payload_class);
CREATE INDEX IF NOT EXISTS idx_supabase_residual_retirement_manifests_run
    ON supabase_residual_retirement_manifests(run_id);
