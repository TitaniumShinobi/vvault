"""VVAULT-native auth and session persistence.

This module owns auth-adjacent persistence for the Flask backend. It talks only
to the local/imported OVVAULTS Postgres database and stores session token hashes,
never raw bearer tokens.
"""

from __future__ import annotations

import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any

from vvault.server import vvault_auth_crypto

try:
    import chatty_body_service
except ImportError:  # Package import path used by pytest.
    from vvault.server import chatty_body_service

AUTH_OWNER = "ovvaults.users"
SESSION_OWNER = "ovvaults.sessions"
OAUTH_DISABLED_PASSWORD_HASH = "!vvault-oauth-disabled!"


def hash_session_token(token: str, secret: str) -> str:
    if not token:
        raise ValueError("session token is required")
    if not secret:
        raise ValueError("session hash secret is required")
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def _identity_lock_key(provider: str, subject: str) -> str:
    return f"vvault-identity:{provider}:{subject}"


class VVaultAuthRepository:
    def _connect(self):
        return chatty_body_service._connect()

    def healthcheck(self) -> dict[str, Any]:
        status = {
            "ready": False,
            "status": "unhealthy",
            "auth_owner": AUTH_OWNER,
            "session_owner": SESSION_OWNER,
            "source_database": chatty_body_service.source_database_name(),
            "checks": {
                "users_readable": False,
                "sessions_readable": False,
                "auth_identity_columns": False,
            },
        }
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, email, password_hash, name, role, auth_provider,
                               oauth_provider, oauth_subject, avatar_url,
                               last_login_at, updated_at
                        FROM users
                        LIMIT 1
                        """
                    )
                    status["checks"]["users_readable"] = True
                    status["checks"]["auth_identity_columns"] = True
                    cur.execute(
                        """
                        SELECT id, user_id, token_hash, created_at, expires_at, revoked_at
                        FROM sessions
                        LIMIT 1
                        """
                    )
                    status["checks"]["sessions_readable"] = True
            status["ready"] = True
            status["status"] = "healthy"
        except Exception as exc:
            status["error_code"] = type(exc).__name__
        return status

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash, name, role, auth_provider,
                           oauth_provider, oauth_subject, avatar_url,
                           last_login_at, created_at, updated_at
                    FROM users
                    WHERE email = %s
                    """,
                    (email.strip().lower(),),
                )
                return _row_to_dict(cur.fetchone())

    def create_password_user(self, *, email: str, password_hash: str, name: str, role: str = "user") -> dict[str, Any]:
        raise RuntimeError("password identity creation is retired; use a verified provider identity")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, name, role, auth_provider, updated_at)
                    VALUES (%s, %s, %s, %s, 'password', now())
                    RETURNING id, email, password_hash, name, role, auth_provider,
                              oauth_provider, oauth_subject, avatar_url,
                              last_login_at, created_at, updated_at
                    """,
                    (email.strip().lower(), password_hash, name, role),
                )
                row = _row_to_dict(cur.fetchone())
            conn.commit()
        return row

    def ensure_external_user(self, *, email: str, name: str | None = None, role: str = "user") -> dict[str, Any]:
        raise RuntimeError("email-only user provisioning is retired; a verified identity is required")

        existing = self.get_user_by_email(email)
        if existing:
            return existing
        return self.upsert_oauth_user(
            email=email,
            name=name or email.split("@")[0],
            role=role,
            oauth_provider="external",
            oauth_subject=email.strip().lower(),
            avatar_url=None,
        )

    def upsert_oauth_user(
        self,
        *,
        email: str,
        name: str,
        role: str = "user",
        oauth_provider: str,
        oauth_subject: str,
        avatar_url: str | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("email-based OAuth upsert is retired; use admit_verified_identity")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (
                        email, password_hash, name, role, auth_provider,
                        oauth_provider, oauth_subject, avatar_url,
                        last_login_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (email) DO UPDATE
                    SET name = COALESCE(EXCLUDED.name, users.name),
                        role = COALESCE(users.role, EXCLUDED.role),
                        auth_provider = EXCLUDED.auth_provider,
                        oauth_provider = EXCLUDED.oauth_provider,
                        oauth_subject = EXCLUDED.oauth_subject,
                        avatar_url = COALESCE(EXCLUDED.avatar_url, users.avatar_url),
                        last_login_at = now(),
                        updated_at = now()
                    RETURNING id, email, password_hash, name, role, auth_provider,
                              oauth_provider, oauth_subject, avatar_url,
                              last_login_at, created_at, updated_at
                    """,
                    (
                        email.strip().lower(),
                        OAUTH_DISABLED_PASSWORD_HASH,
                        name,
                        role,
                        oauth_provider,
                        oauth_provider,
                        oauth_subject,
                        avatar_url,
                    ),
                )
                row = _row_to_dict(cur.fetchone())
            conn.commit()
        return row

    def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (user_id, token_hash, expires_at)
                    VALUES (%s, %s, %s)
                    RETURNING id, user_id, created_at, expires_at, revoked_at
                    """,
                    (user_id, token_hash, expires_at),
                )
                row = _row_to_dict(cur.fetchone())
            conn.commit()
        return row

    def get_session_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        sessions.id AS session_id,
                        sessions.user_id,
                        sessions.created_at AS session_created_at,
                        sessions.expires_at,
                        users.email,
                        users.name,
                        users.role,
                        users.auth_provider
                    FROM sessions
                    JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token_hash = %s
                      AND sessions.revoked_at IS NULL
                      AND sessions.expires_at > now()
                    """,
                    (token_hash,),
                )
                return _row_to_dict(cur.fetchone())

    def revoke_session_by_hash(self, token_hash: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sessions
                    SET revoked_at = now()
                    WHERE token_hash = %s
                      AND revoked_at IS NULL
                    """,
                    (token_hash,),
                )
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def cleanup_expired_sessions(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sessions
                    SET revoked_at = now()
                    WHERE revoked_at IS NULL
                      AND expires_at <= %s
                    """,
                    (_utc_now(),),
                )
                changed = cur.rowcount
            conn.commit()
        return int(changed or 0)

    # Identity-directory API.  These methods are deliberately independent of
    # provider HTTP and Flask routes; callbacks hand them only verified claims.
    def get_external_identity(self, *, provider: str, provider_subject: str) -> dict[str, Any] | None:
        provider = vvault_auth_crypto.normalize_provider(provider)
        if not provider_subject:
            raise ValueError("provider subject is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT identities.id AS identity_id, identities.user_id, identities.provider,
                              identities.provider_subject, identities.issuer, identities.verified_at,
                              users.email, users.name, users.role, users.account_state
                         FROM external_identities identities JOIN users ON users.id=identities.user_id
                        WHERE identities.provider=%s AND identities.provider_subject=%s
                          AND identities.revoked_at IS NULL""",
                    (provider, provider_subject),
                )
                return _row_to_dict(cur.fetchone())

    def admit_verified_identity(
        self, *, provider: str, provider_subject: str, verified_email: str,
        name: str | None, issuer: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Find a durable identity or atomically create one pending account.

        Email is stored as a verified contact but is never used to find or merge
        an account.  The advisory transaction lock prevents a concurrent pair of
        callbacks from creating two users for the same provider subject.
        """
        provider = vvault_auth_crypto.normalize_provider(provider)
        subject = provider_subject.strip()
        email = vvault_auth_crypto.normalize_email(verified_email)
        if not subject:
            raise ValueError("provider subject is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_identity_lock_key(provider, subject),))
                cur.execute(
                    """SELECT users.id, users.email, users.name, users.role, users.account_state
                         FROM external_identities identities JOIN users ON users.id=identities.user_id
                        WHERE identities.provider=%s AND identities.provider_subject=%s
                          AND identities.revoked_at IS NULL""",
                    (provider, subject),
                )
                existing = _row_to_dict(cur.fetchone())
                if existing:
                    conn.commit()
                    return existing, False
                cur.execute(
                    """INSERT INTO users (email, password_hash, name, role, auth_provider, account_state, updated_at)
                       VALUES (%s, %s, %s, 'user', %s, 'PENDING_ENROLLMENT', now())
                       RETURNING id, email, name, role, account_state""",
                    (email, OAUTH_DISABLED_PASSWORD_HASH, name or email.split("@", 1)[0], provider),
                )
                user = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO external_identities
                       (user_id, provider, provider_subject, issuer, verified_at)
                       VALUES (%s, %s, %s, %s, now()) RETURNING id""",
                    (user["id"], provider, subject, issuer),
                )
                identity = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO managed_emails (user_id, normalized_email, identity_id, verified_at)
                       VALUES (%s, %s, %s, now()) ON CONFLICT (user_id, normalized_email) DO NOTHING""",
                    (user["id"], email, identity["id"]),
                )
            conn.commit()
        return user, True

    def create_oauth_transaction(
        self, *, state_digest: str, provider: str, purpose: str,
        nonce_digest: str | None, nonce_ciphertext: bytes | None,
        pkce_verifier_digest: str, pkce_verifier_ciphertext: bytes, redirect_uri: str,
        frontend_origin: str, expires_at: datetime, initiating_user_id: str | None = None,
        initiating_session_id: str | None = None,
    ) -> None:
        provider = vvault_auth_crypto.normalize_provider(provider)
        if purpose not in {"signin", "reauth", "link"}:
            raise ValueError("invalid OAuth transaction purpose")
        linked = bool(initiating_user_id and initiating_session_id)
        if (purpose == "signin") == linked or not pkce_verifier_ciphertext:
            raise ValueError("OAuth transaction actor context is invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO identity_oauth_transactions
                       (state_digest, provider, purpose, nonce_digest, nonce_ciphertext,
                        pkce_verifier_digest, pkce_verifier_ciphertext, redirect_uri, frontend_origin,
                        initiating_user_id, initiating_session_id, expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (state_digest, provider, purpose, nonce_digest, nonce_ciphertext,
                     pkce_verifier_digest, pkce_verifier_ciphertext, redirect_uri, frontend_origin,
                     initiating_user_id, initiating_session_id, expires_at),
                )
            conn.commit()

    def consume_oauth_transaction(self, state_digest: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE identity_oauth_transactions SET consumed_at=now()
                         WHERE state_digest=%s AND consumed_at IS NULL AND expires_at > now()
                         RETURNING provider, purpose, nonce_digest, nonce_ciphertext,
                                   pkce_verifier_digest, pkce_verifier_ciphertext, redirect_uri,
                                   frontend_origin, initiating_user_id, initiating_session_id""",
                    (state_digest,),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def record_session_reauthentication(self, *, session_id: str, user_id: str, provider: str) -> bool:
        provider = vvault_auth_crypto.normalize_provider(provider)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO auth_session_reauth(session_id, user_id, provider, reauthenticated_at)
                       SELECT id, user_id, %s, now() FROM sessions
                        WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at > now()
                       ON CONFLICT (session_id) DO UPDATE SET provider=EXCLUDED.provider,
                         reauthenticated_at=EXCLUDED.reauthenticated_at
                       RETURNING session_id""",
                    (provider, session_id, user_id),
                )
                result = cur.fetchone() is not None
            conn.commit()
        return result

    def link_verified_identity(
        self, *, user_id: str, session_id: str, provider: str,
        provider_subject: str, verified_email: str, issuer: str | None = None,
        max_age_seconds: int = 600,
    ) -> bool:
        """Bind a newly verified provider identity after fresh provider reauth."""
        provider = vvault_auth_crypto.normalize_provider(provider)
        subject = provider_subject.strip()
        email = vvault_auth_crypto.normalize_email(verified_email)
        if not subject or max_age_seconds < 1:
            raise ValueError("identity link arguments are invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_identity_lock_key(provider, subject),))
                cur.execute(
                    """SELECT users.id FROM sessions JOIN users ON users.id=sessions.user_id
                         JOIN auth_session_reauth ON auth_session_reauth.session_id=sessions.id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND users.account_state='ACTIVE'
                          AND auth_session_reauth.reauthenticated_at > now() - make_interval(secs => %s)
                        FOR UPDATE OF sessions""",
                    (session_id, user_id, max_age_seconds),
                )
                if not cur.fetchone():
                    conn.rollback()
                    return False
                cur.execute(
                    """SELECT user_id FROM external_identities
                         WHERE provider=%s AND provider_subject=%s AND revoked_at IS NULL FOR UPDATE""",
                    (provider, subject),
                )
                existing = _row_to_dict(cur.fetchone())
                if existing and str(existing["user_id"]) != str(user_id):
                    conn.rollback()
                    return False
                if not existing:
                    cur.execute(
                        """INSERT INTO external_identities(user_id, provider, provider_subject, issuer, verified_at)
                           VALUES (%s,%s,%s,%s,now()) RETURNING id""",
                        (user_id, provider, subject, issuer),
                    )
                    identity = _row_to_dict(cur.fetchone())
                    cur.execute(
                        """INSERT INTO managed_emails(user_id, normalized_email, identity_id, verified_at)
                           VALUES (%s,%s,%s,now()) ON CONFLICT (user_id, normalized_email) DO NOTHING""",
                        (user_id, email, identity["id"]),
                    )
                # A sensitive identity change invalidates the bearer that
                # authorized it. Plan 3 replaces this with a rotated session.
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (session_id,))
            conn.commit()
        return True

    def list_active_identities(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, provider, provider_subject, issuer, verified_at, created_at
                         FROM external_identities WHERE user_id=%s AND revoked_at IS NULL
                        ORDER BY created_at ASC""",
                    (user_id,),
                )
                return [dict(row) for row in cur.fetchall()]

    def unlink_identity(self, *, user_id: str, session_id: str, identity_id: str, max_age_seconds: int = 600) -> bool:
        """Revoke a linked sign-in method, never the final usable method."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                         JOIN auth_session_reauth ON auth_session_reauth.session_id=sessions.id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND users.account_state='ACTIVE'
                          AND auth_session_reauth.reauthenticated_at > now() - make_interval(secs => %s)
                        FOR UPDATE OF sessions""",
                    (session_id, user_id, max_age_seconds),
                )
                if not cur.fetchone():
                    conn.rollback(); return False
                cur.execute("SELECT count(*) AS count FROM external_identities WHERE user_id=%s AND revoked_at IS NULL FOR UPDATE", (user_id,))
                if int(cur.fetchone()["count"]) <= 1:
                    conn.rollback(); return False
                cur.execute(
                    """UPDATE external_identities SET revoked_at=now()
                         WHERE id=%s AND user_id=%s AND revoked_at IS NULL RETURNING id""",
                    (identity_id, user_id),
                )
                if not cur.fetchone():
                    conn.rollback(); return False
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (session_id,))
            conn.commit()
        return True

    def issue_magic_link_challenge(
        self, *, token_digest: str, normalized_email: str, purpose: str,
        redirect_uri: str, expires_at: datetime, initiating_user_id: str | None = None,
        initiating_session_id: str | None = None,
    ) -> None:
        email = vvault_auth_crypto.normalize_email(normalized_email)
        if purpose not in {"signin", "link"}:
            raise ValueError("invalid magic-link purpose")
        linked = bool(initiating_user_id and initiating_session_id)
        if (purpose == "signin") == linked:
            raise ValueError("magic-link actor context is invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO email_magic_link_challenges
                       (token_digest, normalized_email, purpose, redirect_uri, initiating_user_id,
                        initiating_session_id, expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (token_digest, email, purpose, redirect_uri, initiating_user_id,
                     initiating_session_id, expires_at),
                )
            conn.commit()

    def consume_magic_link_challenge(self, token_digest: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE email_magic_link_challenges SET consumed_at=now()
                         WHERE token_digest=%s AND consumed_at IS NULL AND expires_at > now()
                         RETURNING id, normalized_email, purpose, redirect_uri,
                                   initiating_user_id, initiating_session_id""",
                    (token_digest,),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result
