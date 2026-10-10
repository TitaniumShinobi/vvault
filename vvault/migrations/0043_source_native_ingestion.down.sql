-- Rollback is intentionally schema-only. Production execution requires the
-- migration runner's explicit destructive rollback authorization.
DROP TABLE IF EXISTS ovvaults.canonical_source_projections;
DROP TABLE IF EXISTS ovvaults.source_ingest_receipts;
DROP TABLE IF EXISTS ovvaults.source_native_artifacts;
DROP FUNCTION IF EXISTS ovvaults.reject_source_provenance_mutation();
