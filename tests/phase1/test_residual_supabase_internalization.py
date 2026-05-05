import json

from packages.offboarding import residual_supabase_internalization as residual


class FakeSupabaseClient:
    def __init__(self, tables):
        self.tables = tables
        self.fetched_tables = []

    def fetch_rows(self, table):
        self.fetched_tables.append(table)
        rows = self.tables.get(table, [])
        return rows, len(rows)


class FakeTarget:
    def __init__(self):
        self.archives = []
        self.system_files = []
        self.manifests = []

    def upsert_archive_row(self, *, source_table, row, payload_class):
        self.archives.append(
            {
                "source_table": source_table,
                "row": row,
                "payload_class": payload_class,
            }
        )

    def upsert_system_file(self, *, source_table, row, target_store):
        self.system_files.append(
            {
                "source_table": source_table,
                "row": row,
                "target_store": target_store,
            }
        )

    def record_manifest(self, manifest):
        self.manifests.append(manifest)


def _clear_supabase_env(monkeypatch):
    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_ANON_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_fake_supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)


def test_env_gate_blocks_without_supabase_secrets(monkeypatch):
    _clear_supabase_env(monkeypatch)
    client = FakeSupabaseClient({})
    target = FakeTarget()

    result = residual.run_internalization(client=client, target=target, run_id="test-run")

    assert result["status"] == "blocked"
    assert "required Supabase env vars unavailable" in result["final_verdict"]
    assert result["supabase_env_gate"] == {
        "SUPABASE_URL_SET": False,
        "SUPABASE_SERVICE_ROLE_KEY_SET": False,
        "SUPABASE_SERVICE_KEY_SET": False,
        "SUPABASE_ANON_KEY_SET": False,
    }
    assert client.fetched_tables == []
    assert target.archives == []
    assert target.system_files == []
    assert target.manifests == []


def test_residual_rows_route_to_private_targets_and_sanitized_manifest(monkeypatch):
    _set_fake_supabase_env(monkeypatch)
    tables = {
        "user_sessions": [
            {"id": "session-1", "token": "raw-session-token", "expires_at": "2026-01-01T00:00:00Z"}
        ],
        "service_credentials": [
            {
                "id": "credential-1",
                "service": "broker",
                "encrypted_value": "cipher-secret-value",
            }
        ],
        "strategy_configs": [
            {
                "id": "strategy-1",
                "service": "fxshinobi",
                "params": {"private": "config-secret"},
            }
        ],
        "memory_embeddings": [
            {
                "id": "memory-1",
                "content": "PRIVATE MEMORY TEXT",
                "embedding": [0.125, 0.25],
            }
        ],
        "fxshinobi_health": [{"id": "health-1", "status": "ok", "metadata": {"private": "health-secret"}}],
        "fxshinobi_trades": [{"id": "trade-1", "symbol": "EURUSD", "metadata": {"private": "trade-secret"}}],
        "fxshinobi_snapshots": [
            {"id": "snapshot-1", "state": {"private": "snapshot-secret"}, "created_at": "2026-05-05T00:00:00Z"}
        ],
    }
    client = FakeSupabaseClient(tables)
    target = FakeTarget()

    result = residual.run_internalization(client=client, target=target, run_id="test-run")

    assert result["status"] == "complete"
    assert result["final_verdict"] == "residual supabase data internalized"
    assert client.fetched_tables == residual.RESIDUAL_TABLES

    manifests = {item["source_table"]: item for item in target.manifests}
    assert manifests["user_sessions"]["action"] == "discarded"
    assert manifests["user_sessions"]["discarded_count"] == 1
    assert manifests["user_sessions"]["imported_count"] == 0
    assert manifests["user_sessions"]["archived_count"] == 0

    assert {(item["source_table"], item["target_store"]) for item in target.system_files} == {
        ("service_credentials", "system/credentials/legacy-supabase"),
        ("strategy_configs", "system/configs/legacy-supabase"),
    }
    assert {(item["source_table"], item["payload_class"]) for item in target.archives} == {
        ("memory_embeddings", "memory_embedding"),
        ("fxshinobi_health", "fxshinobi"),
        ("fxshinobi_trades", "fxshinobi"),
        ("fxshinobi_snapshots", "fxshinobi"),
    }

    public_output = json.dumps(
        {
            "result": result,
            "manifests": target.manifests,
        },
        sort_keys=True,
    )
    for private_value in (
        "raw-session-token",
        "cipher-secret-value",
        "config-secret",
        "PRIVATE MEMORY TEXT",
        "health-secret",
        "trade-secret",
        "snapshot-secret",
        "0.125",
    ):
        assert private_value not in public_output

    for table_result in result["table_actions"]:
        assert table_result["aggregate_checksum_sha256"]
        assert "schema" in table_result
