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
                "enrollment_schema": False,
            },
        }
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, email, password_hash, name, role, auth_provider,
                               enrollment_status,
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
                        SELECT id, user_id, token_hash, created_at, expires_at, revoked_at,
                               session_kind, device_id
                        FROM sessions
                        LIMIT 1
                        """
                    )
                    status["checks"]["sessions_readable"] = True
                    for table in (
                        "external_identities", "oauth_transactions",
                        "enrollment_admission_grants", "enrollment_consents",
                        "webauthn_challenges", "webauthn_credentials",
                        "recovery_codes", "trusted_devices", "auth_security_events",
                    ):
                        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
                    status["checks"]["enrollment_schema"] = True
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
                    SELECT id, email, password_hash, name, role, auth_provider, enrollment_status,
                           oauth_provider, oauth_subject, avatar_url,
                           last_login_at, created_at, updated_at
                    FROM users
                    WHERE email = %s
                    """,
                    (email.strip().lower(),),
                )
                return _row_to_dict(cur.fetchone())

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Return a canonical OVVAULTS user only when the UUID exists here."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash, name, role, auth_provider, enrollment_status,
                           oauth_provider, oauth_subject, avatar_url,
                           last_login_at, created_at, updated_at
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id.strip(),),
                )
                return _row_to_dict(cur.fetchone())

    def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        session_kind: str = "normal",
        device_id: str | None = None,
        rotated_from_session_id: str | None = None,
    ) -> dict[str, Any]:
        if session_kind not in {"pending", "device_pending", "normal"}:
            raise ValueError("invalid VVAULT session kind")
        if not device_id:
            raise ValueError("every VVAULT session must be bound to a device")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (user_id, token_hash, expires_at, session_kind, device_id, rotated_from_session_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, user_id, created_at, expires_at, revoked_at, session_kind, device_id
                    """,
                    (user_id, token_hash, expires_at, session_kind, device_id, rotated_from_session_id),
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
                        users.auth_provider,
                        users.enrollment_status,
                        sessions.session_kind,
                        sessions.device_id,
                        devices.status AS device_status
                    FROM sessions
                    JOIN users ON users.id = sessions.user_id
                    LEFT JOIN trusted_devices AS devices ON devices.id = sessions.device_id
                    WHERE sessions.token_hash = %s
                      AND sessions.revoked_at IS NULL
                      AND sessions.expires_at > now()
                    """,
                    (token_hash,),
                )
                return _row_to_dict(cur.fetchone())

    def get_identity(self, *, issuer: str, subject: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT users.id, users.email, users.name, users.role, users.enrollment_status
                    FROM external_identities
                    JOIN users ON users.id = external_identities.user_id
                    WHERE external_identities.issuer = %s AND external_identities.subject = %s
                    """,
                    (issuer, subject),
                )
                return _row_to_dict(cur.fetchone())

    def create_oauth_transaction(
        self, *, state_digest: str, nonce_digest: str, nonce_ciphertext: bytes,
        verifier_digest: str, verifier_ciphertext: bytes, redirect_uri: str,
        invitation_digest: str | None, frontend_origin: str, expires_at: datetime,
        operation: str = "signin", link_user_id: str | None = None,
        link_session_id: str | None = None,
    ) -> None:
        if operation not in {"signin", "link"}:
            raise ValueError("invalid OAuth transaction operation")
        if (operation == "link") != bool(link_user_id and link_session_id):
            raise ValueError("OAuth link transaction requires an authenticated session")
        if operation == "signin" and (link_user_id or link_session_id):
            raise ValueError("OAuth sign-in transaction cannot carry link authority")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO oauth_transactions
                       (state_digest, nonce_digest, nonce_ciphertext, pkce_verifier_digest,
                        pkce_verifier_ciphertext, redirect_uri, invitation_digest,
                        frontend_origin, expires_at, operation, link_user_id, link_session_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (state_digest, nonce_digest, nonce_ciphertext, verifier_digest,
                     verifier_ciphertext, redirect_uri, invitation_digest,
                     frontend_origin, expires_at, operation, link_user_id, link_session_id),
                )
            conn.commit()

    def consume_oauth_transaction(self, state_digest: str) -> dict[str, Any] | None:
        """Atomically consume state before token exchange, preventing callback replay."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE oauth_transactions SET consumed_at = now()
                       WHERE state_digest = %s AND consumed_at IS NULL AND expires_at > now()
                       RETURNING nonce_digest, nonce_ciphertext, pkce_verifier_digest, pkce_verifier_ciphertext,
                                 redirect_uri, invitation_digest, frontend_origin, operation,
                                 link_user_id, link_session_id""",
                    (state_digest,),
                )
                row = _row_to_dict(cur.fetchone())
            conn.commit()
        return row

    def admit_oidc_identity(self, *, issuer: str, subject: str, email: str, name: str, avatar_url: str | None, invitation_digest: str | None) -> dict[str, Any] | None:
        """Consume a matching admission grant and create only a pending account.

        The row lock makes invitation/allowlist consumption and identity binding
        one transaction.  Email is an admission match only, never identity key.
        """
        normalized_email = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM external_identities WHERE issuer=%s AND subject=%s", (issuer, subject))
                existing = _row_to_dict(cur.fetchone())
                if existing:
                    cur.execute("SELECT id, email, name, role, enrollment_status FROM users WHERE id=%s", (existing["user_id"],))
                    row = _row_to_dict(cur.fetchone())
                    conn.commit()
                    return row
                cur.execute(
                    """SELECT id, grant_type, target_user_id FROM enrollment_admission_grants
                       WHERE email=%s AND consumed_at IS NULL AND expires_at > now()
                         AND ((grant_type='allowlist' AND token_digest IS NULL) OR token_digest = %s)
                       ORDER BY CASE WHEN token_digest = %s THEN 0 ELSE 1 END
                       FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (normalized_email, invitation_digest, invitation_digest),
                )
                grant = _row_to_dict(cur.fetchone())
                if not grant:
                    conn.rollback()
                    return None
                target_user_id = grant.get("target_user_id")
                if target_user_id:
                    # Setup/repair may explicitly bind a one-time grant to an
                    # existing legacy UUID. The verified email is admission
                    # matching only; the resulting identity remains issuer+sub.
                    cur.execute(
                        """UPDATE users
                           SET name=COALESCE(NULLIF(name,''), %s), auth_provider='google',
                               avatar_url=COALESCE(%s, avatar_url),
                               enrollment_status='PENDING_ENROLLMENT', updated_at=now()
                           WHERE id=%s AND email=%s AND enrollment_status='LEGACY_PENDING'
                           RETURNING id, email, name, role, enrollment_status""",
                        (name, avatar_url, target_user_id, normalized_email),
                    )
                    user = _row_to_dict(cur.fetchone())
                    if not user:
                        conn.rollback()
                        return None
                else:
                    # A matching email is not identity proof. Existing accounts
                    # require either an authenticated link or a setup-bound grant.
                    cur.execute("SELECT id FROM users WHERE email=%s FOR UPDATE", (normalized_email,))
                    if cur.fetchone():
                        conn.rollback()
                        return None
                    cur.execute(
                        """INSERT INTO users (email, password_hash, name, role, auth_provider, avatar_url, enrollment_status, updated_at)
                           VALUES (%s, %s, %s, CASE WHEN %s = 'owner_bootstrap' THEN 'admin' ELSE 'user' END, 'google', %s, 'PENDING_ENROLLMENT', now())
                           RETURNING id, email, name, role, enrollment_status""",
                        (normalized_email, OAUTH_DISABLED_PASSWORD_HASH, name, grant["grant_type"], avatar_url),
                    )
                    user = _row_to_dict(cur.fetchone())
                cur.execute(
                    "INSERT INTO external_identities (user_id, issuer, subject) VALUES (%s, %s, %s)",
                    (user["id"], issuer, subject),
                )
                cur.execute(
                    """UPDATE enrollment_admission_grants
                       SET consumed_at=now(), consumed_by_user_id=%s
                       WHERE id=%s AND consumed_at IS NULL""",
                    (user["id"], grant["id"]),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("admission grant was already consumed")
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'oidc_admission_consumed', 'allowed', %s::jsonb)""",
                    (user["id"], json.dumps({"grant_type": grant["grant_type"]})),
                )
            conn.commit()
        return user

    def provision_admission_grant(
        self, *, grant_type: str, email: str, token_digest: str | None,
        expires_at: datetime, created_by_user_id: str | None = None,
        target_user_id: str | None = None,
    ) -> None:
        """Setup/repair-only grant provisioning; runtime callbacks never call this."""
        if grant_type not in {"invitation", "allowlist", "owner_bootstrap"}:
            raise ValueError("invalid admission grant type")
        if grant_type != "allowlist" and not token_digest:
            raise ValueError("token digest is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                normalized_email = email.strip().lower()
                if target_user_id:
                    cur.execute(
                        "SELECT 1 FROM users WHERE id=%s AND email=%s AND enrollment_status='LEGACY_PENDING'",
                        (target_user_id, normalized_email),
                    )
                    if not cur.fetchone():
                        raise ValueError("target user must be the exact legacy account for this email")
                cur.execute(
                    """INSERT INTO enrollment_admission_grants
                       (grant_type, email, token_digest, expires_at, created_by_user_id, target_user_id)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (token_digest) DO NOTHING""",
                    (grant_type, normalized_email, token_digest, expires_at,
                     created_by_user_id, target_user_id),
                )
            conn.commit()

    def link_oidc_identity(
        self, *, user_id: str, actor_session_id: str, issuer: str, subject: str,
    ) -> bool:
        """Explicitly bind a verified identity to an already authenticated account."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.id
                       FROM sessions
                       JOIN users ON users.id=sessions.user_id
                       JOIN trusted_devices ON trusted_devices.id=sessions.device_id
                       WHERE sessions.id=%s AND sessions.user_id=%s
                         AND sessions.session_kind='normal'
                         AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                         AND users.enrollment_status='ACTIVE'
                         AND trusted_devices.user_id=sessions.user_id
                         AND trusted_devices.status='TRUSTED'
                       FOR UPDATE OF sessions""",
                    (actor_session_id, user_id),
                )
                if not cur.fetchone():
                    conn.rollback()
                    return False
                cur.execute("SELECT user_id FROM external_identities WHERE issuer=%s AND subject=%s FOR UPDATE", (issuer, subject))
                existing = _row_to_dict(cur.fetchone())
                if existing:
                    conn.rollback()
                    return str(existing["user_id"]) == str(user_id)
                cur.execute(
                    "INSERT INTO external_identities (user_id, issuer, subject) VALUES (%s, %s, %s)",
                    (user_id, issuer, subject),
                )
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'oidc_identity_linked', 'allowed', %s::jsonb)""",
                    (user_id, json.dumps({"issuer": issuer})),
                )
            conn.commit()
        return True

    def issue_pending_oauth_session(self, *, user_id: str, token_hash: str, device_id: str, expires_at: datetime) -> dict[str, Any]:
        return self.create_session(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at,
            session_kind="pending", device_id=device_id,
        )

    def issue_device_pending_session(self, *, user_id: str, token_hash: str, device_id: str, expires_at: datetime) -> dict[str, Any]:
        return self.create_session(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at,
            session_kind="device_pending", device_id=device_id,
        )

    def create_pending_device(self, *, user_id: str, device_digest: str, ip_hash: str | None, user_agent_hash: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO trusted_devices (user_id, device_secret_digest, status, ip_hash, user_agent_hash)
                       VALUES (%s, %s, 'PENDING', %s, %s)
                       RETURNING id, status""",
                    (user_id, device_digest, ip_hash, user_agent_hash),
                )
                row = _row_to_dict(cur.fetchone())
            conn.commit()
        return row

    def record_security_event(self, *, event_type: str, outcome: str, user_id: str | None = None, metadata: dict[str, Any] | None = None, ip_hash: str | None = None, user_agent_hash: str | None = None) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata, ip_hash, user_agent_hash)
                       VALUES (%s, %s, %s, %s::jsonb, %s, %s)""",
                    (user_id, event_type, outcome, json.dumps(metadata or {}), ip_hash, user_agent_hash),
                )
            conn.commit()

    def revoke_device_sessions(self, device_id: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE sessions SET revoked_at = now() WHERE device_id = %s AND revoked_at IS NULL", (device_id,))
                changed = cur.rowcount
            conn.commit()
        return int(changed or 0)

    def record_consents(self, *, user_id: str, documents: list[dict[str, str]], ip_hash: str | None, user_agent_hash: str | None) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for document in documents:
                    cur.execute(
                        """INSERT INTO enrollment_consents (user_id, document_key, document_version, document_sha256, ip_hash, user_agent_hash)
                           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                        (user_id, document["key"], document["version"], document["sha256"], ip_hash, user_agent_hash),
                    )
            conn.commit()

    def consents_complete(self, *, user_id: str, documents: list[dict[str, str]]) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for document in documents:
                    cur.execute(
                        """SELECT 1 FROM enrollment_consents
                           WHERE user_id=%s AND document_key=%s
                             AND document_version=%s AND document_sha256=%s""",
                        (user_id, document["key"], document["version"], document["sha256"]),
                    )
                    if not cur.fetchone():
                        return False
        return True

    def enrollment_progress(self, user_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT document_key, document_version, document_sha256 FROM enrollment_consents WHERE user_id=%s", (user_id,))
                consents = [_row_to_dict(row) for row in cur.fetchall()]
                cur.execute("SELECT EXISTS(SELECT 1 FROM webauthn_credentials WHERE user_id=%s AND revoked_at IS NULL) AS complete", (user_id,))
                mfa = _row_to_dict(cur.fetchone()) or {}
                cur.execute("SELECT count(*) AS count FROM recovery_codes WHERE user_id=%s", (user_id,))
                recovery = _row_to_dict(cur.fetchone()) or {}
                cur.execute("SELECT id, status FROM trusted_devices WHERE user_id=%s ORDER BY created_at DESC", (user_id,))
                devices = [_row_to_dict(row) for row in cur.fetchall()]
        return {"consents": consents, "mfa": bool(mfa.get("complete")), "recovery": int(recovery.get("count") or 0) > 0, "devices": devices}

    def create_webauthn_challenge(
        self, *, user_id: str, session_id: str, challenge_digest: str,
        purpose: str, rp_id: str, allowed_origin: str, expires_at: datetime,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO webauthn_challenges
                       (challenge_digest, user_id, session_id, purpose, rp_id, allowed_origin,
                        user_verification, expires_at)
                       SELECT %s, %s, %s, %s, %s, %s, 'required', %s
                       WHERE EXISTS (
                         SELECT 1 FROM sessions
                         WHERE id=%s AND user_id=%s AND session_kind='pending'
                           AND revoked_at IS NULL AND expires_at > now()
                       )""",
                    (challenge_digest, user_id, session_id, purpose, rp_id, allowed_origin,
                     expires_at, session_id, user_id),
                )
                if cur.rowcount != 1:
                    raise PermissionError("pending enrollment session is invalid")
            conn.commit()

    def get_webauthn_challenge(
        self, *, user_id: str, session_id: str, challenge_digest: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT challenge_digest, purpose, rp_id, allowed_origin, user_verification
                       FROM webauthn_challenges
                       WHERE challenge_digest=%s AND user_id=%s AND session_id=%s
                         AND purpose='registration' AND user_verification='required'
                         AND consumed_at IS NULL AND expires_at > now()""",
                    (challenge_digest, user_id, session_id),
                )
                return _row_to_dict(cur.fetchone())

    def consume_webauthn_challenge_and_save_credential(
        self, *, user_id: str, session_id: str, challenge_digest: str,
        credential_id: str, public_key: bytes, sign_count: int,
        transports: list[str], rp_id: str, allowed_origin: str,
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE webauthn_challenges SET consumed_at=now()
                       WHERE challenge_digest=%s AND user_id=%s AND session_id=%s
                         AND purpose='registration' AND rp_id=%s AND allowed_origin=%s
                         AND user_verification='required' AND consumed_at IS NULL
                         AND expires_at > now()""",
                    (challenge_digest, user_id, session_id, rp_id, allowed_origin),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return False
                cur.execute(
                    """INSERT INTO webauthn_credentials
                       (credential_id, user_id, public_key, sign_count, transports, user_verified_at)
                       VALUES (%s, %s, %s, %s, %s::jsonb, now())""",
                    (credential_id, user_id, public_key, sign_count, json.dumps(transports)),
                )
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'webauthn_registration_verified', 'allowed', %s::jsonb)""",
                    (user_id, json.dumps({"rp_id": rp_id})),
                )
            conn.commit()
        return True

    def issue_recovery_codes(
        self, *, user_id: str, digests: list[str], documents: list[dict[str, str]],
    ) -> bool:
        if len(digests) < 8:
            raise ValueError("at least eight recovery codes are required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT enrollment_status FROM users WHERE id=%s FOR UPDATE", (user_id,))
                user = _row_to_dict(cur.fetchone())
                if not user or user.get("enrollment_status") != "PENDING_ENROLLMENT":
                    conn.rollback()
                    return False
                for document in documents:
                    cur.execute(
                        """SELECT 1 FROM enrollment_consents
                           WHERE user_id=%s AND document_key=%s
                             AND document_version=%s AND document_sha256=%s""",
                        (user_id, document["key"], document["version"], document["sha256"]),
                    )
                    if not cur.fetchone():
                        conn.rollback()
                        return False
                cur.execute(
                    """SELECT 1 FROM webauthn_credentials
                       WHERE user_id=%s AND revoked_at IS NULL AND user_verified_at IS NOT NULL""",
                    (user_id,),
                )
                if not cur.fetchone():
                    conn.rollback()
                    return False
                cur.execute("SELECT 1 FROM recovery_codes WHERE user_id=%s", (user_id,))
                if cur.fetchone():
                    conn.rollback()
                    return False
                for digest in digests:
                    cur.execute("INSERT INTO recovery_codes (user_id, code_digest) VALUES (%s, %s)", (user_id, digest))
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome)
                       VALUES (%s, 'recovery_codes_created', 'allowed')""",
                    (user_id,),
                )
            conn.commit()
        return True

    def approve_pending_device(
        self, *, target_user_id: str, device_id: str, actor_session_id: str,
    ) -> bool:
        """Approve a target device from an existing trusted native session."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.user_id, users.role
                       FROM sessions JOIN users ON users.id=sessions.user_id
                       JOIN trusted_devices ON trusted_devices.id=sessions.device_id
                       WHERE sessions.id=%s AND sessions.session_kind='normal'
                         AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                         AND users.enrollment_status='ACTIVE'
                         AND trusted_devices.user_id=sessions.user_id
                         AND trusted_devices.status='TRUSTED' FOR UPDATE""",
                    (actor_session_id,),
                )
                actor = _row_to_dict(cur.fetchone())
                if not actor or (
                    str(actor["user_id"]) != str(target_user_id)
                    and str(actor.get("role") or "user") != "admin"
                ):
                    conn.rollback()
                    return False
                cur.execute(
                    """UPDATE trusted_devices
                       SET status='TRUSTED', approved_by_user_id=%s, approved_at=now()
                       WHERE id=%s AND user_id=%s AND status='PENDING'""",
                    (actor["user_id"], device_id, target_user_id),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return False
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'trusted_device_approved', 'allowed', %s::jsonb)""",
                    (target_user_id, json.dumps({
                        "device_id": device_id,
                        "actor_user_id": str(actor["user_id"]),
                    })),
                )
            conn.commit()
        return True

    def approve_device_and_rotate_session(
        self, *, owner_user_id: str, device_id: str, pending_session_id: str,
        actor_session_id: str | None, normal_token_hash: str, normal_expires_at: datetime,
        documents: list[dict[str, str]], device_secret_digest: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, session_kind, device_id FROM sessions
                       WHERE id=%s AND user_id=%s AND session_kind IN ('pending','device_pending')
                         AND revoked_at IS NULL AND expires_at > now() FOR UPDATE""",
                    (pending_session_id, owner_user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending or str(pending.get("device_id")) != str(device_id):
                    conn.rollback()
                    return None
                cur.execute(
                    """SELECT id, enrollment_status FROM users WHERE id=%s FOR UPDATE""",
                    (owner_user_id,),
                )
                owner = _row_to_dict(cur.fetchone())
                cur.execute(
                    """SELECT id, user_id, status, device_secret_digest, approved_by_user_id FROM trusted_devices
                       WHERE id=%s AND user_id=%s FOR UPDATE""",
                    (device_id, owner_user_id),
                )
                device = _row_to_dict(cur.fetchone())
                if (
                    not owner or not device or device.get("status") not in {"PENDING", "TRUSTED"}
                    or not hmac.compare_digest(str(device.get("device_secret_digest") or ""), device_secret_digest)
                ):
                    conn.rollback()
                    return None

                actor_user_id = None
                actor_role = None
                if actor_session_id:
                    cur.execute(
                        """SELECT sessions.user_id, users.role
                           FROM sessions
                           JOIN users ON users.id=sessions.user_id
                           JOIN trusted_devices ON trusted_devices.id=sessions.device_id
                           WHERE sessions.id=%s AND sessions.session_kind='normal'
                             AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                             AND users.enrollment_status='ACTIVE'
                             AND trusted_devices.user_id=sessions.user_id
                             AND trusted_devices.status='TRUSTED'""",
                        (actor_session_id,),
                    )
                    actor = _row_to_dict(cur.fetchone())
                    if actor:
                        actor_user_id = str(actor["user_id"])
                        actor_role = str(actor.get("role") or "user")

                permitted = (
                    device.get("status") == "TRUSTED"
                    or actor_user_id == owner_user_id or actor_role == "admin"
                )
                if not permitted:
                    conn.rollback()
                    return None

                if owner.get("enrollment_status") == "PENDING_ENROLLMENT":
                    for document in documents:
                        cur.execute(
                            """SELECT 1 FROM enrollment_consents
                               WHERE user_id=%s AND document_key=%s
                                 AND document_version=%s AND document_sha256=%s""",
                            (owner_user_id, document["key"], document["version"], document["sha256"]),
                        )
                        if not cur.fetchone():
                            conn.rollback()
                            return None
                    cur.execute(
                        """SELECT 1 FROM webauthn_credentials
                           WHERE user_id=%s AND revoked_at IS NULL AND user_verified_at IS NOT NULL""",
                        (owner_user_id,),
                    )
                    if not cur.fetchone():
                        conn.rollback()
                        return None
                    cur.execute("SELECT 1 FROM recovery_codes WHERE user_id=%s", (owner_user_id,))
                    if not cur.fetchone():
                        conn.rollback()
                        return None

                approving_user_id = (
                    str(device.get("approved_by_user_id"))
                    if device.get("status") == "TRUSTED"
                    else actor_user_id or owner_user_id
                )
                if device.get("status") == "PENDING":
                    cur.execute(
                        """UPDATE trusted_devices
                           SET status='TRUSTED', approved_by_user_id=%s, approved_at=now()
                           WHERE id=%s AND user_id=%s AND status='PENDING'""",
                        (approving_user_id, device_id, owner_user_id),
                    )
                    if cur.rowcount != 1:
                        conn.rollback()
                        return None
                if owner.get("enrollment_status") == "PENDING_ENROLLMENT":
                    cur.execute(
                        """UPDATE users SET enrollment_status='ACTIVE', enrollment_completed_at=now(), updated_at=now()
                           WHERE id=%s AND enrollment_status='PENDING_ENROLLMENT'""",
                        (owner_user_id,),
                    )
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (pending_session_id,))
                cur.execute(
                    """INSERT INTO sessions
                       (user_id, token_hash, expires_at, session_kind, device_id, rotated_from_session_id)
                       VALUES (%s, %s, %s, 'normal', %s, %s)
                       RETURNING id, user_id, device_id, expires_at""",
                    (owner_user_id, normal_token_hash, normal_expires_at, device_id, pending_session_id),
                )
                normal = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'trusted_device_approved', 'allowed', %s::jsonb)""",
                    (owner_user_id, json.dumps({"device_id": device_id, "actor_user_id": approving_user_id})),
                )
            conn.commit()
        return normal

    def revoke_device(self, *, device_id: str, actor_session_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.user_id, users.role
                       FROM sessions JOIN users ON users.id=sessions.user_id
                       JOIN trusted_devices ON trusted_devices.id=sessions.device_id
                       WHERE sessions.id=%s AND sessions.session_kind='normal'
                         AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                         AND users.enrollment_status='ACTIVE'
                         AND trusted_devices.user_id=sessions.user_id
                         AND trusted_devices.status='TRUSTED' FOR UPDATE""",
                    (actor_session_id,),
                )
                actor = _row_to_dict(cur.fetchone())
                if not actor:
                    conn.rollback()
                    return False
                cur.execute("SELECT user_id FROM trusted_devices WHERE id=%s FOR UPDATE", (device_id,))
                target = _row_to_dict(cur.fetchone())
                if not target or (str(target["user_id"]) != str(actor["user_id"]) and str(actor.get("role")) != "admin"):
                    conn.rollback()
                    return False
                cur.execute(
                    """UPDATE trusted_devices SET status='REVOKED', revoked_at=now()
                       WHERE id=%s AND status<>'REVOKED'""",
                    (device_id,),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return False
                cur.execute("UPDATE sessions SET revoked_at=now() WHERE device_id=%s AND revoked_at IS NULL", (device_id,))
                cur.execute(
                    """INSERT INTO auth_security_events (user_id, event_type, outcome, metadata)
                       VALUES (%s, 'trusted_device_revoked', 'allowed', %s::jsonb)""",
                    (target["user_id"], json.dumps({"device_id": device_id, "actor_user_id": str(actor["user_id"])})),
                )
            conn.commit()
        return True

    def recover_device_and_rotate_session(
        self, *, owner_user_id: str, device_id: str, pending_session_id: str,
        recovery_code_digest: str, device_secret_digest: str,
        normal_token_hash: str, normal_expires_at: datetime,
    ) -> dict[str, Any] | None:
        """Use one recovery code to trust one OIDC-verified pending device."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sessions.id, sessions.session_kind, users.enrollment_status
                       FROM sessions
                       JOIN users ON users.id=sessions.user_id
                       WHERE sessions.id=%s AND sessions.user_id=%s
                         AND sessions.session_kind IN ('pending','device_pending')
                         AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
                         AND ((sessions.session_kind='device_pending' AND users.enrollment_status='ACTIVE')
                           OR (sessions.session_kind='pending' AND users.enrollment_status='PENDING_ENROLLMENT'))
                       FOR UPDATE OF sessions""",
                    (pending_session_id, owner_user_id),
                )
                pending = _row_to_dict(cur.fetchone())
                if not pending:
                    conn.rollback()
                    return None
                bootstrap = pending.get("session_kind") == "pending"
                if bootstrap:
                    cur.execute(
                        """SELECT 1 FROM enrollment_admission_grants
                           WHERE consumed_by_user_id=%s AND grant_type='owner_bootstrap'
                             AND consumed_at IS NOT NULL""",
                        (owner_user_id,),
                    )
                    if not cur.fetchone():
                        conn.rollback()
                        return None
                    cur.execute(
                        """SELECT 1 FROM webauthn_credentials
                           WHERE user_id=%s AND revoked_at IS NULL
                             AND user_verified_at IS NOT NULL""",
                        (owner_user_id,),
                    )
                    if not cur.fetchone():
                        conn.rollback()
                        return None
                cur.execute(
                    """SELECT id FROM trusted_devices
                       WHERE id=%s AND user_id=%s AND status='PENDING'
                         AND device_secret_digest=%s FOR UPDATE""",
                    (device_id, owner_user_id, device_secret_digest),
                )
                if not cur.fetchone():
                    conn.rollback()
                    return None
                cur.execute(
                    """UPDATE recovery_codes SET used_at=now()
                       WHERE user_id=%s AND code_digest=%s AND used_at IS NULL
                       RETURNING id""",
                    (owner_user_id, recovery_code_digest),
                )
                if not cur.fetchone():
                    conn.rollback()
                    return None
                cur.execute(
                    """UPDATE trusted_devices
                       SET status='TRUSTED', approved_by_user_id=%s, approved_at=now()
                       WHERE id=%s AND user_id=%s AND status='PENDING'""",
                    (owner_user_id, device_id, owner_user_id),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return None
                if bootstrap:
                    cur.execute(
                        """UPDATE users
                           SET enrollment_status='ACTIVE', enrollment_completed_at=now(), updated_at=now()
                           WHERE id=%s AND enrollment_status='PENDING_ENROLLMENT'""",
                        (owner_user_id,),
                    )
                    if cur.rowcount != 1:
                        conn.rollback()
                        return None
                cur.execute(
                    "UPDATE sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL",
                    (pending_session_id,),
                )
                cur.execute(
                    """INSERT INTO sessions
                       (user_id, token_hash, expires_at, session_kind, device_id, rotated_from_session_id)
                       VALUES (%s,%s,%s,'normal',%s,%s)
                       RETURNING id,user_id,device_id,expires_at""",
                    (owner_user_id, normal_token_hash, normal_expires_at,
                     device_id, pending_session_id),
                )
                normal = _row_to_dict(cur.fetchone())
                cur.execute(
                    """INSERT INTO auth_security_events
                       (user_id,event_type,outcome,metadata)
                       VALUES (%s,'trusted_device_recovered','allowed',%s::jsonb)""",
                    (owner_user_id, json.dumps({"device_id": device_id})),
                )
            conn.commit()
        return normal

    def is_bootstrap_user(self, user_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT EXISTS(
                         SELECT 1 FROM enrollment_admission_grants
                         WHERE consumed_by_user_id=%s AND grant_type='owner_bootstrap'
                       ) AS bootstrap""", (user_id,)
                )
                row = _row_to_dict(cur.fetchone()) or {}
        return bool(row.get("bootstrap"))

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
