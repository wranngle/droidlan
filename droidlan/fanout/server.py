"""HTTP fanout endpoints layered onto the round-1 upload server.

Routes:
    POST /fanout/register          -> {"client_id": "..."}
    POST /fanout/unregister        body: client_id=...
    GET  /fanout/pull?client_id=X  -> 204 on timeout, 200 + headers + body otherwise
    POST /fanout/broadcast         multipart/form-data: file=<binary>
    GET  /fanout/clients           -> {"count": N, "ids": [...]}

The broadcast path mirrors the round-1 upload handler shape (multipart) so
mDNS-discovered clients (PR #2) can use the same upload contract.
"""

from __future__ import annotations

import http.server
import json
import re
import socketserver
import threading
from typing import Optional

from .registry import ClientRegistry, FanoutPayload


def _parse_multipart_file(body: bytes, content_type: str) -> Optional[tuple[str, bytes]]:
    boundary_match = re.search(r"boundary=([^;]+)", content_type)
    if not boundary_match:
        return None
    boundary = boundary_match.group(1).strip().strip('"').encode()
    for part in body.split(b"--" + boundary):
        fn_match = re.search(rb'filename="([^"]+)"', part)
        if not fn_match:
            continue
        filename = fn_match.group(1).decode("utf-8", "replace")
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        content = part[header_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        if not content:
            continue
        return filename, content
    return None


def make_handler(registry: ClientRegistry, pull_timeout: float = 5.0):
    class FanoutHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Silence default access log; round-1 PR #4 owns structured logging.
            return

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path == "/fanout/register":
                client_id = registry.register()
                self._json(200, {"client_id": client_id})
                return

            if self.path == "/fanout/unregister":
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                match = re.search(r"client_id=([^&\s]+)", body)
                if match:
                    registry.unregister(match.group(1))
                self._json(200, {"ok": True})
                return

            if self.path == "/fanout/broadcast":
                content_type = self.headers.get("Content-Type", "")
                length = int(self.headers.get("Content-Length") or 0)
                if "multipart/form-data" not in content_type or length <= 0:
                    self._json(400, {"error": "expected multipart/form-data"})
                    return
                body = self.rfile.read(length)
                parsed = _parse_multipart_file(body, content_type)
                if parsed is None:
                    self._json(400, {"error": "no file part"})
                    return
                filename, content = parsed
                payload = FanoutPayload.of(filename, content)
                delivered = registry.broadcast(payload)
                self._json(200, {
                    "delivered": delivered,
                    "sha256": payload.sha256,
                    "bytes": len(content),
                })
                return

            self.send_error(404)

        def do_GET(self) -> None:
            if self.path.startswith("/fanout/pull"):
                match = re.search(r"client_id=([^&\s]+)", self.path)
                if not match:
                    self._json(400, {"error": "client_id required"})
                    return
                payload = registry.pull(match.group(1), timeout=pull_timeout)
                if payload is None:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Filename", payload.filename)
                self.send_header("X-SHA256", payload.sha256)
                self.send_header("Content-Length", str(len(payload.content)))
                self.end_headers()
                self.wfile.write(payload.content)
                return

            if self.path == "/fanout/clients":
                self._json(200, {
                    "count": registry.count(),
                    "ids": registry.client_ids(),
                })
                return

            self.send_error(404)

    return FanoutHandler


# Public alias for tests / docs.
FanoutHandler = make_handler


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "0.0.0.0", port: int = 0,
          registry: ClientRegistry | None = None,
          pull_timeout: float = 5.0) -> tuple[_ThreadingServer, threading.Thread, ClientRegistry]:
    """Start the fanout server on a background thread; return (server, thread, registry).

    port=0 binds an ephemeral port (tests inspect `server.server_address[1]`).
    """
    reg = registry or ClientRegistry()
    handler = make_handler(reg, pull_timeout=pull_timeout)
    server = _ThreadingServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, reg
