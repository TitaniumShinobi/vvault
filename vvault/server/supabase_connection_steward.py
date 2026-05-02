"""
Metadata-only Supabase connection steward for VVAULT.

The steward owns the runtime contract that Supabase is canonical only after a
fresh project-specific proof succeeds. It intentionally records metadata only:
no row contents, secrets, prompts, transcripts, or database payloads.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4


CONNECTED = "connected"
WARMING = "warming"
RECONNECTING = "reconnecting"
DEGRADED = "degraded"
BLOCKED = "blocked"
BOOTING = "booting"
IDENTITY_AUTHORITY_UNAVAILABLE = "IDENTITY_AUTHORITY_UNAVAILABLE"
IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
LIFE_ID_NOT_FOUND = "LIFE_ID_NOT_FOUND"
STALE_REPLAY_REJECTED = "STALE_REPLAY_REJECTED"
SCHEMA_DOWNGRADE_REJECTED = "SCHEMA_DOWNGRADE_REJECTED"
WRITE_CONFLICT = "WRITE_CONFLICT"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: Any) -> float:
    raw = _clean(value)
    if not raw:
        return 0.0
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _schema_version(record: Dict[str, Any]) -> int:
    try:
        return int(record.get("schema_version") or 0)
    except (TypeError, ValueError):
        return 0


class SupabaseConnectionSteward:
    """Continuously proves and exposes VVAULT's canonical Supabase link."""

    def __init__(
        self,
        *,
        get_client: Callable[[], Any],
        refresh_client: Optional[Callable[[], Any]] = None,
        is_configured: Callable[[], bool] = lambda: False,
        using_service_role: Callable[[], bool] = lambda: False,
        logger: Optional[logging.Logger] = None,
        heartbeat_interval_seconds: float = 15.0,
        probe_timeout_seconds: float = 8.0,
        recovery_successes_required: int = 2,
    ) -> None:
        self._get_client = get_client
        self._refresh_client = refresh_client
        self._is_configured = is_configured
        self._using_service_role = using_service_role
        self._logger = logger or logging.getLogger(__name__)
        self._heartbeat_interval_seconds = max(1.0, heartbeat_interval_seconds)
        self._probe_timeout_seconds = max(0.2, probe_timeout_seconds)
        self._recovery_successes_required = max(1, recovery_successes_required)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vvault-supabase-proof")
        self._state: Dict[str, Any] = {
            "connection_state": BOOTING,
            "configured": False,
            "client_created": False,
            "using_service_role": False,
            "last_success_at": None,
            "last_probe_at": None,
            "consecutive_successes": 0,
            "consecutive_failures": 0,
            "latency_ms": None,
            "recovery_proven_at": None,
            "last_error_code": None,
            "last_error_class": None,
            "outage_id": None,
            "canonical": False,
            "storage_mode": "none",
            "pending_outbox_count": 0,
            "reconciliation_status": "idle",
            "last_reconciliation_receipt": None,
            "blocked_replay_count": 0,
        }

    def bootstrap(self) -> None:
        """Run immediate startup proof and start the background heartbeat."""
        self.probe_once(reason="startup")
        if not self.is_connected():
            self.probe_once(reason="startup-recovery")
        self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="vvault-supabase-connection-steward",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def is_connected(self) -> bool:
        return self.snapshot().get("connection_state") == CONNECTED

    def allow_read(self) -> Tuple[bool, Dict[str, Any]]:
        state = self.snapshot()
        return state.get("connection_state") in {CONNECTED, WARMING}, state

    def allow_write(self) -> Tuple[bool, Dict[str, Any]]:
        state = self.snapshot()
        return state.get("connection_state") == CONNECTED, state

    def update_outbox_visibility(
        self,
        *,
        pending_outbox_count: int,
        last_reconciliation_receipt: Optional[Dict[str, Any]] = None,
        blocked_replay_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._state["pending_outbox_count"] = max(0, int(pending_outbox_count or 0))
            self._state["reconciliation_status"] = "blocked" if blocked_replay_count else "idle"
            self._state["last_reconciliation_receipt"] = last_reconciliation_receipt
            if blocked_replay_count is not None:
                self._state["blocked_replay_count"] = max(0, int(blocked_replay_count or 0))
            return dict(self._state)

    def resolve_life_identity(
        self,
        *,
        email: str,
        cached_life_id: Optional[str] = None,
        registry_life_id: Optional[str] = None,
        supabase_life_id: Optional[str] = None,
        proposed_life_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve an immutable LIFE id without ever minting a replacement.

        The steward does not create identities. It only chooses a single trusted
        existing anchor or returns a fail-closed receipt that callers can surface.
        """

        sources: List[Tuple[str, str]] = []
        for source, value in (
            ("cache", cached_life_id),
            ("registry", registry_life_id),
            ("supabase", supabase_life_id),
        ):
            cleaned = _clean(value)
            if cleaned:
                sources.append((source, cleaned))

        grouped: Dict[str, List[str]] = {}
        for source, life_id in sources:
            grouped.setdefault(life_id, []).append(source)

        receipt: Dict[str, Any] = {
            "receipt_id": str(uuid4()),
            "email": _clean(email).lower(),
            "canonical": False,
            "should_mint": False,
            "proposed_life_id": _clean(proposed_life_id) or None,
            "trusted_sources": [{"source": source, "life_user_id": life_id} for source, life_id in sources],
        }

        if len(grouped) > 1:
            receipt.update(
                {
                    "ok": False,
                    "error_code": IDENTITY_CONFLICT,
                    "message": "Multiple trusted LIFE ids exist for this account. Operator reconciliation is required.",
                    "conflict_life_ids": sorted(grouped.keys()),
                }
            )
            return receipt

        if len(grouped) == 1:
            life_id = next(iter(grouped.keys()))
            aliases = []
            proposed = _clean(proposed_life_id)
            if proposed and proposed != life_id:
                aliases.append(proposed)
            receipt.update(
                {
                    "ok": True,
                    "canonical": True,
                    "life_user_id": life_id,
                    "aliases": aliases,
                    "message": "Existing LIFE id reused; replacement minting is forbidden.",
                }
            )
            return receipt

        state = self.snapshot()
        receipt.update(
            {
                "ok": False,
                "error_code": (
                    IDENTITY_AUTHORITY_UNAVAILABLE
                    if state.get("connection_state") != CONNECTED
                    else LIFE_ID_NOT_FOUND
                ),
                "connection_state": state.get("connection_state"),
                "message": "No trusted LIFE id anchor is available. Automatic LIFE id minting is blocked.",
            }
        )
        return receipt

    def plan_replay(
        self,
        *,
        remote_record: Dict[str, Any],
        queued_record: Dict[str, Any],
        mutable_fields: Iterable[str],
        idempotency_key_field: str = "idempotency_key",
        identity_fields: Iterable[str] = ("id", "user_id", "life_user_id"),
    ) -> Dict[str, Any]:
        """Plan a field-scoped retry without overwriting newer product data."""

        remote = dict(remote_record or {})
        queued = dict(queued_record or {})
        mutable = [field for field in mutable_fields if field]
        receipt: Dict[str, Any] = {
            "receipt_id": str(uuid4()),
            "remote_id": remote.get("id"),
            "queued_id": queued.get("id"),
            "applied_fields": [],
            "preserved_remote_fields": sorted(set(remote.keys()) - set(mutable)),
            "canonical": False,
        }

        for field in identity_fields:
            remote_value = _clean(remote.get(field))
            queued_value = _clean(queued.get(field))
            if remote_value and queued_value and remote_value != queued_value:
                receipt.update(
                    {
                        "ok": False,
                        "action": "blocked",
                        "error_code": IDENTITY_CONFLICT,
                        "message": f"Replay would change immutable identity field {field}.",
                    }
                )
                return receipt

        remote_schema = _schema_version(remote)
        queued_schema = _schema_version(queued)
        if remote_schema > queued_schema:
            receipt.update(
                {
                    "ok": False,
                    "action": "blocked",
                    "error_code": SCHEMA_DOWNGRADE_REJECTED,
                    "message": "Queued write uses an older schema than the remote record.",
                }
            )
            return receipt

        remote_updated = _parse_timestamp(remote.get("updated_at") or remote.get("created_at"))
        queued_updated = _parse_timestamp(
            queued.get("accepted_at") or queued.get("updated_at") or queued.get("created_at")
        )
        remote_key = _clean(remote.get(idempotency_key_field))
        queued_key = _clean(queued.get(idempotency_key_field))
        same_operation = bool(remote_key and queued_key and remote_key == queued_key)

        if remote_updated > queued_updated and not same_operation:
            receipt.update(
                {
                    "ok": False,
                    "action": "blocked",
                    "error_code": STALE_REPLAY_REJECTED,
                    "message": "Remote record is newer than the queued write; replay is not safe.",
                }
            )
            return receipt

        if not same_operation:
            receipt.update(
                {
                    "ok": False,
                    "action": "blocked",
                    "error_code": WRITE_CONFLICT,
                    "message": "Queued write does not match the remote idempotency key.",
                }
            )
            return receipt

        patch = {
            field: queued[field]
            for field in mutable
            if field in queued and queued.get(field) != remote.get(field)
        }
        merged = dict(remote)
        merged.update(patch)
        receipt.update(
            {
                "ok": True,
                "canonical": True,
                "action": "apply_field_patch" if patch else "noop",
                "patch": patch,
                "merged_record": merged,
                "applied_fields": sorted(patch.keys()),
                "message": "Replay is field-scoped and preserves remote-only product data.",
            }
        )
        return receipt

    def probe_once(self, *, reason: str = "heartbeat") -> Dict[str, Any]:
        configured = bool(self._is_configured())
        client = self._get_client() if configured else None
        started_at = time.perf_counter()

        if not configured or client is None:
            return self._record_failure(
                error_code="SUPABASE_NOT_CONFIGURED",
                error_class="not_configured" if not configured else "client_missing",
                latency_ms=0,
                reason=reason,
                blocked=True,
            )

        future = self._executor.submit(self._metadata_probe, client)
        try:
            future.result(timeout=self._probe_timeout_seconds)
        except FutureTimeout:
            return self._record_failure(
                error_code="SUPABASE_PROBE_TIMEOUT",
                error_class="timeout",
                latency_ms=self._elapsed_ms(started_at),
                reason=reason,
            )
        except Exception as exc:
            refreshed = self._try_refresh_client()
            return self._record_failure(
                error_code=self._classify_error(exc),
                error_class=type(exc).__name__,
                latency_ms=self._elapsed_ms(started_at),
                reason=reason,
                refreshed_client=refreshed,
            )

        return self._record_success(self._elapsed_ms(started_at), reason=reason)

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self._heartbeat_interval_seconds):
            self.probe_once(reason="heartbeat")

    def _metadata_probe(self, client: Any) -> None:
        client.table("users").select("id").limit(1).execute()
        client.table("vault_files").select("id").limit(1).execute()

    def _try_refresh_client(self) -> bool:
        if not self._refresh_client:
            return False
        try:
            self._refresh_client()
            return True
        except Exception as exc:
            self._logger.warning(
                "SUPABASE_CONNECTION_STEWARD refresh_failed error_class=%s ts=%s",
                type(exc).__name__,
                self._utc_now(),
            )
            return False

    def _record_success(self, latency_ms: int, *, reason: str) -> Dict[str, Any]:
        with self._lock:
            previous_state = self._state["connection_state"]
            successes = int(self._state.get("consecutive_successes") or 0) + 1
            connected = successes >= self._recovery_successes_required
            state = CONNECTED if connected else WARMING
            recovery_proven_at = self._state.get("recovery_proven_at")
            if connected and previous_state != CONNECTED:
                recovery_proven_at = self._utc_now()
            self._state.update(
                {
                    "connection_state": state,
                    "configured": bool(self._is_configured()),
                    "client_created": bool(self._get_client()),
                    "using_service_role": bool(self._using_service_role()),
                    "last_success_at": self._utc_now(),
                    "last_probe_at": self._utc_now(),
                    "consecutive_successes": successes,
                    "consecutive_failures": 0,
                    "latency_ms": latency_ms,
                    "recovery_proven_at": recovery_proven_at,
                    "last_error_code": None,
                    "last_error_class": None,
                    "outage_id": None,
                    "canonical": connected,
                    "storage_mode": "supabase" if connected else "none",
                }
            )
            snapshot = dict(self._state)
        self._logger.info(
            "SUPABASE_CONNECTION_STEWARD route=heartbeat operation=proof dependency=supabase "
            "connection_state=%s error_code=NONE status=200 latency_ms=%s retry_count=0 "
            "recovery_proven=%s storage_mode=%s canonical=%s outage_id=%s reason=%s ts=%s",
            snapshot["connection_state"],
            latency_ms,
            bool(snapshot.get("recovery_proven_at")),
            snapshot["storage_mode"],
            snapshot["canonical"],
            snapshot.get("outage_id"),
            reason,
            self._utc_now(),
        )
        return snapshot

    def _record_failure(
        self,
        *,
        error_code: str,
        error_class: str,
        latency_ms: int,
        reason: str,
        blocked: bool = False,
        refreshed_client: bool = False,
    ) -> Dict[str, Any]:
        with self._lock:
            failures = int(self._state.get("consecutive_failures") or 0) + 1
            outage_id = self._state.get("outage_id") or str(uuid4())
            if blocked:
                state = BLOCKED
            elif failures >= 3:
                state = DEGRADED
            else:
                state = RECONNECTING
            self._state.update(
                {
                    "connection_state": state,
                    "configured": bool(self._is_configured()),
                    "client_created": bool(self._get_client()),
                    "using_service_role": bool(self._using_service_role()),
                    "last_probe_at": self._utc_now(),
                    "consecutive_successes": 0,
                    "consecutive_failures": failures,
                    "latency_ms": latency_ms,
                    "last_error_code": error_code,
                    "last_error_class": error_class,
                    "outage_id": outage_id,
                    "canonical": False,
                    "storage_mode": "none",
                }
            )
            snapshot = dict(self._state)
        self._logger.warning(
            "SUPABASE_CONNECTION_STEWARD route=heartbeat operation=proof dependency=supabase "
            "connection_state=%s error_code=%s status=503 latency_ms=%s retry_count=0 "
            "recovery_proven=%s storage_mode=none canonical=false outage_id=%s "
            "reason=%s refreshed_client=%s ts=%s",
            snapshot["connection_state"],
            error_code,
            latency_ms,
            bool(snapshot.get("recovery_proven_at")),
            outage_id,
            reason,
            refreshed_client,
            self._utc_now(),
        )
        return snapshot

    def _classify_error(self, exc: Exception) -> str:
        lowered = str(exc or "").lower()
        if "522" in lowered or "cloudflare" in lowered:
            return "SUPABASE_TIMEOUT_522"
        if "504" in lowered:
            return "SUPABASE_504"
        if "503" in lowered:
            return "SUPABASE_503"
        if "pgrst000" in lowered or "pgrst001" in lowered or "pgrst002" in lowered or "pgrst003" in lowered:
            return "SUPABASE_POSTGREST_UNAVAILABLE"
        if "timeout" in lowered or "timed out" in lowered:
            return "SUPABASE_PROBE_TIMEOUT"
        if "connection" in lowered or "network" in lowered or "dns" in lowered:
            return "SUPABASE_NETWORK"
        return "SUPABASE_PROBE_FAILED"

    def _elapsed_ms(self, started_at: float) -> int:
        return int(round((time.perf_counter() - started_at) * 1000))

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
