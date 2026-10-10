from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_native_migration_is_additive_owner_scoped_and_append_only():
    sql = (ROOT / "vvault/migrations/0043_source_native_ingestion.up.sql").read_text()
    assert "raw_envelope bytea NOT NULL" in sql
    assert "payload bytea" in sql
    assert "LEGACY_UNCLASSIFIED" in sql and "legacy-unassigned" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "app.vvault_authenticated_user_id" in sql
    assert "app.vvault_relying_party_id" in sql
    assert "reject_source_provenance_mutation" in sql
    assert sql.count("REVOKE UPDATE, DELETE") == 3
    assert "UPDATE ovvaults.vault_files" not in sql
    assert "DELETE FROM ovvaults.vault_files" not in sql
    assert "UPDATE ovvaults.transcripts" not in sql
    assert "DELETE FROM ovvaults.transcripts" not in sql


def test_source_native_down_migration_names_only_new_objects():
    sql = (ROOT / "vvault/migrations/0043_source_native_ingestion.down.sql").read_text()
    assert "canonical_source_projections" in sql
    assert "source_ingest_receipts" in sql
    assert "source_native_artifacts" in sql
    assert "vault_files" not in sql
    assert "transcripts" not in sql
