import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vvault.server import vvault_web_server as server
from vvault.server.supabase_write_outbox import DurableSupabaseWriteOutbox, VAULT_FILE_UPSERT


class _Result:
    def __init__(self, data=None):
        self.data = data or []


class _FakeVaultFilesQuery:
    def __init__(self, client, *, update_payload=None):
        self.client = client
        self.update_payload = update_payload
        self.filters = []

    def select(self, _columns):
        return self

    def update(self, payload):
        return _FakeVaultFilesQuery(self.client, update_payload=payload)

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, _count):
        return self

    def execute(self):
        if self.update_payload is not None:
            self.client.updates.append({"payload": dict(self.update_payload), "filters": list(self.filters)})
            updated = dict(self.client.remote or {})
            updated.update(self.update_payload)
            self.client.remote = updated
            return _Result([updated])
        self.client.selects.append(list(self.filters))
        return _Result([dict(self.client.remote)] if self.client.remote else [])


class _FakeSupabaseClient:
    def __init__(self, remote=None):
        self.remote = remote
        self.selects = []
        self.updates = []

    def table(self, name):
        if name != "vault_files":
            raise AssertionError(f"unexpected table: {name}")
        return _FakeVaultFilesQuery(self)


class TestSystemFileOutboxReplay(unittest.TestCase):
    def setUp(self):
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.outbox = DurableSupabaseWriteOutbox(
            Path(self.tempdir.name) / "outbox.json",
            steward=server.SUPABASE_STEWARD,
            clock=lambda: "2026-04-30T12:00:00+00:00",
        )

    def _state(self, **overrides):
        state = {
            "connection_state": "connected",
            "storage_mode": "supabase",
            "last_error_code": None,
            "blocked_replay_count": 0,
        }
        state.update(overrides)
        return state

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
            "updated_at": "2026-04-30T12:00:00+00:00",
            "schema_version": 1,
        }
        record.update(overrides)
        return record

    def _queue(self, **overrides):
        return self.outbox.queue_write(
            operation=overrides.pop("operation", VAULT_FILE_UPSERT),
            operation_kind=overrides.pop("operation_kind", "upsert"),
            table=overrides.pop("table", "vault_files"),
            record=self._record(**overrides),
            mutable_fields=server.SYSTEM_FILE_OUTBOX_MUTABLE_FIELDS,
            identity_fields=server.SYSTEM_FILE_OUTBOX_IDENTITY_FIELDS,
            idempotency_key="vault_files:system_file:system/prompts/current.md:queued-sha",
        )

    def _run_replay(self, fake_supabase, *, connected=True, state=None):
        state = state or self._state(connection_state="connected" if connected else "blocked")
        return patch.multiple(
            server.SUPABASE_STEWARD,
            allow_write=Mock(return_value=(connected, state)),
            snapshot=Mock(return_value=state),
        ), patch.object(server, "supabase_client", fake_supabase), patch.object(
            server, "SUPABASE_WRITE_OUTBOX", self.outbox
        )

    def test_replay_endpoint_requires_connected_supabase(self):
        self._queue()
        blocked = self._state(connection_state="blocked", last_error_code="SUPABASE_TIMEOUT_522")
        fake = _FakeSupabaseClient(remote=self._record(id="remote-1"))

        context, supabase_patch, outbox_patch = self._run_replay(fake, connected=False, state=blocked)
        with patch.object(server, "VVAULT_SERVICE_TOKEN", "svc-token"), context, supabase_patch, outbox_patch:
            response = self.client.post(
                "/api/vault/system-files/outbox/replay",
                headers={"Authorization": "Bearer svc-token"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["outbox_replay_receipt"]["action"], "blocked")
        self.assertEqual(fake.selects, [])
        self.assertEqual(fake.updates, [])

    def test_successful_replay_applies_only_mutable_fields_and_completes_item(self):
        self._queue()
        fake = _FakeSupabaseClient(
            remote=self._record(
                id="remote-1",
                content="old remote content",
                sha256="old-sha",
                updated_at="2026-04-30T11:59:00+00:00",
                remote_only_feature={"keep": True},
            )
        )

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["replayed"], 1)
        self.assertEqual(self.outbox.pending_count(), 0)
        update = fake.updates[0]["payload"]
        self.assertEqual(update["content"], "queued content")
        self.assertEqual(update["sha256"], "queued-sha")
        self.assertNotIn("storage_path", update)
        self.assertNotIn("is_system", update)
        self.assertNotIn("user_id", update)

    def test_replay_blocks_stale_remote_row(self):
        self._queue()
        fake = _FakeSupabaseClient(
            remote=self._record(
                id="remote-1",
                content="newer remote content",
                updated_at="2026-04-30T12:05:00+00:00",
                remote_only_feature={"keep": True},
            )
        )

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["blocked"], 1)
        self.assertEqual(receipt["items"][0]["error_code"], "STALE_REPLAY_REJECTED")
        self.assertEqual(fake.updates, [])

    def test_replay_blocks_storage_path_identity_conflict(self):
        self._queue()
        fake = _FakeSupabaseClient(
            remote=self._record(
                id="remote-1",
                storage_path="system/prompts/other.md",
                updated_at="2026-04-30T11:59:00+00:00",
            )
        )

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["items"][0]["error_code"], "IDENTITY_CONFLICT")
        self.assertEqual(fake.updates, [])

    def test_replay_preserves_unknown_remote_fields_in_plan_receipt(self):
        self._queue()
        fake = _FakeSupabaseClient(
            remote=self._record(
                id="remote-1",
                content="old remote content",
                updated_at="2026-04-30T11:59:00+00:00",
                future_prompt_setting={"mode": "new"},
            )
        )

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        plan = receipt["items"][0]["plan"]
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["merged_record"]["future_prompt_setting"], {"mode": "new"})
        self.assertNotIn("future_prompt_setting", fake.updates[0]["payload"])

    def test_replay_blocks_unsupported_outbox_items_before_supabase_call(self):
        self.outbox.path.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "outbox_id": "outbox-bad",
                            "status": "queued",
                            "operation": "delete",
                            "operation_kind": "delete",
                            "table": "vault_files",
                            "record": self._record(),
                            "mutable_fields": ["content"],
                            "identity_fields": server.SYSTEM_FILE_OUTBOX_IDENTITY_FIELDS,
                            "idempotency_key": "bad-key",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        fake = _FakeSupabaseClient(remote=self._record(id="remote-1"))

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["items"][0]["error_code"], "UNSUPPORTED_OUTBOX_ITEM")
        self.assertEqual(fake.selects, [])
        self.assertEqual(fake.updates, [])

    def test_missing_remote_row_insert_path_is_blocked(self):
        self._queue()
        fake = _FakeSupabaseClient(remote=None)

        context, supabase_patch, outbox_patch = self._run_replay(fake)
        with context, supabase_patch, outbox_patch:
            receipt = server._replay_system_file_outbox()

        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["items"][0]["error_code"], "REMOTE_RECORD_NOT_FOUND")
        self.assertEqual(fake.updates, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
