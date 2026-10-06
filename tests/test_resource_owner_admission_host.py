from __future__ import annotations

import hashlib
import http.client
import json
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from vvault.server import resource_owner_admission as admission
from vvault.server.resource_owner_admission_host import AdmissionOnlyServer


OWNER = "aaaaaaaa-bbbb-4ccc-9ddd-eeeeeeeeeeee"
ISSUER = "https://auth.example.test"


def _authority(common_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                           key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def _leaf(ca_key, ca_cert, common_name, *, server=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    usage = x509.ExtendedKeyUsage([
        x509.oid.ExtendedKeyUsageOID.SERVER_AUTH if server
        else x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH
    ])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                           key_encipherment=True, data_encipherment=False, key_agreement=False,
                           key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(usage, critical=False).sign(ca_key, hashes.SHA256()))
    return key, cert


def _write_pair(root, name, key, cert):
    key_path = root / f"{name}.key"
    cert_path = root / f"{name}.crt"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_path), str(key_path)


class Repo:
    def lookup(self, **_query):
        return {"state": "ACTIVE", "ownerId": OWNER, "policyVersion": "fixture/1"}

    def readiness(self):
        return {"migrationApplied": True, "bindingSourceAvailable": True}


@pytest.fixture
def mtls_host(tmp_path, monkeypatch):
    ca_key, ca_cert = _authority("fixture-ca")
    server_key, server_cert = _leaf(ca_key, ca_cert, "server", server=True)
    client_key, client_cert = _leaf(ca_key, ca_cert, "auth-client")
    unregistered_key, unregistered_cert = _leaf(ca_key, ca_cert, "unregistered-client")
    wrong_ca_key, wrong_ca_cert = _authority("wrong-ca")
    wrong_key, wrong_cert = _leaf(wrong_ca_key, wrong_ca_cert, "wrong-client")
    ca_path = tmp_path / "ca.crt"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    server_paths = _write_pair(tmp_path, "server", server_key, server_cert)
    client_paths = _write_pair(tmp_path, "client", client_key, client_cert)
    unregistered_paths = _write_pair(tmp_path, "unregistered", unregistered_key, unregistered_cert)
    wrong_paths = _write_pair(tmp_path, "wrong", wrong_key, wrong_cert)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*server_paths)
    context.load_verify_locations(str(ca_path))
    context.verify_mode = ssl.CERT_REQUIRED
    fingerprint = hashlib.sha256(client_cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    monkeypatch.setenv("VVAULT_AUTH_ADMISSION_MTLS_IDENTITIES_JSON", json.dumps([
        {"issuer": ISSUER, "certificateSha256": fingerprint}
    ]))
    server = AdmissionOnlyServer(("127.0.0.1", 0), context, Repo())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], str(ca_path), client_paths, unregistered_paths, wrong_paths
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _context(ca, pair=None):
    context = ssl.create_default_context(cafile=ca)
    context.check_hostname = False
    if pair:
        context.load_cert_chain(*pair)
    return context


def _request(port, context, method, path, body=None, headers=None):
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=3)
    encoded = None if body is None else json.dumps(body)
    connection.request(method, path, body=encoded, headers=headers or {"Content-Type": "application/json"})
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def _payload():
    now = int(time.time())
    return {
        "contract": admission.CONTRACT,
        "requestId": "11111111-2222-4333-8444-555555555555",
        "issuer": ISSUER, "subject": "subject-a", "sessionId": "session-a",
        "clientId": "grid-windows", "applicationId": "grid",
        "audience": "https://vvault.thewreck.org", "capabilities": ["workspace:resolve"],
        "issuedAt": now, "expiresAt": now + 30,
    }


def test_valid_workload_and_admission_only_surface(mtls_host):
    port, ca, client, _unregistered, _wrong = mtls_host
    context = _context(ca, client)
    status, payload = _request(port, context, "POST", "/api/v1/resource/owner-admission/resolve", _payload())
    assert status == 200 and payload["admitted"] is True and payload["ownerId"] == OWNER
    status, payload = _request(port, context, "GET", "/api/ready")
    assert status == 404 and payload["errorCode"] == "OWNER_ADMISSION_ROUTE_NOT_FOUND"


def test_no_certificate_wrong_certificate_and_spoofed_headers_fail_tls(mtls_host):
    port, ca, _client, unregistered, wrong = mtls_host
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _request(port, _context(ca), "POST", "/api/v1/resource/owner-admission/resolve", _payload(), {
            "Content-Type": "application/json", "X-SSL-Client-Verify": "SUCCESS",
        })
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        _request(port, _context(ca, wrong), "POST", "/api/v1/resource/owner-admission/resolve", _payload())
    status, payload = _request(
        port, _context(ca, unregistered), "POST",
        "/api/v1/resource/owner-admission/resolve", _payload(),
    )
    assert status == 401 and payload["errorCode"] == "OWNER_ADMISSION_WORKLOAD_UNAUTHORIZED"


def test_malformed_request_and_non_admission_route_are_bounded(mtls_host):
    port, ca, client, _unregistered, _wrong = mtls_host
    context = _context(ca, client)
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=3)
    connection.request("POST", "/api/v1/resource/owner-admission/resolve", body=b"{", headers={"Content-Length": "1"})
    response = connection.getresponse()
    assert response.status == 400
    connection.close()
    status, payload = _request(port, context, "POST", "/api/v1/resource/workspace/resolve", {})
    assert status == 404 and payload["errorCode"] == "OWNER_ADMISSION_ROUTE_NOT_FOUND"
