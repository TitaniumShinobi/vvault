import sys
from types import SimpleNamespace

import pytest

from vvault.server import chatty_body_service as body
from vvault.server import relying_party_scope


class _Cursor:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, *_args, **_kwargs): return None


class _Connection:
    def __init__(self): self.closed = False
    def cursor(self): return _Cursor()
    def __enter__(self): return self
    def __exit__(self, *_args): self.closed = True
    def close(self): self.closed = True


class _Gate:
    def __init__(self, allowed=True): self.allowed = allowed; self.releases = 0; self.timeouts = []
    def acquire(self, *, timeout): self.timeouts.append(timeout); return self.allowed
    def release(self): self.releases += 1


def _fake_driver(monkeypatch, connect):
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.rows", SimpleNamespace(dict_row=object()))
    monkeypatch.setattr(relying_party_scope, "configure_connection", lambda _cursor: None)


def test_connection_has_bounded_acquire_connect_statement_and_lock_timeouts(monkeypatch):
    captured = {}
    gate = _Gate()
    monkeypatch.setattr(body, "_BODY_DATABASE_CONNECTION_GATE", gate)
    monkeypatch.setenv("VVAULT_BODY_DATABASE_URL", "postgresql://fixture/canonical")
    _fake_driver(monkeypatch, lambda url, **kwargs: captured.update(url=url, **kwargs) or _Connection())
    connection = body._connect()
    assert captured["connect_timeout"] == body.BODY_DATABASE_CONNECT_TIMEOUT_SECONDS
    assert f"statement_timeout={body.BODY_DATABASE_STATEMENT_TIMEOUT_MS}" in captured["options"]
    assert f"lock_timeout={body.BODY_DATABASE_STATEMENT_TIMEOUT_MS}" in captured["options"]
    assert gate.timeouts == [body.BODY_DATABASE_ACQUIRE_TIMEOUT_SECONDS]
    connection.close()
    assert gate.releases == 1


def test_connection_limit_fails_closed_without_opening_driver(monkeypatch):
    gate = _Gate(allowed=False)
    monkeypatch.setattr(body, "_BODY_DATABASE_CONNECTION_GATE", gate)
    monkeypatch.setenv("VVAULT_BODY_DATABASE_URL", "postgresql://fixture/canonical")
    _fake_driver(monkeypatch, lambda *_args, **_kwargs: pytest.fail("driver must not be called"))
    with pytest.raises(TimeoutError, match="connection limit"):
        body._connect()
