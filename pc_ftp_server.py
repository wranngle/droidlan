#!/usr/bin/env python3
"""FTP server on the PC; the phone uploads files into a local directory.

Random credentials are generated and printed at startup. Anonymous access
is disabled by default; pass --user/--password to use stable credentials.
"""

import argparse
import secrets
import socket
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from mdns import register as mdns_register
from qr import print_qr
from upload_server import ProgressTracker, render_progress


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class _ProgressWriter:
    """File-like wrapper that ticks a ProgressTracker on every write().

    pyftpdlib opens the destination file via `self.fs.open()` then hands the
    file object to its data channel, which calls `.write(bytes)` per inbound
    chunk. Wrapping that file object lets us tap the byte stream without
    forking pyftpdlib's STOR implementation.
    """

    def __init__(self, fd, tracker: ProgressTracker):
        self._fd = fd
        self._tracker = tracker

    def write(self, data: bytes) -> int:
        n = self._fd.write(data)
        # Some file objects return None from write(); treat that as len(data).
        bytes_written = n if isinstance(n, int) else len(data)
        self._tracker.update(bytes_written)
        return bytes_written

    def __getattr__(self, name):
        return getattr(self._fd, name)


class ProgressFTPHandler(FTPHandler):
    """FTPHandler that prints a per-file progress bar during STOR uploads.

    After the standard `ftp_STOR` opens the destination file and hands it to
    the data channel, we splice in a `_ProgressWriter` wrapper so each
    inbound chunk ticks the ProgressTracker. The client-advertised size
    arrives via the optional ALLO command; when absent, ProgressTracker
    still prints byte counts but the bar stays parked at 0% until close.
    """

    def ftp_STOR(self, file, mode="w"):  # noqa: D401 - matches superclass
        result = super().ftp_STOR(file, mode)
        total = int(getattr(self, "_pending_allo_size", 0) or 0)
        tracker = ProgressTracker(
            total=total,
            on_update=lambda done, t, pct: render_progress(
                Path(file).name, done, t, pct),
            label=Path(file).name,
        )
        if self.data_channel is not None and self.data_channel.file_obj is not None:
            self.data_channel.file_obj = _ProgressWriter(
                self.data_channel.file_obj, tracker)
        return result

    def on_file_received(self, file: str) -> None:
        print()
        super().on_file_received(file)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2121,
                        help="FTP control port (default: 2121)")
    parser.add_argument("--dir", type=Path, default=Path("incoming"),
                        help="Directory where uploads land (default: ./incoming)")
    parser.add_argument("--user", default="phone",
                        help="FTP username (default: phone)")
    parser.add_argument("--password", default=None,
                        help="FTP password (default: random 12-character token)")
    parser.add_argument("--passive-port-range", default="60000-60099",
                        help="Passive-mode port range (default: 60000-60099)")
    parser.add_argument("--mdns", default=None,
                        help="Broadcast over mDNS as the given hostname (e.g. droidlan-ftp.local)")
    args = parser.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    upload_dir = args.dir.resolve()
    password = args.password or secrets.token_urlsafe(9)

    passive_lo, passive_hi = (int(p) for p in args.passive_port_range.split("-", 1))

    authorizer = DummyAuthorizer()
    authorizer.add_user(args.user, password, str(upload_dir), perm="elradfmw")

    handler = ProgressFTPHandler
    handler.authorizer = authorizer
    handler.passive_ports = range(passive_lo, passive_hi + 1)

    ip = get_local_ip()
    print("=" * 50)
    print("PC FTP SERVER")
    print("=" * 50)
    print()
    print("Connect from your phone's FTP client:")
    print()
    print(f"  Host: {ip}")
    print(f"  Port: {args.port}")
    print(f"  User: {args.user}")
    print(f"  Pass: {password}")
    print()
    print("Or scan this QR code with your phone's FTP client:")
    print()
    print_qr(f"ftp://{args.user}:{password}@{ip}:{args.port}/")
    print()
    print(f"Uploads land in: {upload_dir}")
    print()
    print("Press Ctrl+C to stop.")
    print()

    broadcast = None
    if args.mdns:
        broadcast = mdns_register(args.mdns, args.port, service="ftp", ip=ip)
        print(f"mDNS: broadcasting as {args.mdns} on {ip}:{args.port}")
    try:
        FTPServer(("0.0.0.0", args.port), handler).serve_forever()
    finally:
        if broadcast is not None:
            broadcast.unregister()


if __name__ == "__main__":
    main()
