from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deployment/droplet-deploy-vvault.sh").read_text(encoding="utf-8")
RUNNER = (ROOT / "scripts/deployment/apply-vvault-enrollment-migrations.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/deploy-ci.yml").read_text(encoding="utf-8")
BACKUP_RECEIPTS = (ROOT / "scripts/deployment/create-vvault-enrollment-backup-receipts.py").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / ".github/workflows/bootstrap-vvault-backup-tools.yml").read_text(encoding="utf-8")


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
    assert 'git -C "$repo" fetch origin production:refs/remotes/origin/production' in WORKFLOW
    assert 'git -C "$repo" checkout -B production origin/production' in WORKFLOW
    assert 'git -C "$repo" status --porcelain --untracked-files=normal' in WORKFLOW
    assert 'git -C "$repo" reset --hard origin/production' in WORKFLOW
    assert 'exec "$repo/scripts/deployment/droplet-deploy-vvault.sh"' in WORKFLOW


def test_deployment_creates_private_verified_recovery_receipts_before_migration():
    assert "create-vvault-enrollment-backup-receipts.py" in DEPLOY
    assert DEPLOY.index("prepare_enrollment_recovery_receipts") < DEPLOY.index("apply-vvault-enrollment-migrations.sh")
    assert DEPLOY.rindex("ensure_backup_tools") < DEPLOY.rindex("prepare_enrollment_recovery_receipts")
    for required in (
        "pg_dump",
        "pg_restore",
        '"kind": kind',
        '"verified": True',
        "VVAULT_OBJECT_STORAGE_SERVICE_KEY",
        "S3_SECRET_ACCESS_KEY",
        "stdout contains only receipt paths and opaque receipt IDs",
    ):
        assert required in BACKUP_RECEIPTS


def test_deployment_installs_postgres_client_only_when_required_for_recovery_copy():
    assert 'command -v pg_dump >/dev/null 2>&1 && command -v pg_restore >/dev/null 2>&1' in DEPLOY
    assert 'sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client' in DEPLOY


def test_one_time_bootstrap_is_root_scoped_to_postgresql_client_only():
    assert 'username: root' in BOOTSTRAP
    assert 'workflow_dispatch:' in BOOTSTRAP
    assert 'apt-get install -y -qq postgresql-client' in BOOTSTRAP
    assert 'VVAULT_DEPLOY_KEY' in BOOTSTRAP


def test_deployment_resolves_a_service_environment_file_without_printing_values():
    assert 'systemctl show "$SERVICE" -p EnvironmentFiles --value' in DEPLOY
    assert 'grep -q \'^VVAULT_BODY_DATABASE_URL=.\' "$candidate"' in DEPLOY
    assert 'systemctl show "$SERVICE" -p Environment --value 2>/dev/null | grep \'VVAULT_BODY_DATABASE_URL=\' >/dev/null' in DEPLOY
    assert 'runtime database configuration is missing from the service' in DEPLOY
    assert 'read_systemd_environment' in BACKUP_RECEIPTS
    assert 'stdout=subprocess.PIPE' in BACKUP_RECEIPTS
