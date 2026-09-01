from __future__ import annotations

import os
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


def _binary(name: str) -> str | None:
    preferred = Path("/opt/homebrew/opt/postgresql@18/bin") / name
    return str(preferred) if preferred.exists() else shutil.which(name)


def _run(command: list[str], *, input_text: str | None = None, check: bool = True):
    return subprocess.run(command, input=input_text, text=True, capture_output=True, check=check)


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
        _run(psql, input_text=UP.read_text())
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
