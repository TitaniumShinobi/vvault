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
