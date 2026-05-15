"""PWA companion: installable offline-capable transfer UI.

Exposes :func:`register` which wires a `/pwa/` route into a stdlib
``http.server.BaseHTTPRequestHandler`` subclass by intercepting GET
requests whose path starts with ``/pwa``. The handler returns the
static shell (``index.html``, ``manifest.webmanifest``, ``sw.js``).
The existing upload POST endpoint is reused — the PWA submits to ``/``.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

_EXTRA_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".js": "application/javascript",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def resolve(path: str) -> Path | None:
    """Map a URL path under ``/pwa/`` to a static file, or ``None`` if absent.

    Strips ``/pwa`` prefix and defaults to ``index.html`` for ``/pwa`` or
    ``/pwa/``. Rejects traversal: the resolved file must live under
    :data:`STATIC_DIR`.
    """
    rel = path[len("/pwa"):].lstrip("/")
    if not rel:
        rel = "index.html"
    target = (STATIC_DIR / rel).resolve()
    if STATIC_DIR not in target.parents and target != STATIC_DIR / rel:
        if not str(target).startswith(str(STATIC_DIR)):
            return None
    if not target.is_file():
        return None
    return target


def content_type_for(target: Path) -> str:
    suffix = target.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(target.name)
    return guessed or "application/octet-stream"


def serve(handler) -> bool:
    """Serve the PWA asset for ``handler.path``. Return True if handled."""
    if not handler.path.startswith("/pwa"):
        return False
    target = resolve(handler.path)
    if target is None:
        handler.send_error(404, "PWA asset not found")
        return True
    body = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-type", content_type_for(target))
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)
    return True


def manifest_dict() -> dict:
    """Return the parsed manifest (helper for tests + tooling)."""
    return json.loads((STATIC_DIR / "manifest.webmanifest").read_text("utf-8"))
