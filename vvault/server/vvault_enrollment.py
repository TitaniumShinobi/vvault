"""Small, side-effect-free helpers for VVAULT enrollment security boundaries."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

PENDING = "PENDING_ENROLLMENT"
ACTIVE = "ACTIVE"
REQUIRED_DOCUMENTS = (
    ("vvault:terms", "VVAULT_TERMS_OF_SERVICE.md"),
    ("vvault:privacy", "VVAULT_PRIVACY_NOTICE.md"),
)


def opaque_token(bytes_: int = 32) -> str:
    return secrets.token_urlsafe(bytes_)


def keyed_digest(value: str, key: str) -> str:
    if not value or not key:
        raise ValueError("value and enrollment key are required")
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def safe_compare(value: str, digest: str, key: str) -> bool:
    return hmac.compare_digest(keyed_digest(value, key), digest)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def legal_documents(repo_root: Path) -> list[dict[str, str]]:
    legal_root = repo_root / "docs" / "legal"
    documents: list[dict[str, str]] = []
    for key, filename in REQUIRED_DOCUMENTS:
        content = (legal_root / filename).read_bytes()
        # The checked-in legal document does not carry a separate semantic
        # version. Its immutable content hash is the versioned authority.
        digest = hashlib.sha256(content).hexdigest()
        documents.append({"key": key, "version": digest, "sha256": digest})
    return documents


def consent_set_complete(rows: list[dict[str, Any]], documents: list[dict[str, str]]) -> bool:
    actual = {(str(row.get("document_key")), str(row.get("document_version")), str(row.get("document_sha256"))) for row in rows}
    expected = {(doc["key"], doc["version"], doc["sha256"]) for doc in documents}
    return expected <= actual


def request_evidence(value: str, key: str) -> str | None:
    return keyed_digest(value, key) if value else None


def encrypt_transaction_secret(value: str, key: str) -> bytes:
    if not key:
        raise ValueError("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY is required")
    return Fernet(key.encode("ascii")).encrypt(value.encode("utf-8"))


def decrypt_transaction_secret(value: bytes, key: str) -> str:
    if not key:
        raise ValueError("VVAULT_OAUTH_TRANSACTION_ENCRYPTION_KEY is required")
    try:
        return Fernet(key.encode("ascii")).decrypt(bytes(value)).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("OAuth transaction secret cannot be decrypted") from exc


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("WebAuthn value is required")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError("WebAuthn value is not valid base64url") from exc


def registration_options(*, challenge: bytes, user_id: str, user_name: str, rp_id: str) -> dict[str, Any]:
    """Return strict WebAuthn creation options without retaining raw challenge material."""
    return {
        "challenge": b64url_encode(challenge),
        "rp": {"id": rp_id, "name": "VVAULT"},
        "user": {
            "id": b64url_encode(user_id.encode("utf-8")),
            "name": user_name,
            "displayName": user_name,
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},
            {"type": "public-key", "alg": -257},
        ],
        "authenticatorSelection": {
            "residentKey": "preferred",
            "userVerification": "required",
        },
        "attestation": "none",
        "timeout": 300000,
    }


def registration_challenge_from_credential(credential: dict[str, Any]) -> bytes:
    response = credential.get("response") if isinstance(credential, dict) else None
    encoded = response.get("clientDataJSON") if isinstance(response, dict) else None
    try:
        client_data = json.loads(b64url_decode(str(encoded or "")).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("WebAuthn client data is invalid") from exc
    if client_data.get("type") != "webauthn.create":
        raise ValueError("WebAuthn operation is not registration")
    return b64url_decode(str(client_data.get("challenge") or ""))


def verify_registration_credential(
    credential: dict[str, Any], *, challenge: bytes, rp_id: str, allowed_origin: str
) -> dict[str, Any]:
    """Verify attestation, RP/origin binding, and required user verification."""
    try:
        from webauthn import verify_registration_response
        from webauthn.helpers import parse_registration_credential_json
    except ImportError as exc:  # Fail closed when the native runtime is incomplete.
        raise RuntimeError("VVAULT WebAuthn verification dependency is unavailable") from exc

    parsed = parse_registration_credential_json(credential)
    verification = verify_registration_response(
        credential=parsed,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=allowed_origin,
        require_user_verification=True,
    )
    return {
        "credential_id": b64url_encode(bytes(verification.credential_id)),
        "public_key": bytes(verification.credential_public_key),
        "sign_count": int(verification.sign_count),
        "user_verified": True,
    }
