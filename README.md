# droidlan

**Point your phone's camera at a QR code. That's the whole setup.**

Move files between a PC and an Android phone over LAN: zero-config, no cloud,
no cable. Run a script on the PC; it prints a scannable QR code that drops
the phone straight into the right URL. Designed for the case where the
phone's USB port is broken and every other path (Play Store sign-in, ADB,
cloud sync) is more friction than it's worth.

## First user moment

1. `pip install pyftpdlib requests qrcode` (one time).
2. `python upload_server.py` on the PC.
3. Open the phone camera, point it at the QR code in the terminal, tap the
   notification, pick files in the browser, hit submit.

Files land in `./incoming/` on the PC. No app install, no typing IPs, no
account anywhere. Everything stays on the LAN.

## When you'd want this

- Your Android phone has a broken or missing USB port and you still need to install an APK or move files on or off it.
- You'd rather run a 30-second script than set up a cloud sync, install a phone-side app from the Play Store, or fight ADB-over-Wi-Fi.
- You're on the same Wi-Fi network as the phone and don't need anything to leave the LAN.

Everything is LAN-only. The only outbound internet call is the optional first-run download of [Primitive FTPd](https://github.com/wolpi/prim-ftpd) from GitHub.

## Install

Python 3.9 or newer.

```bash
pip install pyftpdlib requests qrcode
```

Then either clone the repo or just download the three `.py` files. They're independent.

## Three scripts, one flow

### 1. `sideload_server.py`: get an FTP client onto the phone

Hosts an APK over plain HTTP so the phone can install it from its browser. On first run with no APK present, auto-downloads the latest [Primitive FTPd](https://github.com/wolpi/prim-ftpd) release (a free, open-source FTP client/server for Android).

```bash
python sideload_server.py
```

Prints a URL like `http://192.168.1.42:8080/ftp.apk`, plus a QR code that
encodes the same URL. Scan it with the phone camera instead of typing.
Accept "install from unknown source," done.

Skip this script if you already have an FTP client or any other way to upload from the phone.

Flags: `--port`, `--apk /path/to/anything.apk` (use any APK, not just Primitive FTPd).

### 2. `pc_ftp_server.py`: receive files from phone via FTP

Runs an FTP server on the PC that the phone uploads into. Random credentials are generated each run and printed at startup.

```bash
python pc_ftp_server.py
```

Connect from your phone's FTP client to the printed `Host:Port` with the printed `User:Pass`. A QR code encodes the full `ftp://user:pass@host:port/`
URL so a client that supports `ftp://` deep links can open it directly.
Files land in `./incoming/`.

Flags: `--port` (default 2121), `--dir`, `--user`, `--password`, `--passive-port-range`.

### 3. `upload_server.py`: receive files from phone via browser

Browser-form HTTP upload. Useful if you don't want to install an FTP client at all. Just point the phone's camera at the QR code and submit.

```bash
python upload_server.py
```

Phone scans the QR (or visits the printed URL), picks file(s), submits. Files land in `./incoming/`.

Flags: `--port` (default 8080), `--dir`, `--max-bytes` (default 512 MiB).

## Security profile

These scripts assume a trusted LAN. Both `pc_ftp_server.py` and `upload_server.py` listen on `0.0.0.0`, so anyone on the same network who can reach your PC's IP can upload to the configured directory while the server is running. The FTP server uses random credentials by default, but FTP itself is plaintext. Anyone sniffing the LAN sees the password and the file contents. Don't run any of this on public Wi-Fi.

## License

MIT, see [LICENSE](LICENSE).
