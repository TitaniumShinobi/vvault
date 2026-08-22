import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from vvault.server import cleanhouse_files_evidence as evidence
from vvault.server import vvault_file_repository
from vvault.server import vvault_web_server as server


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
        "X-Chatty-Key": "test-service-token",
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
                "data": {"syscheck": {"event": "modified", "path": "/scope/file.txt"}},
            },
            {
                "id": "fim-2",
                "timestamp": "2026-08-22T00:00:02Z",
                "data": {"syscheck": {"event": "deleted", "path": "/scope/old.txt"}},
            },
        ]
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        first = evidence.read_wazuh_alerts(alerts_path=path, limit=1)
        second = evidence.read_wazuh_alerts(
            alerts_path=path,
            after=first["items"][0]["_vvault_cursor"],
            limit=10,
        )
    assert [item["_id"] for item in first["items"]] == ["fim-1"]
    assert [item["_id"] for item in second["items"]] == ["fim-2"]


def test_evidence_route_uses_owner_scoped_repository_receipt():
    payload, body, batch_id = _body()
    receipt = {
        "receipt_id": f"cleanhouse-files:{batch_id}",
        "batch_id": batch_id,
        "accepted_evidence_ids": [payload["events"][0]["evidence_id"]],
    }
    with (
        patch.dict(server.os.environ, {"VVAULT_SERVICE_TOKEN": "test-service-token"}),
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


def test_wazuh_routes_fail_honestly_when_manager_evidence_is_unavailable():
    with (
        patch.dict(server.os.environ, {"VVAULT_SERVICE_TOKEN": "test-service-token"}),
        patch.object(
            server,
            "_cleanhouse_files_owner_context",
            return_value=("11111111-1111-4111-8111-111111111111", "zen-001", None),
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
