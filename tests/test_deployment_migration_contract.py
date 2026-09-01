from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deployment/droplet-deploy-vvault.sh").read_text(encoding="utf-8")
RUNNER = (ROOT / "scripts/deployment/apply-vvault-enrollment-migrations.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/deploy-ci.yml").read_text(encoding="utf-8")


def test_deploy_applies_enrollment_migrations_before_restart_and_refuses_automatic_rollback():
    assert "apply-vvault-enrollment-migrations.sh" in DEPLOY
    assert DEPLOY.index("apply-vvault-enrollment-migrations.sh") < DEPLOY.index('log "restarting $SERVICE"')
    assert "MIGRATIONS_APPLIED=1" in DEPLOY
    assert "automatic code rollback is prohibited" in DEPLOY
    assert "forward-only" in DEPLOY


def test_migration_runner_requires_verified_backup_receipts_and_uses_checksum_ledger():
    for required in (
        "VVAULT_DATABASE_BACKUP_RECEIPT_PATH",
        "VVAULT_DATABASE_BACKUP_RECEIPT_ID",
        "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH",
        "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID",
        'receipt.get("verified") is not True',
        "ovvaults.schema_migrations",
        "pg_advisory_xact_lock",
        "checksum mismatch",
        "0033_identity_directory.up.sql",
        "0034_enrollment_session_hardening.up.sql",
        "0035_chatty_pairing_intents.up.sql",
        "forward_only_restore_verified_backup_required",
    ):
        assert required in RUNNER


def test_migration_runner_does_not_attempt_backup_restore_or_expose_database_url_to_psql_argv():
    assert "pg_dump" not in RUNNER
    assert "pg_restore" not in RUNNER
    assert 'log "$DATABASE_URL"' not in RUNNER
    assert "PGSERVICEFILE=\"$connection_service\"" in RUNNER
    assert "PGSERVICE=\"vvault_enrollment_migrations\"" in RUNNER
    assert 'psql --no-psqlrc -X -v ON_ERROR_STOP=1 -f "$sql_file"' in RUNNER


def test_migration_runner_has_a_no_database_dry_run_for_receipt_and_file_preflight():
    assert 'VVAULT_MIGRATION_DRY_RUN' in RUNNER
    assert 'dry run passed' in RUNNER
    assert 'exit 0' in RUNNER


def test_github_deployment_executes_the_reviewed_repository_contract_not_a_host_trigger():
    assert "/opt/deploy/trigger/deploy-trigger.sh" not in WORKFLOW
    assert 'git -C "$repo" checkout -B production origin/production' in WORKFLOW
    assert 'exec "$repo/scripts/deployment/droplet-deploy-vvault.sh"' in WORKFLOW
