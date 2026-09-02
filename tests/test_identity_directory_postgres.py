from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
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
    # Some developer hosts already run a disposable PostgreSQL instance and
    # cannot allocate another macOS SysV shared-memory segment.  Reuse only an
    # explicitly supplied *test* server, inside a fresh per-run database; the
    # default remains a fully self-contained initdb cluster.
    external_url = os.environ.get("VVAULT_TEST_POSTGRES_URL", "").strip()
    if external_url:
        parsed = urlsplit(external_url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise RuntimeError("VVAULT_TEST_POSTGRES_URL must identify a PostgreSQL test server")
        database = f"vvault_identity_{os.getpid()}_{uuid4().hex[:10]}"
        test_url = urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, ""))
        admin = [binaries["psql"], "-X", "-v", "ON_ERROR_STOP=1", external_url]
        _run(admin + ["-c", f'CREATE DATABASE "{database}"'])
        try:
            yield from _configured_identity_database(
                psql=[binaries["psql"], "-X", "-v", "ON_ERROR_STOP=1", test_url],
                database_url=test_url,
                receipt_root=Path(tempfile.mkdtemp(prefix="vvault-identity-receipts-", dir="/private/tmp")),
            )
        finally:
            _run(admin + ["-c", f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'], check=False)
        return
    root = Path(tempfile.mkdtemp(prefix="vvault-identity-pg-", dir="/private/tmp"))
    data, socket = root / "data", root / "socket"
    socket.mkdir()
    port = 62000 + (os.getpid() % 1000)
    started = False
    try:
        _run([binaries["initdb"], "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale", "--encoding=UTF8"])
        _run([binaries["pg_ctl"], "-D", str(data), "-l", str(root / "postgres.log"), "-o", f"-F -k {socket} -p {port} -c listen_addresses=''", "-w", "start"])
        started = True
        yield from _configured_identity_database(
            psql=[binaries["psql"], "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket), "-p", str(port), "-U", "postgres", "postgres"],
            database_url=f"postgresql:///postgres?host={socket}&port={port}&user=postgres",
            receipt_root=root,
        )
    finally:
        if started:
            _run([binaries["pg_ctl"], "-D", str(data), "-m", "immediate", "-w", "stop"], check=False)
        shutil.rmtree(root, ignore_errors=True)


def _configured_identity_database(*, psql: list[str], database_url: str, receipt_root: Path):
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
          CREATE TABLE ovvaults.vault_files (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES ovvaults.users(id),
            name text NOT NULL DEFAULT 'fixture'
          );
          -- Production owns an older, unrelated migration ledger. The release
          -- runner must leave it alone rather than assuming a `version` column.
          CREATE TABLE ovvaults.schema_migrations (
            migration_name text PRIMARY KEY, applied_on timestamptz NOT NULL DEFAULT now()
          );
    """)
    database_receipt = receipt_root / "database-backup.json"
    object_receipt = receipt_root / "object-storage-backup.json"
    database_receipt.write_text(json.dumps({"kind": "database", "backup_id": "test-db-0001", "verified": True}))
    object_receipt.write_text(json.dumps({"kind": "object_storage", "backup_id": "test-obj-0001", "verified": True}))
    runner_env = os.environ | {
        "VVAULT_BODY_DATABASE_URL": database_url,
        "VVAULT_DATABASE_BACKUP_RECEIPT_PATH": str(database_receipt),
        "VVAULT_DATABASE_BACKUP_RECEIPT_ID": "test-db-0001",
        "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_PATH": str(object_receipt),
        "VVAULT_OBJECT_STORAGE_BACKUP_RECEIPT_ID": "test-obj-0001",
        "VVAULT_MIGRATION_RECEIPT_DIR": str(receipt_root / "migration-receipts"),
        "VVAULT_DEPLOY_REF": "disposable-postgres-proof",
    }
    migration = _run([str(MIGRATION_RUNNER)], env=runner_env, check=False)
    assert migration.returncode == 0, migration.stderr
    ledger = _run(psql + ["-tA"], input_text="SELECT version FROM ovvaults.enrollment_schema_migrations ORDER BY version;")
    assert ledger.stdout.split() == ["0033", "0034", "0035", "0036"]
    yield database_url


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


def test_legacy_owner_upgrades_in_place_after_current_legal_and_enrollment(repository):
    """A returning pre-identity owner keeps its UUID and Vault through upgrade."""
    legacy_id = "11111111-1111-4111-8111-111111111111"
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users(id,email,password_hash,name,role,account_state)
                   VALUES(%s,%s,'!','Existing Owner','user','LEGACY')""",
                (legacy_id, "existing@example.test"),
            )
            cur.execute("INSERT INTO vault_files(user_id,name) VALUES(%s,'continuity-file')", (legacy_id,))
        conn.commit()
    owner, created = repository.admit_verified_identity(
        provider="google", provider_subject="existing-google-subject",
        verified_email="existing@example.test", name="Existing Owner",
        issuer="https://accounts.google.com", allow_legacy_compatibility=True,
    )
    assert not created and str(owner["id"]) == legacy_id
    assert owner["account_state"] == "LEGACY" and owner["_legacy_continuity"] is True
    pending = repository.create_legacy_consent_session(
        user_id=legacy_id, token_hash="legacy-receipt-token",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert pending and pending["enrollment_session_kind"] == "LEGACY"
    completed = repository.complete_legacy_consent(
        user_id=legacy_id, pending_session_id=str(pending["id"]), normal_token_hash="legacy-normal-token",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        documents=[
            {"key": "terms", "version": "current", "sha256": "terms"},
            {"key": "privacy", "version": "current", "sha256": "privacy"},
            {"key": "eeccd", "version": "current", "sha256": "eeccd"},
        ],
        device_secret_digest="legacy-upgrade-device",
    )
    assert completed and completed["enrollment_session_kind"] == "PENDING_ENROLLMENT"
    assert repository.create_webauthn_registration_challenge(
        user_id=legacy_id, session_id=str(completed["id"]), challenge_digest="legacy-upgrade-challenge",
        rp_id="vvault.example", allowed_origin="https://vvault.example", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert repository.store_webauthn_credential(
        user_id=legacy_id, credential_id="legacy_upgrade_credential_012345", public_key=b"public-key",
        sign_count=0, transports=["internal"], user_verified=True,
    )
    assert repository.replace_recovery_codes(
        user_id=legacy_id, session_id=str(completed["id"]),
        code_digests=[f"legacy-recovery-{index}" for index in range(8)],
    )
    active = repository.complete_enrollment(
        user_id=legacy_id, pending_session_id=str(completed["id"]), device_id=str(completed["enrollment_device_id"]),
        normal_token_hash="legacy-active-token", expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        required_documents=[
            {"key": "terms", "version": "current", "sha256": "terms"},
            {"key": "privacy", "version": "current", "sha256": "privacy"},
            {"key": "eeccd", "version": "current", "sha256": "eeccd"},
        ],
    )
    assert active and active["enrollment_session_kind"] == "NORMAL"
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_state FROM users WHERE id=%s", (legacy_id,))
            assert cur.fetchone()["account_state"] == "ACTIVE"
            cur.execute("SELECT user_id FROM vault_files WHERE name='continuity-file'")
            assert str(cur.fetchone()["user_id"]) == legacy_id


def test_trusted_owner_approves_only_its_own_pending_device_transfer(repository):
    """An opaque transfer code cannot cross owner boundaries or issue an old-device session."""
    owner = "33333333-3333-4333-8333-333333333333"
    other_owner = "44444444-4444-4444-8444-444444444444"
    with repository._connect() as conn:
        with conn.cursor() as cur:
            for user_id, email in ((owner, "owner@example.test"), (other_owner, "other@example.test")):
                cur.execute("INSERT INTO users(id,email,password_hash,name,role,account_state) VALUES(%s,%s,'!','Owner','user','ACTIVE')", (user_id, email))
            cur.execute(
                """INSERT INTO enrollment_devices(user_id,device_secret_digest,status,approved_by_user_id,approved_at)
                   VALUES(%s,'trusted-owner','TRUSTED',%s,now()) RETURNING id""",
                (owner, owner),
            )
            trusted_device = str(cur.fetchone()["id"])
            cur.execute("INSERT INTO sessions(user_id,token_hash,expires_at,enrollment_session_kind,enrollment_device_id) VALUES(%s,'trusted-owner-session',now()+interval '1 hour','NORMAL',%s) RETURNING id", (owner, trusted_device))
            trusted_session = str(cur.fetchone()["id"])
            cur.execute("INSERT INTO enrollment_devices(user_id,device_secret_digest,status) VALUES(%s,'pending-owner','PENDING') RETURNING id", (owner,))
            pending_device = str(cur.fetchone()["id"])
            cur.execute("INSERT INTO sessions(user_id,token_hash,expires_at,enrollment_session_kind,enrollment_device_id) VALUES(%s,'pending-owner-session',now()+interval '1 hour','PENDING_DEVICE',%s) RETURNING id", (owner, pending_device))
            pending_session = str(cur.fetchone()["id"])
        conn.commit()
    assert repository.create_pending_device_transfer(user_id=owner, pending_session_id=pending_session, code_digest="transfer-digest", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert not repository.approve_pending_device_transfer(actor_user_id=other_owner, actor_session_id=trusted_session, code_digest="transfer-digest")
    assert repository.approve_pending_device_transfer(actor_user_id=owner, actor_session_id=trusted_session, code_digest="transfer-digest")
    normal = repository.complete_approved_pending_device(user_id=owner, pending_session_id=pending_session, normal_token_hash="new-device-normal", expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    assert normal and normal["enrollment_session_kind"] == "NORMAL"
    assert not repository.complete_approved_pending_device(user_id=owner, pending_session_id=pending_session, normal_token_hash="replay", expires_at=datetime.now(timezone.utc) + timedelta(days=30))


def test_pending_google_subject_returns_to_its_one_legacy_owner_without_moving_vault_data(repository):
    """Recover only an empty mistaken pending row created before the legacy bridge."""
    legacy_id = "22222222-2222-4222-8222-222222222222"
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users(id,email,password_hash,name,role,account_state)
                   VALUES(%s,%s,'!','Canonical Owner','user','LEGACY')""",
                (legacy_id, "canonical@example.test"),
            )
            cur.execute("INSERT INTO vault_files(user_id,name) VALUES(%s,'already-owned')", (legacy_id,))
        conn.commit()
    pending, created = repository.admit_verified_identity(
        provider="google", provider_subject="mistaken-pending-subject",
        verified_email="canonical@example.test", name="Canonical Owner",
    )
    assert created and pending["account_state"] == "PENDING_ENROLLMENT"
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions(user_id,token_hash,expires_at)
                   VALUES(%s,'pending-session',now()+interval '1 hour')""",
                (pending["id"],),
            )
        conn.commit()
    owner, created_again = repository.admit_verified_identity(
        provider="google", provider_subject="mistaken-pending-subject",
        verified_email="canonical@example.test", name="Canonical Owner",
        allow_legacy_compatibility=True,
    )
    assert not created_again and str(owner["id"]) == legacy_id
    assert owner["_legacy_continuity"] is True
    with repository._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM external_identities WHERE provider_subject='mistaken-pending-subject'")
            assert str(cur.fetchone()["user_id"]) == legacy_id
            cur.execute("SELECT user_id FROM vault_files WHERE name='already-owned'")
            assert str(cur.fetchone()["user_id"]) == legacy_id
            cur.execute("SELECT revoked_at IS NOT NULL AS revoked FROM sessions WHERE token_hash='pending-session'")
            assert cur.fetchone()["revoked"] is True


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
