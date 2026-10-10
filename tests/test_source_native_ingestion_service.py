import hashlib

import pytest

from vvault.server.source_native_ingestion_service import (
    SourceNativeIngestionService,
    SourceProjectionCollision,
)


class Cursor:
    def __init__(self, store): self.store, self.one, self.calls = store, None, []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        if "SELECT receipt FROM ovvaults.source_ingest_receipts" in normalized:
            self.one = self.store.get(("ingest", params[0]))
        elif "INSERT INTO ovvaults.source_native_artifacts" in normalized:
            self.store["raw"] = params[8]
            self.one = {
                "id": "11111111-1111-1111-1111-111111111111",
                "raw_envelope": params[8],
                "raw_envelope_sha256": params[9],
                "raw_envelope_bytes": params[10],
                "payload": params[11] if len(params) > 11 else None,
                "payload_sha256": params[12] if len(params) > 12 else None,
                "payload_bytes": params[13] if len(params) > 13 else None,
            }
        elif "INSERT INTO ovvaults.source_ingest_receipts" in normalized:
            import json
            self.store[("ingest", params[0])] = {"receipt": json.loads(params[6])}
            self.one = None
        elif "SELECT receipt FROM ovvaults.canonical_source_projections" in normalized:
            self.one = self.store.get(("projection", params[0]))
        elif "SELECT id::text FROM ovvaults.vault_files" in normalized:
            self.one = {"id": "occupied"} if self.store.get("collision") else None
        elif "INSERT INTO ovvaults.vault_files" in normalized:
            self.store["vault_insert_count"] = self.store.get("vault_insert_count", 0) + 1
            self.store["vault_params"] = params
            self.one = {
                "id": "22222222-2222-2222-2222-222222222222",
                "user_id": params[0], "construct_id": params[10],
                "sha256": params[7], "size_bytes": params[6], "content": params[8],
                "object_key": params[3], "storage_path": params[11],
            }
        elif "INSERT INTO ovvaults.construct_legacy_classifications" in normalized:
            self.store["classification"] = params
            self.one = None
        elif "INSERT INTO ovvaults.canonical_source_projections" in normalized:
            import json
            self.store[("projection", params[0])] = {"receipt": json.loads(params[-2])}
            self.one = None
        else: self.one = None
    def fetchone(self): return self.one


class Connection:
    def __init__(self, store):
        self.store = store
        store.setdefault("commits", 0)
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return Cursor(self.store)
    def commit(self): self.store["commits"] += 1
    def rollback(self): self.store["rollbacks"] = self.store.get("rollbacks", 0) + 1


def service():
    store = {}
    return SourceNativeIngestionService(connect=lambda: Connection(store)), store


def test_ingest_preserves_exact_bytes_and_replays_deterministically():
    subject, store = service()
    raw = b"\xef\xbb\xbfline1\r\nline2\x00"
    kwargs = dict(owner_user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                  relying_party_id="chatty", provider="codex", source_kind="jsonl",
                  source_collection="desktop-rollouts", raw_envelope=raw, actor="test",
                  stable_source_id="thread-1")
    first = subject.ingest(**kwargs)
    second = subject.ingest(**kwargs)
    assert store["raw"] == raw
    assert first["rawEnvelopeSha256"] == hashlib.sha256(raw).hexdigest()
    assert first["rawEnvelopeBytes"] == len(raw)
    assert first["classification"] == "LEGACY_UNCLASSIFIED"
    assert second["operationId"] == first["operationId"]
    assert second["result"] == "already_applied"


def test_ingest_rejects_text_instead_of_silently_reencoding():
    subject, _ = service()
    with pytest.raises(TypeError, match="raw_envelope must be bytes"):
        subject.ingest(owner_user_id="owner", relying_party_id="chatty", provider="codex",
                       source_kind="jsonl", source_collection="rollouts",
                       raw_envelope="not bytes", actor="test")


def test_projection_receipt_is_deterministic_and_replay_safe():
    subject, _ = service()
    digest = "a" * 64
    kwargs = dict(source_artifact_id="11111111-1111-1111-1111-111111111111",
                  owner_user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                  relying_party_id="chatty", destination_table="transcripts",
                  destination_record_id="22222222-2222-2222-2222-222222222222",
                  projection_contract="codex-event-sequence", projection_version="1",
                  transform_sha256=digest, source_sha256="b" * 64,
                  destination_sha256="c" * 64, actor="test")
    first = subject.record_projection(**kwargs)
    second = subject.record_projection(**kwargs)
    assert second["operationId"] == first["operationId"]
    assert second["result"] == "already_applied"


def test_projection_refuses_unknown_destination():
    subject, _ = service()
    with pytest.raises(ValueError, match="destination_table"):
        subject.record_projection(source_artifact_id="id", owner_user_id="owner",
          relying_party_id="chatty", destination_table="construct_principals",
          destination_record_id="id", projection_contract="x", projection_version="1",
          transform_sha256="a"*64, source_sha256="b"*64,
          destination_sha256="c"*64, actor="test")


def _atomic_kwargs(**overrides):
    values = dict(owner_user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                  relying_party_id="chatty", provider="codex", source_kind="jsonl",
                  source_collection="desktop-rollouts", raw_envelope=b'{"native":true}\n',
                  projection_content="User: hello\nAssistant: hello\n", actor="test",
                  stable_source_id="thread-1", source_locator="rollout/thread-1.jsonl",
                  projection_contract="codex-event-sequence", projection_version="1")
    values.update(overrides)
    return values


def test_atomic_backfill_is_private_unassigned_and_replays_without_second_file():
    subject, store = service()
    first = subject.ingest_and_project_vault_file(**_atomic_kwargs())
    second = subject.ingest_and_project_vault_file(**_atomic_kwargs())
    assert first["readbackVerified"] is True
    assert first["classification"] == "LEGACY_UNCLASSIFIED"
    assert first["classificationConstruct"] == "legacy-unassigned"
    assert store["vault_params"][10] is None
    assert store["classification"][1:3] == ("legacy-unassigned", "LEGACY_UNCLASSIFIED")
    assert store["vault_insert_count"] == 1
    assert second["operationId"] == first["operationId"]
    assert second["result"] == "already_applied"


def test_atomic_backfill_assigns_zen_only_with_explicit_adapter_evidence():
    subject, store = service()
    receipt = subject.ingest_and_project_vault_file(
        **_atomic_kwargs(explicit_construct_evidence=True, construct_id="zen-001"))
    assert receipt["classification"] == "ACCOUNT_PRIVATE"
    assert receipt["classificationConstruct"] == "zen-001"
    assert store["vault_params"][10] == "zen-001"
    assert store["classification"][1:3] == ("zen-001", "ACCOUNT_PRIVATE")


def test_atomic_backfill_rejects_unproven_construct_assignment():
    subject, _ = service()
    with pytest.raises(ValueError, match="explicit construct evidence"):
        subject.ingest_and_project_vault_file(**_atomic_kwargs(construct_id="zen-001"))


def test_atomic_backfill_fails_closed_on_immutable_destination_collision():
    subject, store = service()
    store["collision"] = True
    with pytest.raises(SourceProjectionCollision):
        subject.ingest_and_project_vault_file(**_atomic_kwargs())
    assert store.get("vault_insert_count", 0) == 0
    assert store["rollbacks"] == 1
