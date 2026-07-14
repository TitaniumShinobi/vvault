from unittest.mock import patch

from vvault.server import vvault_web_server as server


def _runtime_status(*, ready: bool):
    return {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "authority": "vvault_body",
        "storage_mode": "vvault_body",
        "canonical": ready,
        "connection_state": "connected" if ready else "degraded",
        "runtime": {},
        "body_database": {"ready": ready, "schema": "ovvaults"},
        "storage": {},
        "auth": {},
    }


def test_runtime_status_exposes_stable_ovvaults_authority_fields():
    body_status = {
        "ready": True,
        "connection_state": "connected",
        "schema": "ovvaults",
    }
    with patch.object(server, "_body_database_dependency_status", return_value=body_status), patch.object(
        server, "_storage_dependency_metadata", return_value={}
    ), patch.object(server, "_auth_dependency_metadata", return_value={}):
        status = server._get_vvault_runtime_status()

    assert status["authority"] == "vvault_body"
    assert status["storage_mode"] == "vvault_body"
    assert status["canonical"] is True
    assert status["connection_state"] == "connected"


def test_ready_route_returns_stable_contract_for_ready_and_degraded_states():
    server.app.config["TESTING"] = True
    client = server.app.test_client()

    for ready, expected_http in ((True, 200), (False, 503)):
        with patch.object(server, "_get_vvault_runtime_status", return_value=_runtime_status(ready=ready)):
            response = client.get("/api/ready")

        payload = response.get_json()
        assert response.status_code == expected_http
        assert payload["authority"] == "vvault_body"
        assert payload["storage_mode"] == "vvault_body"
        assert payload["canonical"] is ready
        assert payload["connection_state"] == ("connected" if ready else "degraded")
