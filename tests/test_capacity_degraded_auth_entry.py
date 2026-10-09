from vvault.server import vvault_web_server as server


def _critical_capacity():
    return {"critical": True, "deployment_blocked": True, "warning": True, "authentication_regression": False}


def test_critical_capacity_is_explicit_json_not_an_authentication_or_device_error(monkeypatch):
    monkeypatch.setattr(server.capacity_readiness, "capacity_status", lambda *_args, **_kwargs: _critical_capacity())
    response = server.app.test_client().get("/api/auth/oauth/google", headers={"Accept": "application/json"})
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error_code"] == "CAPACITY_DEGRADED"
    assert payload["authentication_regression"] is False
    assert "passkey" not in payload["error"].lower()
    assert "device" not in payload["error"].lower()


def test_critical_capacity_browser_response_names_storage_and_preserves_data(monkeypatch):
    monkeypatch.setattr(server.capacity_readiness, "capacity_status", lambda *_args, **_kwargs: _critical_capacity())
    response = server.app.test_client().get("/api/auth/oauth/google", headers={"Accept": "text/html"})
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert "Storage capacity is critically low" in body
    assert "account and Vault data remain protected" in body
    assert "passkey" not in body.lower()
