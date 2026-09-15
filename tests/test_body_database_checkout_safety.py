"""Connection failures before scoped work must not replay writes."""
from contextlib import nullcontext
from unittest.mock import Mock
import pytest
from vvault.server import chatty_body_service as body
from vvault.server import relying_party_scope


def test_pool_checks_connection_before_checkout(monkeypatch):
    import psycopg_pool
    constructor = Mock()
    constructor.check_connection = object()
    monkeypatch.setattr(psycopg_pool, 'ConnectionPool', constructor)
    monkeypatch.setattr(body, 'database_url', lambda: 'postgresql://fixture.invalid/canary')
    body._new_body_database_pool()
    assert constructor.call_args.kwargs['check'] is constructor.check_connection
    assert constructor.call_args.kwargs['open'] is False


def checkout():
    inner = Mock()
    conn = Mock()
    conn.cursor.return_value = nullcontext(object())
    inner.__enter__ = Mock(return_value=conn)
    inner.__exit__ = Mock(return_value=False)
    return inner, conn


@pytest.mark.parametrize('failure', [RuntimeError('scope failed'), KeyboardInterrupt()])
def test_scope_failure_returns_checkout_without_running_body(monkeypatch, failure):
    inner, _ = checkout()
    configure = Mock(side_effect=failure)
    monkeypatch.setattr(relying_party_scope, 'configure_connection', configure)
    ran = False
    with pytest.raises(type(failure)):
        with body._ScopedConnection(inner):
            ran = True
    assert not ran
    inner.__enter__.assert_called_once()
    inner.__exit__.assert_called_once()
    assert inner.__exit__.call_args.args[1] is failure
    configure.assert_called_once()


def test_checkout_failure_does_not_double_exit():
    inner, _ = checkout()
    inner.__enter__.side_effect = RuntimeError('checkout failed')
    with pytest.raises(RuntimeError):
        with body._ScopedConnection(inner):
            pytest.fail('body must not run')
    inner.__exit__.assert_not_called()


def test_success_configures_scope_then_exits_once(monkeypatch):
    inner, conn = checkout()
    configure = Mock()
    monkeypatch.setattr(relying_party_scope, 'configure_connection', configure)
    with body._ScopedConnection(inner) as actual:
        assert actual is conn
        configure.assert_called_once()
        inner.__exit__.assert_not_called()
    inner.__exit__.assert_called_once_with(None, None, None)
