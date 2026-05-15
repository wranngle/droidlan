"""Contract tests for the /pwa/ companion route.

What the user actually cares about: when they open `http://<pc-ip>:8080/pwa/`
on their phone, the browser sees a real installable PWA. That means:

  - `/pwa/manifest.webmanifest` is reachable, JSON-valid, and carries the
    keys a browser uses to decide installability (name, start_url, display,
    icons).
  - `/pwa/sw.js` is served as JavaScript so the browser will register it as
    a service worker.
  - `/pwa/` itself returns the shell HTML with the upload form.
  - Traversal attempts (`/pwa/../upload_server.py`) do NOT leak source.

Server is the real `upload_server.py` on an ephemeral port — we don't mock
the system under test.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import upload_server


REQUIRED_MANIFEST_KEYS = ("name", "start_url", "display", "icons")


@pytest.fixture()
def server():
    """Start upload_server.UploadHandler on an ephemeral port; tear down after."""
    import socketserver

    tmp = tempfile.mkdtemp(prefix="droidlan-pwa-test-")
    upload_server.UPLOAD_DIR = Path(tmp).resolve()
    upload_server.MAX_BYTES = 1024 * 1024

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), upload_server.UploadHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.2).read()
            break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.02)

    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1.0)


def _get(port: int, path: str):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0)


def test_manifest_served_with_correct_content_type_and_required_keys(server):
    resp = _get(server, "/pwa/manifest.webmanifest")
    assert resp.status == 200
    ctype = resp.headers.get("Content-Type", "")
    assert "manifest" in ctype or "json" in ctype, ctype
    manifest = json.loads(resp.read().decode("utf-8"))
    for key in REQUIRED_MANIFEST_KEYS:
        assert key in manifest, f"manifest missing {key!r}: {manifest}"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"].startswith("/pwa")
    assert isinstance(manifest["icons"], list) and len(manifest["icons"]) >= 1
    sizes = {icon.get("sizes") for icon in manifest["icons"]}
    assert {"192x192", "512x512"}.issubset(sizes), sizes


def test_service_worker_served_as_javascript(server):
    resp = _get(server, "/pwa/sw.js")
    assert resp.status == 200
    ctype = resp.headers.get("Content-Type", "")
    assert "javascript" in ctype, ctype
    body = resp.read().decode("utf-8")
    assert "addEventListener" in body, "sw.js does not look like a service worker"


def test_index_returns_shell_with_upload_form(server):
    for path in ("/pwa/", "/pwa/index.html"):
        resp = _get(server, path)
        assert resp.status == 200, path
        ctype = resp.headers.get("Content-Type", "")
        assert "text/html" in ctype, ctype
        body = resp.read().decode("utf-8")
        assert "<title>" in body
        assert 'action="/"' in body, "shell must POST to existing upload endpoint"
        assert "manifest.webmanifest" in body, "shell must link the manifest"
        assert "/pwa/sw.js" in body, "shell must register the service worker"


def test_icons_served_as_png(server):
    for size in (192, 512):
        resp = _get(server, f"/pwa/icon-{size}.png")
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/png"
        body = resp.read()
        assert body.startswith(b"\x89PNG\r\n\x1a\n"), "not a valid PNG signature"


def test_path_traversal_is_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/pwa/../upload_server.py")
    assert exc.value.code == 404


def test_unrelated_paths_still_serve_upload_form(server):
    resp = _get(server, "/")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "Upload files to PC" in body, "root must still serve the legacy form"
