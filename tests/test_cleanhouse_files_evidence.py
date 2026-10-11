import base64
import hashlib
import json
import subprocess
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from vvault.server import cleanhouse_files_evidence as evidence
from vvault.server import vvault_file_repository
from vvault.server import vvault_web_server as server


REPO_ROOT = Path(__file__).resolve().parents[1]


def _body():
    payload = {
        "schema": evidence.BATCH_SCHEMA,
        "events": [{
            "evidence_id": "wazuh:wazuh-manager-alerts:alert-1",
            "created_at": "2026-08-22T00:00:00+00:00",
            "payload": {"provider": "wazuh", "path": "/scope/file.txt"},
        }],
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return payload, body, hashlib.sha256(body).hexdigest()


def _auth_headers(batch_id=None):
    headers = {
        "X-CleanHouse-Key": "chf_v1_dedicated-test-credential-value-that-is-long-enough",
        "X-Chatty-User": "devon@example.com",
        "X-CleanHouse-Instance": "zen-001",
    }
    if batch_id:
        headers.update({"X-CleanHouse-Batch-Id": batch_id, "Idempotency-Key": batch_id})
    return headers


def test_batch_validation_requires_exact_raw_body_digest():
    payload, body, batch_id = _body()
    accepted_batch_id, events = evidence.validate_batch(
        payload, raw_body=body, expected_batch_id=batch_id
    )
    assert accepted_batch_id == batch_id
    assert events[0]["evidence_id"] == payload["events"][0]["evidence_id"]
    with pytest.raises(evidence.CleanHouseEvidenceError, match="digest mismatch"):
        evidence.validate_batch(payload, raw_body=body, expected_batch_id="0" * 64)


def test_manager_alert_feed_is_fim_only_and_replays_from_durable_cursor():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "alerts.json"
        records = [
            {"id": "non-fim", "timestamp": "2026-08-22T00:00:00Z", "data": {}},
            {
                "id": "fim-1",
                "timestamp": "2026-08-22T00:00:01Z",
                "agent": {"id": "001", "name": "zen-001"},
                "data": {"syscheck": {"event": "modified", "path": "/Users/test/scope/file.txt"}},
            },
            {
                "id": "fim-2",
                "timestamp": "2026-08-22T00:00:02Z",
                "agent": {"id": "001", "name": "zen-001"},
                "data": {"syscheck": {"event": "deleted", "path": "/Users/test/scope/old.txt"}},
            },
        ]
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        attestation = {"manager_active": True, "api_authenticated": True, "agent_id": "001"}
        first = evidence.read_wazuh_alerts(
            alerts_path=path, limit=1, agent_id="001", monitored_scope="/Users/test/scope",
            manager_attestation=attestation,
        )
        second = evidence.read_wazuh_alerts(
            alerts_path=path,
            after=first["items"][0]["_vvault_cursor"],
            limit=10,
            agent_id="001",
            monitored_scope="/Users/test/scope",
            manager_attestation=attestation,
        )
    assert [item["_id"] for item in first["items"]] == ["fim-1"]
    assert [item["_id"] for item in second["items"]] == ["fim-2"]


def test_manager_jwt_is_refreshed_once_after_401():
    calls = []
    evidence._WAZUH_TOKEN_CACHE.update({"token": "", "expires_at": 0.0})

    def transport(request):
        calls.append((request.method, request.full_url, request.headers.get("Authorization")))
        if request.full_url.endswith("/security/user/authenticate"):
            token = "stale" if sum("authenticate" in call[1] for call in calls) == 1 else "fresh"
            return {"data": {"token": token}}
        if request.headers.get("Authorization") == "Bearer stale":
            raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, None)
        return {"data": {"affected_items": [{"name": "manager"}]}}

    with patch.dict(evidence.os.environ, {
        "VVAULT_WAZUH_MANAGER_USERNAME": "cleanhouse-ingest",
        "VVAULT_WAZUH_MANAGER_PASSWORD": "secret",
    }):
        payload = evidence.manager_api_request("/manager/info", transport=transport)

    assert payload["data"]["affected_items"][0]["name"] == "manager"
    assert sum("authenticate" in call[1] for call in calls) == 2
    assert calls[-1][2] == "Bearer fresh"


def test_manager_installer_is_pinned_manager_only_and_keeps_api_on_loopback():
    script = (REPO_ROOT / "scripts" / "install-wazuh-manager.sh").read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "scripts" / "vvault-wazuh-manager-install-wrapper.sh").read_text(
        encoding="utf-8"
    )
    bootstrap = (REPO_ROOT / "scripts" / "bootstrap-wazuh-manager-deploy.sh").read_text(
        encoding="utf-8"
    )
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-wazuh-manager.yml").read_text(
        encoding="utf-8"
    )
    assert "wazuh-manager_4.14.7-1_amd64.deb" in script
    assert "f54a48683683fea476b133646c6a2ad884c3d61d0f7d85bf8b0602e127e0e14a6976fc5cf5962cc47de2794fdd0e2abe2f195de1d7a7b9c69da4b79c09a970f7" in script
    assert "host: ['127.0.0.1']" in script
    assert "--resolve localhost:55000:127.0.0.1" in script
    assert "https://127.0.0.1:55000" not in script
    assert "wazuh-indexer wazuh-dashboard filebeat" in script
    assert "docker-ce" not in script
    assert "apt-get install -y \"${stage}/${PACKAGE}\"" in script
    assert "wazuh:wazuh" not in script
    assert "WAZUH_API_BOOTSTRAP_PASSWORD" not in workflow
    assert "sudo -n /bin/bash" not in workflow
    assert "sudo -n /usr/local/libexec/vvault-wazuh-manager-install" in workflow
    assert "deploy ALL=(root) NOPASSWD: VVAULT_WAZUH_MANAGER_INSTALL" in bootstrap
    assert "bootstrap must run from a root-owned private copy" in bootstrap
    assert "reviewed Wazuh deploy wrapper must be a root-owned private copy" in bootstrap
    assert "iptables -I OUTPUT 1 -p tcp -d 127.0.0.1 --dport 55000" in script
    assert "ADMIN_CREDENTIALS_ROTATED=1" in script
    assert "systemctl stop wazuh-manager" in script
    assert "less than 5 GiB is free under /var after bounded Wazuh recovery" in script
    assert "-name 'vd_*.tar' -o -name 'vd_*.tar.xz'" in script
    assert script.index("find /var/ossec/tmp -maxdepth 1") < script.index(
        "less than 5 GiB is free under /var after bounded Wazuh recovery"
    )
    assert script.index('if [[ "${MANAGER_INSTALLED}" -eq 0 ]]') < script.index(
        'curl --fail --silent --show-error --location'
    )
    assert "chown root:wazuh /var/ossec/etc/ossec.conf" in script
    assert script.index("chown root:wazuh /var/ossec/etc/ossec.conf") < script.index(
        "systemctl enable --now wazuh-manager"
    )
    assert 'api_call PUT "/security/users/${user_id}"' in script
    assert "existing cleanhouse-ingest user has no recoverable" not in script
    assert script.index("iptables -I OUTPUT 1") < script.index("apt-get install")
    assert script.index("remove_api_guard", script.index("api_call PUT \"/security/users/${admin_user_id}")) < script.index("install -d -m 0750")
    assert "/bin/bash \"${PRIVATE_INSTALLER}\"" in wrapper
    installer_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
    assert f'EXPECTED_SHA256="{installer_sha256}"' in wrapper
    assert "workflow_dispatch:" in workflow
    assert "branches:\n      - production" in workflow
    assert "VVAULT_DEPLOY_KEY" in workflow
    assert "dpkg-query -W -f='${Version}' wazuh-manager | grep -qx '4.14.7-1'" in workflow


def test_manager_installer_updates_multi_root_wazuh_configuration():
    script = (REPO_ROOT / "scripts" / "install-wazuh-manager.sh").read_text(encoding="utf-8")
    configuration = script.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

    with TemporaryDirectory() as directory:
        path = Path(directory) / "ossec.conf"
        path.write_text(
            """<!-- preserved -->
<ossec_config>
  <global><jsonout_output>no</jsonout_output></global>
  <vulnerability-detection><enabled>yes</enabled><index-status>yes</index-status></vulnerability-detection>
  <indexer><enabled>yes</enabled></indexer>
</ossec_config>
<ossec_config>
  <auth><disabled>no</disabled></auth>
  <cluster><key></key></cluster>
</ossec_config>
""",
            encoding="utf-8",
        )
        configuration = configuration.replace(
            "Path('/var/ossec/etc/ossec.conf')",
            f"Path({str(path)!r})",
            1,
        )
        subprocess.run(["python3", "-c", configuration], check=True)
        updated = path.read_text(encoding="utf-8")

    document = ET.fromstring(
        f"<document>{updated}</document>",
        parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)),
    )
    configs = [node for node in document if node.tag == "ossec_config"]
    assert len(configs) == 2
    assert configs[0].findtext("global/jsonout_output") == "yes"
    assert configs[0].findtext("global/alerts_log") == "yes"
    assert configs[1].findtext("auth/disabled") == "yes"
    assert configs[1].findtext("auth/remote_enrollment") == "no"
    assert configs[0].findtext("vulnerability-detection/enabled") == "no"
    assert configs[0].findtext("vulnerability-detection/index-status") == "no"
    assert configs[0].findtext("indexer/enabled") == "no"
    assert "preserved" in updated
    assert "\n  </ossec_config>" not in updated
    assert updated.count("\n</ossec_config>") == 2
    assert "<key></key>" in updated
    assert "<key />" not in updated


def test_rotated_alert_stream_reports_gap_and_filters_agent_and_scope():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "alerts.json"
        records = [
            {"id": "other", "agent": {"id": "999"}, "data": {"syscheck": {"path": "/Users/test/scope/no"}}},
            {"id": "outside", "agent": {"id": "001"}, "data": {"syscheck": {"path": "/Users/test/other/no"}}},
            {"id": "accepted", "agent": {"id": "001"}, "data": {"syscheck": {"path": "/Users/test/scope/yes"}}},
        ]
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        result = evidence.read_wazuh_alerts(
            alerts_path=path,
            after="wazuh-jsonl.v1:1:2:3",
            agent_id="001",
            monitored_scope="/Users/test/scope",
            manager_attestation={"manager_active": True, "api_authenticated": True, "agent_id": "001"},
        )

    assert [item["_id"] for item in result["items"]] == ["accepted"]
    assert result["gap_state"] == "rotation_or_replacement"
    assert result["agent_id"] == "001"


def test_evidence_route_uses_owner_scoped_repository_receipt():
    payload, body, batch_id = _body()
    receipt = {
        "receipt_id": f"cleanhouse-files:{batch_id}",
        "batch_id": batch_id,
        "accepted_evidence_ids": [payload["events"][0]["evidence_id"]],
    }
    with (
        patch.object(
            server,
            "db_get_user",
            return_value={"id": "11111111-1111-4111-8111-111111111111", "email": "devon@example.com"},
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "verify_cleanhouse_files_credential",
            return_value=True,
        ),
        patch.object(
            server,
            "_cleanhouse_files_owner_context",
            return_value=("11111111-1111-4111-8111-111111111111", "zen-001", None),
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "append_cleanhouse_files_evidence_batch",
            return_value=receipt,
        ) as append,
    ):
        response = server.app.test_client().post(
            "/api/cleanhouse/files/evidence",
            data=body,
            headers={**_auth_headers(batch_id), "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert response.get_json()["receipt_id"] == receipt["receipt_id"]
    assert append.call_args.kwargs["user_id"] == "11111111-1111-4111-8111-111111111111"


def test_pairing_route_returns_only_rsa_encrypted_owner_scoped_credential():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    owner_id = "11111111-1111-4111-8111-111111111111"
    with (
        patch.object(server, "get_current_user", return_value=({
            "id": owner_id,
            "email": "devon@example.com",
            "role": "admin",
        }, "session-token")),
        patch.object(server, "_cleanhouse_files_owner_context", return_value=(owner_id, "zen-001", None)),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "store_cleanhouse_files_credential_hash",
            return_value={"action": "created"},
        ) as store,
    ):
        response = server.app.test_client().post(
            "/api/cleanhouse/files/pair",
            json={"instance_id": "zen-001", "public_key_pem": public_pem},
            headers={"Authorization": "Bearer session-token"},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["credential_type"] == "cleanhouse_files_pairing"
    assert payload["algorithm"] == "RSA-OAEP-3072-SHA256"
    assert "credential" not in payload
    credential = private_key.decrypt(
        base64.b64decode(payload["ciphertext"]),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    ).decode("utf-8")
    assert credential.startswith(evidence.PAIRING_TOKEN_PREFIX)
    assert store.call_args.kwargs["credential_sha256"] == evidence.pairing_token_hash(credential)


def test_pairing_document_loads_before_browser_local_bearer_auth():
    with patch.object(server, "get_current_user") as get_current_user:
        response = server.app.test_client().get("/api/cleanhouse/files/pair")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert response.headers["Cache-Control"] == "no-store"
    assert "script-src 'nonce-" in response.headers["Content-Security-Policy"]
    assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
    document = response.get_data(as_text=True)
    assert "Pair CleanHouse" in document
    assert "localStorage.getItem('vvault_token')" in document
    assert "'Authorization': `Bearer ${token}`" in document
    assert evidence.PAIRING_TOKEN_PREFIX not in document
    assert '"ciphertext"' not in document
    get_current_user.assert_not_called()


def test_pairing_post_without_bearer_or_verified_cookie_stays_fail_closed():
    with (
        patch.object(server, "get_current_user", return_value=(None, None)),
        patch.object(server, "verify_standalone_auth_session_token", return_value=None),
    ):
        response = server.app.test_client().post(
            "/api/cleanhouse/files/pair",
            json={"instance_id": "zen-001", "public_key_pem": "not-a-key"},
        )

    assert response.status_code == 401
    assert response.get_json() == {"success": False, "error": "Authentication required"}


def test_pairing_form_accepts_verified_same_origin_auth_cookie_with_csrf():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    owner_id = "11111111-1111-4111-8111-111111111111"
    csrf_token = "pairing-csrf-token"
    client = server.app.test_client()
    client.set_cookie("cleanhouse_pair_csrf", csrf_token)
    client.set_cookie("auth_sid", "signed-cookie")
    with (
        patch.object(server, "get_current_user", return_value=(None, None)),
        patch.object(
            server,
            "verify_standalone_auth_session_token",
            return_value={"email": "devon@example.com", "name": "Devon"},
        ),
        patch.object(
            server,
            "_ensure_vvault_user",
            return_value={"id": owner_id, "email": "devon@example.com", "role": "admin"},
        ),
        patch.object(server, "_resolve_backend_origin", return_value="https://vvault.thewreck.org"),
        patch.object(
            server,
            "_cleanhouse_files_owner_context",
            return_value=(owner_id, "zen-001", None),
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "store_cleanhouse_files_credential_hash",
            return_value={"action": "created"},
        ),
    ):
        response = client.post(
            "/api/cleanhouse/files/pair",
            data={
                "instance_id": "zen-001",
                "public_key_pem": public_pem,
                "csrf_token": csrf_token,
            },
            headers={
                "Origin": "https://vvault.thewreck.org",
                "Sec-Fetch-Site": "same-origin",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["credential_type"] == "cleanhouse_files_pairing"


def test_pairing_form_rejects_cross_origin_cookie_request():
    client = server.app.test_client()
    client.set_cookie("auth_sid", "signed-cookie")
    with (
        patch.object(server, "get_current_user", return_value=(None, None)),
        patch.object(
            server,
            "verify_standalone_auth_session_token",
            return_value={"email": "devon@example.com", "name": "Devon"},
        ),
        patch.object(
            server,
            "_ensure_vvault_user",
            return_value={"id": "11111111-1111-4111-8111-111111111111", "email": "devon@example.com"},
        ),
        patch.object(server, "_resolve_backend_origin", return_value="https://vvault.thewreck.org"),
    ):
        response = client.post(
            "/api/cleanhouse/files/pair",
            data={"instance_id": "zen-001"},
            headers={"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Same-origin pairing required"


def test_evidence_route_accepts_dedicated_cleanhouse_pairing_credential():
    payload, body, batch_id = _body()
    owner_id = "11111111-1111-4111-8111-111111111111"
    receipt = {
        "receipt_id": f"cleanhouse-files:{batch_id}",
        "batch_id": batch_id,
        "accepted_evidence_ids": [payload["events"][0]["evidence_id"]],
    }
    with (
        patch.object(server, "db_get_user", return_value={"id": owner_id, "email": "devon@example.com"}),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "verify_cleanhouse_files_credential",
            return_value=True,
        ) as verify,
        patch.object(server, "_cleanhouse_files_owner_context", return_value=(owner_id, "zen-001", None)),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "append_cleanhouse_files_evidence_batch",
            return_value=receipt,
        ),
    ):
        response = server.app.test_client().post(
            "/api/cleanhouse/files/evidence",
            data=body,
            headers={
                "X-CleanHouse-Key": "chf_v1_dedicated-test-credential-value-that-is-long-enough",
                "X-Chatty-User": "devon@example.com",
                "X-CleanHouse-Instance": "zen-001",
                "X-CleanHouse-Batch-Id": batch_id,
                "Idempotency-Key": batch_id,
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["receipt_id"] == receipt["receipt_id"]
    assert verify.call_args.kwargs["user_id"] == owner_id


def test_pairing_credential_normalizes_database_uuid_for_owner_scoped_wazuh_routes():
    owner_id = "11111111-1111-4111-8111-111111111111"
    with (
        patch.object(
            server,
            "db_get_user",
            return_value={"id": UUID(owner_id), "email": "devon@example.com"},
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "verify_cleanhouse_files_credential",
            return_value=True,
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "list_construct_file_rows",
            return_value=[{"id": "canonical-zen-001"}],
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "get_cleanhouse_wazuh_enrollment_receipt",
            return_value=None,
        ),
    ):
        response = server.app.test_client().get(
            "/api/cleanhouse/files/wazuh/status",
            headers={
                "X-CleanHouse-Key": "chf_v1_dedicated-test-credential-value-that-is-long-enough",
                "X-Chatty-User": "devon@example.com",
                "X-CleanHouse-Instance": "zen-001",
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["provider"] == "wazuh_manager"
    assert payload["state"] == "unavailable"


def test_repository_stores_only_pairing_hash_and_verifies_constant_time():
    repository = object.__new__(vvault_file_repository.VVaultFileRepository)
    captured = {}
    repository.find_by_path = lambda **_kwargs: None
    repository.upsert = lambda record: captured.setdefault("record", record) or {"action": "created"}
    credential = "chf_v1_repository-test-credential-value-that-is-long-enough"
    credential_sha256 = evidence.pairing_token_hash(credential)
    repository.store_cleanhouse_files_credential_hash(
        user_id="11111111-1111-4111-8111-111111111111",
        callsign="zen-001",
        credential_sha256=credential_sha256,
    )
    stored_content = captured["record"]["content"]
    assert credential not in stored_content
    assert credential_sha256 in stored_content
    repository.find_by_path = lambda **_kwargs: {"content": stored_content}
    assert repository.verify_cleanhouse_files_credential(
        user_id="11111111-1111-4111-8111-111111111111",
        callsign="zen-001",
        credential=credential,
    ) is True
    assert repository.verify_cleanhouse_files_credential(
        user_id="11111111-1111-4111-8111-111111111111",
        callsign="zen-001",
        credential=credential + "wrong",
    ) is False


def test_repository_enrollment_receipt_retry_is_idempotent_without_storing_key():
    owner_id = "11111111-1111-4111-8111-111111111111"
    fingerprint = "a" * 64
    identity = hashlib.sha256(f"{owner_id}:zen-001:zen-001".encode()).hexdigest()
    existing_receipt = {
        "schema": "ovvaults.cleanhouse.wazuh_enrollment.receipt.v1",
        "receipt_id": f"cleanhouse-wazuh-enrollment:{identity}",
        "owner_user_id": owner_id,
        "instance_id": "zen-001",
        "agent_id": "001",
        "agent_name": "zen-001",
        "manager": "vvault.thewreck.org",
        "monitored_scope": "/Users/devon/Documents/GitHub/cleanhouse",
        "key_fingerprint": fingerprint,
        "secret_material_stored": False,
        "created_at": "2026-08-22T00:00:00+00:00",
        "storage_owner": vvault_file_repository.FILE_OWNER,
    }

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO vault_files"):
                self.row = None
            elif normalized.startswith("SELECT content, sha256"):
                content = json.dumps(existing_receipt, sort_keys=True, separators=(",", ":"))
                self.row = {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
            else:
                raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

    repository = object.__new__(vvault_file_repository.VVaultFileRepository)
    connection = Connection()
    repository._connect = lambda: connection
    result = repository.append_cleanhouse_wazuh_enrollment_receipt(
        user_id=owner_id,
        callsign="zen-001",
        agent_id="001",
        agent_name="zen-001",
        manager="vvault.thewreck.org",
        monitored_scope="/Users/devon/Documents/GitHub/cleanhouse",
        key_fingerprint=fingerprint,
    )

    assert result == existing_receipt
    assert result["secret_material_stored"] is False
    assert "client_key" not in result
    assert connection.committed is True


def test_wazuh_routes_fail_honestly_when_manager_evidence_is_unavailable():
    with (
        patch.object(
            server,
            "db_get_user",
            return_value={"id": "11111111-1111-4111-8111-111111111111", "email": "devon@example.com"},
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "verify_cleanhouse_files_credential",
            return_value=True,
        ),
        patch.object(
            server,
            "_cleanhouse_files_owner_context",
            return_value=("11111111-1111-4111-8111-111111111111", "zen-001", None),
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "get_cleanhouse_wazuh_enrollment_receipt",
            return_value={"agent_id": "001", "monitored_scope": "/Users/test/scope"},
        ),
        patch.object(
            server.cleanhouse_files_evidence,
            "manager_attestation",
            return_value={"manager_active": True, "api_authenticated": True, "agent_id": "001"},
        ),
        patch.object(
            server.cleanhouse_files_evidence,
            "read_wazuh_alerts",
            side_effect=evidence.WazuhEvidenceUnavailable("Wazuh manager alert stream is unavailable"),
        ),
    ):
        response = server.app.test_client().get(
            "/api/cleanhouse/files/wazuh/events", headers=_auth_headers()
        )
    assert response.status_code == 503
    assert response.get_json()["state"] == "unavailable"


def test_enrollment_route_is_owner_scoped_idempotent_and_never_persists_client_key():
    owner_id = "11111111-1111-4111-8111-111111111111"
    client_key = "base64-client-key"
    fingerprint = hashlib.sha256(client_key.encode()).hexdigest()
    receipt = {"receipt_id": "cleanhouse-wazuh-enrollment:receipt"}
    with (
        patch.dict(server.os.environ, {
            "VVAULT_WAZUH_AGENT_MANAGER": "vvault.thewreck.org",
            "VVAULT_WAZUH_AGENT_NAME": "zen-001",
        }),
        patch.object(
            server,
            "db_get_user",
            return_value={"id": owner_id, "email": "devon@example.com"},
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "verify_cleanhouse_files_credential",
            return_value=True,
        ),
        patch.object(
            server,
            "_cleanhouse_files_owner_context",
            return_value=(owner_id, "zen-001", None),
        ),
        patch.object(
            server.cleanhouse_files_evidence,
            "create_or_reuse_agent",
            return_value={"agent_id": "001", "agent_name": "zen-001", "client_key": client_key},
        ),
        patch.object(
            server.VAULT_FILE_REPOSITORY,
            "append_cleanhouse_wazuh_enrollment_receipt",
            return_value=receipt,
        ) as append,
    ):
        response = server.app.test_client().post(
            "/api/cleanhouse/files/wazuh/enroll",
            json={
                "instance_id": "zen-001",
                "agent_name": "zen-001",
                "manager": "vvault.thewreck.org",
                "monitored_scope": "/Users/devon/Documents/GitHub/cleanhouse",
            },
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["client_key"] == client_key
    assert payload["key_fingerprint"] == fingerprint
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert "client_key" not in append.call_args.kwargs
    assert append.call_args.kwargs["key_fingerprint"] == fingerprint


class _FakeCursor:
    def __init__(self, *, collision=False):
        self.collision = collision
        self.next_row = None
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if "INSERT INTO vault_files" in normalized and "cleanhouse_file_evidence_receipt" in normalized:
            self.next_row = {"id": "receipt-row"}
        elif "INSERT INTO vault_files" in normalized and "cleanhouse_file_evidence" in normalized:
            self.next_row = None if self.collision else {"id": "event-row", "sha256": params[5]}
        elif "SELECT id::text AS id, sha256" in normalized:
            self.next_row = {"id": "event-row", "sha256": "0" * 64}
        elif "SELECT id::text AS id, content, sha256" in normalized:
            self.next_row = None
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.next_row


class _FakeConnection:
    def __init__(self, *, collision=False):
        self.cursor_instance = _FakeCursor(collision=collision)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


def _event():
    content = '{"payload":{"path":"/scope/file.txt"}}'
    return {
        "evidence_id": "wazuh:event:1",
        "created_at": "2026-08-22T00:00:00+00:00",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def test_repository_appends_events_and_receipt_without_mutable_update():
    repository = object.__new__(vvault_file_repository.VVaultFileRepository)
    connection = _FakeConnection()
    repository._connect = lambda: connection
    receipt = repository.append_cleanhouse_files_evidence_batch(
        user_id="11111111-1111-4111-8111-111111111111",
        callsign="zen-001",
        batch_id="a" * 64,
        events=[_event()],
    )
    assert receipt["accepted_evidence_ids"] == ["wazuh:event:1"]
    assert receipt["storage_owner"] == "ovvaults.vault_files"
    assert connection.committed is True
    assert all(not sql.startswith("UPDATE ") for sql, _params in connection.cursor_instance.statements)


def test_repository_rejects_same_evidence_id_with_different_content():
    repository = object.__new__(vvault_file_repository.VVaultFileRepository)
    repository._connect = lambda: _FakeConnection(collision=True)
    with pytest.raises(ValueError, match="evidence ID collision"):
        repository.append_cleanhouse_files_evidence_batch(
            user_id="11111111-1111-4111-8111-111111111111",
            callsign="zen-001",
            batch_id="b" * 64,
            events=[_event()],
        )
