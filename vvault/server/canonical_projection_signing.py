"""Canonical Ed25519 signing primitives for VVAULT projections.

This module signs only caller-supplied JSON payloads.  It does not decide what
is canonical, who owns a projection, or whether the payload is authorized.
Those decisions remain with the service constructing the projection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ALGORITHM = "Ed25519"
PRIVATE_KEY_ENV = "VVAULT_OFFLINE_SNAPSHOT_PRIVATE_KEY_PEM"
_SIGNATURE_FIELDS = frozenset({"algorithm", "keyId", "signature"})


def _assert_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("CANONICAL_PROJECTION_PAYLOAD_INVALID")
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("CANONICAL_PROJECTION_PAYLOAD_INVALID")
        for item in value.values():
            _assert_json_value(item)
        return
    raise ValueError("CANONICAL_PROJECTION_PAYLOAD_INVALID")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes, rejecting non-JSON numbers."""
    _assert_json_value(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("CANONICAL_PROJECTION_PAYLOAD_INVALID") from exc
    return encoded.encode("utf-8")


def load_private_key(pem: str | None = None) -> Ed25519PrivateKey:
    encoded = (pem or os.environ.get(PRIVATE_KEY_ENV) or "").strip()
    if not encoded:
        raise RuntimeError("OFFLINE_SNAPSHOT_SIGNING_KEY_UNAVAILABLE")
    try:
        key = serialization.load_pem_private_key(encoded.encode("utf-8"), password=None)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OFFLINE_SNAPSHOT_SIGNING_KEY_INVALID") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("OFFLINE_SNAPSHOT_SIGNING_KEY_INVALID")
    return key


def _public_key_from_pem(pem: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem.strip().encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("PROJECTION_VERIFICATION_KEY_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("PROJECTION_VERIFICATION_KEY_INVALID")
    return key


def _public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _key_id(public_key: Ed25519PublicKey) -> str:
    # Use the encoding-independent DER/SPKI digest so harmless PEM formatting
    # differences cannot change the identity of the same verification key.
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(public_der).hexdigest()


def _legacy_pem_key_id(public_key: Ed25519PublicKey) -> str:
    """Compatibility identity for projections signed before DER/SPKI alignment."""
    return hashlib.sha256(_public_key_bytes(public_key)).hexdigest()


def public_key_document(*, private_key_pem: str | None = None) -> dict[str, Any]:
    public_key = load_private_key(private_key_pem).public_key()
    return {
        "algorithm": ALGORITHM,
        "keyId": _key_id(public_key),
        "publicKeyPem": _public_key_bytes(public_key).decode("utf-8"),
    }


def sign_canonical_payload(
    payload: dict[str, Any],
    *,
    private_key_pem: str | None = None,
) -> dict[str, str]:
    """Sign a canonical JSON object and return detached signature fields."""
    if not isinstance(payload, dict):
        raise ValueError("CANONICAL_PROJECTION_PAYLOAD_INVALID")
    key = load_private_key(private_key_pem)
    public_key = key.public_key()
    signature = key.sign(canonical_json_bytes(payload))
    return {
        "algorithm": ALGORITHM,
        "keyId": _key_id(public_key),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_canonical_payload(
    payload: dict[str, Any],
    signature_fields: dict[str, Any],
    *,
    public_key_pem: str | None = None,
    private_key_pem: str | None = None,
) -> None:
    """Verify detached signature fields against the exact canonical payload."""
    if not isinstance(payload, dict) or not isinstance(signature_fields, dict):
        raise ValueError("CANONICAL_PROJECTION_SIGNATURE_INVALID")
    if set(signature_fields) != _SIGNATURE_FIELDS:
        raise ValueError("CANONICAL_PROJECTION_SIGNATURE_INVALID")
    if signature_fields.get("algorithm") != ALGORITHM:
        raise ValueError("CANONICAL_PROJECTION_SIGNATURE_INVALID")
    if public_key_pem is not None:
        public_key = _public_key_from_pem(public_key_pem)
    else:
        public_key = load_private_key(private_key_pem).public_key()
    if signature_fields.get("keyId") not in {
        _key_id(public_key),
        _legacy_pem_key_id(public_key),
    }:
        raise ValueError("CANONICAL_PROJECTION_KEY_ID_MISMATCH")
    try:
        signature = base64.b64decode(
            str(signature_fields.get("signature") or ""),
            validate=True,
        )
        public_key.verify(signature, canonical_json_bytes(payload))
    except (InvalidSignature, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "CANONICAL_PROJECTION_PAYLOAD_INVALID":
            raise
        raise ValueError("CANONICAL_PROJECTION_SIGNATURE_INVALID") from exc
