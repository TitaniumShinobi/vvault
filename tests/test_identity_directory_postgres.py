from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vvault.server import vvault_auth_repository as auth_repository


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "vvault/migrations/0033_identity_directory.up.sql"
DOWN = ROOT / "vvault/migrations/0033_identity_directory.down.sql"
UP_0034 = ROOT / "vvault/migrations/0034_enrollment_session_hardening.up.sql"
DOWN_0034 = ROOT / "vvault/migrations/0034_enrollment_session_hardening.down.sql"
UP_0035 = ROOT / "vvault/migrations/0035_chatty_pairing_intents.up.sql"
MIGRATION_RUNNER = ROOT / "scripts/deployment/apply-vvault-enrollment-migrations.sh"


def _binary(name: str) -> str | None:
    preferred = Path("/opt/homebrew/opt/postgresql@18/bin") / name
    return str(preferred) if preferred.exists() else shutil.which(name)


def _run(command: list[str], *, input_text: str | None = None, check: bool = True, env: dict[str, str] | None = None):
    return subprocess.run(command, input=input_text, text=True, capture_output=True, check=check, env=env)


@pytest.fixture(scope="module")
def identity_postgres():
    binaries = {name: _binary(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(binaries.values()):
        pytest.skip("PostgreSQL binaries are unavailable")
    root = Path(tempfile.mkdtemp(prefix="vvault-identity-pg-", dir="/private/tmp"))
    data, socket = root / "data", root / "socket"
    socket.mkdir()
    port = 62000 + (os.getpid() % 1000)
    started = False
    try:
        _run([binaries["initdb"], "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale", "--encoding=UTF8"])
        _run([binaries["pg_ctl"], "-D", str(data), "-l", str(root / "postgres.log"), "-o", f"-F -k {socket} -p {port} -c listen_addresses=''", "-w", "start"])
        started = True
        psql = [binaries["psql"], "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket), "-p", str(port), "-U", "postgres", "postgres"]
        _run(psql, input_text="""
          CREATE EXTENSION IF NOT EXISTS citext;
          CREATE SCHEMA ovvaults;
          CREATE TABLE ovvaults.users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email citext NOT NULL UNIQUE,
            password_hash text NOT NULL, name text, role text NOT NULL DEFAULT 'user',
            auth_provider text, oauth_provider text, oauth_subject text, avatar_url text,
            last_login_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
          );
          CREATE TABLE ovvaults.sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES ovvaults.users(id),
            token_hash text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL, revoked_at timestamptz
          );
        """)
        database_receipt = root / "database-backup.json"
        object_receipt = root / "object-storage-backup.json"
        database_receipt.write_text(json.dumps({"kind": "database", "backup_id": "test-db-0001", "verified": True}))
        object_receipt.write_text(json.dumps({"kind": "object_storage", "backup_id": "test-obj-0001", "verified": True}))
        runner_env = os.environ | {
            "VVAULT_BODY_DATABASE_URL": f"postgresql:///postgres?host={socket}&port={port}&user=postgres",
            "VVAULT_DATABASE_BACKUP_RECEIPT_PATH": str(database_receipt),
            "VVAULT_DATABASE_BACKUP_RECEIPT_ID": "test-db-0001",
            "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH": str(object_receipt),
            "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID": "test-obj-0001",
            "VVAULT_MIGRATION_RECEIPT_DIR": str(root / "migration-receipts"),
            "VVAULT_DEPLOY_REF": "disposable-postgres-proof",
        }
        migration = _run([str(MIGRATION_RUNNER)], env=runner_env, check=False)
        assert migration.returncode == 0, migration.stderr
        ledger = _run(psql + ["-tA"], input_text="SELECT version FROM ovvaults.schema_migrations ORDER BY version;")
        assert ledger.stdout.split() == ["0033", "0034", "0035"]
        yield f"host={socket} port={port} user=postgres dbname=postgres"
    finally:
        if started:
            _run([binaries["pg_ctl"], "-D", str(data), "-m", "immediate", "-w", "stop"], check=False)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def repository(identity_postgres, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    def connect():
        return psycopg.connect(identity_postgres, row_factory=dict_row, options="-c search_path=ovvaults,public")

    monkeypatch.setattr(auth_repository.chatty_body_service, "_connect", connect)
    return auth_repository.VVaultAuthRepository()


def test_provider_subject_is_the_only_automatic_account_key(repository):
    first, created = repository.admit_verified_identity(
        provider="google", provider_subject="google-subject-a", verified_email="same@example.com", name="A",
        issuer="https://accounts.google.com",
    )
    assert created and first["account_state"] == "PENDING_ENROLLMENT"
    same, created_again = repository.admit_verified_identity(
        provider="google", provider_subject="google-subject-a", verified_email="renamed@example.com", name="A2",
    )
    assert not created_again and same["id"] == first["id"]
    second, second_created = repository.admit_verified_identity(
        provider="github", provider_subject="github-subject-b", verified_email="same@example.com", name="B",
    )
    assert second_created and second["id"] != first["id"]


def test_same_subject_race_creates_one_account(repository):
    def admit(_):
        return repository.admit_verified_identity(
            provider="github", provider_subject="racing-subject", verified_email="race@example.com", name="Race",
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, range(2)))
    assert sum(1 for _row, created in outcomes if created) == 1
    assert outcomes[0][0]["id"] == outcomes[1][0]["id"]


def test_oauth_and_magic_tokens_are_atomically_single_use(repository):
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    repository.create_oauth_transaction(
        state_digest="state-digest", provider="google", purpose="signin", nonce_digest="nonce",
        nonce_ciphertext=b"encrypted-nonce", pkce_verifier_digest="verifier",
        pkce_verifier_ciphertext=b"encrypted-verifier", redirect_uri="https://vvault.example/callback",
        frontend_origin="https://vvault.example", expires_at=expiry,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: repository.consume_oauth_transaction("state-digest"), range(2)))
    assert sum(value is not None for value in outcomes) == 1
    repository.issue_magic_link_challenge(
        token_digest="magic-digest", normalized_email="magic@example.com", purpose="signin",
        redirect_uri="https://vvault.example/#/consume", expires_at=expiry,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: repository.consume_magic_link_challenge("magic-digest"), range(2)))
    assert sum(value is not None for value in outcomes) == 1


def test_linking_requires_recent_reauthentication_and_rejects_collision(repository):
    user, _ = repository.admit_verified_identity(
        provider="email", provider_subject="owner@example.com", verified_email="owner@example.com", name="Owner",
    )
    other, _ = repository.admit_verified_identity(
        provider="github", provider_subject="taken-subject", verified_email="other@example.com", name="Other",
    )
    import psycopg
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET account_state='ACTIVE' WHERE id=%s", (user["id"],))
            cur.execute("INSERT INTO sessions(user_id, token_hash, expires_at) VALUES(%s,'owner-session',now()+interval '1 hour') RETURNING id", (user["id"],))
            session_id = str(cur.fetchone()["id"])
        conn.commit()
    assert not repository.link_verified_identity(
        user_id=str(user["id"]), session_id=session_id, provider="google", provider_subject="new-subject",
        verified_email="owner@example.com",
    )
    assert repository.record_session_reauthentication(session_id=session_id, user_id=str(user["id"]), provider="email")
    assert repository.link_verified_identity(
        user_id=str(user["id"]), session_id=session_id, provider="google", provider_subject="new-subject",
        verified_email="owner@example.com", issuer="https://accounts.google.com",
    )
    assert not repository.link_verified_identity(
        user_id=str(user["id"]), session_id=session_id, provider="github", provider_subject="taken-subject",
        verified_email="owner@example.com",
    )
    assert other["id"] != user["id"]


def test_rollback_refuses_destructive_identity_removal(identity_postgres):
    import psycopg
    with psycopg.connect(identity_postgres) as conn:
        with pytest.raises(psycopg.Error, match="Refusing destructive rollback"):
            conn.execute(DOWN.read_text())
        conn.rollback()
        with pytest.raises(psycopg.Error, match="Refusing destructive rollback"):
            conn.execute(DOWN_0034.read_text())


def test_0034_completes_enrollment_with_rotated_device_bound_session(repository):
    user, _ = repository.admit_verified_identity(
        provider="google", provider_subject="enrollment-subject", verified_email="enroll@example.com", name="Enroll",
        issuer="https://accounts.google.com",
    )
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    pending = repository.create_pending_enrollment_session(
        user_id=str(user["id"]), device_secret_digest="pending-device-digest", token_hash="pending-token",
        expires_at=expires, label="Test browser",
    )
    assert pending and pending["enrollment_session_kind"] == "PENDING_ENROLLMENT"
    documents = [
        {"key": "terms", "version": "2026-09", "sha256": "terms-hash"},
        {"key": "privacy", "version": "2026-09", "sha256": "privacy-hash"},
    ]
    assert repository.record_enrollment_consents(
        user_id=str(user["id"]), session_id=str(pending["id"]), documents=documents,
    )
    assert repository.create_webauthn_registration_challenge(
        user_id=str(user["id"]), session_id=str(pending["id"]), challenge_digest="challenge-digest",
        rp_id="vvault.example", allowed_origin="https://vvault.example", expires_at=expires,
    )
    assert repository.consume_webauthn_registration_challenge(
        user_id=str(user["id"]), session_id=str(pending["id"]), challenge_digest="challenge-digest",
    ) == {"rp_id": "vvault.example", "allowed_origin": "https://vvault.example"}
    assert repository.store_webauthn_credential(
        user_id=str(user["id"]), credential_id="credential_identifier_012345", public_key=b"public-key",
        sign_count=0, transports=["internal"], user_verified=True,
    )
    assert repository.replace_recovery_codes(
        user_id=str(user["id"]), session_id=str(pending["id"]),
        code_digests=[f"recovery-{index}" for index in range(8)],
    )
    normal = repository.complete_enrollment(
        user_id=str(user["id"]), pending_session_id=str(pending["id"]),
        device_id=str(pending["enrollment_device_id"]), normal_token_hash="normal-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), required_documents=documents,
    )
    assert normal and normal["enrollment_session_kind"] == "NORMAL"
    assert repository.get_enrollment_session_by_hash("pending-token") is None
    current = repository.get_enrollment_session_by_hash("normal-token")
    assert current and current["account_state"] == "ACTIVE" and current["device_status"] == "TRUSTED"


def test_0034_pending_device_requires_approval_or_one_time_recovery(repository):
    user, _ = repository.admit_verified_identity(
        provider="github", provider_subject="active-owner-subject", verified_email="active@example.com", name="Active",
    )
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET account_state='ACTIVE' WHERE id=%s", (user["id"],))
            cur.execute(
                """INSERT INTO enrollment_devices(user_id, device_secret_digest, status, approved_by_user_id, approved_at)
                   VALUES(%s,'trusted-device','TRUSTED',%s,now()) RETURNING id""",
                (user["id"], user["id"]),
            )
            trusted_device = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id)
                   VALUES(%s,'trusted-session',now()+interval '1 hour','NORMAL',%s) RETURNING id""",
                (user["id"], trusted_device),
            )
            trusted_session = str(cur.fetchone()["id"])
            for index in range(8):
                cur.execute("INSERT INTO enrollment_recovery_codes(user_id, code_digest) VALUES(%s,%s)", (user["id"], f"recover-{index}"))
        conn.commit()
    pending = repository.issue_pending_device_session(
        user_id=str(user["id"]), device_secret_digest="new-device", token_hash="new-pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert pending and pending["enrollment_session_kind"] == "PENDING_DEVICE"
    normal = repository.approve_pending_device(
        actor_user_id=str(user["id"]), actor_session_id=trusted_session,
        pending_session_id=str(pending["id"]), normal_token_hash="new-normal",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert normal and normal["enrollment_session_kind"] == "NORMAL"
    assert repository.revoke_enrollment_device(
        actor_user_id=str(user["id"]), actor_session_id=trusted_session,
        device_id=str(normal["enrollment_device_id"]),
    )
    assert repository.get_enrollment_session_by_hash("new-normal") is None


def test_0035_pairing_intent_is_active_trusted_single_use_and_uniquely_bound(repository):
    """Exercise the forward pairing migration against a disposable PostgreSQL server.

    This is deliberately repository-level rather than a route mock: it proves the
    migrated constraints and atomic UPDATE used by the server-to-server redeem path.
    """
    user, _ = repository.admit_verified_identity(
        provider="google", provider_subject="pairing-owner", verified_email="pairing@example.com", name="Pairing",
    )
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET account_state='ACTIVE' WHERE id=%s", (user["id"],))
            cur.execute(
                """INSERT INTO enrollment_devices(user_id, device_secret_digest, status, approved_by_user_id, approved_at)
                   VALUES(%s,'pairing-trusted-device','TRUSTED',%s,now()) RETURNING id""",
                (user["id"], user["id"]),
            )
            device_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id)
                   VALUES(%s,'pairing-trusted-session',now()+interval '1 hour','NORMAL',%s) RETURNING id""",
                (user["id"], device_id),
            )
            session_id = str(cur.fetchone()["id"])
        conn.commit()

    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    callback = "http://127.0.0.1:5050/api/vvault/pairing/callback"
    assert repository.create_chatty_pairing_intent(
        code_digest="pairing-code-one", user_id=str(user["id"]), session_id=session_id,
        callback_uri=callback, expires_at=expiry,
    )
    account_id = "33333333-3333-4333-8333-333333333333"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: repository.consume_chatty_pairing_intent(
                code_digest="pairing-code-one", callback_uri=callback, chatty_account_id=account_id,
            ),
            range(2),
        ))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)["audience"] == "chatty-developer-local"

    assert repository.create_chatty_pairing_intent(
        code_digest="pairing-code-two", user_id=str(user["id"]), session_id=session_id,
        callback_uri=callback, expires_at=expiry,
    )
    # A Chatty account cannot be paired twice, even with distinct opaque codes.
    assert repository.consume_chatty_pairing_intent(
        code_digest="pairing-code-two", callback_uri=callback, chatty_account_id=account_id,
    ) is None
