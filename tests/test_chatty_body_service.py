from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from vvault.server import chatty_body_service as body
from vvault.server import vvault_web_server as server


def _created_at() -> datetime:
    return datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def test_database_url_requires_explicit_body_contract(monkeypatch):
    monkeypatch.delenv("VVAULT_BODY_DATABASE_URL", raising=False)
    monkeypatch.delenv("VVAULT_BODY_DATABASE_NAME", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db.example.local:5432/postgres")
    monkeypatch.setenv("USER", "devonwoodson")

    assert body.database_url() is None
    assert body.source_database_name() is None


def test_database_url_uses_explicit_body_contract(monkeypatch):
    explicit = "postgresql://vvault@example.invalid:25060/vvault_body_custom?sslmode=require"
    monkeypatch.setenv("VVAULT_BODY_DATABASE_URL", explicit)

    assert body.database_url() == explicit
    assert body.source_database_name() == "vvault_body_custom"


def test_connect_rejects_missing_explicit_body_contract(monkeypatch):
    monkeypatch.delenv("VVAULT_BODY_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="local database fallback is disabled"):
        body._connect()


def test_backend_startup_rejects_missing_explicit_body_contract(monkeypatch):
    monkeypatch.setattr(server.chatty_body_service, "database_url", lambda: None)

    with pytest.raises(RuntimeError, match="local database fallback is disabled"):
        server.main()


def test_construct_list_is_body_native_and_uses_canonical_construct_metadata(monkeypatch):
    monkeypatch.setenv("VVAULT_BODY_DATABASE_URL", "postgresql://devonwoodson@127.0.0.1:5432/vvault_body_20260504t123219z")
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "id": "file-1",
                "filename": "chat_with_zen-001.md",
                "object_key": "instances/zen-001/chatty/chat_with_zen-001.md",
                "storage_path": "instances/zen-001/chatty/chat_with_zen-001.md",
                "construct_id": "zen-001",
                "metadata": {},
                "content_type": "conversation",
                "created_at": _created_at(),
                "sha256": "abc",
            },
            {
                "id": "file-2",
                "filename": "identity.json",
                "object_key": "source-refs/file-2",
                "storage_path": "instances/nova-001/identity/identity.json",
                "construct_id": "nova-001",
                "metadata": {"kind": "identity"},
                "content_type": "identity",
                "created_at": _created_at(),
                "sha256": "def",
            },
        ]

    monkeypatch.setattr(
        body,
        "_rows",
        fake_rows,
    )

    payload, status = body.list_constructs().to_response()

    assert status == 200
    assert payload["success"] is True
    assert payload["status"] == "body_native"
    assert payload["storage_mode"] == "vvault_body"
    assert payload["source_database"] == "vvault_body_20260504t123219z"
    assert "construct_id" in captured["sql"]
    assert "chat_with" not in captured["params"][0]
    assert payload["constructs"] == [
        {
            "construct_id": "nova-001",
            "name": "Nova",
            "filename": "chat_with_nova-001.md",
            "created_at": "2026-05-04T12:00:00+00:00",
            "body_source": "ovvaults.vault_files",
        },
        {
            "construct_id": "zen-001",
            "name": "Zen",
            "filename": "chat_with_zen-001.md",
            "created_at": "2026-05-04T12:00:00+00:00",
            "body_source": "ovvaults.vault_files",
        }
    ]
    assert payload["count"] == 2


def test_construct_file_inventory_is_body_native(monkeypatch):
    def fake_rows(sql, params=()):
        assert "storage_path" in sql
        assert "construct_id" in sql
        assert params[0] == "nova-001"
        return [
            {
                "id": "identity-1",
                "filename": "identity.bak.json",
                "object_key": "source-refs/identity-1",
                "storage_path": "instances/nova-001/identity/identity.bak.json",
                "content_type": "identity",
                "file_type": "identity",
                "created_at": _created_at(),
                "sha256": "unavailable:source",
                "construct_id": "nova-001",
                "metadata": {"kind": "identity"},
                "content": "{\"name\":\"Nova\"}",
            },
            {
                "id": "chat-1",
                "filename": "chat_with_nova-001.md",
                "object_key": "source-refs/chat-1",
                "storage_path": "instances/nova-001/chatty/chat_with_nova-001.md",
                "content_type": "conversation",
                "file_type": "transcript",
                "created_at": _created_at(),
                "sha256": "abc",
                "construct_id": "nova-001",
                "metadata": {},
                "content": "# Nova transcript",
            },
        ]

    monkeypatch.setattr(body, "_rows", fake_rows)

    payload, status = body.construct_files("nova-001").to_response()

    assert status == 200
    assert payload["status"] == "body_native"
    assert payload["counts"] == {"assets": 0, "documents": 1, "identity": 1}
    assert payload["identity"][0]["path"] == "instances/nova-001/identity/identity.bak.json"
    assert payload["identity"][0]["construct_id"] == "nova-001"
    assert payload["identity"][0]["has_materialized_content"] is True
    assert payload["identity"][0]["body_source"] == "ovvaults.vault_files"


def test_body_read_routes_answer_from_materialized_content(monkeypatch):
    def fake_rows(sql, params=()):
        if "FROM transcripts" in sql:
            return [
                {
                    "id": "tx-1",
                    "title": "instances/zen-001/chatty/chat_with_zen-001.md",
                    "content": "Devon: hello\nZen: present",
                    "created_at": _created_at(),
                    "source_row_id": "source-tx-1",
                    "source_hash": "abc",
                }
            ]
        if "FROM vault_files" in sql and params and params[0] == "zen-001":
            return [
                {
                    "id": "prompt-1",
                    "filename": "instances/zen-001/identity/prompt.json",
                    "object_key": "instances/zen-001/identity/prompt.json",
                    "content_type": "identity",
                    "file_type": "identity",
                    "created_at": _created_at(),
                    "sha256": "prompt-hash",
                    "content": '{"name":"Zen","description":"Primary construct","instructions":"Stay grounded."}',
                    "metadata": {},
                    "construct_id": "zen-001",
                },
                {
                    "id": "voice-1",
                    "filename": "instances/zen-001/identity/voice.json",
                    "object_key": "instances/zen-001/identity/voice.json",
                    "content_type": "identity",
                    "file_type": "identity",
                    "created_at": _created_at(),
                    "sha256": "voice-hash",
                    "content": '{"text":"calm"}',
                    "metadata": {},
                    "construct_id": "zen-001",
                },
            ]
        return []

    monkeypatch.setattr(body, "_rows", fake_rows)

    checks = [
        body.transcript_body("zen-001"),
        body.identity("zen-001"),
        body.memories("zen-001"),
    ]

    for result in checks:
        payload, status = result.to_response()
        assert status == 200
        assert payload["success"] is True
        assert payload["status"] == "body_native"
        assert payload["storage_mode"] == "vvault_body"
        assert payload["body_native_available"] is True

    transcript_payload, _ = body.transcript_body("zen-001").to_response()
    identity_payload, _ = body.identity("zen-001").to_response()
    memories_payload, _ = body.memories("zen-001").to_response()

    assert "Devon: hello" in transcript_payload["content"]
    assert identity_payload["name"] == "Zen"
    assert identity_payload["instructions"] == "Stay grounded."
    assert memories_payload["total_pairs"] == 1
    assert memories_payload["memories"][0]["tag"] == "first_exchange"


class _FakeCursor:
    def __init__(self, row):
        self.row = dict(row) if row else None
        self.updated = None
        self.select_params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        normalized = sql.lstrip().upper()
        if normalized.startswith("SELECT"):
            self.select_params.append(params)
        if normalized.startswith("UPDATE"):
            self.updated = {
                **self.row,
                "content": params[0],
                "source_hash": params[1],
                "materialized_at": _created_at(),
            }
            self.row = self.updated

    def fetchone(self):
        return self.row


class _FakeConn:
    def __init__(self, row):
        self.cursor_obj = _FakeCursor(row)
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _fake_transcript_row(content="Devon: hello"):
    return {
        "id": uuid4(),
        "user_id": uuid4(),
        "title": "instances/zen-001/chatty/chat_with_zen-001.md",
        "content": content,
        "source_hash": "old",
        "created_at": _created_at(),
    }


def test_update_transcript_body_writes_to_ovvaults_transcripts(monkeypatch):
    conn = _FakeConn(_fake_transcript_row())
    monkeypatch.setattr(body, "_connect", lambda: conn)

    payload, status = body.update_transcript_body("zen-001", {"content": "replacement body"}).to_response()

    assert status == 200
    assert payload["status"] == "body_native"
    assert payload["action"] == "updated"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert conn.committed is True
    assert conn.cursor_obj.updated["content"] == "replacement body"
    assert conn.cursor_obj.updated["source_hash"] == body._sha256_text("replacement body")


def test_append_transcript_message_writes_to_ovvaults_transcripts(monkeypatch):
    conn = _FakeConn(_fake_transcript_row("Existing"))
    monkeypatch.setattr(body, "_connect", lambda: conn)

    payload, status = body.append_transcript_message(
        "zen-001",
        {"role": "user", "content": "new body message", "timestamp": "2026-05-05T12:00:00Z"},
    ).to_response()

    assert status == 200
    assert payload["status"] == "body_native"
    assert payload["action"] == "appended"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert "Existing" in conn.cursor_obj.updated["content"]
    assert "new body message" in conn.cursor_obj.updated["content"]
    assert "**User**" in conn.cursor_obj.updated["content"]
    assert body.PLACEHOLDER_TRANSCRIPT_CONTENT in conn.cursor_obj.select_params[0]


def test_lin_append_transcript_message_targets_real_row_not_placeholder(monkeypatch):
    conn = _FakeConn(_fake_transcript_row("# Lin\n\nExisting real body"))
    conn.cursor_obj.row["title"] = "instances/lin-001/chatty/chat_with_lin-001.md"
    monkeypatch.setattr(body, "_connect", lambda: conn)

    payload, status = body.append_transcript_message(
        "lin-001",
        {"role": "assistant", "content": "Lin body seam proof", "timestamp": "2026-05-05T12:00:00Z"},
    ).to_response()

    assert status == 200
    assert payload["status"] == "body_native"
    assert payload["construct_id"] == "lin-001"
    assert payload["storage_path"] == "instances/lin-001/chatty/chat_with_lin-001.md"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert "# Lin" in conn.cursor_obj.updated["content"]
    assert "Lin body seam proof" in conn.cursor_obj.updated["content"]
    assert conn.cursor_obj.select_params[0][0] == "instances/lin-001/chatty/chat_with_lin-001.md"
    assert conn.cursor_obj.select_params[0][1] == body.PLACEHOLDER_TRANSCRIPT_CONTENT


def test_append_transcript_message_blocks_when_no_writable_transcript(monkeypatch):
    conn = _FakeConn(None)
    monkeypatch.setattr(body, "_connect", lambda: conn)

    payload, status = body.append_transcript_message("zen-001", {"role": "user", "content": "x"}).to_response()

    assert status == 503
    assert payload["status"] == "body_missing"
    assert "No writable materialized transcript row" in payload["reason"]
    assert conn.rolled_back is True


def test_message_generation_persists_user_and_assistant_messages_in_one_body_write(monkeypatch):
    conn = _FakeConn(_fake_transcript_row("Existing"))
    monkeypatch.setattr(body, "_connect", lambda: conn)
    monkeypatch.setattr(body, "_generate_assistant_response", lambda construct_id, message: "real local model response")

    payload, status = body.message("zen-001", {"constructId": "zen-001", "message": "hello", "timestamp": "2026-05-05T12:00:00Z"}).to_response()

    assert status == 200
    assert payload["status"] == "body_native"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert payload["response"] == "real local model response"
    assert conn.committed is True
    assert "hello" in conn.cursor_obj.updated["content"]
    assert "real local model response" in conn.cursor_obj.updated["content"]


def test_message_generation_failure_is_not_reported_as_body_missing(monkeypatch):
    monkeypatch.setattr(body, "_generate_assistant_response", lambda *_args: (_ for _ in ()).throw(RuntimeError("ollama unavailable")))

    payload, status = body.message("zen-001", {"constructId": "zen-001", "message": "hello"}).to_response()

    assert status == 503
    assert payload["status"] == "generation_blocked"
    assert payload["error_code"] == "VVAULT_GENERATION_BLOCKED"
    assert payload["persistence_owner"] == "ovvaults.transcripts"


def _route_headers():
    return {"X-Chatty-User": "dwoodson92@gmail.com"}


def test_chatty_construct_routes_answer_from_body(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(
        server.chatty_body_service,
        "_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "file-1",
                "filename": "chat_with_zen-001.md",
                "object_key": "instances/zen-001/chatty/chat_with_zen-001.md",
                "content_type": "conversation",
                "created_at": _created_at(),
                "sha256": "abc",
            },
            {
                "id": "file-2",
                "filename": "identity.json",
                "object_key": "source-refs/file-2",
                "storage_path": "instances/nova-001/identity/identity.json",
                "construct_id": "nova-001",
                "metadata": {"kind": "identity"},
                "content_type": "identity",
                "created_at": _created_at(),
                "sha256": "def",
            }
        ],
    )

    constructs = client.get("/api/chatty/constructs", headers=_route_headers())
    files = client.get("/api/chatty/construct/zen-001/files", headers=_route_headers())

    assert constructs.status_code == 200
    assert constructs.get_json()["status"] == "body_native"
    assert constructs.get_json()["storage_mode"] == "vvault_body"
    assert [item["construct_id"] for item in constructs.get_json()["constructs"]] == ["nova-001", "zen-001"]
    assert files.status_code == 200
    assert files.get_json()["status"] == "body_native"
    assert hasattr(server, "chatty_body_service")


def test_chatty_read_content_routes_answer_from_body(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(server.chatty_body_service, "_rows", lambda sql, params=(): [
        {
            "id": "tx-1",
            "title": "instances/zen-001/chatty/chat_with_zen-001.md",
            "filename": "instances/zen-001/identity/prompt.json",
            "object_key": "instances/zen-001/identity/prompt.json",
            "content_type": "identity",
            "file_type": "identity",
            "created_at": _created_at(),
            "sha256": "abc",
            "content": '{"name":"Zen","instructions":"Stay grounded."}' if "FROM vault_files" in sql else "Devon: hello\nZen: present",
            "metadata": {},
            "construct_id": "zen-001",
            "source_row_id": "source-1",
            "source_hash": "abc",
        }
    ])

    responses = [
        client.get("/api/chatty/transcript/zen-001", headers=_route_headers()),
        client.get("/api/chatty/construct/zen-001/identity", headers=_route_headers()),
        client.get("/api/chatty/construct/zen-001/memories", headers=_route_headers()),
    ]

    for response in responses:
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "body_native"
        assert payload["storage_mode"] == "vvault_body"
    assert hasattr(server, "chatty_body_service")


def test_chatty_write_content_routes_use_body_service(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)

    def fake_result(route, **payload):
        return body.BodyResult(
            status="body_native",
            route=route,
            source_database="test",
            payload={
                "body_native_available": True,
                "persistence_owner": "ovvaults.transcripts",
                **payload,
            },
        )

    monkeypatch.setattr(
        server.chatty_body_service,
        "update_transcript_body",
        lambda construct_id, payload: fake_result(f"/api/chatty/transcript/{construct_id}", action="updated"),
    )
    monkeypatch.setattr(
        server.chatty_body_service,
        "append_transcript_message",
        lambda construct_id, payload: fake_result(f"/api/chatty/transcript/{construct_id}/message", action="appended"),
    )
    monkeypatch.setattr(
        server.chatty_body_service,
        "message",
        lambda construct_id, payload: fake_result("/api/chatty/message", response="ok"),
    )

    responses = [
        client.post("/api/chatty/transcript/zen-001", json={"content": "x"}, headers=_route_headers()),
        client.post("/api/chatty/transcript/zen-001/message", json={"role": "user", "content": "x"}, headers=_route_headers()),
        client.post("/api/chatty/message", json={"constructId": "zen-001", "message": "hello"}, headers=_route_headers()),
    ]

    for response in responses:
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "body_native"
        assert payload["storage_mode"] == "vvault_body"
        assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert hasattr(server, "chatty_body_service")
