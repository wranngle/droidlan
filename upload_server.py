#!/usr/bin/env python3
"""HTTP upload server. Phone visits the URL in a browser, picks file(s),
submits. Uploads land in a local directory.
"""

import argparse
import http.server
import re
import socket
import socketserver
import sys
import time
from pathlib import Path

from mdns import register as mdns_register
from qr import print_qr

HTML_FORM = """<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Upload to PC</title>
    <style>
        body {{ font-family: sans-serif; padding: 20px; font-size: 18px; }}
        input[type=file] {{ font-size: 18px; margin: 20px 0; }}
        input[type=submit] {{ font-size: 24px; padding: 15px 30px;
                              background: #4CAF50; color: white; border: none; }}
        h1 {{ color: #333; }}
        .success {{ color: green; font-size: 24px; }}
        .error {{ color: #c62828; font-size: 20px; }}
    </style>
</head>
<body>
    <h1>Upload files to PC</h1>
    <form method="POST" enctype="multipart/form-data">
        <input type="file" name="file" multiple><br><br>
        <input type="submit" value="UPLOAD">
    </form>
    {message}
</body>
</html>
"""

UPLOAD_DIR: Path
MAX_BYTES: int

# Chunk size for streaming reads. Small enough that a 100MB transfer
# produces hundreds of updates, large enough not to syscall-thrash.
PROGRESS_CHUNK = 64 * 1024  # 64 KiB


class ProgressTracker:
    """Throttle byte-count callbacks to ~one per `step_pct` of total.

    Fires the `on_update(bytes_done, total, pct)` callback the first time
    `update()` crosses each percentage threshold (every `step_pct`% of
    `total`), plus a final 100% update on completion. A 100MB transfer
    with the default 10% step yields 11 updates (0% start, 10%, 20%, ...,
    100% complete) — comfortably above the ≥10 spec floor.

    The class is intentionally state-machine-only so it can be unit-tested
    without I/O.
    """

    def __init__(self, total: int, on_update, step_pct: int = 10,
                 label: str = ""):
        if total < 0:
            raise ValueError("total must be >= 0")
        if not 1 <= step_pct <= 100:
            raise ValueError("step_pct must be in [1, 100]")
        self.total = total
        self.on_update = on_update
        self.step_pct = step_pct
        self.label = label
        self.bytes_done = 0
        # Next percentage threshold we will fire on.
        self._next_threshold = 0
        self._fired_complete = False

    def update(self, n: int) -> None:
        """Advance by `n` bytes; fire `on_update` on threshold crossings."""
        if n < 0:
            raise ValueError("n must be >= 0")
        self.bytes_done += n
        # Don't exceed total in the reported value; some transports
        # over-report on the final chunk.
        capped = min(self.bytes_done, self.total) if self.total else self.bytes_done
        pct = int(capped * 100 / self.total) if self.total else 100
        while pct >= self._next_threshold and self._next_threshold <= 100:
            self.on_update(capped, self.total, self._next_threshold)
            self._next_threshold += self.step_pct
        if capped >= self.total and not self._fired_complete:
            if self._next_threshold <= 100:
                self.on_update(capped, self.total, 100)
            self._fired_complete = True

    def finish(self) -> None:
        """Force a terminal 100% callback if one has not yet fired."""
        if not self._fired_complete:
            self.on_update(self.bytes_done, self.total, 100)
            self._fired_complete = True


def render_progress(label: str, bytes_done: int, total: int, pct: int,
                    stream=None) -> None:
    """Print a single in-place progress line.

    Uses \\r so the line repaints rather than scrolls. Falls back to
    plain newline-terminated lines when stdout is not a TTY (CI, pipes)
    so the output stays readable in logs.
    """
    if stream is None:
        stream = sys.stdout
    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = "#" * filled + "-" * (bar_width - filled)
    line = f"[{bar}] {pct:3d}%  {bytes_done}/{total}  {label}"
    if stream.isatty():
        stream.write("\r" + line)
        if pct >= 100:
            stream.write("\n")
    else:
        stream.write(line + "\n")
    stream.flush()


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def safe_filename(raw: str) -> str:
    # Strip any directory components so a malicious client can't traverse out.
    return Path(raw).name or "upload"


class UploadHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_FORM.format(message="").encode("utf-8"))

    def _read_with_progress(self, content_length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = content_length
        tracker = ProgressTracker(
            total=content_length,
            on_update=lambda done, total, pct: render_progress(
                "upload", done, total, pct),
            label="upload",
        )
        while remaining > 0:
            chunk = self.rfile.read(min(PROGRESS_CHUNK, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            tracker.update(len(chunk))
        tracker.finish()
        return b"".join(chunks)

    def do_POST(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return

        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return
        if content_length <= 0:
            self.send_error(400, "Empty upload")
            return
        if content_length > MAX_BYTES:
            self.send_error(413, f"Upload exceeds {MAX_BYTES}-byte limit")
            return

        boundary_match = re.search(r"boundary=([^;]+)", content_type)
        if not boundary_match:
            self.send_error(400, "Missing multipart boundary")
            return

        boundary = boundary_match.group(1).strip().strip('"').encode()
        body = self._read_with_progress(content_length)

        saved: list[str] = []
        for part in body.split(b"--" + boundary):
            fn_match = re.search(rb'filename="([^"]+)"', part)
            if not fn_match:
                continue
            filename = safe_filename(fn_match.group(1).decode("utf-8", "replace"))
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            content = part[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if not content:
                continue
            (UPLOAD_DIR / filename).write_bytes(content)
            saved.append(filename)
            print(f"Received: {filename} ({len(content)} bytes)")

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        if saved:
            msg = f'<p class="success">Uploaded: {", ".join(saved)}</p>'
        else:
            msg = '<p class="error">No file selected.</p>'
        self.wfile.write(HTML_FORM.format(message=msg).encode("utf-8"))


def main() -> None:
    global UPLOAD_DIR, MAX_BYTES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP port (default: 8080)")
    parser.add_argument("--dir", type=Path, default=Path("incoming"),
                        help="Directory where uploads land (default: ./incoming)")
    parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024,
                        help="Reject uploads larger than this (default: 512 MiB)")
    parser.add_argument("--mdns", default=None,
                        help="Broadcast over mDNS as the given hostname (e.g. droidlan-upload.local)")
    args = parser.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR = args.dir.resolve()
    MAX_BYTES = args.max_bytes

    ip = get_local_ip()
    print("=" * 50)
    print("HTTP UPLOAD SERVER")
    print("=" * 50)
    print()
    upload_url = f"http://{ip}:{args.port}"
    print(f"On your phone, open: {upload_url}")
    print()
    print("Or scan this QR code with your phone's camera:")
    print()
    print_qr(upload_url)
    print()
    print(f"Uploads land in: {UPLOAD_DIR}")
    print()
    print("Press Ctrl+C to stop.")

    broadcast = None
    if args.mdns:
        broadcast = mdns_register(args.mdns, args.port, service="http", ip=ip)
        print(f"mDNS: broadcasting as {args.mdns} on {ip}:{args.port}")

    with socketserver.TCPServer(("", args.port), UploadHandler) as httpd:
        try:
            httpd.serve_forever()
        finally:
            if broadcast is not None:
                broadcast.unregister()


if __name__ == "__main__":
    main()
