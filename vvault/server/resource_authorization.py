"""Milestone-1 AUTH resource assertion validation for VVAULT.

This boundary is intentionally separate from the legacy Chatty assertion v2
validator.  A locally valid assertion is never sufficient: every protected
request must also receive a matching positive response from AUTH's directly
authenticated resource-status service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


AUTH_CONTRACT = "life.auth.resource-authorization/v1"
WIRE_CONTRACT = "life.vvault.resource-workspace/v1"
TOKEN_TYPE = "life-resource+jwt"
PRODUCTION_AUDIENCE = "https://vvault.thewreck.org"
WORKSPACE_RESOLVE_CAPABILITY = "workspace:resolve"
RESOURCE_STATUS_PATH = "/api/auth/resource-status"
REQUIRED_MIGRATION = "0041_resource_workspace_milestone1"
MAX_ASSERTION_SECONDS = 60

REGISTERED_APPLICATIONS = {
    ("grid-windows", "grid"): frozenset({WORKSPACE_RESOLVE_CAPABILITY}),
}

_HEADER_FIELDS = frozenset({"alg", "typ", "kid"})
_CLAIM_FIELDS = frozenset({
    "contract", "wire_contract", "iss", "sub", "aud", "client_id",
    "application_id", "owner_id", "sid", "jti", "grant_id",
    "capabilities", "admission_version", "iat", "exp",
})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_KID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


class ResourceAuthorizationError(RuntimeError):
    def __init__(self, code: str, http_status: int):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class ResourceTrustUnavailable(ResourceAuthorizationError):
    def __init__(self, code: str = "RESOURCE_TRUST_UNAVAILABLE"):
        super().__init__(code, 503)


def _decode_segment(value: str, label: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceAuthorizationError(f"RESOURCE_ASSERTION_{label.upper()}_INVALID", 401) from exc
    if not isinstance(decoded, dict):
        raise ResourceAuthorizationError(f"RESOURCE_ASSERTION_{label.upper()}_INVALID", 401)
    return decoded


def _load_public_key(value: str) -> tuple[Ed25519PublicKey, str]:
    try:
        key = serialization.load_pem_public_key(value.strip().replace("\\n", "\n").encode())
    except (TypeError, ValueError) as exc:
        raise ResourceTrustUnavailable("RESOURCE_KEY_RING_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ResourceTrustUnavailable("RESOURCE_KEY_RING_INVALID")
    canonical = key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, hashlib.sha256(canonical).hexdigest()


def resolve_public_key_ring(configured: Mapping[str, str] | None = None) -> dict[str, Ed25519PublicKey]:
    entries = dict(configured or {})
    if not entries:
        raw = str(os.environ.get("AUTH_RESOURCE_PUBLIC_KEYS_JSON") or "").strip()
        if not raw:
            raise ResourceTrustUnavailable("RESOURCE_KEY_RING_UNAVAILABLE")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResourceTrustUnavailable("RESOURCE_KEY_RING_INVALID") from exc
        if isinstance(parsed, dict) and isinstance(parsed.get("keys"), list):
            entries = {
                str(item.get("kid") or "").strip(): str(item.get("publicKeyPem") or "")
                for item in parsed["keys"] if isinstance(item, dict)
            }
        elif isinstance(parsed, dict):
            entries = {str(key): str(value) for key, value in parsed.items()}
        else:
            raise ResourceTrustUnavailable("RESOURCE_KEY_RING_INVALID")
    ring: dict[str, Ed25519PublicKey] = {}
    for kid, pem in entries.items():
        if not _KID.fullmatch(kid):
            raise ResourceTrustUnavailable("RESOURCE_KEY_RING_INVALID")
        key, derived_kid = _load_public_key(pem)
        if kid != derived_kid:
            raise ResourceTrustUnavailable("RESOURCE_KEY_ID_MISMATCH")
        ring[kid] = key
    if not ring:
        raise ResourceTrustUnavailable("RESOURCE_KEY_RING_UNAVAILABLE")
    return ring


def configured_issuer() -> str:
    value = str(os.environ.get("AUTH_RESOURCE_ISSUER") or "").strip()
    if not value:
        raise ResourceTrustUnavailable("RESOURCE_ISSUER_UNAVAILABLE")
    return value


def configured_audience() -> str:
    value = str(os.environ.get("AUTH_RESOURCE_AUDIENCE") or PRODUCTION_AUDIENCE).strip()
    if value != PRODUCTION_AUDIENCE:
        raise ResourceTrustUnavailable("RESOURCE_AUDIENCE_INVALID")
    return value


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512


def verify_resource_assertion(
    assertion: str,
    *,
    public_keys: Mapping[str, str] | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    now_seconds: int | None = None,
) -> dict[str, Any]:
    encoded = str(assertion or "").strip()
    if len(encoded) > 16384:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_INVALID", 401)
    parts = encoded.split(".")
    if len(parts) != 3:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_INVALID", 401)
    header = _decode_segment(parts[0], "header")
    claims = _decode_segment(parts[1], "claims")
    if set(header) != _HEADER_FIELDS or set(claims) != _CLAIM_FIELDS:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_FIELDS_INVALID", 401)
    if header.get("alg") != "EdDSA" or header.get("typ") != TOKEN_TYPE:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_TYPE_INVALID", 401)
    kid = str(header.get("kid") or "")
    key = resolve_public_key_ring(public_keys).get(kid)
    if key is None:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_KEY_UNTRUSTED", 401)
    try:
        signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except (InvalidSignature, ValueError, UnicodeEncodeError) as exc:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_SIGNATURE_INVALID", 401) from exc

    expected_issuer = issuer if issuer is not None else configured_issuer()
    expected_audience = audience if audience is not None else configured_audience()
    if claims.get("contract") != AUTH_CONTRACT or claims.get("wire_contract") != WIRE_CONTRACT:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_VERSION_UNSUPPORTED", 401)
    if claims.get("iss") != expected_issuer or claims.get("aud") != expected_audience:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_AUTHORITY_INVALID", 401)
    client_id = str(claims.get("client_id") or "")
    application_id = str(claims.get("application_id") or "")
    permitted = REGISTERED_APPLICATIONS.get((client_id, application_id))
    if permitted is None:
        raise ResourceAuthorizationError("RESOURCE_APPLICATION_NOT_ADMITTED", 401)
    owner_id = str(claims.get("owner_id") or "")
    if not _UUID.fullmatch(owner_id):
        raise ResourceAuthorizationError("RESOURCE_OWNER_BINDING_INVALID", 401)
    for field in ("sub", "sid", "jti", "grant_id", "admission_version"):
        if not _bounded_text(claims.get(field)):
            raise ResourceAuthorizationError("RESOURCE_ASSERTION_BINDING_INVALID", 401)
    capabilities = claims.get("capabilities")
    if (
        not isinstance(capabilities, list) or not capabilities
        or capabilities != sorted(set(capabilities))
        or any(not isinstance(item, str) or not _SAFE_ID.fullmatch(item) for item in capabilities)
        or any(item not in permitted for item in capabilities)
    ):
        raise ResourceAuthorizationError("RESOURCE_CAPABILITIES_INVALID", 401)
    if type(claims.get("iat")) is not int or type(claims.get("exp")) is not int:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_TIME_INVALID", 401)
    issued_at = claims["iat"]
    expires_at = claims["exp"]
    now = int(time.time() if now_seconds is None else now_seconds)
    if issued_at > now or expires_at <= now or expires_at - issued_at > MAX_ASSERTION_SECONDS:
        raise ResourceAuthorizationError("RESOURCE_ASSERTION_EXPIRED", 401)
    return {
        "contract": AUTH_CONTRACT,
        "wireContract": WIRE_CONTRACT,
        "issuer": expected_issuer,
        "subject": str(claims["sub"]),
        "audience": expected_audience,
        "clientId": client_id,
        "applicationId": application_id,
        "relyingPartyId": application_id,
        "ownerUserId": owner_id,
        "sessionId": str(claims["sid"]),
        "assertionId": str(claims["jti"]),
        "grantId": str(claims["grant_id"]),
        "capabilities": tuple(capabilities),
        "admissionVersion": str(claims["admission_version"]),
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "keyId": kid,
        "ownerFingerprint": hashlib.sha256(owner_id.encode()).hexdigest()[:16],
    }


@dataclass(frozen=True)
class ResourceStatusClient:
    url: str
    certificate_path: str
    private_key_path: str
    ca_bundle_path: str
    timeout_seconds: float = 2.0
    post: Callable[..., Any] = requests.post

    @classmethod
    def from_environment(cls) -> "ResourceStatusClient":
        values = {
            "url": str(os.environ.get("AUTH_RESOURCE_STATUS_URL") or "").strip(),
            "certificate_path": str(os.environ.get("AUTH_RESOURCE_MTLS_CERT_PATH") or "").strip(),
            "private_key_path": str(os.environ.get("AUTH_RESOURCE_MTLS_KEY_PATH") or "").strip(),
            "ca_bundle_path": str(os.environ.get("AUTH_RESOURCE_CA_BUNDLE_PATH") or "").strip(),
        }
        if not all(values.values()) or not values["url"].endswith(RESOURCE_STATUS_PATH):
            raise ResourceTrustUnavailable("RESOURCE_STATUS_CONFIGURATION_UNAVAILABLE")
        return cls(**values)

    def _request(self, assertion: str) -> tuple[int, dict[str, Any]]:
        try:
            response = self.post(
                self.url,
                json={"assertion": assertion},
                cert=(self.certificate_path, self.private_key_path),
                verify=self.ca_bundle_path,
                timeout=self.timeout_seconds,
                allow_redirects=False,
                headers={"Accept": "application/json"},
            )
            payload = response.json()
        except (requests.RequestException, ValueError, OSError) as exc:
            raise ResourceTrustUnavailable("RESOURCE_STATUS_UNAVAILABLE") from exc
        if not isinstance(payload, dict):
            raise ResourceTrustUnavailable("RESOURCE_STATUS_INVALID")
        return int(response.status_code), payload

    def validate(self, assertion: str, verified: Mapping[str, Any]) -> dict[str, Any]:
        status, payload = self._request(assertion)
        if status != 200 or payload.get("active") is not True:
            code = str(payload.get("errorCode") or "RESOURCE_GRANT_INACTIVE")
            mapped = 403 if status == 403 else 409 if status == 409 else 503 if status >= 500 else 401
            raise ResourceAuthorizationError(code, mapped)
        expected = {
            "contract": verified["contract"], "wire_contract": verified["wireContract"],
            "issuer": verified["issuer"], "subject": verified["subject"],
            "sid": verified["sessionId"], "jti": verified["assertionId"],
            "grant_id": verified["grantId"], "audience": verified["audience"],
            "client_id": verified["clientId"], "application_id": verified["applicationId"],
            "owner_id": verified["ownerUserId"], "capabilities": list(verified["capabilities"]),
            "admission_version": verified["admissionVersion"], "expires_at": verified["expiresAt"],
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ResourceAuthorizationError("RESOURCE_STATUS_BINDING_MISMATCH", 401)
        if int(payload.get("expires_at") or 0) <= int(time.time()):
            raise ResourceAuthorizationError("RESOURCE_GRANT_INACTIVE", 401)
        return payload

    def probe(self) -> bool:
        status, payload = self._request("readiness-probe-not-an-assertion")
        return status == 401 and payload.get("errorCode") == "RESOURCE_ASSERTION_INVALID"


def trust_readiness(repository: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": AUTH_CONTRACT,
        "wireContract": WIRE_CONTRACT,
        "ready": False,
        "issuer": None,
        "audience": PRODUCTION_AUDIENCE,
        "signingKeys": {"ready": False, "acceptedKeyCount": 0, "keyIds": []},
        "authStatus": {"configured": False, "reachable": False, "workloadAuthenticated": False},
        "admission": {
            "clientId": "grid-windows", "applicationId": "grid", "relyingPartyId": "grid",
            "capabilities": [WORKSPACE_RESOLVE_CAPABILITY], "consistent": False, "enabled": False,
        },
        "migration": {"id": REQUIRED_MIGRATION, "applied": False},
    }
    try:
        issuer = configured_issuer()
        configured_audience()
        ring = resolve_public_key_ring()
        result["issuer"] = issuer
        result["signingKeys"] = {
            "ready": True, "acceptedKeyCount": len(ring), "keyIds": sorted(ring),
        }
        database = repository.readiness()
        result["admission"].update({
            "consistent": bool(database.get("admissionConsistent")),
            "enabled": bool(database.get("admissionEnabled")),
        })
        result["migration"]["applied"] = bool(database.get("migrationApplied"))
        client = ResourceStatusClient.from_environment()
        result["authStatus"]["configured"] = True
        reachable = client.probe()
        result["authStatus"].update({"reachable": reachable, "workloadAuthenticated": reachable})
        result["ready"] = bool(
            reachable and result["signingKeys"]["ready"]
            and result["admission"]["consistent"] and result["admission"]["enabled"]
            and result["migration"]["applied"]
        )
    except ResourceAuthorizationError as exc:
        result["errorCode"] = exc.code
    except Exception:
        result["errorCode"] = "RESOURCE_TRUST_UNAVAILABLE"
    return result
