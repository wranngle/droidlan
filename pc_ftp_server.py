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


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


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
    args = parser.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    upload_dir = args.dir.resolve()
    password = args.password or secrets.token_urlsafe(9)

    passive_lo, passive_hi = (int(p) for p in args.passive_port_range.split("-", 1))

    authorizer = DummyAuthorizer()
    authorizer.add_user(args.user, password, str(upload_dir), perm="elradfmw")

    handler = FTPHandler
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
    print(f"Uploads land in: {upload_dir}")
    print()
    print("Press Ctrl+C to stop.")
    print()

    FTPServer(("0.0.0.0", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
