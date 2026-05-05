import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from vvault.server import vvault_auth_repository
from vvault.server import vvault_web_server as server


def _now():
    return datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)


class FakeAuthRepository:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.users = {}
        self.sessions = {}
        self.revoked = []

    def healthcheck(self):
        status = "healthy" if self.ready else "unhealthy"
        payload = {
            "ready": self.ready,
            "status": status,
            "auth_owner": "ovvaults.users",
            "session_owner": "ovvaults.sessions",
            "source_database": "vvault_body_test",
            "checks": {
                "users_readable": self.ready,
                "sessions_readable": self.ready,
                "auth_identity_columns": self.ready,
            },
        }
        if not self.ready:
            payload["error_code"] = "OperationalError"
        return payload

    def get_user_by_email(self, email):
        return self.users.get(email.strip().lower())

    def create_password_user(self, *, email, password_hash, name, role="user"):
        if not self.ready:
            raise RuntimeError("auth db unavailable")
        key = email.strip().lower()
        if key in self.users:
            raise RuntimeError("duplicate")
        user = {
            "id": f"user-{len(self.users) + 1}",
            "email": key,
            "password_hash": password_hash,
            "name": name,
            "role": role,
            "auth_provider": "password",
            "created_at": _now(),
        }
        self.users[key] = user
        return user

    def ensure_external_user(self, *, email, name=None, role="user"):
        existing = self.get_user_by_email(email)
        if existing:
            return existing
        return self.upsert_oauth_user(
            email=email,
            name=name or email.split("@")[0],
            role=role,
            oauth_provider="external",
            oauth_subject=email,
        )

    def upsert_oauth_user(self, *, email, name, role="user", oauth_provider, oauth_subject, avatar_url=None):
        if not self.ready:
            raise RuntimeError("auth db unavailable")
        key = email.strip().lower()
        user = self.users.get(key) or {
            "id": f"user-{len(self.users) + 1}",
            "email": key,
            "password_hash": vvault_auth_repository.OAUTH_DISABLED_PASSWORD_HASH,
            "created_at": _now(),
        }
        user.update(
            {
                "name": name,
                "role": role,
                "auth_provider": oauth_provider,
                "oauth_provider": oauth_provider,
                "oauth_subject": oauth_subject,
                "avatar_url": avatar_url,
                "last_login_at": _now(),
            }
        )
        self.users[key] = user
        return user

    def create_session(self, *, user_id, token_hash, expires_at):
        if not self.ready:
            raise RuntimeError("auth db unavailable")
        self.sessions[token_hash] = {
            "session_id": f"session-{len(self.sessions) + 1}",
            "user_id": user_id,
            "session_created_at": _now(),
            "expires_at": expires_at,
        }
        return self.sessions[token_hash]

    def get_session_by_hash(self, token_hash):
        session = self.sessions.get(token_hash)
        if not session:
            return None
        user = next(user for user in self.users.values() if user["id"] == session["user_id"])
        return {**session, "email": user["email"], "name": user.get("name"), "role": user.get("role"), "auth_provider": user.get("auth_provider")}

    def revoke_session_by_hash(self, token_hash):
        self.revoked.append(token_hash)
        return bool(self.sessions.pop(token_hash, None))

    def cleanup_expired_sessions(self):
        return 0


class TestVVaultNativeAuthPersistence(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        server.app.secret_key = "native-auth-test-secret"
        server.app.config["SECRET_KEY"] = "native-auth-test-secret"
        self.client = server.app.test_client()

    def _patch_auth(self, repo):
        return patch.object(server, "AUTH_REPOSITORY", repo)

    def test_register_persists_user_and_hashed_session_locally_without_supabase(self):
        repo = FakeAuthRepository()

        with self._patch_auth(repo), patch.object(
            server, "verify_turnstile_token", return_value=True
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": "Devon@example.com",
                    "password": "password123",
                    "confirmPassword": "password123",
                    "name": "Devon",
                    "turnstileToken": "token",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        token = payload["token"]
        self.assertIn("devon@example.com", repo.users)
        self.assertTrue(repo.sessions)
        self.assertNotIn(token, str(repo.sessions))
        self.assertFalse(hasattr(server, "supabase_client"))
        self.assertFalse(hasattr(server, "SUPABASE_STEWARD"))

    def test_login_logout_and_bearer_verify_use_local_session_owner(self):
        repo = FakeAuthRepository()
        password_hash = bcrypt_hash("password123")
        repo.create_password_user(email="devon@example.com", password_hash=password_hash, name="Devon")

        with self._patch_auth(repo):
            login = self.client.post("/api/auth/login", json={"email": "devon@example.com", "password": "password123"})
            self.assertEqual(login.status_code, 200)
            token = login.get_json()["token"]
            verify = self.client.get("/api/auth/verify", headers={"Authorization": f"Bearer {token}"})
            logout = self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
            after_logout = self.client.get("/api/auth/verify", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(verify.status_code, 200)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(after_logout.status_code, 401)
        self.assertTrue(repo.revoked)
        self.assertFalse(hasattr(server, "supabase_client"))

    def test_oauth_health_reports_vvault_auth_authority_without_supabase_fields(self):
        repo = FakeAuthRepository()
        with self._patch_auth(repo), patch.object(server, "GOOGLE_CLIENT_ID", "google-client-id"), patch.object(
            server, "GOOGLE_CLIENT_SECRET", "google-client-secret"
        ):
            response = self.client.get("/api/auth/google/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["vvault_auth_ready"])
        self.assertEqual(payload["auth_owner"], "ovvaults.users")
        self.assertEqual(payload["session_owner"], "ovvaults.sessions")
        self.assertNotIn("supabase_mode", payload)
        self.assertNotIn("supabase_identity_authority_available", payload)

    def test_oauth_callback_stores_identity_and_session_locally_without_supabase(self):
        repo = FakeAuthRepository()
        google_client = Mock()
        google_client.prepare_token_request.return_value = ("https://oauth.example/token", {}, "body")
        google_client.add_token.return_value = ("https://oauth.example/userinfo", {}, None)
        token_response = Mock(ok=True)
        token_response.json.return_value = {"access_token": "token"}
        discovery_response = Mock()
        discovery_response.json.return_value = {"token_endpoint": "https://oauth.example/token", "userinfo_endpoint": "https://oauth.example/userinfo"}
        userinfo_response = Mock(ok=True)
        userinfo_response.json.return_value = {
            "email": "oauth@example.com",
            "email_verified": True,
            "given_name": "OAuth",
            "sub": "google-subject",
            "picture": "https://example.com/avatar.png",
        }

        with self.client.session_transaction() as flask_session:
            flask_session["oauth_callback_url"] = "http://localhost:8000/api/auth/google/callback"
            flask_session["oauth_frontend_url"] = "http://localhost:7784"

        with self._patch_auth(repo), patch.object(
            server, "google_client", google_client
        ), patch.object(server, "GOOGLE_CLIENT_ID", "google-client-id"), patch.object(
            server, "GOOGLE_CLIENT_SECRET", "google-client-secret"
        ), patch.object(server.requests, "get", side_effect=[discovery_response, userinfo_response]), patch.object(
            server.requests, "post", return_value=token_response
        ):
            response = self.client.get("/api/auth/google/callback?code=test-code")

        self.assertEqual(response.status_code, 302)
        redirect_query = parse_qs(urlparse(response.headers["Location"]).query)
        token = redirect_query["token"][0]
        self.assertIn("oauth@example.com", repo.users)
        self.assertEqual(repo.users["oauth@example.com"]["auth_provider"], "google")
        self.assertTrue(repo.sessions)
        self.assertNotIn(token, str(repo.sessions))
        self.assertFalse(hasattr(server, "supabase_client"))

    def test_auth_db_unavailable_blocks_success_without_fallback(self):
        repo = FakeAuthRepository(ready=False)
        with self._patch_auth(repo), patch.object(server, "verify_turnstile_token", return_value=True):
            login = self.client.post("/api/auth/login", json={"email": "admin@vvault.com", "password": "admin123"})
            register = self.client.post(
                "/api/auth/register",
                json={
                    "email": "devon@example.com",
                    "password": "password123",
                    "confirmPassword": "password123",
                    "name": "Devon",
                    "turnstileToken": "token",
                },
            )

        self.assertEqual(login.status_code, 503)
        self.assertEqual(register.status_code, 503)
        self.assertEqual(login.get_json()["error_code"], "VVAULT_AUTH_UNAVAILABLE")
        self.assertEqual(register.get_json()["error_code"], "VVAULT_AUTH_UNAVAILABLE")


def bcrypt_hash(password):
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
