"""Verified relying-party scope for consumer-owned VVAULT data."""
from __future__ import annotations

from contextvars import ContextVar

_ALLOWED = frozenset({"chatty", "chatty-cli", "vvault"})
_scope: ContextVar[str] = ContextVar("vvault_relying_party_id", default="vvault")


def set_relying_party_id(value: str) -> None:
    value = str(value or "").strip()
    if value not in _ALLOWED:
        raise ValueError("untrusted_relying_party_id")
    _scope.set(value)


def current_relying_party_id() -> str:
    return _scope.get()


def configure_connection(cursor) -> None:
    """Bind PostgreSQL RLS to server-verified, never client-provided, scope."""
    cursor.execute(
        "SELECT set_config('app.vvault_relying_party_id', %s, false)",
        (current_relying_party_id(),),
    )
