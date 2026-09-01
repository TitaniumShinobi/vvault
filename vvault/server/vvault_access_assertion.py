"""Verification for short-lived Auth-issued VVAULT access assertions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ASSERTION_VERSION = "life-auth-vvault-access-assertion/v1"
ALLOWED_SCOPES = frozenset({
    "constructs:read", "identity:read", "knowledge:read", "memory:read",
    "transcripts:read", "transcripts:append", "work:read", "work:append",
})
_ALLOWED_HEADERS = {"alg", "typ", "kid"}
_ALLOWED_CLAIMS = {
    "iss", "aud", "sub", "jti", "iat", "exp", "version",
    "vvault_owner_id", "sid", "scopes",
}
_KID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


class AccessAssertionRejected(ValueError):
    """The supplied assertion is malformed, untrusted, or unauthorized."""


class AccessAssertionUnavailable(RuntimeError):
    """The verifier has no valid trusted key configuration."""


def _decode_segment(value: str, label: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessAssertionRejected(f"access assertion {label} is invalid") from exc
    if not isinstance(decoded, dict):
        raise AccessAssertionRejected(f"access assertion {label} must be an object")
    return decoded


def _load_public_key(encoded: str) -> tuple[Ed25519PublicKey, str]:
    try:
        key = serialization.load_pem_public_key(encoded.strip().replace("\\n", "\n").encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
    canonical = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, hashlib.sha256(canonical).hexdigest()


def resolve_public_key_ring(configured: Mapping[str, str] | None = None) -> dict[str, Ed25519PublicKey]:
    entries: dict[str, str] = dict(configured or {})
    if not entries:
        raw_ring = str(os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_PUBLIC_KEYS_JSON") or "").strip()
        if raw_ring:
            try:
                parsed = json.loads(raw_ring)
            except json.JSONDecodeError as exc:
                raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID") from exc
            if isinstance(parsed, dict) and isinstance(parsed.get("keys"), list):
                for item in parsed["keys"]:
                    if isinstance(item, dict):
                        entries[str(item.get("kid") or "").strip()] = str(item.get("publicKeyPem") or "")
            elif isinstance(parsed, dict):
                entries = {str(k): str(v) for k, v in parsed.items()}
            else:
                raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
        else:
            pem = str(os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_PUBLIC_KEY_PEM") or os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_PUBLIC_KEY_PEM") or "").strip()
            if pem:
                _, derived = _load_public_key(pem)
                entries[str(os.environ.get("AUTH_VVAULT_ACCESS_ASSERTION_KEY_ID") or os.environ.get("AUTH_VVAULT_ACCOUNT_ASSERTION_KEY_ID") or derived).strip()] = pem
    if not entries:
        raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_UNAVAILABLE")
    ring: dict[str, Ed25519PublicKey] = {}
    for kid, pem in entries.items():
        if not _KID.fullmatch(kid):
            raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_RING_INVALID")
        key, derived = _load_public_key(pem)
        if kid != derived:
            raise AccessAssertionUnavailable("ACCESS_ASSERTION_KEY_ID_MISMATCH")
        ring[kid] = key
    return ring


def verify_access_assertion(assertion: str, *, public_keys: Mapping[str, str] | None = None, issuer: str | None = None, now_seconds: int | None = None) -> dict[str, Any]:
    parts = str(assertion or "").strip().split(".")
    if len(parts) != 3:
        raise AccessAssertionRejected("access assertion must be a compact JWT")
    header, claims = _decode_segment(parts[0], "header"), _decode_segment(parts[1], "claims")
    if set(header) - _ALLOWED_HEADERS or set(claims) - _ALLOWED_CLAIMS:
        raise AccessAssertionRejected("access assertion contains unsupported fields")
    if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
        raise AccessAssertionRejected("access assertion algorithm is not allowed")
    kid = str(header.get("kid") or "").strip()
    public_key = resolve_public_key_ring(public_keys).get(kid)
    if public_key is None:
        raise AccessAssertionRejected("access assertion key id is not trusted")
    try:
        signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except (InvalidSignature, ValueError, UnicodeEncodeError) as exc:
        raise AccessAssertionRejected("access assertion signature is invalid") from exc
    if claims.get("version") != ASSERTION_VERSION or claims.get("iss") != str(issuer or os.environ.get("AUTH_JWT_ISSUER") or "quantum-auth") or claims.get("aud") != "vvault":
        raise AccessAssertionRejected("access assertion issuer, audience, or version is invalid")
    owner = str(claims.get("vvault_owner_id") or "").strip()
    if not _UUID.fullmatch(owner):
        raise AccessAssertionRejected("access assertion owner is invalid")
    if any(not str(claims.get(name) or "").strip() for name in ("sub", "sid", "jti")):
        raise AccessAssertionRejected("access assertion subject, session, and id are required")
    scopes = claims.get("scopes")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(scope, str) or scope not in ALLOWED_SCOPES for scope in scopes) or len(set(scopes)) != len(scopes):
        raise AccessAssertionRejected("access assertion scopes are invalid")
    try:
        issued_at, expires_at = int(claims.get("iat")), int(claims.get("exp"))
    except (TypeError, ValueError) as exc:
        raise AccessAssertionRejected("access assertion iat and exp are required") from exc
    instant = int(time.time() if now_seconds is None else now_seconds)
    if issued_at > instant + 30 or expires_at <= instant or expires_at - issued_at > 60:
        raise AccessAssertionRejected("access assertion is expired or outside the allowed lifetime")
    return {"subject": str(claims["sub"]).strip(), "ownerUserId": owner, "sessionId": str(claims["sid"]).strip(), "assertionId": str(claims["jti"]).strip(), "issuedAt": issued_at, "expiresAt": expires_at, "keyId": kid, "scopes": frozenset(scopes), "ownerFingerprint": hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]}


def required_scope(method: str, path: str) -> str | None:
    scopes = required_scopes(method, path)
    return sorted(scopes)[0] if scopes else None


def required_scopes(method: str, path: str) -> frozenset[str]:
    method, path = str(method or "GET").upper(), str(path or "")
    if path.startswith("/api/chatty/work-programs"):
        if method in {"GET", "HEAD"} or path.endswith(("/context", "/preflight-inspect", "/scope-resolve", "/evidence/resolve")):
            return frozenset({"work:read"})
        return frozenset({"work:append"}) if method == "POST" else frozenset()
    if method == "POST":
        exact = {
            "/api/chatty/system-runtimes/auto-001/context": {"identity:read", "transcripts:read", "knowledge:read"},
            "/api/chatty/system-runtimes/auto-001/exchanges": {"transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/hydro/events": {"transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/threads/index": {"transcripts:read"},
            "/api/chatty/system-runtimes/auto-001/hydro/catalog": {"identity:read", "constructs:read"},
            "/api/chatty/system-runtimes/auto-001/actions/grants": {"transcripts:read", "transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/actions/events": {"transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/code/projects/binding": {"work:read"},
            "/api/chatty/system-runtimes/auto-001/code/threads/history": {"work:read", "transcripts:read"},
            "/api/chatty/system-runtimes/auto-001/code/proposal-contexts": {"work:append"},
            "/api/chatty/system-runtimes/auto-001/hydro/dispatches": {"transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/hydro/cancellations": {"transcripts:append"},
            "/api/chatty/system-runtimes/auto-001/hydro/worker-receipts": {"transcripts:append"},
        }
        if path in exact:
            return frozenset(exact[path])
        if "/transcript/" in path or path == "/api/chatty/message" or path.endswith("/events"):
            return frozenset({"transcripts:append"})
        return frozenset()
    if method in {"GET", "HEAD"}:
        if "/transcript/" in path or "/threads" in path:
            return frozenset({"transcripts:read"})
        if "/memories" in path or "/ledger" in path:
            return frozenset({"memory:read"})
        if any(token in path for token in ("/identity", "/capsule", "/account-context", "/human-context")):
            return frozenset({"identity:read"})
        return frozenset({"knowledge:read"}) if "/knowledge" in path else frozenset({"constructs:read"})
    return frozenset()
