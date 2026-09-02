#!/usr/bin/env python3
import argparse
import http.client
import mimetypes
import socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit


class VVaultFrontendProxy(BaseHTTPRequestHandler):
    backend_host = "localhost"
    backend_port = 8000
    public_origin = "http://localhost:7784"
    dist_dir = Path("dist")
    repo_dir = Path(".")

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def _handle(self):
        if self.path.startswith("/api/"):
            self._proxy_api()
            return
        self._serve_static()

    def _proxy_api(self):
        self._proxy_to(self.backend_host, self.backend_port, self.path)

    def _proxy_to(self, host, port, path):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        headers["Host"] = f"{host}:{port}"
        headers["X-Forwarded-Host"] = urlsplit(self.public_origin).netloc
        headers["X-Forwarded-Proto"] = urlsplit(self.public_origin).scheme
        forwarded_for = headers.get("X-Forwarded-For")
        client_ip = self.client_address[0] if self.client_address else ""
        if client_ip:
            headers["X-Forwarded-For"] = f"{forwarded_for}, {client_ip}" if forwarded_for else client_ip
        if body:
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection(host, port, timeout=30)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding"}:
                    self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except OSError as exc:
            self._send_backend_unavailable(exc)
        finally:
            conn.close()

    def _send_backend_unavailable(self, exc):
        import json
        payload = json.dumps({
            "success": False,
            "error": "vvault_backend_unavailable",
            "message": f"VVAULT backend is not listening at http://{self.backend_host}:{self.backend_port}",
            "detail": exc.__class__.__name__,
        }).encode("utf-8")
        self.send_response(503, "VVAULT BACKEND UNAVAILABLE")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self):
        raw_path = urlsplit(self.path).path
        relative = raw_path.lstrip("/") or "index.html"
        candidate = self._static_candidate(relative)

        if candidate is None:
            candidate = self.dist_dir.resolve() / "index.html"

        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _static_candidate(self, relative):
        dist_root = self.dist_dir.resolve()
        candidate = (dist_root / relative).resolve()
        if str(candidate).startswith(str(dist_root)) and candidate.is_file():
            return candidate

        repo_root = self.repo_dir.resolve()
        roots = []
        if relative.startswith("assets/"):
            roots.append((repo_root, relative))
        if relative.startswith("assets/legal/"):
            roots.append((repo_root / "html", Path(relative).name))
        if relative in {
            "vvault-terms.html",
            "vvault-privacy.html",
            "vvault-eeccd.html",
            "terms-of-service.html",
            "privacy-notice.html",
            "european-electronic-communications-code-disclosure.html",
        }:
            roots.append((repo_root / "html", relative))

        for root, path in roots:
            root = root.resolve()
            candidate = (root / path).resolve()
            if str(candidate).startswith(str(root)) and candidate.is_file():
                return candidate
        return None

    def log_message(self, fmt, *args):
        return


class DualStackThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="::")
    parser.add_argument("--port", type=int, default=7784)
    parser.add_argument("--backend-host", default="localhost")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--public-origin", default="")
    parser.add_argument("--dist", default="dist")
    args = parser.parse_args()

    VVaultFrontendProxy.backend_host = args.backend_host
    VVaultFrontendProxy.backend_port = args.backend_port
    VVaultFrontendProxy.public_origin = args.public_origin or f"http://localhost:{args.port}"
    VVaultFrontendProxy.dist_dir = Path(args.dist)
    VVaultFrontendProxy.repo_dir = Path(args.dist).resolve().parent

    server_cls = DualStackThreadingHTTPServer if ":" in args.host else ThreadingHTTPServer
    server = server_cls((args.host, args.port), VVaultFrontendProxy)
    print(
        f"VVAULT frontend proxy listening on {args.host}:{args.port} as {VVaultFrontendProxy.public_origin}/",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
