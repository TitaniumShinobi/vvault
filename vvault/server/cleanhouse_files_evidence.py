"""VVAULT-native transport helpers for CleanHouse Files evidence.

This module deliberately uses the existing VVAULT runtime and OVVAULTS
authority. It does not require a Wazuh indexer, dashboard, container, or a
second database. The Wazuh manager's local JSON alert stream is exposed only
through an authenticated VVAULT route, and durable evidence is stored by the
existing ``ovvaults.vault_files`` repository.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


BATCH_SCHEMA = "cleanhouse.files_evidence.batch.v1"
FEED_SCHEMA = "vvault.cleanhouse.wazuh_feed.v1"
INVENTORY_SCHEMA = "vvault.cleanhouse.wazuh_inventory.v1"
ENROLLMENT_SCHEMA = "vvault.cleanhouse.wazuh_enrollment.v1"
DEFAULT_ALERTS_PATH = Path("/var/ossec/logs/alerts/alerts.json")
MAX_BATCH_EVENTS = 200
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_FEED_EVENTS = 500
MAX_FEED_BYTES = 4 * 1024 * 1024
PAIRING_SCHEMA = "vvault.cleanhouse.files_pairing.v1"
PAIRING_TOKEN_PREFIX = "chf_v1_"
MIN_PAIRING_RSA_BITS = 3072
_WAZUH_TOKEN_LOCK = threading.RLock()
_WAZUH_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}


class CleanHouseEvidenceError(ValueError):
    """A bounded, caller-safe CleanHouse evidence contract failure."""


class WazuhEvidenceUnavailable(RuntimeError):
    """The local Wazuh manager evidence source is not currently usable."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_instance_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or len(candidate) > 80:
        raise CleanHouseEvidenceError("CleanHouse instance is required")
    if not all(character.isalnum() or character in {"-", "_"} for character in candidate):
        raise CleanHouseEvidenceError("CleanHouse instance is invalid")
    return candidate


def validate_agent_name(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 128:
        raise CleanHouseEvidenceError("Wazuh agent name is required")
    if not all(character.isalnum() or character in {"-", "_", "."} for character in candidate):
        raise CleanHouseEvidenceError("Wazuh agent name is invalid")
    return candidate


def validate_monitored_scope(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 4096 or not candidate.startswith("/"):
        raise CleanHouseEvidenceError("Wazuh monitored scope must be an absolute path")
    normalized = os.path.normpath(candidate)
    if normalized == "/" or normalized.startswith("/Users/") is False:
        raise CleanHouseEvidenceError("Wazuh monitored scope must be a bounded user path")
    return normalized


def _manager_api_origin() -> str:
    origin = (os.environ.get("VVAULT_WAZUH_MANAGER_API_URL") or "https://127.0.0.1:55000").rstrip("/")
    parsed = urllib.parse.urlparse(origin)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise WazuhEvidenceUnavailable("Wazuh manager API must remain loopback-only TLS")
    return origin


def _manager_ssl_context() -> ssl.SSLContext:
    ca_cert = str(os.environ.get("VVAULT_WAZUH_MANAGER_CA_CERT") or "").strip()
    context = ssl.create_default_context(cafile=ca_cert or None)
    # The API is hard-pinned to loopback above. Wazuh's package certificate is
    # verified through its local CA but is not guaranteed to carry a loopback
    # IP subjectAltName, so hostname matching would reject the authenticated
    # local endpoint despite a valid pinned chain.
    context.check_hostname = False
    return context


def _transport_json(
    request: urllib.request.Request,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None,
) -> dict[str, Any]:
    if transport is not None:
        payload = transport(request)
    else:
        with urllib.request.urlopen(request, timeout=10, context=_manager_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise WazuhEvidenceUnavailable("Wazuh manager API returned an invalid payload")
    return payload


def _mint_manager_token(
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> str:
    username = str(os.environ.get("VVAULT_WAZUH_MANAGER_USERNAME") or "").strip()
    password = str(os.environ.get("VVAULT_WAZUH_MANAGER_PASSWORD") or "").strip()
    if not username or not password:
        raise WazuhEvidenceUnavailable("Wazuh manager API credentials are unavailable")
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        f"{_manager_api_origin()}/security/user/authenticate",
        headers={"Accept": "application/json", "Authorization": f"Basic {encoded}"},
        method="POST",
    )
    payload = _transport_json(request, transport)
    token = str(payload.get("data", {}).get("token") if isinstance(payload.get("data"), dict) else payload.get("token") or "").strip()
    if not token and isinstance(payload.get("data"), str):
        token = str(payload["data"]).strip()
    if not token and len(payload) == 1:
        token = str(next(iter(payload.values())) or "").strip()
    if not token:
        raise WazuhEvidenceUnavailable("Wazuh manager API authentication failed")
    with _WAZUH_TOKEN_LOCK:
        # Wazuh defaults to 900 seconds. Refresh a minute early.
        _WAZUH_TOKEN_CACHE.update({"token": token, "expires_at": time.monotonic() + 840})
    return token


def _manager_token(
    *,
    force_refresh: bool = False,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> str:
    with _WAZUH_TOKEN_LOCK:
        if (
            not force_refresh
            and _WAZUH_TOKEN_CACHE.get("token")
            and float(_WAZUH_TOKEN_CACHE.get("expires_at") or 0) > time.monotonic()
        ):
            return str(_WAZUH_TOKEN_CACHE["token"])
    return _mint_manager_token(transport)


def manager_api_request(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not path.startswith("/"):
        raise ValueError("Wazuh API path must be absolute")
    for attempt in range(2):
        token = _manager_token(force_refresh=attempt == 1, transport=transport)
        request = urllib.request.Request(
            f"{_manager_api_origin()}{path}",
            data=(canonical_json(body).encode("utf-8") if body is not None else None),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method=method,
        )
        try:
            return _transport_json(request, transport)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                with _WAZUH_TOKEN_LOCK:
                    _WAZUH_TOKEN_CACHE.update({"token": "", "expires_at": 0.0})
                continue
            raise WazuhEvidenceUnavailable("Wazuh manager API request failed") from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise WazuhEvidenceUnavailable("Wazuh manager API is unavailable") from exc
    raise WazuhEvidenceUnavailable("Wazuh manager API authentication failed")


def pairing_token_hash(token: str) -> str:
    candidate = str(token or "").strip()
    if not candidate.startswith(PAIRING_TOKEN_PREFIX) or len(candidate) < 48 or len(candidate) > 256:
        raise CleanHouseEvidenceError("CleanHouse pairing credential is invalid")
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()


def encrypt_pairing_credential(token: str, public_key_pem: Any) -> dict[str, Any]:
    pairing_token_hash(token)
    candidate = str(public_key_pem or "").strip()
    if len(candidate) < 256 or len(candidate) > 8192:
        raise CleanHouseEvidenceError("CleanHouse pairing public key is invalid")
    try:
        public_key = serialization.load_pem_public_key(candidate.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CleanHouseEvidenceError("CleanHouse pairing public key is invalid") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < MIN_PAIRING_RSA_BITS:
        raise CleanHouseEvidenceError("CleanHouse pairing requires an RSA-3072 or stronger public key")
    ciphertext = public_key.encrypt(
        token.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "schema": PAIRING_SCHEMA,
        "algorithm": "RSA-OAEP-3072-SHA256",
        "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def validate_batch(
    payload: Any,
    *,
    raw_body: bytes,
    expected_batch_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    if len(raw_body) > MAX_BATCH_BYTES:
        raise CleanHouseEvidenceError("CleanHouse evidence batch is too large")
    if not isinstance(payload, dict) or payload.get("schema") != BATCH_SCHEMA:
        raise CleanHouseEvidenceError("Unsupported CleanHouse evidence schema")
    events = payload.get("events")
    if not isinstance(events, list) or not events or len(events) > MAX_BATCH_EVENTS:
        raise CleanHouseEvidenceError("CleanHouse evidence batch size is invalid")
    batch_id = hashlib.sha256(raw_body).hexdigest()
    if not expected_batch_id or expected_batch_id != batch_id:
        raise CleanHouseEvidenceError("CleanHouse evidence batch digest mismatch")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in events:
        if not isinstance(item, dict):
            raise CleanHouseEvidenceError("CleanHouse evidence event must be an object")
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id or len(evidence_id) > 512:
            raise CleanHouseEvidenceError("CleanHouse evidence_id is invalid")
        if evidence_id in seen:
            raise CleanHouseEvidenceError("CleanHouse evidence batch contains duplicate evidence IDs")
        event_payload = item.get("payload")
        if not isinstance(event_payload, dict):
            raise CleanHouseEvidenceError("CleanHouse evidence payload must be an object")
        canonical = canonical_json(item)
        if len(canonical.encode("utf-8")) > MAX_EVENT_BYTES:
            raise CleanHouseEvidenceError("CleanHouse evidence event is too large")
        seen.add(evidence_id)
        normalized.append(
            {
                "evidence_id": evidence_id,
                "created_at": str(item.get("created_at") or ""),
                "payload": event_payload,
                "content": canonical,
                "sha256": sha256_text(canonical),
            }
        )
    return batch_id, normalized


def _cursor(*, stat_result: os.stat_result, offset: int) -> str:
    return f"wazuh-jsonl.v1:{stat_result.st_dev}:{stat_result.st_ino}:{max(0, offset)}"


def _cursor_state(value: str, stat_result: os.stat_result) -> tuple[int, str]:
    if not value:
        return 0, "initial"
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "wazuh-jsonl.v1":
        return 0, "invalid_cursor"
    try:
        device, inode, offset = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return 0, "invalid_cursor"
    if device != stat_result.st_dev or inode != stat_result.st_ino:
        return 0, "rotation_or_replacement"
    if offset > stat_result.st_size:
        return 0, "truncated"
    return max(0, offset), "none_observed"


def _path_within_scope(path: str, scope: str) -> bool:
    normalized = os.path.normpath(str(path or ""))
    boundary = os.path.normpath(str(scope or ""))
    return bool(normalized and boundary) and (
        normalized == boundary or normalized.startswith(boundary.rstrip("/") + "/")
    )


def read_wazuh_alerts(
    *,
    after: str = "",
    limit: int = 100,
    alerts_path: Path | None = None,
    agent_id: str = "",
    monitored_scope: str = "",
    manager_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = alerts_path or Path(os.environ.get("VVAULT_WAZUH_ALERTS_PATH") or DEFAULT_ALERTS_PATH)
    try:
        stat_result = path.stat()
    except OSError as exc:
        raise WazuhEvidenceUnavailable("Wazuh manager alert stream is unavailable") from exc
    bounded_limit = max(1, min(int(limit), MAX_FEED_EVENTS))
    expected_agent_id = str(agent_id or os.environ.get("VVAULT_WAZUH_AGENT_ID") or "").strip()
    scope = validate_monitored_scope(
        monitored_scope or os.environ.get("VVAULT_WAZUH_MONITORED_SCOPE") or ""
    )
    attestation = manager_attestation if isinstance(manager_attestation, dict) else {}
    authenticated = bool(
        attestation.get("manager_active")
        and attestation.get("api_authenticated")
        and str(attestation.get("agent_id") or "") == expected_agent_id
    )
    if not expected_agent_id or not authenticated:
        raise WazuhEvidenceUnavailable("Wazuh manager evidence is not authenticated for this agent")
    start, gap_state = _cursor_state(after, stat_result)
    items: list[dict[str, Any]] = []
    consumed = 0
    offset = start
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            while len(items) < bounded_limit and consumed < MAX_FEED_BYTES:
                line_start = handle.tell()
                line = handle.readline(MAX_EVENT_BYTES + 1)
                if not line:
                    offset = handle.tell()
                    break
                if len(line) > MAX_EVENT_BYTES or not line.endswith(b"\n"):
                    offset = line_start
                    break
                consumed += len(line)
                offset = handle.tell()
                try:
                    source = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(source, dict):
                    continue
                data = source.get("data") if isinstance(source.get("data"), dict) else {}
                syscheck = data.get("syscheck") if isinstance(data.get("syscheck"), dict) else {}
                path_value = str(syscheck.get("path") or syscheck.get("file") or "").strip()
                source_agent = source.get("agent") if isinstance(source.get("agent"), dict) else {}
                if (
                    not path_value
                    or str(source_agent.get("id") or "").strip() != expected_agent_id
                    or not _path_within_scope(path_value, scope)
                ):
                    continue
                raw_hash = sha256_text(canonical_json(source))
                event_cursor = _cursor(stat_result=stat_result, offset=offset)
                items.append(
                    {
                        "_index": "wazuh-manager-alerts",
                        "_id": str(source.get("id") or raw_hash),
                        "_source": source,
                        "_vvault_cursor": event_cursor,
                        "_vvault_raw_sha256": raw_hash,
                    }
                )
    except OSError as exc:
        raise WazuhEvidenceUnavailable("Wazuh manager alert stream could not be read") from exc
    return {
        "schema": FEED_SCHEMA,
        "provider": "wazuh_manager",
        "evidence_authenticated": True,
        "agent_id": expected_agent_id,
        "monitored_scope": scope,
        "manager": str(attestation.get("manager") or ""),
        "manager_version": str(attestation.get("manager_version") or ""),
        "stream_identity": f"{stat_result.st_dev}:{stat_result.st_ino}",
        "stream_generation": f"{stat_result.st_ino}:{stat_result.st_ctime_ns}",
        "gap_state": gap_state,
        "cursor": _cursor(stat_result=stat_result, offset=offset),
        "items": items,
    }


def _affected_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    items = data.get("affected_items") if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def manager_attestation(
    *,
    agent_id: str | None = None,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected_agent_id = str(agent_id or os.environ.get("VVAULT_WAZUH_AGENT_ID") or "").strip()
    if not expected_agent_id:
        raise WazuhEvidenceUnavailable("Wazuh enrolled agent ID is unavailable")
    status_payload = manager_api_request("/manager/status", transport=transport)
    status_items = _affected_items(status_payload)
    status_map = status_items[0] if status_items else (
        status_payload.get("data") if isinstance(status_payload.get("data"), dict) else {}
    )
    manager_active = any(
        str(value).lower() in {"active", "running"}
        for value in status_map.values()
    ) if isinstance(status_map, dict) else False
    agent_payload = manager_api_request(
        f"/agents?agents_list={urllib.parse.quote(expected_agent_id, safe='')}",
        transport=transport,
    )
    agents = _affected_items(agent_payload)
    matching = next((item for item in agents if str(item.get("id") or "") == expected_agent_id), None)
    if not manager_active or matching is None:
        raise WazuhEvidenceUnavailable("Wazuh manager or enrolled agent is unavailable")
    info_payload = manager_api_request("/manager/info", transport=transport)
    info_items = _affected_items(info_payload)
    info = info_items[0] if info_items else {}
    return {
        "manager_active": True,
        "api_authenticated": True,
        "agent_id": expected_agent_id,
        "agent_name": str(matching.get("name") or ""),
        "agent_status": str(matching.get("status") or ""),
        "manager": str(info.get("name") or "wazuh-manager"),
        "manager_version": str(info.get("version") or ""),
    }


def create_or_reuse_agent(
    *,
    agent_name: str,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> dict[str, str]:
    name = validate_agent_name(agent_name)
    listing = manager_api_request(
        f"/agents?search={urllib.parse.quote(name, safe='')}&limit=500",
        transport=transport,
    )
    exact = [item for item in _affected_items(listing) if str(item.get("name") or "") == name]
    if len(exact) > 1:
        raise WazuhEvidenceUnavailable("Multiple Wazuh agents share the requested CleanHouse name")
    if exact:
        agent_id = str(exact[0].get("id") or "").strip()
    else:
        created = manager_api_request(
            "/agents",
            method="POST",
            body={"name": name},
            transport=transport,
        )
        created_items = _affected_items(created)
        created_item = created_items[0] if created_items else (
            created.get("data") if isinstance(created.get("data"), dict) else {}
        )
        agent_id = str(created_item.get("id") or "").strip()
    if not agent_id:
        raise WazuhEvidenceUnavailable("Wazuh manager did not return an agent ID")
    key_payload = manager_api_request(
        f"/agents/{urllib.parse.quote(agent_id, safe='')}/key",
        transport=transport,
    )
    key_items = _affected_items(key_payload)
    key_item = key_items[0] if key_items else (
        key_payload.get("data") if isinstance(key_payload.get("data"), dict) else {}
    )
    key = str(key_item.get("key") or "").strip()
    if not key:
        raise WazuhEvidenceUnavailable("Wazuh manager did not return the agent client key")
    return {"agent_id": agent_id, "agent_name": name, "client_key": key}


def query_wazuh_inventory(
    *,
    offset: int,
    limit: int,
    agent_id: str = "",
    monitored_scope: str = "",
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    agent_id = str(agent_id or os.environ.get("VVAULT_WAZUH_AGENT_ID") or "").strip()
    scope = validate_monitored_scope(
        monitored_scope or os.environ.get("VVAULT_WAZUH_MONITORED_SCOPE") or ""
    )
    attestation = manager_attestation(agent_id=agent_id, transport=transport)
    query = urllib.parse.urlencode({"offset": max(0, int(offset)), "limit": max(1, min(int(limit), 500))})
    payload = manager_api_request(
        f"/syscheck/{urllib.parse.quote(agent_id, safe='')}?{query}",
        transport=transport,
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    affected = data.get("affected_items") if isinstance(data, dict) else []
    if isinstance(affected, list):
        data = dict(data)
        data["affected_items"] = [
            {**item, "agent_id": agent_id}
            for item in affected
            if isinstance(item, dict) and _path_within_scope(str(item.get("file") or item.get("path") or ""), scope)
        ]
        data["total_affected_items"] = len(data["affected_items"])
    return {
        "schema": INVENTORY_SCHEMA,
        "provider": "wazuh_manager",
        "evidence_authenticated": True,
        "agent_id": agent_id,
        "monitored_scope": scope,
        "manager": attestation["manager"],
        "manager_version": attestation["manager_version"],
        "data": data,
    }
