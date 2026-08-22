"""VVAULT-native transport helpers for CleanHouse Files evidence.

This module deliberately uses the existing VVAULT runtime and OVVAULTS
authority. It does not require a Wazuh indexer, dashboard, container, or a
second database. The Wazuh manager's local JSON alert stream is exposed only
through an authenticated VVAULT route, and durable evidence is stored by the
existing ``ovvaults.vault_files`` repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


BATCH_SCHEMA = "cleanhouse.files_evidence.batch.v1"
FEED_SCHEMA = "vvault.cleanhouse.wazuh_feed.v1"
INVENTORY_SCHEMA = "vvault.cleanhouse.wazuh_inventory.v1"
DEFAULT_ALERTS_PATH = Path("/var/ossec/logs/alerts/alerts.json")
MAX_BATCH_EVENTS = 200
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_FEED_EVENTS = 500
MAX_FEED_BYTES = 4 * 1024 * 1024


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


def _cursor_offset(value: str, stat_result: os.stat_result) -> int:
    if not value:
        return 0
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "wazuh-jsonl.v1":
        return 0
    try:
        device, inode, offset = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return 0
    if device != stat_result.st_dev or inode != stat_result.st_ino:
        return 0
    return max(0, min(offset, stat_result.st_size))


def read_wazuh_alerts(
    *,
    after: str = "",
    limit: int = 100,
    alerts_path: Path | None = None,
) -> dict[str, Any]:
    path = alerts_path or Path(os.environ.get("VVAULT_WAZUH_ALERTS_PATH") or DEFAULT_ALERTS_PATH)
    try:
        stat_result = path.stat()
    except OSError as exc:
        raise WazuhEvidenceUnavailable("Wazuh manager alert stream is unavailable") from exc
    bounded_limit = max(1, min(int(limit), MAX_FEED_EVENTS))
    start = _cursor_offset(after, stat_result)
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
                if not str(syscheck.get("path") or syscheck.get("file") or "").strip():
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
        "cursor": _cursor(stat_result=stat_result, offset=offset),
        "items": items,
    }


def query_wazuh_inventory(
    *,
    offset: int,
    limit: int,
    transport: Callable[[urllib.request.Request], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base_url = (os.environ.get("VVAULT_WAZUH_MANAGER_API_URL") or "https://127.0.0.1:55000").rstrip("/")
    agent_id = str(os.environ.get("VVAULT_WAZUH_AGENT_ID") or "").strip()
    token = str(os.environ.get("VVAULT_WAZUH_MANAGER_TOKEN") or "").strip()
    if not agent_id or not token:
        raise WazuhEvidenceUnavailable("Wazuh manager inventory credentials are unavailable")
    query = urllib.parse.urlencode({"offset": max(0, int(offset)), "limit": max(1, min(int(limit), 500))})
    request = urllib.request.Request(
        f"{base_url}/syscheck/{urllib.parse.quote(agent_id, safe='')}?{query}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        if transport is not None:
            payload = transport(request)
        else:
            ca_cert = str(os.environ.get("VVAULT_WAZUH_MANAGER_CA_CERT") or "").strip()
            context = ssl.create_default_context(cafile=ca_cert or None)
            with urllib.request.urlopen(request, timeout=10, context=context) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise WazuhEvidenceUnavailable("Wazuh manager inventory is unavailable") from exc
    if not isinstance(payload, dict):
        raise WazuhEvidenceUnavailable("Wazuh manager inventory returned an invalid payload")
    return {
        "schema": INVENTORY_SCHEMA,
        "provider": "wazuh_manager",
        "evidence_authenticated": True,
        "agent_id": agent_id,
        "data": payload.get("data") if isinstance(payload.get("data"), dict) else payload,
    }
