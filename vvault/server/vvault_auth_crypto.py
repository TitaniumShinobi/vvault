"""Provider-neutral primitives for VVAULT identity transactions.

The callers own raw OAuth or email-link tokens.  This module intentionally only
creates opaque values and their keyed digests so persistence never needs a raw
credential.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken


SUPPORTED_PROVIDERS = frozenset({"google", "github", "email"})


def opaque_token(bytes_: int = 32) -> str:
    if bytes_ < 32:
        raise ValueError("identity tokens must contain at least 256 bits")
    return secrets.token_urlsafe(bytes_)


def keyed_digest(value: str, key: str) -> str:
    if not value or len(key) < 32:
        raise ValueError("a value and a 32+ character identity key are required")
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def safe_compare(value: str, digest: str, key: str) -> bool:
    return hmac.compare_digest(keyed_digest(value, key), digest)


def pkce_challenge(verifier: str) -> str:
    if not verifier:
        raise ValueError("PKCE verifier is required")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def seal_transaction_secret(value: str, encryption_key: str) -> bytes:
    """Encrypt a callback-only secret before persistence.

    `encryption_key` is a distinct Fernet key supplied from protected runtime
    configuration; it must never be derived from the token-digest HMAC key.
    """
    if not value:
        raise ValueError("transaction secret is required")
    try:
        return Fernet(encryption_key.encode("ascii")).encrypt(value.encode("utf-8"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY is invalid") from exc


def open_transaction_secret(ciphertext: bytes, encryption_key: str) -> str:
    if not ciphertext:
        raise ValueError("transaction secret ciphertext is required")
    try:
        return Fernet(encryption_key.encode("ascii")).decrypt(bytes(ciphertext)).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("OAuth transaction secret cannot be decrypted") from exc


def normalize_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("unsupported identity provider")
    return provider


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    # Delivery and provider proof validate mailbox ownership.  This rejects
    # malformed routing input without pretending to prove an address exists.
    if len(email) > 320 or email.count("@") != 1:
        raise ValueError("a valid email address is required")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise ValueError("a valid email address is required")
    return email


def normalize_recovery_code(value: str) -> str:
    """Normalize a human-entered recovery code before keyed hashing.

    The normalized form is deliberately small and unambiguous; callers never
    persist or log it.  A code has 128 bits of entropy before formatting.
    """
    code = value.strip().upper().replace("-", "")
    if len(code) != 32 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in code):
        raise ValueError("recovery code is invalid")
    return code


def recovery_codes(*, count: int = 10) -> list[str]:
    """Create display-once recovery codes without persisting raw values."""
    if not 1 <= count <= 20:
        raise ValueError("recovery code count must be between 1 and 20")
    generated: set[str] = set()
    while len(generated) < count:
        raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
        generated.add(f"{raw[:8]}-{raw[8:16]}-{raw[16:24]}-{raw[24:32]}")
    return sorted(generated)


def digest_recovery_codes(values: Iterable[str], key: str) -> list[str]:
    digests = [keyed_digest(normalize_recovery_code(value), key) for value in values]
    if not digests or len(digests) != len(set(digests)):
        raise ValueError("recovery codes must be non-empty and unique")
    return digests


def normalize_device_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = " ".join(value.split())
    if not label:
        return None
    if len(label) > 120:
        raise ValueError("device label is too long")
    return label


def normalize_webauthn_credential_id(value: str) -> str:
    """Accept URL-safe base64 credential identifiers without decoding them."""
    credential_id = value.strip()
    if not 16 <= len(credential_id) <= 2048:
        raise ValueError("WebAuthn credential ID is invalid")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in credential_id):
        raise ValueError("WebAuthn credential ID is invalid")
    return credential_id
