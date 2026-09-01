from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "vvault/migrations/0032_deny_by_default_enrollment.up.sql"
DOWN = ROOT / "vvault/migrations/0032_deny_by_default_enrollment.down.sql"


def _run(command, *, input_text=None, check=True):
    return subprocess.run(
        command, input=input_text, text=True, capture_output=True, check=check,
    )


@pytest.fixture(scope="module")
def enrollment_postgres():
    required = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(required.values()):
        pytest.skip("PostgreSQL binaries are unavailable")
    root = Path(tempfile.mkdtemp(prefix="vvault-enrollment-pg-", dir="/private/tmp"))
    data_dir, socket_dir = root / "pgdata", root / "socket"
    socket_dir.mkdir()
    port = 61000 + (os.getpid() % 1000)
    started = False
    try:
        _run([
            required["initdb"], "-D", str(data_dir), "-A", "trust", "-U", "postgres",
            "--no-locale", "--encoding=UTF8",
        ])
        _run([
            required["pg_ctl"], "-D", str(data_dir), "-l", str(root / "postgres.log"),
            "-o", f"-F -k {socket_dir} -p {port} -c listen_addresses=''", "-w", "start",
        ])
        started = True
        psql = [
            required["psql"], "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket_dir),
            "-p", str(port), "-U", "postgres", "postgres",
        ]
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
          INSERT INTO ovvaults.users(email,password_hash,name,role)
            VALUES ('legacy@example.com','legacy-disabled','Legacy Owner','admin');
        """)
        import psycopg
        from psycopg.rows import dict_row
        from vvault.server.vvault_auth_repository import VVaultAuthRepository

        class PreMigrationRepository(VVaultAuthRepository):
            def _connect(self):
                return psycopg.connect(
                    f"host={socket_dir} port={port} user=postgres dbname=postgres",
                    row_factory=dict_row,
                    options="-c search_path=ovvaults,public",
                )

        pre_migration_auth_ready = PreMigrationRepository().healthcheck()["ready"]
        _run(psql, input_text=UP.read_text())
        _run(psql, input_text=UP.read_text())
        yield {
            "psql": psql,
            "dsn": f"host={socket_dir} port={port} user=postgres dbname=postgres",
            "pre_migration_auth_ready": pre_migration_auth_ready,
        }
    finally:
        if started:
            _run([required["pg_ctl"], "-D", str(data_dir), "-m", "immediate", "-w", "stop"], check=False)
        shutil.rmtree(root, ignore_errors=True)


def test_0032_constraints_immutability_and_rollback(enrollment_postgres):
    assert enrollment_postgres["pre_migration_auth_ready"] is False
    psql = enrollment_postgres["psql"]
    ids = _run(psql, input_text="""
      SET search_path=ovvaults,public;
      INSERT INTO users(email,password_hash,name,enrollment_status) VALUES
        ('a@example.com','!','A','ACTIVE'),('b@example.com','!','B','ACTIVE');
      INSERT INTO trusted_devices(user_id,device_secret_digest,status,approved_by_user_id,approved_at)
        SELECT id,'a-device','TRUSTED',id,now() FROM users WHERE email='a@example.com';
    """)
    assert ids.returncode == 0
    wrong_owner = _run(psql, input_text="""
      SET search_path=ovvaults,public;
      INSERT INTO sessions(user_id,token_hash,expires_at,session_kind,device_id)
      SELECT b.id,'wrong-owner',now()+interval '1 hour','normal',d.id
      FROM users b CROSS JOIN trusted_devices d WHERE b.email='b@example.com' AND d.device_secret_digest='a-device';
    """, check=False)
    assert wrong_owner.returncode != 0
    assert "session device owner mismatch" in wrong_owner.stderr

    inactive_normal = _run(psql, input_text="""
      SET search_path=ovvaults,public;
      INSERT INTO users(email,password_hash,name,enrollment_status)
        VALUES ('pending@example.com','!','Pending','PENDING_ENROLLMENT');
      INSERT INTO trusted_devices(user_id,device_secret_digest,status,approved_by_user_id,approved_at)
        SELECT id,'pending-approved-device','TRUSTED',id,now() FROM users WHERE email='pending@example.com';
      INSERT INTO sessions(user_id,token_hash,expires_at,session_kind,device_id)
        SELECT u.id,'inactive-normal',now()+interval '1 hour','normal',d.id
        FROM users u JOIN trusted_devices d ON d.user_id=u.id WHERE u.email='pending@example.com';
    """, check=False)
    assert inactive_normal.returncode != 0
    assert "normal session requires active account and trusted device" in inactive_normal.stderr

    immutable = _run(psql, input_text="""
      SET search_path=ovvaults,public;
      INSERT INTO enrollment_consents(user_id,document_key,document_version,document_sha256)
        SELECT id,'terms','v1','abc' FROM users WHERE email='a@example.com';
      UPDATE enrollment_consents SET document_sha256='forged';
    """, check=False)
    assert immutable.returncode != 0
    assert "immutable" in immutable.stderr

    refused = _run(psql, input_text=DOWN.read_text(), check=False)
    assert refused.returncode != 0
    assert "Refusing destructive rollback" in refused.stderr


def test_0032_atomic_grant_consumption_and_email_collision(enrollment_postgres, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from vvault.server import vvault_auth_repository as auth_repo

    def connect():
        return psycopg.connect(
            enrollment_postgres["dsn"], row_factory=dict_row,
            options="-c search_path=ovvaults,public",
        )

    monkeypatch.setattr(auth_repo.chatty_body_service, "_connect", connect)
    repo = auth_repo.VVaultAuthRepository()
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email='legacy@example.com'")
            legacy_user_id = str(cur.fetchone()["id"])
    repo.provision_admission_grant(
        grant_type="owner_bootstrap", email="legacy@example.com",
        token_digest="digest-legacy", expires_at=expires,
        target_user_id=legacy_user_id,
    )
    claimed = repo.admit_oidc_identity(
        issuer="https://accounts.google.com", subject="legacy-subject",
        email="legacy@example.com", name="Legacy Owner", avatar_url=None,
        invitation_digest="digest-legacy",
    )
    assert claimed and str(claimed["id"]) == legacy_user_id
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enrollment_status FROM users WHERE id=%s", (legacy_user_id,))
            assert cur.fetchone()["enrollment_status"] == "PENDING_ENROLLMENT"
            cur.execute(
                """INSERT INTO trusted_devices(user_id,device_secret_digest,status)
                   VALUES (%s,'legacy-pending-device','PENDING') RETURNING id""",
                (legacy_user_id,),
            )
            device_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO sessions(user_id,token_hash,expires_at,session_kind,device_id)
                   VALUES (%s,'legacy-pending-session',now()+interval '1 hour','pending',%s)
                   RETURNING id""",
                (legacy_user_id, device_id),
            )
            pending_session_id = str(cur.fetchone()["id"])
        conn.commit()
    repo.create_webauthn_challenge(
        user_id=legacy_user_id, session_id=pending_session_id,
        challenge_digest="challenge-once", purpose="registration",
        rp_id="localhost", allowed_origin="http://localhost:7784",
        expires_at=expires,
    )

    def consume_challenge(index):
        return repo.consume_webauthn_challenge_and_save_credential(
            user_id=legacy_user_id, session_id=pending_session_id,
            challenge_digest="challenge-once", credential_id=f"credential-{index}",
            public_key=b"public-key", sign_count=0, transports=[],
            rp_id="localhost", allowed_origin="http://localhost:7784",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        challenge_outcomes = list(pool.map(consume_challenge, range(2)))
    assert sum(challenge_outcomes) == 1
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recovery_codes(user_id,code_digest) VALUES (%s,'bootstrap-recovery-once')",
                (legacy_user_id,),
            )
        conn.commit()
    bootstrap_normal = repo.recover_device_and_rotate_session(
        owner_user_id=legacy_user_id, device_id=device_id,
        pending_session_id=pending_session_id,
        recovery_code_digest="bootstrap-recovery-once",
        device_secret_digest="legacy-pending-device",
        normal_token_hash="legacy-bootstrap-normal", normal_expires_at=expires,
    )
    assert bootstrap_normal is not None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enrollment_status FROM users WHERE id=%s", (legacy_user_id,))
            assert cur.fetchone()["enrollment_status"] == "ACTIVE"
            cur.execute(
                "SELECT used_at FROM recovery_codes WHERE code_digest='bootstrap-recovery-once'",
            )
            assert cur.fetchone()["used_at"] is not None

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users(email,password_hash,name,enrollment_status)
                   VALUES ('link@example.com','!','Link Owner','ACTIVE') RETURNING id"""
            )
            link_user_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO trusted_devices
                     (user_id,device_secret_digest,status,approved_by_user_id,approved_at)
                   VALUES (%s,'link-device','TRUSTED',%s,now()) RETURNING id""",
                (link_user_id, link_user_id),
            )
            link_device_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO sessions(user_id,token_hash,expires_at,session_kind,device_id)
                   VALUES (%s,'link-session',now()+interval '1 hour','normal',%s)
                   RETURNING id""",
                (link_user_id, link_device_id),
            )
            link_session_id = str(cur.fetchone()["id"])
        conn.commit()
    assert repo.link_oidc_identity(
        user_id=link_user_id, actor_session_id=link_session_id,
        issuer="https://accounts.google.com", subject="linked-subject",
    ) is True
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (link_session_id,))
        conn.commit()
    assert repo.link_oidc_identity(
        user_id=link_user_id, actor_session_id=link_session_id,
        issuer="https://accounts.google.com", subject="stale-link-subject",
    ) is False
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recovery_codes(user_id,code_digest) VALUES (%s,'recovery-once')",
                (link_user_id,),
            )
            cur.execute(
                """INSERT INTO trusted_devices(user_id,device_secret_digest,status)
                   VALUES (%s,'recovery-device-secret','PENDING') RETURNING id""",
                (link_user_id,),
            )
            recovery_device_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO sessions(user_id,token_hash,expires_at,session_kind,device_id)
                   VALUES (%s,'recovery-pending',now()+interval '1 hour','device_pending',%s)
                   RETURNING id""",
                (link_user_id, recovery_device_id),
            )
            recovery_session_id = str(cur.fetchone()["id"])
        conn.commit()

    def recover(index):
        return repo.recover_device_and_rotate_session(
            owner_user_id=link_user_id, device_id=recovery_device_id,
            pending_session_id=recovery_session_id,
            recovery_code_digest="recovery-once",
            device_secret_digest="recovery-device-secret",
            normal_token_hash=f"recovered-normal-{index}",
            normal_expires_at=expires,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery_outcomes = list(pool.map(recover, range(2)))
    assert sum(outcome is not None for outcome in recovery_outcomes) == 1
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT used_at FROM recovery_codes WHERE code_digest='recovery-once'",
            )
            assert cur.fetchone()["used_at"] is not None

    repo.create_oauth_transaction(
        state_digest="state-once", nonce_digest="nonce", nonce_ciphertext=b"nonce",
        verifier_digest="verifier", verifier_ciphertext=b"verifier",
        redirect_uri="http://127.0.0.1:8000/api/auth/google/callback",
        invitation_digest=None, frontend_origin="http://localhost:7784",
        expires_at=expires,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback_outcomes = list(pool.map(
            lambda _index: repo.consume_oauth_transaction("state-once"), range(2),
        ))
    assert sum(outcome is not None for outcome in callback_outcomes) == 1

    repo.provision_admission_grant(
        grant_type="invitation", email="new@example.com", token_digest="digest-new", expires_at=expires,
    )

    def admit():
        return repo.admit_oidc_identity(
            issuer="https://accounts.google.com", subject="subject-new",
            email="new@example.com", name="New", avatar_url=None,
            invitation_digest="digest-new",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: admit(), range(2)))
    assert sum(outcome is not None for outcome in outcomes) == 1
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM external_identities WHERE issuer=%s AND subject=%s", ("https://accounts.google.com", "subject-new"))
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT count(*) AS count FROM users WHERE email='new@example.com' AND enrollment_status='PENDING_ENROLLMENT'")
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT count(*) AS count FROM enrollment_admission_grants WHERE token_digest='digest-new' AND consumed_at IS NOT NULL")
            assert cur.fetchone()["count"] == 1

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users(email,password_hash,name,enrollment_status) VALUES ('collision@example.com','!','Existing','ACTIVE')")
        conn.commit()
    repo.provision_admission_grant(
        grant_type="invitation", email="collision@example.com", token_digest="digest-collision", expires_at=expires,
    )
    assert repo.admit_oidc_identity(
        issuer="https://accounts.google.com", subject="different-subject",
        email="collision@example.com", name="Attacker", avatar_url=None,
        invitation_digest="digest-collision",
    ) is None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT consumed_at FROM enrollment_admission_grants WHERE token_digest='digest-collision'")
            assert cur.fetchone()["consumed_at"] is None
            cur.execute("SELECT count(*) AS count FROM external_identities WHERE subject='different-subject'")
            assert cur.fetchone()["count"] == 0
