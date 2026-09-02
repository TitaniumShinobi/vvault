"""VVAULT-native auth and session persistence.

This module owns auth-adjacent persistence for the Flask backend. It talks only
to the local/imported OVVAULTS Postgres database and stores session token hashes,
never raw bearer tokens.
"""

from __future__ import annotations

import hmac
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from psycopg.errors import UniqueViolation

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
                        sessions.enrollment_session_kind,
                        sessions.enrollment_device_id,
                        users.email,
                        users.name,
                        users.role,
                        users.auth_provider,
                        users.account_state,
                        enrollment_devices.status AS enrollment_device_status
                    FROM sessions
                    JOIN users ON users.id = sessions.user_id
                    LEFT JOIN enrollment_devices ON enrollment_devices.id = sessions.enrollment_device_id
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
        allow_legacy_compatibility: bool = False,
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
                          AND identities.revoked_at IS NULL
                        FOR UPDATE OF identities, users""",
                    (provider, subject),
                )
                existing = _row_to_dict(cur.fetchone())
                if existing:
                    # A pre-continuity callback could have created a blank
                    # PENDING_ENROLLMENT owner for a verified Google subject
                    # which actually belongs to one legacy owner.  Recover
                    # only that tightly defined case.  This is deliberately
                    # not an email-based account merge: the existing subject
                    # is moved only after locking it, proving the pending row
                    # owns no Vault records, and finding exactly one LEGACY
                    # owner with the same verified contact.
                    if allow_legacy_compatibility and existing["account_state"] == "PENDING_ENROLLMENT":
                        if vvault_auth_crypto.normalize_email(existing["email"] or "") != email:
                            conn.rollback()
                            raise ValueError("pending identity email does not match verified provider email")
                        cur.execute(
                            """SELECT id, email, name, role, account_state
                                 FROM users
                                WHERE account_state='LEGACY' AND lower(email)=%s
                                FOR UPDATE""",
                            (email,),
                        )
                        candidates = [_row_to_dict(row) for row in cur.fetchall()]
                        if len(candidates) != 1:
                            conn.rollback()
                            if len(candidates) > 1:
                                raise ValueError("legacy owner match is ambiguous")
                            raise ValueError("no eligible legacy owner for pending identity")
                        legacy = candidates[0]
                        cur.execute(
                            """SELECT id, user_id FROM external_identities
                                 WHERE provider=%s AND provider_subject=%s AND revoked_at IS NULL
                                 FOR UPDATE""",
                            (provider, subject),
                        )
                        identity = _row_to_dict(cur.fetchone())
                        if not identity or str(identity["user_id"]) != str(existing["id"]):
                            conn.rollback()
                            raise ValueError("provider identity changed during continuity recovery")
                        cur.execute(
                            """SELECT count(*) AS count FROM vault_files
                                 WHERE user_id=%s""",
                            (existing["id"],),
                        )
                        pending_file_count = int((_row_to_dict(cur.fetchone()) or {"count": 0})["count"])
                        if pending_file_count:
                            conn.rollback()
                            raise ValueError("pending identity owns Vault records and cannot be safely recovered")
                        # managed_emails forbids owner reassignment by trigger.
                        # Retire the pending contact record, then create the
                        # equivalent verified contact for the preserved owner.
                        cur.execute(
                            """UPDATE managed_emails SET revoked_at=now()
                                 WHERE user_id=%s AND normalized_email=%s AND revoked_at IS NULL""",
                            (existing["id"], email),
                        )
                        cur.execute(
                            """UPDATE external_identities SET user_id=%s
                                 WHERE id=%s""",
                            (legacy["id"], identity["id"]),
                        )
                        cur.execute(
                            """INSERT INTO managed_emails
                               (user_id, normalized_email, identity_id, verified_at)
                               VALUES (%s, %s, %s, now())
                               ON CONFLICT (user_id, normalized_email) DO NOTHING""",
                            (legacy["id"], email, identity["id"]),
                        )
                        cur.execute(
                            """UPDATE sessions SET revoked_at=now()
                                 WHERE user_id=%s AND revoked_at IS NULL""",
                            (existing["id"],),
                        )
                        cur.execute(
                            """UPDATE users SET auth_provider=%s, updated_at=now()
                                 WHERE id=%s
                             RETURNING id, email, name, role, account_state""",
                            (provider, legacy["id"]),
                        )
                        legacy = _row_to_dict(cur.fetchone())
                        legacy["_legacy_continuity"] = True
                        conn.commit()
                        return legacy, False
                    conn.commit()
                    return existing, False
                if allow_legacy_compatibility:
                    # This one-time bridge binds a verified Google subject to
                    # exactly one existing LEGACY owner. Email never becomes
                    # an identity key or a general account lookup.
                    cur.execute(
                        """SELECT id, email, name, role, account_state
                             FROM users
                            WHERE account_state='LEGACY' AND lower(email)=%s
                            FOR UPDATE""",
                        (email,),
                    )
                    candidates = [_row_to_dict(row) for row in cur.fetchall()]
                    if len(candidates) == 1:
                        user = candidates[0]
                        cur.execute(
                            """INSERT INTO external_identities
                               (user_id, provider, provider_subject, issuer, verified_at)
                               VALUES (%s, %s, %s, %s, now()) RETURNING id""",
                            (user["id"], provider, subject, issuer),
                        )
                        identity = _row_to_dict(cur.fetchone())
                        cur.execute(
                            """INSERT INTO managed_emails
                               (user_id, normalized_email, identity_id, verified_at)
                               VALUES (%s, %s, %s, now())
                               ON CONFLICT (user_id, normalized_email) DO NOTHING""",
                            (user["id"], email, identity["id"]),
                        )
                        cur.execute(
                            """UPDATE users SET auth_provider=%s, updated_at=now()
                                 WHERE id=%s
                             RETURNING id, email, name, role, account_state""",
                            (provider, user["id"]),
                        )
                        user = _row_to_dict(cur.fetchone())
                        user["_legacy_continuity"] = True
                        conn.commit()
                        return user, False
                    if len(candidates) > 1:
                        conn.rollback()
                        raise ValueError("legacy owner match is ambiguous")
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

    def create_chatty_pairing_intent(
        self, *, code_digest: str, user_id: str, session_id: str,
        callback_uri: str, expires_at: datetime,
    ) -> bool:
        """Persist a short-lived opaque pairing code for an active VVAULT session."""
        if not code_digest or not user_id or not session_id or not callback_uri:
            raise ValueError("pairing intent arguments are incomplete")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chatty_pairing_intents
                       (code_digest, user_id, session_id, audience, callback_uri, expires_at)
                       SELECT %s, sessions.user_id, sessions.id, 'chatty-developer-local', %s, %s
                         FROM sessions JOIN users ON users.id=sessions.user_id
                         JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE sessions.id=%s AND sessions.user_id=%s
                          AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                          AND sessions.enrollment_session_kind='NORMAL'
                          AND users.account_state='ACTIVE'
                          AND enrollment_devices.status='TRUSTED'
                       RETURNING code_digest""",
                    (code_digest, callback_uri, expires_at, session_id, user_id),
                )
                created = cur.fetchone() is not None
            conn.commit()
        return created

    def consume_chatty_pairing_intent(
        self, *, code_digest: str, callback_uri: str, chatty_account_id: str,
    ) -> dict[str, Any] | None:
        """Atomically bind a Chatty account and consume one opaque pairing code."""
        try:
            account_id = str(UUID(str(chatty_account_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Chatty account identifier is invalid") from exc
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE chatty_pairing_intents SET consumed_at=now(), chatty_account_id=%s
                             WHERE code_digest=%s AND audience='chatty-developer-local' AND callback_uri=%s
                               AND consumed_at IS NULL AND expires_at > now()
                           RETURNING link_id, audience""",
                        (account_id, code_digest, callback_uri),
                    )
                    result = _row_to_dict(cur.fetchone())
                conn.commit()
        except UniqueViolation:
            # A different one-time code already bound this Chatty account.  This
            # is an expected cross-request race/duplicate, not a server error.
            return None
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

    # Enrollment/session API.  The web layer verifies provider and WebAuthn
    # proofs; this repository persists only their bounded, already-validated
    # results.  All new session state uses the 0034 enrollment_* namespace so
    # legacy 0033 sessions remain readable during staged rollout.
    def create_pending_enrollment_session(
        self, *, user_id: str, device_secret_digest: str, token_hash: str,
        expires_at: datetime, ip_hash: str | None = None,
        user_agent_hash: str | None = None, label: str | None = None,
    ) -> dict[str, Any] | None:
        if not device_secret_digest or not token_hash:
            raise ValueError("device and session token digests are required")
        label = vvault_auth_crypto.normalize_device_label(label)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM users WHERE id=%s AND account_state='PENDING_ENROLLMENT' FOR UPDATE""",
                    (user_id,),
                )
                if not cur.fetchone():
                    conn.rollback(); return None
                cur.execute(
                    """INSERT INTO enrollment_devices
                       (user_id, device_secret_digest, label, status, ip_hash, user_agent_hash)
                       VALUES (%s,%s,%s,'PENDING',%s,%s) RETURNING id""",
                    (user_id, device_secret_digest, label, ip_hash, user_agent_hash),
                )
                device = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO sessions
                       (user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id)
                       VALUES (%s,%s,%s,'PENDING_ENROLLMENT',%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, token_hash, expires_at, device["id"]),
                )
                session = _row_to_dict(cur.fetchone())
            conn.commit()
        return session

    def create_legacy_consent_session(
        self, *, user_id: str, token_hash: str, expires_at: datetime,
    ) -> dict[str, Any] | None:
        """Issue a receipt-only session for an existing ACTIVE or LEGACY owner.

        ``LEGACY`` is the migration-compatible session kind for this bounded
        legal checkpoint.  The account state remains authoritative, so this
        does not turn device verification into a legal acceptance event.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sessions (user_id, token_hash, expires_at, enrollment_session_kind)
                       SELECT id, %s, %s, 'LEGACY' FROM users
                        WHERE id=%s AND account_state IN ('ACTIVE', 'LEGACY')
                       RETURNING id, user_id, expires_at, enrollment_session_kind""",
                    (token_hash, expires_at, user_id),
                )
                session = _row_to_dict(cur.fetchone())
            conn.commit()
        return session

    def issue_legacy_session(
        self, *, user_id: str, token_hash: str, expires_at: datetime,
        required_documents: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        """Issue a normal browser session for a recertified legacy owner.

        This is intentionally limited to an owner that has already renewed all
        current legal receipts.  A legacy record without those receipts must
        enter the one-time recertification path instead.
        """
        required = {(str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or "")) for row in required_documents}
        if not token_hash or not required or any(not all(row) for row in required):
            raise ValueError("current legacy consent receipts are required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._has_current_legal_receipts_locked(cur, user_id=user_id, required=required):
                    conn.rollback(); return None
                cur.execute(
                    """INSERT INTO sessions (user_id, token_hash, expires_at, enrollment_session_kind)
                       SELECT id, %s, %s, 'LEGACY' FROM users
                        WHERE id=%s AND account_state='LEGACY'
                       RETURNING id, user_id, expires_at, enrollment_session_kind""",
                    (token_hash, expires_at, user_id),
                )
                session = _row_to_dict(cur.fetchone())
            conn.commit()
        return session

    @staticmethod
    def _has_current_legal_receipts_locked(
        cur: Any, *, user_id: str, required: set[tuple[str, str, str]],
    ) -> bool:
        """Return whether this owner accepted every current legal artifact.

        This intentionally compares the immutable key, content version, and
        digest triple.  A receipt for an older document must never satisfy a
        later Terms, Privacy, or EECCD checkpoint.
        """
        cur.execute(
            """SELECT document_key, document_version, document_sha256
                 FROM enrollment_consents WHERE user_id=%s""",
            (user_id,),
        )
        actual = {
            (str(row["document_key"]), str(row["document_version"]), str(row["document_sha256"]))
            for row in cur.fetchall()
        }
        return required.issubset(actual)

    def has_current_legal_receipts(
        self, *, user_id: str, required_documents: Sequence[Mapping[str, str]],
    ) -> bool:
        """Check current legal receipts for an ACTIVE or LEGACY owner.

        The caller supplies the server-derived document manifest; browser
        values are never trusted as versions or digests.
        """
        required = {
            (str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or ""))
            for row in required_documents
        }
        if not required or any(not all(row) for row in required):
            raise ValueError("current legal receipt manifest is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM users WHERE id=%s AND account_state IN ('ACTIVE', 'LEGACY', 'PENDING_ENROLLMENT')",
                    (user_id,),
                )
                if not cur.fetchone():
                    return False
                return self._has_current_legal_receipts_locked(cur, user_id=user_id, required=required)

    def issue_known_device_session(
        self, *, user_id: str, device_secret_digest: str, token_hash: str,
        expires_at: datetime, required_documents: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        """Rotate an ACTIVE owner's session only on its existing trusted device.

        Legal recertification and device recognition are separate checks.  The
        trusted device digest is an opaque, HttpOnly browser cookie digest; it
        is never an identity selector and is always owner-bound in this query.
        """
        required = {
            (str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or ""))
            for row in required_documents
        }
        if not token_hash or not device_secret_digest or not required or any(not all(row) for row in required):
            raise ValueError("known device session arguments are incomplete")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE id=%s AND account_state='ACTIVE' FOR UPDATE", (user_id,))
                if not cur.fetchone() or not self._has_current_legal_receipts_locked(cur, user_id=user_id, required=required):
                    conn.rollback(); return None
                cur.execute(
                    """SELECT id FROM enrollment_devices
                         WHERE user_id=%s AND device_secret_digest=%s AND status='TRUSTED'
                         FOR UPDATE""",
                    (user_id, device_secret_digest),
                )
                device = _row_to_dict(cur.fetchone())
                if not device:
                    conn.rollback(); return None
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id)
                       VALUES(%s,%s,%s,'NORMAL',%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, token_hash, expires_at, device["id"]),
                )
                session = _row_to_dict(cur.fetchone())
            conn.commit()
        return session

    def get_enrollment_session_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.id AS session_id, sessions.user_id, sessions.expires_at,
                              sessions.enrollment_session_kind, sessions.enrollment_device_id,
                              users.account_state, enrollment_devices.status AS device_status
                         FROM sessions
                         JOIN users ON users.id=sessions.user_id
                         LEFT JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE sessions.token_hash=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now()""",
                    (token_hash,),
                )
                return _row_to_dict(cur.fetchone())

    def record_enrollment_consents(
        self, *, user_id: str, session_id: str,
        documents: Sequence[Mapping[str, str]], ip_hash: str | None = None,
        user_agent_hash: str | None = None,
    ) -> bool:
        receipts = {(str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or "")) for row in documents}
        if not receipts or any(not all(receipt) for receipt in receipts):
            raise ValueError("complete consent receipts are required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now()
                          AND sessions.enrollment_session_kind='PENDING_ENROLLMENT'
                          AND users.account_state='PENDING_ENROLLMENT' FOR UPDATE""",
                    (session_id, user_id),
                )
                if not cur.fetchone():
                    conn.rollback(); return False
                for key, version, sha256 in receipts:
                    cur.execute(
                        """INSERT INTO enrollment_consents
                           (user_id, document_key, document_version, document_sha256, ip_hash, user_agent_hash)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (user_id, key, version, sha256, ip_hash, user_agent_hash),
                    )
            conn.commit()
        return True

    def complete_legacy_consent(
        self, *, user_id: str, pending_session_id: str, normal_token_hash: str,
        expires_at: datetime, documents: Sequence[Mapping[str, str]],
        ip_hash: str | None = None, user_agent_hash: str | None = None,
        device_secret_digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Renew receipts and rotate back to the same legacy owner session."""
        receipts = {(str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or "")) for row in documents}
        if not normal_token_hash or not receipts or any(not all(receipt) for receipt in receipts):
            raise ValueError("complete continuity-consent arguments are required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT users.account_state FROM sessions JOIN users ON users.id=sessions.user_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='LEGACY'
                          AND users.account_state IN ('ACTIVE', 'LEGACY') FOR UPDATE OF sessions, users""",
                    (pending_session_id, user_id),
                )
                owner = _row_to_dict(cur.fetchone())
                if not owner:
                    conn.rollback(); return None
                for key, version, sha256 in receipts:
                    cur.execute(
                        """INSERT INTO enrollment_consents
                           (user_id, document_key, document_version, document_sha256, ip_hash, user_agent_hash)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (user_id, key, version, sha256, ip_hash, user_agent_hash),
                    )
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                session_kind = 'LEGACY'
                device_id = None
                if owner["account_state"] == 'ACTIVE':
                    # A current legal receipt alone never trusts a new device.
                    # Reuse a matching trusted device when present; otherwise
                    # leave the browser in the separate pending-device gate.
                    if device_secret_digest:
                        cur.execute(
                            """SELECT id FROM enrollment_devices WHERE user_id=%s
                                 AND device_secret_digest=%s AND status='TRUSTED' FOR UPDATE""",
                            (user_id, device_secret_digest),
                        )
                        device = _row_to_dict(cur.fetchone())
                    else:
                        device = None
                    if device:
                        session_kind, device_id = 'NORMAL', device['id']
                    else:
                        if not device_secret_digest:
                            conn.rollback(); return None
                        cur.execute(
                            """SELECT id FROM enrollment_devices WHERE user_id=%s
                                 AND device_secret_digest=%s AND status='PENDING' FOR UPDATE""",
                            (user_id, device_secret_digest),
                        )
                        device = _row_to_dict(cur.fetchone())
                        if not device:
                            cur.execute(
                                """INSERT INTO enrollment_devices(user_id, device_secret_digest, status, ip_hash, user_agent_hash)
                                   VALUES(%s,%s,'PENDING',%s,%s) RETURNING id""",
                                (user_id, device_secret_digest, ip_hash, user_agent_hash),
                            )
                            device = _row_to_dict(cur.fetchone())
                        device_id = device["id"]
                        session_kind = 'PENDING_DEVICE'
                elif owner["account_state"] == 'LEGACY':
                    # Continuity upgrades the existing owner in place.  Legal
                    # acceptance is only the first gate: the same owner must
                    # now complete passkey, recovery-code, and device trust
                    # before becoming ACTIVE.  No user, Vault, or owner-bound
                    # record is copied or replaced.
                    if not device_secret_digest:
                        conn.rollback(); return None
                    cur.execute(
                        """INSERT INTO enrollment_devices(user_id, device_secret_digest, status, ip_hash, user_agent_hash)
                           VALUES(%s,%s,'PENDING',%s,%s) RETURNING id""",
                        (user_id, device_secret_digest, ip_hash, user_agent_hash),
                    )
                    device_id = _row_to_dict(cur.fetchone())["id"]
                    cur.execute("UPDATE users SET account_state='PENDING_ENROLLMENT', updated_at=now() WHERE id=%s", (user_id,))
                    session_kind = 'PENDING_ENROLLMENT'
                cur.execute(
                    """INSERT INTO sessions (user_id, token_hash, expires_at, enrollment_session_kind,
                                              enrollment_device_id, rotated_from_session_id)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, normal_token_hash, expires_at, session_kind, device_id, pending_session_id),
                )
                session = _row_to_dict(cur.fetchone())
            conn.commit()
        return session

    def create_webauthn_registration_challenge(
        self, *, user_id: str, session_id: str, challenge_digest: str,
        rp_id: str, allowed_origin: str, expires_at: datetime,
    ) -> bool:
        if not challenge_digest or not rp_id or not allowed_origin:
            raise ValueError("complete WebAuthn challenge binding is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO enrollment_webauthn_challenges
                       (challenge_digest, user_id, session_id, rp_id, allowed_origin, expires_at)
                       SELECT %s,%s,%s,%s,%s,%s WHERE EXISTS (
                         SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                          WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                            AND sessions.expires_at > now()
                            AND sessions.enrollment_session_kind='PENDING_ENROLLMENT'
                            AND users.account_state='PENDING_ENROLLMENT'
                       ) RETURNING challenge_digest""",
                    (challenge_digest, user_id, session_id, rp_id, allowed_origin, expires_at, session_id, user_id),
                )
                created = cur.fetchone() is not None
            conn.commit()
        return created

    def consume_webauthn_registration_challenge(
        self, *, user_id: str, session_id: str, challenge_digest: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE enrollment_webauthn_challenges SET consumed_at=now()
                         WHERE challenge_digest=%s AND user_id=%s AND session_id=%s
                           AND consumed_at IS NULL AND expires_at > now()
                         RETURNING rp_id, allowed_origin""",
                    (challenge_digest, user_id, session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def create_webauthn_assertion_challenge(
        self, *, user_id: str, session_id: str, challenge_digest: str,
        rp_id: str, allowed_origin: str, expires_at: datetime,
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO enrollment_webauthn_challenges
                       (challenge_digest, user_id, session_id, rp_id, allowed_origin, expires_at)
                       SELECT %s,%s,%s,%s,%s,%s WHERE EXISTS (
                         SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                          WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                            AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                            AND users.account_state='ACTIVE') RETURNING challenge_digest""",
                    (challenge_digest, user_id, session_id, rp_id, allowed_origin, expires_at, session_id, user_id),
                )
                created = cur.fetchone() is not None
            conn.commit()
        return created

    def consume_webauthn_assertion_challenge(self, *, user_id: str, session_id: str, challenge_digest: str) -> dict[str, Any] | None:
        return self.consume_webauthn_registration_challenge(
            user_id=user_id, session_id=session_id, challenge_digest=challenge_digest,
        )

    def list_active_webauthn_credentials(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT credential_id, public_key, sign_count FROM enrollment_webauthn_credentials
                         WHERE user_id=%s AND revoked_at IS NULL ORDER BY created_at ASC""",
                    (user_id,),
                )
                return [dict(row) for row in cur.fetchall()]

    def store_webauthn_credential(
        self, *, user_id: str, credential_id: str, public_key: bytes,
        sign_count: int, transports: Sequence[str], user_verified: bool,
    ) -> bool:
        credential_id = vvault_auth_crypto.normalize_webauthn_credential_id(credential_id)
        if not public_key or sign_count < 0 or not user_verified:
            return False
        allowed_transports = {"ble", "hybrid", "internal", "nfc", "usb"}
        normalized_transports = sorted({str(value) for value in transports})
        if any(value not in allowed_transports for value in normalized_transports):
            raise ValueError("WebAuthn transport is invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO enrollment_webauthn_credentials
                       (credential_id, user_id, public_key, sign_count, transports, user_verified_at)
                       VALUES (%s,%s,%s,%s,%s::jsonb,now())
                       ON CONFLICT (credential_id) DO NOTHING RETURNING credential_id""",
                    (credential_id, user_id, bytes(public_key), sign_count, json.dumps(normalized_transports)),
                )
                stored = cur.fetchone() is not None
            conn.commit()
        return stored

    def replace_recovery_codes(
        self, *, user_id: str, session_id: str, code_digests: Sequence[str],
    ) -> bool:
        digests = [str(value) for value in code_digests]
        if not 8 <= len(digests) <= 20 or len(digests) != len(set(digests)) or any(not value for value in digests):
            raise ValueError("8-20 unique recovery code digests are required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_ENROLLMENT'
                          AND users.account_state='PENDING_ENROLLMENT' FOR UPDATE""",
                    (session_id, user_id),
                )
                if not cur.fetchone():
                    conn.rollback(); return False
                cur.execute("SELECT 1 FROM enrollment_recovery_codes WHERE user_id=%s LIMIT 1 FOR UPDATE", (user_id,))
                if cur.fetchone():
                    conn.rollback(); return False
                for digest in digests:
                    cur.execute("INSERT INTO enrollment_recovery_codes(user_id, code_digest) VALUES(%s,%s)", (user_id, digest))
            conn.commit()
        return True

    def complete_enrollment(
        self, *, user_id: str, pending_session_id: str, device_id: str,
        normal_token_hash: str, expires_at: datetime,
        required_documents: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        required = {(str(row.get("key") or ""), str(row.get("version") or ""), str(row.get("sha256") or "")) for row in required_documents}
        if not required or not normal_token_hash:
            raise ValueError("enrollment completion arguments are incomplete")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.id FROM sessions JOIN users ON users.id=sessions.user_id
                        JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.enrollment_device_id=%s
                          AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                          AND sessions.enrollment_session_kind='PENDING_ENROLLMENT'
                          AND users.account_state='PENDING_ENROLLMENT'
                          AND enrollment_devices.status='PENDING' FOR UPDATE OF sessions, users, enrollment_devices""",
                    (pending_session_id, user_id, device_id),
                )
                if not cur.fetchone():
                    conn.rollback(); return None
                cur.execute("SELECT document_key, document_version, document_sha256 FROM enrollment_consents WHERE user_id=%s", (user_id,))
                actual = {(str(row["document_key"]), str(row["document_version"]), str(row["document_sha256"])) for row in cur.fetchall()}
                if not required.issubset(actual):
                    conn.rollback(); return None
                cur.execute("SELECT 1 FROM enrollment_webauthn_credentials WHERE user_id=%s AND revoked_at IS NULL LIMIT 1", (user_id,))
                if not cur.fetchone():
                    conn.rollback(); return None
                cur.execute("SELECT count(*) AS count FROM enrollment_recovery_codes WHERE user_id=%s AND used_at IS NULL", (user_id,))
                if int(cur.fetchone()["count"]) < 8:
                    conn.rollback(); return None
                cur.execute("UPDATE users SET account_state='ACTIVE', updated_at=now() WHERE id=%s", (user_id,))
                cur.execute("UPDATE enrollment_devices SET status='TRUSTED', approved_by_user_id=%s, approved_at=now() WHERE id=%s", (user_id, device_id))
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id, rotated_from_session_id)
                       VALUES(%s,%s,%s,'NORMAL',%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, normal_token_hash, expires_at, device_id, pending_session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def issue_pending_device_session(
        self, *, user_id: str, device_secret_digest: str, token_hash: str,
        expires_at: datetime, ip_hash: str | None = None,
        user_agent_hash: str | None = None, label: str | None = None,
    ) -> dict[str, Any] | None:
        if not device_secret_digest or not token_hash:
            raise ValueError("device and session token digests are required")
        label = vvault_auth_crypto.normalize_device_label(label)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE id=%s AND account_state='ACTIVE' FOR UPDATE", (user_id,))
                if not cur.fetchone():
                    conn.rollback(); return None
                cur.execute(
                    """SELECT id FROM enrollment_devices
                         WHERE user_id=%s AND device_secret_digest=%s AND status='PENDING' FOR UPDATE""",
                    (user_id, device_secret_digest),
                )
                device = _row_to_dict(cur.fetchone())
                if not device:
                    cur.execute(
                        """INSERT INTO enrollment_devices(user_id, device_secret_digest, label, status, ip_hash, user_agent_hash)
                           VALUES(%s,%s,%s,'PENDING',%s,%s) RETURNING id""",
                        (user_id, device_secret_digest, label, ip_hash, user_agent_hash),
                    )
                    device = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id)
                       VALUES(%s,%s,%s,'PENDING_DEVICE',%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, token_hash, expires_at, device["id"]),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def _trusted_actor_locked(self, cur: Any, *, actor_user_id: str, actor_session_id: str) -> bool:
        cur.execute(
            """SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id
                JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > now() AND sessions.enrollment_session_kind='NORMAL'
                  AND users.account_state='ACTIVE' AND enrollment_devices.status='TRUSTED' FOR UPDATE OF sessions""",
            (actor_session_id, actor_user_id),
        )
        return cur.fetchone() is not None

    def approve_pending_device(
        self, *, actor_user_id: str, actor_session_id: str, pending_session_id: str,
        normal_token_hash: str, expires_at: datetime,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._trusted_actor_locked(cur, actor_user_id=actor_user_id, actor_session_id=actor_session_id):
                    conn.rollback(); return None
                cur.execute(
                    """SELECT sessions.user_id, sessions.enrollment_device_id FROM sessions
                         JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                          AND enrollment_devices.status='PENDING' FOR UPDATE OF sessions, enrollment_devices""",
                    (pending_session_id, actor_user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback(); return None
                device_id = str(pending["enrollment_device_id"])
                cur.execute("UPDATE enrollment_devices SET status='TRUSTED', approved_by_user_id=%s, approved_at=now() WHERE id=%s", (actor_user_id, device_id))
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id, rotated_from_session_id)
                       VALUES(%s,%s,%s,'NORMAL',%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (actor_user_id, normal_token_hash, expires_at, device_id, pending_session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def recover_pending_device(
        self, *, user_id: str, pending_session_id: str, recovery_code_digest: str,
        normal_token_hash: str, expires_at: datetime,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.enrollment_device_id FROM sessions JOIN enrollment_devices
                         ON enrollment_devices.id=sessions.enrollment_device_id JOIN users ON users.id=sessions.user_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                          AND enrollment_devices.status='PENDING' AND users.account_state='ACTIVE'
                        FOR UPDATE OF sessions, enrollment_devices""",
                    (pending_session_id, user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback(); return None
                cur.execute(
                    """UPDATE enrollment_recovery_codes SET used_at=now()
                         WHERE user_id=%s AND code_digest=%s AND used_at IS NULL RETURNING id""",
                    (user_id, recovery_code_digest),
                )
                if not cur.fetchone():
                    conn.rollback(); return None
                device_id = str(pending["enrollment_device_id"])
                cur.execute("UPDATE enrollment_devices SET status='TRUSTED', approved_by_user_id=%s, approved_at=now() WHERE id=%s", (user_id, device_id))
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id, rotated_from_session_id)
                       VALUES(%s,%s,%s,'NORMAL',%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, normal_token_hash, expires_at, device_id, pending_session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def create_pending_device_transfer(
        self, *, user_id: str, pending_session_id: str, code_digest: str,
        expires_at: datetime,
    ) -> bool:
        """Store a short-lived, hashed transfer code for one pending device."""
        if not code_digest:
            raise ValueError("device transfer digest is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO enrollment_device_transfer_codes(code_digest, user_id, pending_session_id, expires_at)
                       SELECT %s, sessions.user_id, sessions.id, %s FROM sessions
                        JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                       WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                         AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                         AND enrollment_devices.status='PENDING'
                       RETURNING code_digest""",
                    (code_digest, expires_at, pending_session_id, user_id),
                )
                created = cur.fetchone() is not None
            conn.commit()
        return created

    def approve_pending_device_transfer(
        self, *, actor_user_id: str, actor_session_id: str, code_digest: str,
    ) -> bool:
        """Approve one unfamiliar device from a different trusted device.

        This does not issue a session to the approving browser.  The pending
        browser must redeem its own bound HttpOnly session in a second step.
        """
        if not code_digest:
            return False
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._trusted_actor_locked(cur, actor_user_id=actor_user_id, actor_session_id=actor_session_id):
                    conn.rollback(); return False
                cur.execute(
                    """SELECT transfer.pending_session_id, sessions.enrollment_device_id
                         FROM enrollment_device_transfer_codes AS transfer
                         JOIN sessions ON sessions.id=transfer.pending_session_id
                         JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE transfer.code_digest=%s AND transfer.user_id=%s
                          AND transfer.consumed_at IS NULL AND transfer.expires_at > now()
                          AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                          AND sessions.enrollment_session_kind='PENDING_DEVICE'
                          AND enrollment_devices.status='PENDING'
                        FOR UPDATE OF transfer, sessions, enrollment_devices""",
                    (code_digest, actor_user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback(); return False
                cur.execute("UPDATE enrollment_device_transfer_codes SET consumed_at=now() WHERE code_digest=%s", (code_digest,))
                cur.execute(
                    """UPDATE enrollment_devices SET status='TRUSTED', approved_by_user_id=%s, approved_at=now()
                         WHERE id=%s""",
                    (actor_user_id, pending["enrollment_device_id"]),
                )
            conn.commit()
        return True

    def complete_approved_pending_device(
        self, *, user_id: str, pending_session_id: str, normal_token_hash: str, expires_at: datetime,
    ) -> dict[str, Any] | None:
        """Issue the normal session only to the browser that held the pending cookie."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.enrollment_device_id FROM sessions
                         JOIN users ON users.id=sessions.user_id
                         JOIN enrollment_devices ON enrollment_devices.id=sessions.enrollment_device_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                          AND users.account_state='ACTIVE' AND enrollment_devices.status='TRUSTED'
                        FOR UPDATE OF sessions, enrollment_devices""",
                    (pending_session_id, user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback(); return None
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind,
                                              enrollment_device_id, rotated_from_session_id)
                       VALUES(%s,%s,%s,'NORMAL',%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, normal_token_hash, expires_at, pending["enrollment_device_id"], pending_session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def complete_pending_device_webauthn(
        self, *, user_id: str, pending_session_id: str, credential_id: str,
        new_sign_count: int, normal_token_hash: str, expires_at: datetime,
    ) -> dict[str, Any] | None:
        """Promote a pending device after a validated assertion and counter update."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.enrollment_device_id FROM sessions JOIN enrollment_devices
                         ON enrollment_devices.id=sessions.enrollment_device_id JOIN users ON users.id=sessions.user_id
                        WHERE sessions.id=%s AND sessions.user_id=%s AND sessions.revoked_at IS NULL
                          AND sessions.expires_at > now() AND sessions.enrollment_session_kind='PENDING_DEVICE'
                          AND enrollment_devices.status='PENDING' AND users.account_state='ACTIVE'
                        FOR UPDATE OF sessions, enrollment_devices""",
                    (pending_session_id, user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback(); return None
                cur.execute(
                    """UPDATE enrollment_webauthn_credentials SET sign_count=%s
                         WHERE credential_id=%s AND user_id=%s AND revoked_at IS NULL
                           AND (sign_count=0 OR %s > sign_count) RETURNING credential_id""",
                    (new_sign_count, credential_id, user_id, new_sign_count),
                )
                if not cur.fetchone():
                    conn.rollback(); return None
                device_id = str(pending["enrollment_device_id"])
                cur.execute("UPDATE enrollment_devices SET status='TRUSTED', approved_by_user_id=%s, approved_at=now() WHERE id=%s", (user_id, device_id))
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions(user_id, token_hash, expires_at, enrollment_session_kind, enrollment_device_id, rotated_from_session_id)
                       VALUES(%s,%s,%s,'NORMAL',%s,%s)
                       RETURNING id, user_id, expires_at, enrollment_session_kind, enrollment_device_id""",
                    (user_id, normal_token_hash, expires_at, device_id, pending_session_id),
                )
                result = _row_to_dict(cur.fetchone())
            conn.commit()
        return result

    def revoke_enrollment_device(self, *, actor_user_id: str, actor_session_id: str, device_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._trusted_actor_locked(cur, actor_user_id=actor_user_id, actor_session_id=actor_session_id):
                    conn.rollback(); return False
                cur.execute(
                    """UPDATE enrollment_devices SET status='REVOKED', revoked_at=now()
                         WHERE id=%s AND user_id=%s AND status <> 'REVOKED' RETURNING id""",
                    (device_id, actor_user_id),
                )
                if not cur.fetchone():
                    conn.rollback(); return False
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE enrollment_device_id=%s AND revoked_at IS NULL", (device_id,))
            conn.commit()
        return True

    def revoke_all_user_sessions(self, *, user_id: str, except_session_id: str | None = None) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if except_session_id:
                    cur.execute("UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND id<>%s AND revoked_at IS NULL", (user_id, except_session_id))
                else:
                    cur.execute("UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
                changed = int(cur.rowcount or 0)
            conn.commit()
        return changed
