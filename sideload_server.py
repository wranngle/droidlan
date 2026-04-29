#!/usr/bin/env python3
"""Local OTA sideloading station.

Hosts an APK file over plain HTTP so a phone with a broken or missing USB
port can install it from its browser. If no APK is present, it auto-fetches
the latest Primitive FTPd release from GitHub.
"""

import argparse
import http.server
import os
import socket
import socketserver
import sys
from pathlib import Path

import requests

DEFAULT_APK_NAME = "ftp.apk"
PRIMITIVE_FTPD_RELEASES_API = "https://api.github.com/repos/wolpi/prim-ftpd/releases/latest"


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def download_primitive_ftpd(target: Path) -> bool:
    """Download the latest Primitive FTPd APK from GitHub releases."""
    print("Fetching latest Primitive FTPd release from GitHub...")
    try:
        response = requests.get(
            PRIMITIVE_FTPD_RELEASES_API,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        response.raise_for_status()
        release = response.json()
        print(f"Found release: {release.get('tag_name', 'unknown')}")

        # Prefer a universal APK over arch-specific ones (smaller, broader support).
        apk_asset = None
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".apk") and (apk_asset is None or "arm64" not in name):
                apk_asset = asset
        if not apk_asset:
            print("ERROR: No APK found in release assets")
            return False

        print(f"Downloading: {apk_asset['name']} ({apk_asset['size'] // 1024} KB)")
        apk_response = requests.get(apk_asset["browser_download_url"], timeout=60, stream=True)
        apk_response.raise_for_status()
        with target.open("wb") as f:
            for chunk in apk_response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Saved as: {target}")
        return True
    except requests.RequestException as exc:
        print(f"Download failed: {exc}")
        return False


def run_server(serve_dir: Path, apk_name: str, port: int, ip: str) -> None:
    os.chdir(serve_dir)
    url = f"http://{ip}:{port}/{apk_name}"
    apk_size_kb = (serve_dir / apk_name).stat().st_size // 1024

    print("=" * 60)
    print()
    print("  LOCAL OTA SIDELOADING STATION")
    print("  " + "-" * 29)
    print()
    print("  Type this URL on your Android device:")
    print()
    print(f"  >>>  {url}  <<<")
    print()
    print("=" * 60)
    print()
    print(f"  Serving from: {serve_dir}")
    print(f"  File size: {apk_size_kb} KB")
    print()
    print("  Press Ctrl+C to stop the server")
    print()

    with socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP port (default: 8080)")
    parser.add_argument("--apk", type=Path, default=script_dir / DEFAULT_APK_NAME,
                        help=f"APK file to serve (default: ./{DEFAULT_APK_NAME})")
    args = parser.parse_args()

    apk_path: Path = args.apk.resolve()

    print("=" * 50)
    print("LOCAL OTA SIDELOADING STATION")
    print("=" * 50)
    print()

    ip = get_local_ip()
    print(f"Local IP: {ip}")
    if ip == "127.0.0.1":
        print("WARNING: could not determine LAN IP. Check your network connection.")

    if not apk_path.exists():
        apk_path.parent.mkdir(parents=True, exist_ok=True)
        if not download_primitive_ftpd(apk_path):
            print()
            print("Auto-download failed. Get an APK manually from")
            print("https://github.com/wolpi/prim-ftpd/releases/latest")
            print(f"and save it to {apk_path}, or pass --apk /path/to/your.apk.")
            sys.exit(1)
    else:
        print(f"APK already present: {apk_path}")

    print()
    run_server(apk_path.parent, apk_path.name, args.port, ip)


if __name__ == "__main__":
    main()
