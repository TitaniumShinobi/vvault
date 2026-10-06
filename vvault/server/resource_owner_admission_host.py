"""Dedicated admission-only direct-mTLS host for VVAULT Milestone 1."""

from __future__ import annotations

import json
import os
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from vvault.server import resource_owner_admission as admission
from vvault.server.resource_owner_admission_repository import (
    RESOURCE_OWNER_ADMISSION_REPOSITORY,
)


ADMISSION_PATH = "/api/v1/resource/owner-admission/resolve"
HEALTH_PATH = "/healthz"
READINESS_PATH = "/readyz"
MAX_BODY_BYTES = 16 * 1024
SOCKET_TIMEOUT_SECONDS = 5


class AdmissionOnlyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], context: ssl.SSLContext, repository: Any):
        self.repository = repository
        super().__init__(address, AdmissionOnlyHandler, bind_and_activate=False)
        self.socket = context.wrap_socket(self.socket, server_side=True)
        self.server_bind()
        self.server_activate()


class AdmissionOnlyHandler(BaseHTTPRequestHandler):
    server_version = "VVAULT-Owner-Admission/1"
    sys_version = ""

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _route_path(self) -> str | None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            return None
        return parsed.path

    def do_GET(self) -> None:  # noqa: N802
        route = self._route_path()
        if route == HEALTH_PATH:
            self._send(200, {"ok": True, "service": "vvault-owner-admission"})
            return
        if route == READINESS_PATH:
            state = admission.trust_readiness(self.server.repository)
            self._send(200 if state["ready"] else 503, {
                "ready": bool(state["ready"]),
                "service": "vvault-owner-admission",
                "contract": admission.CONTRACT,
            })
            return
        self._send(404, {"success": False, "errorCode": "OWNER_ADMISSION_ROUTE_NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self._route_path() != ADMISSION_PATH:
            self._send(404, {"success": False, "errorCode": "OWNER_ADMISSION_ROUTE_NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
            if length < 2 or length > MAX_BODY_BYTES:
                raise admission.OwnerAdmissionError("OWNER_ADMISSION_REQUEST_INVALID", 400)
            self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)
            body = self.rfile.read(length)
            self.close_connection = True
            if len(body) != length:
                raise admission.OwnerAdmissionError("OWNER_ADMISSION_REQUEST_INVALID", 400)
            payload = json.loads(body)
            certificate = self.connection.getpeercert(binary_form=True)
            issuer = admission.authenticate_peer_certificate(certificate)
            verified = admission.validate_request(payload, issuer)
            self._send(200, admission.resolve(verified, self.server.repository))
        except (ValueError, json.JSONDecodeError, socket.timeout):
            self._send(400, {"success": False, "errorCode": "OWNER_ADMISSION_REQUEST_INVALID",
                             "contract": admission.CONTRACT})
        except admission.OwnerAdmissionError as exc:
            self._send(exc.http_status, {"success": False, "errorCode": exc.code,
                                         "contract": admission.CONTRACT})

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, {"success": False, "errorCode": "OWNER_ADMISSION_METHOD_NOT_ALLOWED"})

    do_DELETE = do_PUT
    do_PATCH = do_PUT


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError("OWNER_ADMISSION_HOST_CONFIGURATION_INVALID")
    return value


def create_server(repository: Any = RESOURCE_OWNER_ADMISSION_REPOSITORY) -> AdmissionOnlyServer:
    host = _required("VVAULT_OWNER_ADMISSION_HOST")
    try:
        port = int(_required("VVAULT_OWNER_ADMISSION_PORT"))
    except ValueError as exc:
        raise RuntimeError("OWNER_ADMISSION_HOST_CONFIGURATION_INVALID") from exc
    if not 1 <= port <= 65535 or host in {"0.0.0.0", "::"}:
        raise RuntimeError("OWNER_ADMISSION_HOST_CONFIGURATION_INVALID")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(
        _required("VVAULT_OWNER_ADMISSION_SERVER_CERT_PATH"),
        _required("VVAULT_OWNER_ADMISSION_SERVER_KEY_PATH"),
    )
    context.load_verify_locations(_required("VVAULT_OWNER_ADMISSION_CLIENT_CA_PATH"))
    return AdmissionOnlyServer((host, port), context, repository)


def main() -> None:
    server = create_server()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
