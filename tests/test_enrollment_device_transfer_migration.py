from pathlib import Path


def test_device_transfer_migration_creates_only_digest_backed_transfer_state():
    source = (Path(__file__).parents[1] / "vvault/migrations/0036_enrollment_device_transfer_codes.up.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS ovvaults.enrollment_device_transfer_codes" in source
    assert "code_digest TEXT PRIMARY KEY" in source
    assert "user_id UUID NOT NULL REFERENCES ovvaults.users(id)" in source
    assert "pending_session_id UUID NOT NULL REFERENCES ovvaults.sessions(id)" in source
    assert "WHERE consumed_at IS NULL" in source
    assert "plaintext" in source
