"""Signed, revisioned offline construct projections for Chatty Core hosts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vvault.server import canonical_projection_signing

SNAPSHOT_VERSION = "chatty-construct-snapshot/v1"
DEFAULT_TTL_SECONDS = 6 * 60 * 60
MAX_TTL_SECONDS = 24 * 60 * 60


def _canonical_json(value: Any) -> bytes:
    return canonical_projection_signing.canonical_json_bytes(value)


def _load_private_key(pem: str | None = None) -> Ed25519PrivateKey:
    return canonical_projection_signing.load_private_key(pem)


def public_key_document(*, private_key_pem: str | None = None) -> dict[str, Any]:
    return canonical_projection_signing.public_key_document(
        private_key_pem=private_key_pem
    )


def issue_construct_snapshot(
    *,
    owner_user_id: str,
    construct_id: str,
    identity: dict[str, Any],
    capsule: dict[str, Any],
    memory: dict[str, Any] | None,
    permissions: dict[str, Any] | None,
    capabilities: dict[str, Any] | None,
    revision: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    private_key_pem: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not owner_user_id or not construct_id or not revision:
        raise ValueError("SNAPSHOT_OWNER_CONSTRUCT_AND_REVISION_REQUIRED")
    if not identity or not capsule:
        raise ValueError("SNAPSHOT_IDENTITY_AND_CAPSULE_REQUIRED")
    bounded_ttl = min(MAX_TTL_SECONDS, max(60, int(ttl_seconds)))
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=bounded_ttl)
    payload = {
        "constructId": construct_id,
        "ownerUserId": owner_user_id,
        "revision": revision,
        "identity": identity,
        "capsule": capsule,
        "memory": memory or {},
        "permissions": permissions or {},
        "capabilities": capabilities or {},
        "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        "authority": "ovvaults",
        "deferredExecutionOnly": True,
    }
    signature_fields = canonical_projection_signing.sign_canonical_payload(
        payload,
        private_key_pem=private_key_pem,
    )
    return {
        "version": SNAPSHOT_VERSION,
        "algorithm": signature_fields["algorithm"],
        "keyId": signature_fields["keyId"],
        "payload": payload,
        "signature": signature_fields["signature"],
    }
