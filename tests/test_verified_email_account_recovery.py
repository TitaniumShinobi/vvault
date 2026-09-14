"""Focused contract checks for the verified-email device-factor recovery flow."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_recovery_magic_link_is_a_distinct_one_time_purpose():
    migration = (ROOT / "vvault/migrations/0037_verified_email_account_recovery.up.sql").read_text()
    actor_migration = (ROOT / "vvault/migrations/0038_verified_email_recovery_actor_context.up.sql").read_text()
    repository = (ROOT / "vvault/server/vvault_auth_repository.py").read_text()
    server = (ROOT / "vvault/server/vvault_web_server.py").read_text()

    assert "'recovery'" in migration
    assert "purpose IN ('signin', 'recovery')" in actor_migration
    assert "initiating_user_id IS NULL" in actor_migration
    assert 'purpose = "recovery" if intent == "ACCOUNT_RECOVERY" else "signin"' in server
    assert 'challenge.get("purpose") not in {"signin", "recovery"}' in server
    assert 'if purpose not in {"signin", "link", "recovery"}' in repository


def test_magic_link_request_never_claims_delivery_after_a_server_failure():
    server = (ROOT / "vvault/server/vvault_web_server.py").read_text()

    assert 'logger.warning("magic-link request not delivered: %s", type(exc).__name__)' in server
    assert 'return jsonify({"success": False, "error": "magic_link_delivery_failed"}), 503' in server


def test_recovery_requires_verified_owner_and_revokes_old_factors_before_reenrollment():
    repository = (ROOT / "vvault/server/vvault_auth_repository.py").read_text()
    server = (ROOT / "vvault/server/vvault_web_server.py").read_text()

    assert "def resolve_verified_email_owner" in repository
    assert "def begin_verified_email_recovery" in repository
    assert "users.account_state='ACTIVE'" in repository
    for statement in (
        "UPDATE sessions SET revoked_at=now()",
        "UPDATE enrollment_devices SET status='REVOKED'",
        "UPDATE enrollment_webauthn_credentials SET revoked_at=now()",
        "UPDATE enrollment_recovery_codes SET used_at=now()",
        "account_state='PENDING_ENROLLMENT'",
    ):
        assert statement in repository
    assert "begin_verified_email_recovery(email=email, expected_owner_id=str(owner[\"id\"]))" in server


def test_recovery_does_not_leave_stale_codes_blocking_reenrollment_or_expose_a_device_bypass():
    repository = (ROOT / "vvault/server/vvault_auth_repository.py").read_text()
    login = (ROOT / "src/components/CinematicLogin.js").read_text()
    enrollment = (ROOT / "src/components/EnrollmentFlow.js").read_text()

    assert "WHERE user_id=%s AND used_at IS NULL LIMIT 1 FOR UPDATE" in repository
    assert "intent:'ACCOUNT_RECOVERY'" in login
    assert "This resets lost device factors only" in login
    assert "Can’t use your passkey or recovery code?" in enrollment
    assert "Create a passkey" in enrollment
    assert "Generate recovery codes" in enrollment
