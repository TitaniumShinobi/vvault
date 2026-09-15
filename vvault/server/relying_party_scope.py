"""Verified relying-party scope for consumer-owned VVAULT data."""
from __future__ import annotations

from contextvars import ContextVar
import re

_ALLOWED = frozenset({"chatty", "chatty-cli", "vvault"})
_scope: ContextVar[str] = ContextVar("vvault_relying_party_id", default="vvault")
_account_user_id: ContextVar[str] = ContextVar("vvault_authenticated_user_id", default="")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def set_relying_party_id(value: str) -> None:
    value = str(value or "").strip()
    if value not in _ALLOWED:
        raise ValueError("untrusted_relying_party_id")
    _scope.set(value)


def current_relying_party_id() -> str:
    return _scope.get()


def set_authenticated_user_id(value: str | None) -> None:
    """Install the server-verified account principal for DB RLS."""
    normalized = str(value or "").strip()
    if normalized and not _UUID.fullmatch(normalized):
        raise ValueError("untrusted_authenticated_user_id")
    _account_user_id.set(normalized)


def current_authenticated_user_id() -> str:
    return _account_user_id.get()


def configure_connection(cursor) -> None:
    """Bind PostgreSQL RLS to server-verified request context only."""
    cursor.execute(
        "SELECT set_config('app.vvault_relying_party_id', %s, false)",
        (current_relying_party_id(),),
    )
    cursor.execute(
        "SELECT set_config('app.vvault_authenticated_user_id', %s, false)",
        (current_authenticated_user_id(),),
    )
