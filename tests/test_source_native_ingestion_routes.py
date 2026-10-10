import base64

from vvault.server import vvault_web_server as server


OWNER = "3ea20f29-424d-4f9a-87b8-4c6bf66695ce"


def _client(monkeypatch):
    server.app.config["TESTING"] = True
    monkeypatch.setattr(
        server, "_body_database_dependency_status",
        lambda: {"ready": True, "required": True, "status": "ready"},
    )
    monkeypatch.setattr(
        server,
        "get_current_user",
        lambda: ({"id": OWNER, "user_id": OWNER, "email": "owner@example.test",
                  "status": "ACTIVE", "auth_mode": "session"}, "native-session"),
    )
    return server.app.test_client()


def _payload(**overrides):
    value = {
        "provider": "codex",
        "sourceKind": "jsonl",
        "sourceCollection": "desktop-rollouts",
        "stableSourceId": "thread-1",
        "sourceLocator": "rollouts/thread-1.jsonl",
        "rawEnvelopeBase64": base64.b64encode(b'{"native":true}\n').decode(),
        "projectionContent": "User: hello\nAssistant: hello\n",
        "projectionContract": "codex-event-sequence",
        "projectionVersion": "1",
        "sourceMetadata": {"segmentCount": 1},
    }
    value.update(overrides)
    return value


def test_source_native_route_derives_owner_and_forwards_exact_bytes(monkeypatch):
    captured = {}

    class Service:
        def ingest_and_project_vault_file(self, **kwargs):
            captured.update(kwargs)
            return {"operationId": "a" * 64, "result": "applied",
                    "classification": "LEGACY_UNCLASSIFIED"}

    monkeypatch.setattr(server.source_native_ingestion_service,
                        "SourceNativeIngestionService", Service)
    response = _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                         json=_payload())
    assert response.status_code == 201
    assert captured["owner_user_id"] == OWNER
    assert captured["raw_envelope"] == b'{"native":true}\n'
    assert captured["construct_id"] is None
    assert captured["explicit_construct_evidence"] is False
    assert captured["actor"] == "owner@example.test"
    assert response.get_json()["sourceOwner"] == "ovvaults.source_native_artifacts"


def test_source_native_route_assigns_only_proven_codex_history_to_zen(monkeypatch):
    captured = {}

    class Service:
        def ingest_and_project_vault_file(self, **kwargs):
            captured.update(kwargs)
            return {"operationId": "a" * 64, "result": "applied"}

    monkeypatch.setattr(server.source_native_ingestion_service,
                        "SourceNativeIngestionService", Service)
    payload = _payload(
        sourceCollection="codex-desktop-rollouts",
        projectionContract="life.vvault.provider-transcript.codex-desktop/v1",
        sourceMetadata={"classificationEvidence": {
            "threadSource": "user", "supportedCodexSurface": True,
            "subagentMarkerAbsent": True,
        }},
    )
    response = _client(monkeypatch).post("/api/vault/source-native-ingestions", json=payload)
    assert response.status_code == 201
    assert captured["explicit_construct_evidence"] is True
    assert captured["construct_id"] == "zen-001"
    assert captured["file_type"] == "transcript"


def test_source_native_route_rejects_invalid_base64_without_calling_service(monkeypatch):
    class Service:
        def ingest_and_project_vault_file(self, **_kwargs):
            raise AssertionError("service must not be called")

    monkeypatch.setattr(server.source_native_ingestion_service,
                        "SourceNativeIngestionService", Service)
    response = _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                         json=_payload(rawEnvelopeBase64="%%%"))
    assert response.status_code == 400
    assert response.get_json()["errorCode"] == "SOURCE_INGESTION_BASE64_INVALID"


def test_source_native_route_rejects_unknown_fields_and_non_object_metadata(monkeypatch):
    unknown = _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                        json=_payload(constructId="zen-001"))
    assert unknown.status_code == 400
    assert unknown.get_json()["errorCode"] == "SOURCE_INGESTION_UNKNOWN_FIELDS"
    metadata = _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                         json=_payload(sourceMetadata=[]))
    assert metadata.status_code == 400
    assert metadata.get_json()["errorCode"] == "SOURCE_INGESTION_SCHEMA_INVALID"


def test_source_native_route_maps_replay_and_collision(monkeypatch):
    class Replay:
        def ingest_and_project_vault_file(self, **_kwargs):
            return {"operationId": "a" * 64, "result": "already_applied"}

    monkeypatch.setattr(server.source_native_ingestion_service,
                        "SourceNativeIngestionService", Replay)
    assert _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                     json=_payload()).status_code == 200

    class Collision:
        def ingest_and_project_vault_file(self, **_kwargs):
            raise server.source_native_ingestion_service.SourceProjectionCollision("occupied")

    monkeypatch.setattr(server.source_native_ingestion_service,
                        "SourceNativeIngestionService", Collision)
    response = _client(monkeypatch).post("/api/vault/source-native-ingestions",
                                         json=_payload())
    assert response.status_code == 409
    assert response.get_json()["errorCode"] == "SOURCE_INGESTION_COLLISION"
