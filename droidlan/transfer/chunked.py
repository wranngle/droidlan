"""Chunked file transfer with checkpoint-based resume.

Wire format (stdlib http.server / urllib only):

    POST /upload?name=<safe-name>
        Content-Range: bytes <start>-<end>/<total>
        X-Droidlan-Sha256: <hex>          # final chunk only; optional
        <body = raw bytes of this chunk>

    -> 200 + JSON {"received": <offset>, "complete": false}
    -> 201 + JSON {"received": <total>, "complete": true, "sha256": "..."}
    -> 409 + JSON {"received": <server-side-offset>}  on offset mismatch
    -> 422 + JSON {"error": "sha256 mismatch"}        on final-hash mismatch

    GET /resume?name=<safe-name>
    -> 200 + JSON {"received": <offset>, "complete": <bool>}

State on disk (under `--state-dir`, defaults to upload-dir):

    <name>.part         # partial bytes, appended in order
    <name>.ckpt         # checkpoint JSON (offset, total, started_at)

On final-chunk success, `<name>.part` is renamed to `<name>` and the
checkpoint file is removed. Resume is therefore the natural state of any
half-finished `<name>.part` + `<name>.ckpt` pair.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import socket
import socketserver
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

CHUNK_SIZE_DEFAULT = 1 * 1024 * 1024  # 1 MiB

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class Checkpoint:
    name: str
    total: int
    offset: int
    started_at: float
    sha256_expected: Optional[str] = None


def _log_event(action: str, **fields: object) -> None:
    """Emit one ECS-shaped JSON line to the droidlan log channel.

    Mirrors the channel used by `log.py` (PR #4) so when that lands the
    chunked-transfer events flow through the same file unchanged.
    """
    path = Path(os.environ.get("DROIDLAN_LOG_PATH", "logs/droidlan.jsonl"))
    record = {
        "@timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "event": {
            "kind": "event",
            "action": action,
            "dataset": "droidlan.transfer",
        },
        "service": {"name": "chunked_transfer"},
    }
    for k, v in fields.items():
        if v is not None:
            record[k] = v
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        # Logging never breaks a transfer; silently drop on disk errors.
        pass


def safe_name(raw: str) -> str:
    """Reject anything that could escape the state directory."""
    if not raw:
        raise ValueError("empty name")
    # Reject any path separator or parent-dir token outright rather than
    # silently rewriting via Path().name — surprise is the enemy here.
    if "/" in raw or "\\" in raw or raw == ".." or raw.startswith("../"):
        raise ValueError(f"unsafe name: {raw!r}")
    if not _SAFE_NAME.match(raw):
        raise ValueError(f"unsafe name: {raw!r}")
    return raw


def sha256_file(path: Path, buf: int = 64 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def _ckpt_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.ckpt"


def _part_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.part"


def load_checkpoint(state_dir: Path, name: str) -> Optional[Checkpoint]:
    p = _ckpt_path(state_dir, name)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return Checkpoint(**raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def save_checkpoint(state_dir: Path, ckpt: Checkpoint) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = _ckpt_path(state_dir, ckpt.name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(ckpt), separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(p)


def finalize_transfer(state_dir: Path,
                      final_dir: Path,
                      name: str,
                      expected_sha256: Optional[str] = None) -> tuple[Path, str]:
    """Move `<name>.part` -> `<final_dir>/<name>`, hash-verify, drop checkpoint.

    Returns (final_path, sha256_hex). Raises ValueError on hash mismatch.
    """
    part = _part_path(state_dir, name)
    final_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(part)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError(
            f"sha256 mismatch for {name}: got {digest}, "
            f"expected {expected_sha256}"
        )
    final_path = final_dir / name
    part.replace(final_path)
    ckpt = _ckpt_path(state_dir, name)
    if ckpt.exists():
        ckpt.unlink()
    return final_path, digest


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class ChunkedTransferHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler bound by `make_handler(state_dir, final_dir)`."""

    state_dir: Path = Path(".")
    final_dir: Path = Path(".")

    # Silence default access log; transfer events flow through _log_event.
    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/resume":
            self._json(404, {"error": "not found"})
            return
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            name = safe_name((qs.get("name") or [""])[0])
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        ckpt = load_checkpoint(self.state_dir, name)
        if ckpt is None:
            # No checkpoint == fresh transfer expected from offset 0.
            self._json(200, {"received": 0, "complete": False})
            return
        self._json(200, {
            "received": ckpt.offset,
            "complete": ckpt.offset >= ckpt.total,
            "total": ckpt.total,
        })

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/upload":
            self._json(404, {"error": "not found"})
            return
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            name = safe_name((qs.get("name") or [""])[0])
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        crange = self.headers.get("Content-Range", "")
        m = re.match(r"^bytes\s+(\d+)-(\d+)/(\d+)$", crange)
        if not m:
            self._json(400, {"error": "missing or malformed Content-Range"})
            return
        start, end, total = (int(x) for x in m.groups())
        # end is inclusive per RFC 7233; chunk length therefore is end-start+1.
        chunk_len = end - start + 1
        if chunk_len <= 0 or total <= 0 or end >= total:
            self._json(400, {"error": "invalid range"})
            return

        ckpt = load_checkpoint(self.state_dir, name) or Checkpoint(
            name=name, total=total, offset=0, started_at=time.time()
        )
        if ckpt.total != total:
            self._json(409, {"error": "total size changed",
                             "received": ckpt.offset})
            return
        if start != ckpt.offset:
            _log_event("error",
                       transfer={"name": name, "expected_offset": ckpt.offset,
                                 "got_offset": start})
            self._json(409, {"received": ckpt.offset,
                             "error": "offset mismatch"})
            return

        body = self._read_exact(chunk_len)
        if body is None:
            self._json(400, {"error": "short body"})
            return

        part = _part_path(self.state_dir, name)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        mode = "ab" if part.exists() and ckpt.offset > 0 else "wb"
        with part.open(mode) as f:
            f.write(body)

        ckpt.offset = end + 1
        expected_sha = self.headers.get("X-Droidlan-Sha256")
        if expected_sha:
            ckpt.sha256_expected = expected_sha
        save_checkpoint(self.state_dir, ckpt)

        if ckpt.offset >= ckpt.total:
            try:
                final_path, digest = finalize_transfer(
                    self.state_dir, self.final_dir, name,
                    expected_sha256=ckpt.sha256_expected,
                )
            except ValueError as exc:
                _log_event("error", transfer={"name": name},
                           error={"message": str(exc)})
                self._json(422, {"error": str(exc)})
                return
            _log_event("complete",
                       transfer={"name": name, "bytes": ckpt.total,
                                 "sha256": digest,
                                 "path": str(final_path)})
            self._json(201, {"received": ckpt.total, "complete": True,
                             "sha256": digest})
            return

        _log_event("chunk",
                   transfer={"name": name, "offset": ckpt.offset,
                             "total": ckpt.total})
        self._json(200, {"received": ckpt.offset, "complete": False})

    def _read_exact(self, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            piece = self.rfile.read(n - len(buf))
            if not piece:
                return None
            buf.extend(piece)
        return bytes(buf)


def make_handler(state_dir: Path,
                 final_dir: Path) -> type[ChunkedTransferHandler]:
    """Bind state/final directories into a handler class."""
    class _Bound(ChunkedTransferHandler):
        pass
    _Bound.state_dir = state_dir
    _Bound.final_dir = final_dir
    return _Bound


class ThreadingHTTPServer(socketserver.ThreadingMixIn,
                          http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(state_dir: Path,
          final_dir: Path,
          host: str = "",
          port: int = 0) -> ThreadingHTTPServer:
    """Build a ready-to-serve server. Caller runs `.serve_forever()`."""
    handler_cls = make_handler(state_dir, final_dir)
    return ThreadingHTTPServer((host, port), handler_cls)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def upload_in_chunks(src: Path,
                     server_url: str,
                     name: Optional[str] = None,
                     chunk_size: int = CHUNK_SIZE_DEFAULT,
                     max_chunks: Optional[int] = None,
                     on_chunk: Optional[Callable[[int, int], None]] = None,
                     verify_sha256: bool = True) -> dict:
    """Upload `src` in chunks to a `ChunkedTransferHandler` at `server_url`.

    - Queries `GET /resume?name=...` first to pick up an existing offset.
    - Sends chunks via `POST /upload?name=...` with `Content-Range`.
    - On the final chunk, includes `X-Droidlan-Sha256` so the server
      hashes the assembled file and compares.
    - `max_chunks` caps how many chunks this call will send (used by tests
      to simulate a mid-transfer interruption without OS-level network
      kills). Callers can simply invoke `upload_in_chunks` a second time
      with no `max_chunks` to finish the job.

    Returns the parsed JSON body from the server's response to the last
    chunk sent (or to the resume probe, if nothing remained to send).

    Stdlib only — no `requests`.
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(src)
    name = safe_name(name or src.name)
    total = src.stat().st_size
    if total == 0:
        raise ValueError("cannot upload empty file")

    # Probe server for current offset.
    probe_url = f"{server_url.rstrip('/')}/resume?name={urllib.parse.quote(name)}"
    with urllib.request.urlopen(probe_url, timeout=10) as resp:
        probe = json.loads(resp.read().decode("utf-8"))
    start_offset = int(probe.get("received", 0))
    _log_event("start" if start_offset == 0 else "resume",
               transfer={"name": name, "offset": start_offset,
                         "total": total})

    sha_hex = sha256_file(src) if verify_sha256 else None

    sent_chunks = 0
    last_response: dict = probe
    offset = start_offset
    with src.open("rb") as f:
        f.seek(offset)
        while offset < total:
            if max_chunks is not None and sent_chunks >= max_chunks:
                break
            chunk = f.read(chunk_size)
            if not chunk:
                break
            chunk_end = offset + len(chunk) - 1  # inclusive
            is_final = chunk_end + 1 >= total
            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {offset}-{chunk_end}/{total}",
                "Content-Length": str(len(chunk)),
            }
            if is_final and sha_hex:
                headers["X-Droidlan-Sha256"] = sha_hex
            req = urllib.request.Request(
                f"{server_url.rstrip('/')}/upload?name={urllib.parse.quote(name)}",
                data=chunk, method="POST", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                last_response = json.loads(resp.read().decode("utf-8"))
            offset = int(last_response.get("received", chunk_end + 1))
            sent_chunks += 1
            if on_chunk is not None:
                on_chunk(offset, total)
    return last_response


# ---------------------------------------------------------------------------
# Module-level helpers exported for tests / ad-hoc CLI use.
# ---------------------------------------------------------------------------


def iter_chunks(src: Path,
                chunk_size: int = CHUNK_SIZE_DEFAULT) -> Iterable[bytes]:
    with src.open("rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def main() -> None:  # pragma: no cover - convenience CLI
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--state-dir", type=Path, default=Path("incoming"))
    s.add_argument("--final-dir", type=Path, default=Path("incoming"))
    s.add_argument("--port", type=int, default=8080)

    c = sub.add_parser("send")
    c.add_argument("src", type=Path)
    c.add_argument("--url", default="http://127.0.0.1:8080")
    c.add_argument("--chunk-size", type=int, default=CHUNK_SIZE_DEFAULT)

    args = p.parse_args()
    if args.cmd == "serve":
        srv = serve(args.state_dir, args.final_dir, port=args.port)
        host = socket.gethostbyname(socket.gethostname())
        print(f"Resumable upload server on http://{host}:{args.port}")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.shutdown()
    elif args.cmd == "send":
        result = upload_in_chunks(args.src, args.url,
                                  chunk_size=args.chunk_size)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
