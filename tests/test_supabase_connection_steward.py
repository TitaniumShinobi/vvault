import unittest

from vvault.server.supabase_connection_steward import (
    BLOCKED,
    CONNECTED,
    DEGRADED,
    IDENTITY_AUTHORITY_UNAVAILABLE,
    IDENTITY_CONFLICT,
    RECONNECTING,
    SCHEMA_DOWNGRADE_REJECTED,
    STALE_REPLAY_REJECTED,
    WARMING,
    SupabaseConnectionSteward,
)


class _FakeQuery:
    def __init__(self, client):
        self.client = client

    def select(self, _columns):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        self.client.calls += 1
        if self.client.failures:
            failure = self.client.failures.pop(0)
            if failure:
                raise RuntimeError(failure)
        return type("Result", (), {"data": [{"id": "metadata-only"}]})()


class _FakeSupabase:
    def __init__(self, failures=None):
        self.failures = list(failures or [])
        self.calls = 0

    def table(self, _name):
        return _FakeQuery(self)


class TestSupabaseConnectionSteward(unittest.TestCase):
    def make_steward(self, client, configured=True):
        return SupabaseConnectionSteward(
            get_client=lambda: client,
            is_configured=lambda: configured,
            using_service_role=lambda: True,
            heartbeat_interval_seconds=999,
            probe_timeout_seconds=1,
            recovery_successes_required=2,
        )

    def test_default_probe_timeout_budget_allows_slow_local_supabase_metadata_proof(self):
        steward = SupabaseConnectionSteward(
            get_client=lambda: _FakeSupabase(),
            is_configured=lambda: True,
            using_service_role=lambda: True,
            heartbeat_interval_seconds=999,
        )

        self.assertGreaterEqual(steward._probe_timeout_seconds, 8.0)

    def test_explicit_probe_timeout_override_still_wins(self):
        steward = SupabaseConnectionSteward(
            get_client=lambda: _FakeSupabase(),
            is_configured=lambda: True,
            using_service_role=lambda: True,
            heartbeat_interval_seconds=999,
            probe_timeout_seconds=0.75,
        )

        self.assertEqual(steward._probe_timeout_seconds, 0.75)

    def test_configured_client_without_two_table_proofs_is_not_connected(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        first = steward.probe_once(reason="test")

        self.assertEqual(first["connection_state"], WARMING)
        self.assertFalse(steward.allow_write()[0])
        self.assertTrue(steward.allow_read()[0])

    def test_two_successful_metadata_probes_mark_connection_connected(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        steward.probe_once(reason="test-1")
        second = steward.probe_once(reason="test-2")

        self.assertEqual(second["connection_state"], CONNECTED)
        self.assertTrue(second["canonical"])
        self.assertEqual(second["storage_mode"], "supabase")
        self.assertTrue(steward.allow_write()[0])

    def test_missing_config_blocks_readiness_and_writes(self):
        steward = self.make_steward(None, configured=False)

        state = steward.probe_once(reason="test")

        self.assertEqual(state["connection_state"], BLOCKED)
        self.assertEqual(state["last_error_code"], "SUPABASE_NOT_CONFIGURED")
        self.assertFalse(steward.allow_write()[0])

    def test_failed_probe_enters_reconnecting_then_degraded(self):
        client = _FakeSupabase(["error code 522", "error code 522", "error code 522"])
        steward = self.make_steward(client)

        first = steward.probe_once(reason="test-1")
        steward.probe_once(reason="test-2")
        third = steward.probe_once(reason="test-3")

        self.assertEqual(first["connection_state"], RECONNECTING)
        self.assertFalse(steward.allow_write()[0])
        self.assertEqual(third["connection_state"], DEGRADED)
        self.assertEqual(third["last_error_code"], "SUPABASE_TIMEOUT_522")
        self.assertFalse(third["canonical"])

    def test_recovery_requires_two_successful_probes(self):
        client = _FakeSupabase(["503"])
        steward = self.make_steward(client)

        steward.probe_once(reason="fail")
        first_recovery = steward.probe_once(reason="recover-1")
        second_recovery = steward.probe_once(reason="recover-2")

        self.assertEqual(first_recovery["connection_state"], WARMING)
        self.assertEqual(second_recovery["connection_state"], CONNECTED)
        self.assertTrue(second_recovery["recovery_proven_at"])

    def test_identity_resolution_reuses_existing_life_id_and_keeps_candidate_alias(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)
        steward.probe_once(reason="warm")
        steward.probe_once(reason="connected")

        receipt = steward.resolve_life_identity(
            email="dwoodson92@gmail.com",
            registry_life_id="devon_woodson_1762969514958",
            proposed_life_id="devon_woodson_1774390416168",
        )

        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["canonical"])
        self.assertFalse(receipt["should_mint"])
        self.assertEqual(receipt["life_user_id"], "devon_woodson_1762969514958")
        self.assertEqual(receipt["aliases"], ["devon_woodson_1774390416168"])

    def test_identity_resolution_fails_closed_on_conflicting_trusted_life_ids(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        receipt = steward.resolve_life_identity(
            email="dwoodson92@gmail.com",
            registry_life_id="devon_woodson_1762969514958",
            supabase_life_id="devon_woodson_1774390416168",
        )

        self.assertFalse(receipt["ok"])
        self.assertFalse(receipt["canonical"])
        self.assertFalse(receipt["should_mint"])
        self.assertEqual(receipt["error_code"], IDENTITY_CONFLICT)
        self.assertEqual(
            receipt["conflict_life_ids"],
            ["devon_woodson_1762969514958", "devon_woodson_1774390416168"],
        )

    def test_identity_resolution_blocks_new_life_id_when_authority_is_unavailable(self):
        steward = self.make_steward(None, configured=False)
        steward.probe_once(reason="blocked")

        receipt = steward.resolve_life_identity(
            email="new-user@example.com",
            proposed_life_id="new_user_1774390416168",
        )

        self.assertFalse(receipt["ok"])
        self.assertFalse(receipt["canonical"])
        self.assertFalse(receipt["should_mint"])
        self.assertEqual(receipt["error_code"], IDENTITY_AUTHORITY_UNAVAILABLE)
        self.assertEqual(receipt["connection_state"], BLOCKED)

    def test_replay_rejects_stale_write_instead_of_overwriting_newer_remote_record(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        receipt = steward.plan_replay(
            remote_record={
                "id": "file-1",
                "user_id": "user-1",
                "updated_at": "2026-04-30T12:00:00+00:00",
                "schema_version": 2,
                "content": "new remote content",
                "feature_flag": "new-product-field",
            },
            queued_record={
                "id": "file-1",
                "user_id": "user-1",
                "updated_at": "2026-04-30T11:00:00+00:00",
                "schema_version": 2,
                "content": "stale queued content",
            },
            mutable_fields=["content"],
        )

        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error_code"], STALE_REPLAY_REJECTED)
        self.assertNotIn("merged_record", receipt)

    def test_replay_rejects_schema_downgrade_before_field_patch(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        receipt = steward.plan_replay(
            remote_record={
                "id": "file-1",
                "user_id": "user-1",
                "updated_at": "2026-04-30T10:00:00+00:00",
                "schema_version": 3,
            },
            queued_record={
                "id": "file-1",
                "user_id": "user-1",
                "updated_at": "2026-04-30T11:00:00+00:00",
                "schema_version": 2,
                "idempotency_key": "op-1",
            },
            mutable_fields=["content"],
        )

        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error_code"], SCHEMA_DOWNGRADE_REJECTED)

    def test_idempotent_replay_preserves_remote_only_product_fields(self):
        client = _FakeSupabase()
        steward = self.make_steward(client)

        receipt = steward.plan_replay(
            remote_record={
                "id": "file-1",
                "user_id": "user-1",
                "updated_at": "2026-04-30T10:00:00+00:00",
                "schema_version": 2,
                "idempotency_key": "op-1",
                "content": "old",
                "new_feature_config": {"enabled": True},
            },
            queued_record={
                "id": "file-1",
                "user_id": "user-1",
                "accepted_at": "2026-04-30T10:01:00+00:00",
                "schema_version": 2,
                "idempotency_key": "op-1",
                "content": "retry content",
            },
            mutable_fields=["content"],
        )

        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["action"], "apply_field_patch")
        self.assertEqual(receipt["patch"], {"content": "retry content"})
        self.assertEqual(receipt["merged_record"]["content"], "retry content")
        self.assertEqual(receipt["merged_record"]["new_feature_config"], {"enabled": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
