# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

A small toolkit for moving files between a PC and an Android phone with no working USB port — everything transfers over LAN, not over a cable. Three independent Python scripts cover the bootstrap and ongoing-transfer cases; pick whichever script matches the moment.

## Running the scripts

All three scripts are launched directly with `python <script>.py` from any working directory and run until Ctrl+C. Each accepts `--help` for its full flag set.

| Script | Default port | Purpose | External deps |
|---|---|---|---|
| `sideload_server.py` | 8080 | Serves an APK over HTTP so the phone can install it from its browser. Auto-downloads the latest Primitive FTPd from `wolpi/prim-ftpd` on first run. | `requests` |
| `pc_ftp_server.py` | 2121 (passive 60000–60099) | FTP server on the **PC** that the phone uploads into. Random credentials generated and printed at startup. Anonymous access is off. | `pyftpdlib` |
| `upload_server.py` | 8080 | Browser-form HTTP upload. Stdlib only. | none |

Install third-party deps with `pip install -r requirements.txt` (pins `cryptography`, `pyftpdlib`, `qrcode`, `requests`, `zeroconf`).

Run the test suite with `pytest tests/ -q`. No build step or linter is configured.

## Architecture: the bootstrap → transfer flow

The scripts are designed to be used in sequence the first time, then individually thereafter:

1. **Bootstrap (one-time):** run `sideload_server.py` on the PC. It auto-detects the LAN IP, downloads the latest Primitive FTPd APK if `ftp.apk` isn't already present, and serves the directory over HTTP. The phone visits `http://<pc-ip>:8080/ftp.apk` in its browser to install an FTP client.
2. **Ongoing transfer:** with an FTP/HTTP client on the phone, use `pc_ftp_server.py` (FTP push from phone → PC) or `upload_server.py` (browser upload from phone → PC) to move files. Both deposit into `./incoming/` by default; override with `--dir`.

`sideload_server.py` is the only script that reaches the internet (GitHub releases API + asset download). The other two are LAN-only and listen on `0.0.0.0`.

## Conventions when extending

- **Three independent scripts, not a package.** Each script is self-contained — `get_local_ip()` is duplicated across all three rather than extracted to a shared module, so a user can grab any single file and run it. Don't introduce a shared module without strong reason.
- **`argparse` with sensible defaults, no env vars.** All configuration is via CLI flags. `--help` is the documentation.
- **No hardcoded paths, IPs, or credentials.** LAN IP is auto-detected via the UDP-socket trick in `get_local_ip()`. Directories default to `./incoming` (relative). FTP credentials are random per-run unless overridden.
- **LAN-trust security model.** These scripts assume a trusted network. README's "Security profile" section is the canonical statement; keep it honest if you change defaults.
