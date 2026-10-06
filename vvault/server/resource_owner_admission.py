"""Read-only AUTH subject to canonical VVAULT owner admission boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
import uuid
from typing import Any, Mapping

from vvault.server.resource_authorization import (
    PRODUCTION_AUDIENCE,
    REGISTERED_APPLICATIONS,
    WORKSPACE_RESOLVE_CAPABILITY,
)


CONTRACT = "life.vvault.resource-owner-admission/v1"
REQUIRED_MIGRATION = "0042_auth_resource_owner_admission"
MAX_REQUEST_SECONDS = 30
MAX_RESPONSE_SECONDS = 60
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_REQUEST_FIELDS = frozenset({
    "contract", "requestId", "issuer", "subject", "sessionId", "clientId",
    "applicationId", "audience", "capabilities", "issuedAt", "expiresAt",
})


class OwnerAdmissionError(RuntimeError):
    def __init__(self, code: str, http_status: int):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def configured_workloads() -> dict[str, frozenset[str]]:
    raw = str(os.environ.get("VVAULT_AUTH_ADMISSION_MTLS_IDENTITIES_JSON") or "").strip()
    if not raw:
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_TRUST_UNAVAILABLE", 503)
    try:
        values = json.loads(raw)
        result: dict[str, set[str]] = {}
        for item in values:
            issuer = str(item["issuer"]).strip()
            fingerprint = str(item["certificateSha256"]).lower().replace(":", "")
            if not issuer or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError
            result.setdefault(fingerprint, set()).add(issuer)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_TRUST_INVALID", 503) from exc
    if not result or any(len(issuers) != 1 for issuers in result.values()):
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_TRUST_INVALID", 503)
    return {fingerprint: frozenset(issuers) for fingerprint, issuers in result.items()}


def authenticate_direct_mtls(environ: Mapping[str, Any]) -> str:
    """Authenticate only an actual direct TLS peer, never a proxy header."""
    peer_socket = environ.get("werkzeug.socket")
    if not isinstance(peer_socket, ssl.SSLSocket):
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED", 401)
    certificate = peer_socket.getpeercert(binary_form=True)
    return authenticate_peer_certificate(certificate)


def authenticate_peer_certificate(certificate: bytes | None) -> str:
    """Map a DER TLS peer certificate to its sole configured AUTH issuer."""
    if not certificate:
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED", 401)
    issuers = configured_workloads().get(hashlib.sha256(certificate).hexdigest())
    if not issuers or len(issuers) != 1:
        raise OwnerAdmissionError("OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED", 401)
    return next(iter(issuers))


def validate_request(payload: Any, workload_issuer: str, *, now_seconds: int | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        raise OwnerAdmissionError("OWNER_ADMISSION_REQUEST_INVALID", 400)
    if payload.get("contract") != CONTRACT or payload.get("issuer") != workload_issuer:
        raise OwnerAdmissionError("OWNER_ADMISSION_BINDING_INVALID", 403)
    try:
        uuid.UUID(str(payload.get("requestId")))
    except ValueError as exc:
        raise OwnerAdmissionError("OWNER_ADMISSION_REQUEST_INVALID", 400) from exc
    for field in ("issuer", "subject", "sessionId", "clientId", "applicationId"):
        value = payload.get(field)
        if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value):
            raise OwnerAdmissionError("OWNER_ADMISSION_REQUEST_INVALID", 400)
    client = payload["clientId"]
    application = payload["applicationId"]
    permitted = REGISTERED_APPLICATIONS.get((client, application))
    capabilities = payload.get("capabilities")
    if (
        permitted is None or payload.get("audience") != PRODUCTION_AUDIENCE
        or capabilities != [WORKSPACE_RESOLVE_CAPABILITY]
        or any(value not in permitted for value in capabilities)
    ):
        raise OwnerAdmissionError("OWNER_ADMISSION_APPLICATION_NOT_ADMITTED", 403)
    issued = payload.get("issuedAt")
    expires = payload.get("expiresAt")
    now = int(time.time() if now_seconds is None else now_seconds)
    if type(issued) is not int or type(expires) is not int or issued > now or expires <= now or expires - issued > MAX_REQUEST_SECONDS:
        raise OwnerAdmissionError("OWNER_ADMISSION_REQUEST_EXPIRED", 401)
    return dict(payload)


def resolve(payload: dict[str, Any], repository: Any, *, now_seconds: int | None = None) -> dict[str, Any]:
    now = int(time.time() if now_seconds is None else now_seconds)
    try:
        decision = repository.lookup(
            issuer=payload["issuer"], subject=payload["subject"],
            client_id=payload["clientId"], application_id=payload["applicationId"],
            audience=payload["audience"], capability=WORKSPACE_RESOLVE_CAPABILITY,
        )
    except Exception as exc:
        raise OwnerAdmissionError("OWNER_ADMISSION_UNAVAILABLE", 503) from exc
    base = {
        "contract": CONTRACT, "requestId": payload["requestId"],
        "issuer": payload["issuer"], "subject": payload["subject"],
        "sessionId": payload["sessionId"], "clientId": payload["clientId"],
        "applicationId": payload["applicationId"], "audience": payload["audience"],
        "capabilities": [WORKSPACE_RESOLVE_CAPABILITY],
        "expiresAt": now + MAX_RESPONSE_SECONDS,
    }
    state = str(decision.get("state") or "ERROR")
    if state == "ACTIVE":
        return {**base, "admitted": True, "ownerId": str(decision["ownerId"]),
                "policyVersion": str(decision["policyVersion"])}
    dispositions = {
        "UNKNOWN": ("ACCOUNT_LINK_REQUIRED", "NO_OWNER_BINDING"),
        "PENDING": ("ENROLLMENT_REQUIRED", "OWNER_ENROLLMENT_PENDING"),
        "REVOKED": ("REAUTH_REQUIRED", "OWNER_BINDING_REVOKED"),
        "DISABLED": ("REAUTH_REQUIRED", "OWNER_DISABLED"),
    }
    if state == "CONFLICT":
        raise OwnerAdmissionError("OWNER_ADMISSION_CONFLICT", 409)
    if state not in dispositions:
        raise OwnerAdmissionError("OWNER_ADMISSION_UNAVAILABLE", 503)
    disposition, reason = dispositions[state]
    return {**base, "admitted": False, "disposition": disposition, "reasonCode": reason}


def trust_readiness(repository: Any) -> dict[str, Any]:
    result = {
        "contract": CONTRACT, "ready": False,
        "workloadTrust": {"configured": False, "directMtlsRequired": True, "identityCount": 0},
        "bindingSource": {"authoritative": "ovvaults.auth_resource_owner_bindings",
                          "available": False, "migrationId": REQUIRED_MIGRATION,
                          "migrationApplied": False},
    }
    try:
        workloads = configured_workloads()
        result["workloadTrust"].update({"configured": True, "identityCount": len(workloads)})
        state = repository.readiness()
        result["bindingSource"].update({
            "available": bool(state.get("bindingSourceAvailable")),
            "migrationApplied": bool(state.get("migrationApplied")),
        })
        result["ready"] = bool(
            result["workloadTrust"]["configured"]
            and result["bindingSource"]["available"]
            and result["bindingSource"]["migrationApplied"]
        )
    except OwnerAdmissionError as exc:
        result["errorCode"] = exc.code
    except Exception:
        result["errorCode"] = "OWNER_ADMISSION_UNAVAILABLE"
    return result
