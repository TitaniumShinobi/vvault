from datetime import datetime, timedelta, timezone

from vvault.server import vvault_auth_repository as auth_repository


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
        elif statement.startswith("SELECT id FROM enrollment_devices"):
            self._row = None
        elif statement.startswith("INSERT INTO enrollment_devices"):
            self._row = {"id": "trusted-browser"}
        elif statement.startswith("INSERT INTO sessions"):
            self._row = {"id": "normal-session", "enrollment_device_id": "trusted-browser"}

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


def test_verified_active_login_binds_new_browser_before_normal_session(monkeypatch):
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = auth_repository.VVaultAuthRepository()
    monkeypatch.setattr(repository, "_connect", lambda: connection)
    monkeypatch.setattr(repository, "_has_current_legal_receipts_locked", lambda *_args, **_kwargs: True)

    result = repository.issue_known_device_session(
        user_id="owner-a",
        device_secret_digest="opaque-browser-digest",
        token_hash="session-digest",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        required_documents=[{"key": "terms", "version": "1", "sha256": "a" * 64}],
    )

    assert result["id"] == "normal-session"
    assert connection.committed and not connection.rolled_back
    device_insert = next(item for item in cursor.statements if item[0].startswith("INSERT INTO enrollment_devices"))
    assert device_insert[1] == ("owner-a", "opaque-browser-digest", "owner-a")
    session_insert = next(item for item in cursor.statements if item[0].startswith("INSERT INTO sessions"))
    assert session_insert[1][-1] == "trusted-browser"
