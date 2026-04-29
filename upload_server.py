#!/usr/bin/env python3
"""HTTP upload server. Phone visits the URL in a browser, picks file(s),
submits. Uploads land in a local directory.
"""

import argparse
import http.server
import re
import socket
import socketserver
from pathlib import Path

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
        body = self.rfile.read(content_length)

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
    args = parser.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR = args.dir.resolve()
    MAX_BYTES = args.max_bytes

    ip = get_local_ip()
    print("=" * 50)
    print("HTTP UPLOAD SERVER")
    print("=" * 50)
    print()
    print(f"On your phone, open: http://{ip}:{args.port}")
    print()
    print(f"Uploads land in: {UPLOAD_DIR}")
    print()
    print("Press Ctrl+C to stop.")

    with socketserver.TCPServer(("", args.port), UploadHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
