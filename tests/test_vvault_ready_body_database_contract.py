from vvault.server import vvault_web_server as server


def _runtime_status(*, ready: bool):
    body_database = {
        "required": True,
        "ready": ready,
        "status": "healthy" if ready else "unhealthy",
        "configured": True,
        "schema": "ovvaults",
        "source_database": "vvault_body_20260504t123219z",
        "checks": {
            "vault_files_readable": ready,
            "transcripts_readable": ready,
        },
    }
    return {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": ready,
        "connection_state": "connected" if ready else "degraded",
        "runtime": {},
        "body_database": body_database,
        "storage": {},
        "auth": {},
    }


def test_ready_response_exposes_current_vvault_body_authority(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    monkeypatch.setattr(server, "_get_vvault_runtime_status", lambda: _runtime_status(ready=True))

    response = client.get("/api/ready")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ready"] is True
    assert payload["authority"] == "vvault_body"
    assert payload["canonical"] is True
    assert payload["storage_mode"] == "vvault_body"
    assert payload["body_database"]["schema"] == "ovvaults"
    assert payload["body_database"]["checks"]["vault_files_readable"] is True
    assert payload["body_database"]["checks"]["transcripts_readable"] is True


def test_ready_returns_503_when_body_database_is_unavailable(monkeypatch):
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    monkeypatch.setattr(server, "_get_vvault_runtime_status", lambda: _runtime_status(ready=False))

    response = client.get("/api/ready")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["ready"] is False
    assert payload["canonical"] is False
    assert payload["status"] == "not_ready"
    assert payload["body_database"]["checks"]["vault_files_readable"] is False
    assert payload["body_database"]["checks"]["transcripts_readable"] is False


class _Cursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        self.calls.append(sql)


class _Connection:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self.calls)


def test_body_database_dependency_status_reads_current_tables(monkeypatch):
    calls = []
    monkeypatch.setattr(server.chatty_body_service, "database_url", lambda: "postgresql://vvault.example/vvault_body")
    monkeypatch.setattr(
        server.chatty_body_service,
        "source_database_name",
        lambda _url: "vvault_body_20260504t123219z",
    )
    monkeypatch.setattr(server.chatty_body_service, "BODY_SCHEMA", "ovvaults")
    monkeypatch.setattr(server.chatty_body_service, "_connect", lambda: _Connection(calls))

    payload = server._body_database_dependency_status()

    assert payload["ready"] is True
    assert payload["schema"] == "ovvaults"
    assert payload["source_database"] == "vvault_body_20260504t123219z"
    assert payload["checks"]["vault_files_readable"] is True
    assert payload["checks"]["transcripts_readable"] is True
    assert calls == [
        "SELECT 1 FROM vault_files LIMIT 1",
        "SELECT 1 FROM transcripts LIMIT 1",
    ]
