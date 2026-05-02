"""
Durable local outbox for narrowly approved Supabase write retries.

This module is intentionally route-agnostic. Callers must prove a write class is
safe before enqueueing it, and replay must be planned by SupabaseConnectionSteward
before anything touches Supabase again.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4


DESTRUCTIVE_OPERATION_NOT_QUEUEABLE = "DESTRUCTIVE_OPERATION_NOT_QUEUEABLE"
OPERATION_NOT_QUEUEABLE = "OPERATION_NOT_QUEUEABLE"
OUTBOX_ITEM_NOT_FOUND = "OUTBOX_ITEM_NOT_FOUND"
REPLAY_WRITE_FAILED = "REPLAY_WRITE_FAILED"
REMOTE_RECORD_NOT_FOUND = "REMOTE_RECORD_NOT_FOUND"
UNSUPPORTED_OUTBOX_ITEM = "UNSUPPORTED_OUTBOX_ITEM"
VAULT_FILE_UPSERT = "vault_file_upsert"

_SAFE_OPERATIONS = {VAULT_FILE_UPSERT}
_DESTRUCTIVE_KINDS = {"delete", "truncate", "drop", "merge_identity", "replace_identity"}
_DEFAULT_IDENTITY_FIELDS = ("id", "user_id", "life_user_id", "supabase_user_id")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_fields(fields: Iterable[str]) -> List[str]:
    return sorted({str(field).strip() for field in fields if str(field or "").strip()})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stable_idempotency_key(
    *,
    operation: str,
    table: str,
    operation_kind: str,
    record: Dict[str, Any],
    mutable_fields: Iterable[str],
    identity_fields: Iterable[str],
) -> str:
    mutable = _clean_fields(mutable_fields)
    identity = _clean_fields(identity_fields)
    payload = {
        "operation": operation,
        "table": table,
        "operation_kind": operation_kind,
        "identity": {field: record.get(field) for field in identity if field in record},
        "mutable": {field: record.get(field) for field in mutable if field in record},
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class DurableSupabaseWriteOutbox:
    """JSON-file backed outbox for one approved retryable write family."""

    def __init__(
        self,
        path: Path | str,
        *,
        steward: Optional[Any] = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._steward = steward
        self._clock = clock
        self._blocked_replay_count = 0

    def pending_count(self) -> int:
        return sum(1 for item in self._load_items() if item.get("status") == "queued")

    def queue_write(
        self,
        *,
        operation: str,
        table: str,
        record: Dict[str, Any],
        mutable_fields: Iterable[str],
        operation_kind: str = "upsert",
        identity_fields: Iterable[str] = _DEFAULT_IDENTITY_FIELDS,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        accepted_at = self._clock()
        operation = str(operation or "").strip()
        table = str(table or "").strip()
        operation_kind = str(operation_kind or "").strip().lower()
        mutable = _clean_fields(mutable_fields)
        identity = _clean_fields(identity_fields)

        if operation_kind in _DESTRUCTIVE_KINDS:
            return self._reject_receipt(
                operation=operation,
                table=table,
                error_code=DESTRUCTIVE_OPERATION_NOT_QUEUEABLE,
                message="Destructive operations are not queueable.",
            )
        if operation not in _SAFE_OPERATIONS:
            return self._reject_receipt(
                operation=operation,
                table=table,
                error_code=OPERATION_NOT_QUEUEABLE,
                message="Write operation is not on the narrow retry allowlist.",
            )

        queued_record = dict(record or {})
        key = idempotency_key or _stable_idempotency_key(
            operation=operation,
            table=table,
            operation_kind=operation_kind,
            record=queued_record,
            mutable_fields=mutable,
            identity_fields=identity,
        )
        queued_record["idempotency_key"] = key
        queued_record["accepted_at"] = accepted_at

        items = self._load_items()
        for item in items:
            if item.get("status") == "queued" and item.get("idempotency_key") == key:
                receipt = self._queue_receipt(item=item, action="already_queued")
                self._publish_visibility(last_receipt=receipt)
                return receipt

        item = {
            "outbox_id": f"outbox-{key[:16]}",
            "status": "queued",
            "operation": operation,
            "operation_kind": operation_kind,
            "table": table,
            "record": queued_record,
            "mutable_fields": mutable,
            "identity_fields": identity,
            "idempotency_key": key,
            "accepted_at": accepted_at,
            "receipts": [],
        }
        receipt = self._queue_receipt(item=item, action="queued")
        item["receipts"].append(receipt)
        items.append(item)
        self._save_items(items)
        self._publish_visibility(last_receipt=receipt)
        return receipt

    def replay_pending(
        self,
        *,
        remote_loader: Callable[[Dict[str, Any]], Dict[str, Any]],
        writer: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        item_validator: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        items = self._load_items()
        replay_receipt: Dict[str, Any] = {
            "receipt_id": str(uuid4()),
            "action": "reconcile_outbox",
            "queued": 0,
            "replayed": 0,
            "skipped": 0,
            "blocked": 0,
            "items": [],
            "started_at": self._clock(),
        }

        for item in items:
            if item.get("status") != "queued":
                replay_receipt["skipped"] += 1
                continue

            replay_receipt["queued"] += 1
            validation_block = item_validator(item) if item_validator else None
            if validation_block:
                self._block_item(
                    item,
                    replay_receipt,
                    {
                        "outbox_id": item.get("outbox_id"),
                        "idempotency_key": item.get("idempotency_key"),
                        "operation": item.get("operation"),
                        "table": item.get("table"),
                        "action": "blocked",
                        "ok": False,
                        "error_code": validation_block.get("error_code") or UNSUPPORTED_OUTBOX_ITEM,
                        "message": validation_block.get("message") or "Outbox item is not replayable.",
                    },
                )
                continue

            remote_record = dict(remote_loader(item) or {})
            if not remote_record:
                self._block_item(
                    item,
                    replay_receipt,
                    {
                        "outbox_id": item.get("outbox_id"),
                        "idempotency_key": item.get("idempotency_key"),
                        "operation": item.get("operation"),
                        "table": item.get("table"),
                        "action": "blocked",
                        "ok": False,
                        "error_code": REMOTE_RECORD_NOT_FOUND,
                        "message": "Remote record is missing; replay insert is not enabled for this write class.",
                    },
                )
                continue

            queued_record = dict(item.get("record") or {})
            plan = self._steward.plan_replay(
                remote_record=remote_record,
                queued_record=queued_record,
                mutable_fields=item.get("mutable_fields") or [],
                identity_fields=item.get("identity_fields") or _DEFAULT_IDENTITY_FIELDS,
            )
            item_receipt = {
                "outbox_id": item.get("outbox_id"),
                "idempotency_key": item.get("idempotency_key"),
                "operation": item.get("operation"),
                "table": item.get("table"),
                "plan": plan,
            }

            if not plan.get("ok"):
                item_receipt.update(
                    {
                        "action": "blocked",
                        "ok": False,
                        "error_code": plan.get("error_code"),
                        "message": plan.get("message"),
                    }
                )
                self._block_item(item, replay_receipt, item_receipt)
                continue

            try:
                write_receipt = {"action": "noop"}
                if plan.get("action") != "noop":
                    write_receipt = writer(item, plan.get("patch") or {}, plan)
            except Exception as exc:
                item_receipt.update(
                    {
                        "action": "blocked",
                        "ok": False,
                        "error_code": REPLAY_WRITE_FAILED,
                        "message": str(exc),
                    }
                )
                self._block_item(item, replay_receipt, item_receipt)
                continue

            item["status"] = "completed"
            item["completed_at"] = self._clock()
            item_receipt.update(
                {
                    "action": "replayed" if plan.get("action") != "noop" else "noop",
                    "ok": True,
                    "write_receipt": write_receipt,
                    "applied_fields": plan.get("applied_fields") or [],
                    "preserved_remote_fields": plan.get("preserved_remote_fields") or [],
                }
            )
            replay_receipt["replayed"] += 1
            item.setdefault("receipts", []).append(item_receipt)
            replay_receipt["items"].append(item_receipt)

        replay_receipt["completed_at"] = self._clock()
        replay_receipt["pending_outbox_count"] = sum(1 for item in items if item.get("status") == "queued")
        replay_receipt["blocked_replay_count"] = self._blocked_replay_count
        self._save_items(items)
        self._publish_visibility(last_receipt=replay_receipt)
        return replay_receipt

    def _block_item(
        self,
        item: Dict[str, Any],
        replay_receipt: Dict[str, Any],
        item_receipt: Dict[str, Any],
    ) -> None:
        item["status"] = "blocked"
        item["blocked_at"] = self._clock()
        replay_receipt["blocked"] += 1
        self._blocked_replay_count += 1
        item.setdefault("receipts", []).append(item_receipt)
        replay_receipt["items"].append(item_receipt)

    def _load_items(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in payload.get("items", []) if isinstance(item, dict)]

    def _save_items(self, items: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump({"items": items}, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self.path)

    def _publish_visibility(self, *, last_receipt: Dict[str, Any]) -> None:
        if not self._steward or not hasattr(self._steward, "update_outbox_visibility"):
            return
        self._steward.update_outbox_visibility(
            pending_outbox_count=self.pending_count(),
            last_reconciliation_receipt=last_receipt,
            blocked_replay_count=self._blocked_replay_count,
        )

    def _queue_receipt(self, *, item: Dict[str, Any], action: str) -> Dict[str, Any]:
        return {
            "receipt_id": str(uuid4()),
            "ok": True,
            "action": action,
            "outbox_id": item.get("outbox_id"),
            "operation": item.get("operation"),
            "table": item.get("table"),
            "operation_kind": item.get("operation_kind"),
            "idempotency_key": item.get("idempotency_key"),
            "status": item.get("status"),
            "queued_fields": item.get("mutable_fields") or [],
            "identity_fields": item.get("identity_fields") or [],
            "accepted_at": item.get("accepted_at"),
            "message": "Write queued durably; replay requires steward planning before Supabase mutation.",
        }

    def _reject_receipt(self, *, operation: str, table: str, error_code: str, message: str) -> Dict[str, Any]:
        return {
            "receipt_id": str(uuid4()),
            "ok": False,
            "action": "rejected",
            "operation": operation,
            "table": table,
            "status": "not_queued",
            "error_code": error_code,
            "message": message,
        }
