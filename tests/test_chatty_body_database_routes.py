from vvault.server import vvault_web_server as server


class _BodyResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def to_response(self):
        return self.payload, self.status


def _chatty_headers():
    return {"X-Chatty-User": "devon@example.com"}


def test_chatty_transcript_read_uses_body_native_service(monkeypatch):
    server.app.config["TESTING"] = True
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)
    client = server.app.test_client()
    calls = []

    def transcript_body(construct_id):
        calls.append(construct_id)
        return _BodyResponse({
            "success": True,
            "construct_id": construct_id,
            "content": "# Chat with Zen\n",
            "storage_mode": "vvault_body",
            "storage_owner": "ovvaults.vault_files",
            "transcript_owner": "ovvaults.transcripts",
            "transcript_compatibility_owner": "ovvaults.vault_files",
        })

    monkeypatch.setattr(server.chatty_body_service, "transcript_body", transcript_body)

    response = client.get("/api/chatty/transcript/zen-001", headers=_chatty_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["storage_mode"] == "vvault_body"
    assert payload["storage_owner"] == "ovvaults.vault_files"
    assert payload["transcript_owner"] == "ovvaults.transcripts"
    assert calls == ["zen-001"]


def test_chatty_transcript_update_uses_body_native_service(monkeypatch):
    server.app.config["TESTING"] = True
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)
    client = server.app.test_client()
    calls = []

    def update_transcript_body(construct_id, payload):
        calls.append((construct_id, payload))
        return _BodyResponse({
            "success": True,
            "action": "updated",
            "construct_id": construct_id,
            "persistence_owner": "ovvaults.transcripts",
            "body_source": "ovvaults.transcripts",
        })

    monkeypatch.setattr(server.chatty_body_service, "update_transcript_body", update_transcript_body)

    response = client.post(
        "/api/chatty/transcript/zen-001",
        headers=_chatty_headers(),
        json={"content": "# Chat with Zen\n\nUpdated", "force": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["action"] == "updated"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert calls == [("zen-001", {"content": "# Chat with Zen\n\nUpdated", "force": True})]


def test_chatty_message_append_uses_body_native_service(monkeypatch):
    server.app.config["TESTING"] = True
    monkeypatch.delenv("VVAULT_SERVICE_TOKEN", raising=False)
    client = server.app.test_client()
    calls = []

    def append_transcript_message(construct_id, payload):
        calls.append((construct_id, payload))
        return _BodyResponse({
            "success": True,
            "action": "appended",
            "construct_id": construct_id,
            "persistence_owner": "ovvaults.transcripts",
            "body_source": "ovvaults.transcripts",
        })

    monkeypatch.setattr(server.chatty_body_service, "append_transcript_message", append_transcript_message)
    message = {
        "role": "assistant",
        "content": "I am here.",
        "timestamp": "2026-06-26T12:00:00",
    }

    response = client.post(
        "/api/chatty/transcript/zen-001/message",
        headers=_chatty_headers(),
        json=message,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["action"] == "appended"
    assert payload["persistence_owner"] == "ovvaults.transcripts"
    assert calls == [("zen-001", message)]
