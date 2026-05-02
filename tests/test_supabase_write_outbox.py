import tempfile
import unittest
from pathlib import Path

from vvault.server.supabase_connection_steward import (
    IDENTITY_CONFLICT,
    STALE_REPLAY_REJECTED,
    SupabaseConnectionSteward,
)
from vvault.server.supabase_write_outbox import (
    DESTRUCTIVE_OPERATION_NOT_QUEUEABLE,
    VAULT_FILE_UPSERT,
    DurableSupabaseWriteOutbox,
)


class TestDurableSupabaseWriteOutbox(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.clock_value = "2026-04-30T12:00:00+00:00"
        self.steward = SupabaseConnectionSteward(
            get_client=lambda: None,
            heartbeat_interval_seconds=999,
        )
        self.outbox = DurableSupabaseWriteOutbox(
            Path(self.tempdir.name) / "outbox.json",
            steward=self.steward,
            clock=lambda: self.clock_value,
        )

    def _record(self, **overrides):
        record = {
            "storage_path": "system/prompts/current.md",
            "is_system": True,
            "user_id": None,
            "filename": "system/prompts/current.md",
            "file_type": "text/markdown",
            "content": "queued content",
            "metadata": "{}",
            "sha256": "queued-sha",
            "updated_at": self.clock_value,
            "schema_version": 1,
        }
        record.update(overrides)
        return record

    def _queue(self, **overrides):
        return self.outbox.queue_write(
            operation=VAULT_FILE_UPSERT,
            table="vault_files",
            record=self._record(**overrides),
            mutable_fields=["filename", "file_type", "content", "metadata", "sha256", "updated_at"],
            identity_fields=["storage_path", "is_system", "user_id"],
            idempotency_key=overrides.get("idempotency_key", "vault_files:system_file:system/prompts/current.md:queued-sha"),
        )

    def test_queue_receipt_shape_and_operator_visibility(self):
        receipt = self._queue()

        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["action"], "queued")
        self.assertEqual(receipt["operation"], VAULT_FILE_UPSERT)
        self.assertEqual(receipt["table"], "vault_files")
        self.assertEqual(receipt["status"], "queued")
        self.assertEqual(receipt["queued_fields"], ["content", "file_type", "filename", "metadata", "sha256", "updated_at"])
        self.assertEqual(self.outbox.pending_count(), 1)
        self.assertEqual(self.steward.snapshot()["pending_outbox_count"], 1)
        self.assertEqual(self.steward.snapshot()["last_reconciliation_receipt"]["outbox_id"], receipt["outbox_id"])

    def test_idempotency_key_is_stable_for_same_write(self):
        first = self.outbox.queue_write(
            operation=VAULT_FILE_UPSERT,
            table="vault_files",
            record=self._record(),
            mutable_fields=["content", "sha256"],
            identity_fields=["storage_path", "is_system", "user_id"],
        )
        second = self.outbox.queue_write(
            operation=VAULT_FILE_UPSERT,
            table="vault_files",
            record=self._record(),
            mutable_fields=["sha256", "content"],
            identity_fields=["user_id", "storage_path", "is_system"],
        )

        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(second["action"], "already_queued")
        self.assertEqual(self.outbox.pending_count(), 1)

    def test_replay_blocks_stale_remote_instead_of_overwrite(self):
        self._queue()

        receipt = self.outbox.replay_pending(
            remote_loader=lambda _item: self._record(
                content="newer remote",
                updated_at="2026-04-30T12:05:00+00:00",
                idempotency_key="different-remote-op",
            ),
            writer=lambda _item, _patch, _plan: {"ok": True},
        )

        self.assertEqual(receipt["blocked"], 1)
        self.assertEqual(receipt["items"][0]["error_code"], STALE_REPLAY_REJECTED)
        self.assertEqual(self.outbox.pending_count(), 0)
        self.assertEqual(self.steward.snapshot()["blocked_replay_count"], 1)

    def test_replay_blocks_identity_conflict(self):
        self._queue()

        receipt = self.outbox.replay_pending(
            remote_loader=lambda _item: self._record(
                storage_path="system/prompts/other.md",
                idempotency_key="vault_files:system_file:system/prompts/current.md:queued-sha",
            ),
            writer=lambda _item, _patch, _plan: {"ok": True},
        )

        self.assertEqual(receipt["blocked"], 1)
        self.assertEqual(receipt["items"][0]["error_code"], IDENTITY_CONFLICT)

    def test_replay_preserves_unknown_remote_fields(self):
        self._queue()
        writes = []

        receipt = self.outbox.replay_pending(
            remote_loader=lambda _item: self._record(
                content="old remote",
                idempotency_key="vault_files:system_file:system/prompts/current.md:queued-sha",
                remote_only_feature={"enabled": True},
            ),
            writer=lambda item, patch, plan: writes.append((item, patch, plan)) or {"ok": True, "updated": True},
        )

        self.assertEqual(receipt["replayed"], 1)
        self.assertEqual(writes[0][1]["content"], "queued content")
        self.assertEqual(writes[0][2]["merged_record"]["remote_only_feature"], {"enabled": True})
        self.assertIn("remote_only_feature", writes[0][2]["preserved_remote_fields"])

    def test_destructive_operations_are_not_queueable(self):
        receipt = self.outbox.queue_write(
            operation=VAULT_FILE_UPSERT,
            operation_kind="delete",
            table="vault_files",
            record=self._record(),
            mutable_fields=["content"],
        )

        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["action"], "rejected")
        self.assertEqual(receipt["error_code"], DESTRUCTIVE_OPERATION_NOT_QUEUEABLE)
        self.assertFalse((Path(self.tempdir.name) / "outbox.json").exists())

    def test_successful_replay_marks_item_complete(self):
        self._queue()

        receipt = self.outbox.replay_pending(
            remote_loader=lambda _item: self._record(
                content="old remote",
                idempotency_key="vault_files:system_file:system/prompts/current.md:queued-sha",
            ),
            writer=lambda _item, _patch, _plan: {"ok": True},
        )

        self.assertEqual(receipt["replayed"], 1)
        self.assertEqual(receipt["pending_outbox_count"], 0)
        self.assertEqual(self.outbox.pending_count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
