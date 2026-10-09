from datetime import datetime, timedelta, timezone
from pathlib import Path

from vvault.server import vvault_auth_repository as auth_repository


REPO_ROOT = Path(__file__).resolve().parent.parent
APP = (REPO_ROOT / "src" / "App.js").read_text(encoding="utf-8")
SERVER = (REPO_ROOT / "vvault" / "server" / "vvault_web_server.py").read_text(encoding="utf-8")
MIGRATION = (REPO_ROOT / "vvault" / "migrations" / "0039_returning_owner_session_without_device_gate.up.sql").read_text(encoding="utf-8")


class _Cursor:
    def __init__(self):
        self.statements = []
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self.statements.append((statement, params))
        if statement.startswith("SELECT 1 FROM users"):
            self._row = {"present": 1}
        elif statement.startswith("INSERT INTO sessions"):
            self._row = {
                "id": "normal-session",
                "user_id": "owner-a",
                "enrollment_session_kind": "NORMAL",
                "enrollment_device_id": None,
            }

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_active_owner_session_does_not_require_a_trusted_browser(monkeypatch):
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = auth_repository.VVaultAuthRepository()
    monkeypatch.setattr(repository, "_connect", lambda: connection)
    monkeypatch.setattr(repository, "_has_current_legal_receipts_locked", lambda *_args, **_kwargs: True)

    result = repository.issue_active_session(
        user_id="owner-a",
        token_hash="session-digest",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        required_documents=[{"key": "terms", "version": "1", "sha256": "a" * 64}],
    )

    assert result["id"] == "normal-session"
    assert result["enrollment_device_id"] is None
    assert connection.committed and not connection.rolled_back


def test_frontend_and_callback_do_not_restore_the_device_gate():
    assert "device_approval_required') === '1') return <CinematicLogin" in APP
    active_branch = SERVER.split('elif state == "ACTIVE":', 1)[1].split("else:", 1)[0]
    assert "issue_active_session" in active_branch
    assert "issue_pending_device_session" not in active_branch


def test_database_allows_verified_active_owner_session_without_device_gate():
    assert "NEW.enrollment_session_kind = 'NORMAL' AND NEW.enrollment_device_id IS NULL" in MIGRATION
    assert "account_state_value <> 'ACTIVE'" in MIGRATION
    assert "normal session requires active account" in MIGRATION
