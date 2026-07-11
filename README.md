<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/droidlan-wordmark-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/brand/droidlan-wordmark-light.png">
  <img alt="droidlan" src="docs/brand/droidlan-wordmark-light.png" width="30%">
</picture>

#### QR launch · zero-config LAN transfer · FTP or browser upload · mDNS discovery · installable PWA

# Point your phone's camera at a QR code. That's the whole setup.

**[Quick start](#-quick-start) | [Three scripts](#-three-scripts-one-flow) | [Security profile](#-security-profile) | [License](#license)**

### 📲 **`python upload_server.py`** and scan the QR it prints

No cloud, no cable, no account, no phone-side app. Everything stays on the LAN.

**❤️ [Sponsor this project](https://github.com/sponsors/wranngle) ❤️**

[![License](https://img.shields.io/github/license/wranngle/droidlan?color=A371F7)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/wranngle/droidlan)](https://github.com/wranngle/droidlan/commits/main)
[![Contributors](https://img.shields.io/github/contributors/wranngle/droidlan)](https://github.com/wranngle/droidlan/graphs/contributors)

[![GitHub stars](https://img.shields.io/github/stars/wranngle/droidlan?style=social)](https://github.com/wranngle/droidlan/stargazers)
[![Follow on GitHub](https://img.shields.io/github/followers/wranngle?style=social)](https://github.com/wranngle)
</div>

---

![Real droidlan session: upload_server.py prints a QR code in the terminal, the served page opens in a browser, files are dropped on the drop zone and land in incoming/ on the PC](docs/hero.webp)

*Browser-side upload; the same QR drives it from a phone.*

Move files between a PC and an Android phone over LAN: zero-config, no cloud,
no cable. Run a script on the PC; it prints a scannable QR code that drops
the phone straight into the right URL. Designed for the case where the
phone's USB port is broken and every other path (Play Store sign-in, ADB,
cloud sync) is more friction than it's worth. Three independent scripts, one
umbrella CLI, 91 tests.

## ⚡ Features

- 📲 **QR launch**: every script prints a scannable QR code in the terminal; the phone camera drops straight into the right URL, no typed IPs.
- 📲 **Browser upload** (`upload_server.py`): the browser the phone already has is the whole client. Drag-and-drop PWA at the root, no-JS fallback at `/basic`, 512 MiB default cap.
- 📲 **FTP receive** (`pc_ftp_server.py`): an FTP server on the PC with random per-run credentials, printed at startup; the QR encodes the full `ftp://` URL for repeat transfers.
- 📲 **APK sideload** (`sideload_server.py`): hosts an APK over plain HTTP so the phone installs an FTP client from its own browser; auto-downloads the latest [Primitive FTPd](https://github.com/wolpi/prim-ftpd) release when none is supplied.
- 📲 **mDNS discovery**: `--mdns <hostname>` on all three scripts broadcasts the server over zeroconf, so the phone resolves `<hostname>.local` instead of an IP.
- 📲 **Installable PWA**: the upload page ships a real service worker and home-screen install; the app shell caches for offline launch, uploads POST straight through.

```mermaid
flowchart LR
    A[PC script prints a QR] --> B[Phone camera scans it]
    B --> C[Browser or FTP client]
    C --> D[incoming/ on the PC]
```

## 🧭 When you'd want this

- Your Android phone has a broken or missing USB port and you still need to install an APK or move files on or off it.
- You'd rather run a 30-second script than set up a cloud sync, install a phone-side app from the Play Store, or fight ADB-over-Wi-Fi.
- You're on the same Wi-Fi network as the phone and don't need anything to leave the LAN.

Everything is LAN-only. The only outbound internet call is the optional first-run download of [Primitive FTPd](https://github.com/wolpi/prim-ftpd) from GitHub.

## 🚀 Quick start

Python 3.9 or newer.

1. `pip install -r requirements.txt` (one time).
2. `python upload_server.py` on the PC.
3. Open the phone camera, point it at the QR code in the terminal, tap the
   notification, pick files in the browser, hit submit.

Files land in `./incoming/` on the PC. No app install, no typing IPs, no
account anywhere. Everything stays on the LAN.

Either clone the repo or just download the three `.py` files. They're independent.

![The terminal after python upload_server.py: the served URL and its scannable QR code](docs/brand/qr-terminal.png)

*The real terminal QR from `upload_server.py`.*

## 🧰 Three scripts, one flow

Each script runs on its own. Pick the one that matches how you want files to move.

### 1. `sideload_server.py`: get an FTP client onto the phone

Pick this one when the phone has no FTP client yet and no working way to install one. It hosts an APK over plain HTTP so the phone installs it from its own browser. On first run with no APK present, it auto-downloads the latest [Primitive FTPd](https://github.com/wolpi/prim-ftpd) release (a free, open-source FTP client/server for Android).

```bash
python sideload_server.py
```

Prints a URL like `http://192.168.1.42:8080/ftp.apk`, plus a QR code that encodes the same URL. Scan it with the phone camera instead of typing. Accept "install from unknown source," done.

Skip this script if you already have an FTP client or any other way to upload from the phone.

Flags: `--port`, `--apk /path/to/anything.apk` (use any APK, not just Primitive FTPd), `--mdns <hostname>` (broadcast the URL over mDNS so the phone can resolve `<hostname>.local` instead of typing an IP).

### 2. `pc_ftp_server.py`: receive files from phone via FTP

Pick this one for repeat transfers: once the phone has an FTP client, you connect a single time and keep moving files without re-scanning anything. It runs an FTP server on the PC that the phone uploads into. Random credentials are generated each run and printed at startup.

```bash
python pc_ftp_server.py
```

Connect from your phone's FTP client to the printed `Host:Port` with the printed `User:Pass`. A QR code encodes the full `ftp://user:pass@host:port/` URL, so a client that supports `ftp://` deep links can open it directly. Files land in `./incoming/`.

Flags: `--port` (default 2121), `--dir`, `--user`, `--password`, `--passive-port-range`, `--mdns <hostname>`.

### 3. `upload_server.py`: receive files from phone via browser

Pick this one when you want zero installs on the phone: the browser it already has is the whole client. The PC serves a browser upload form; the phone scans a QR code, picks files, and submits.

```bash
python upload_server.py
```

Phone scans the QR (or visits the printed URL), picks file(s), submits. Files land in `./incoming/`. The root URL serves the installable PWA shell (drag-and-drop, offline shell, home-screen install); `/basic` keeps a no-JS fallback form for ancient browsers.

Flags: `--port` (default 8080), `--dir`, `--max-bytes` (default 512 MiB), `--mdns <hostname>`.

![The served PWA upload shell: dark page, green dashed drop zone, file picker and submit button](docs/brand/pwa-landing.png)

*The served PWA shell in a desktop browser.*

## 📦 What you can fling across the LAN

<table>
<tr>
<td align="center" width="33%"><b>Camera roll</b><br/>photos and videos off the phone, no cloud round trip</td>
<td align="center" width="33%"><b>APKs</b><br/>onto a phone whose USB port is dead</td>
<td align="center" width="33%"><b>Documents</b><br/>PDFs, downloads, exports, anything the file picker shows</td>
</tr>
<tr>
<td align="center" width="33%"><b>Folders</b><br/>repeat transfers through the phone's FTP client</td>
<td align="center" width="33%"><b>Big files</b><br/>512 MiB browser-upload cap by default, raise it with <code>--max-bytes</code></td>
<td align="center" width="33%"><b>...anything the phone can share</b><br/>it's all bytes over the LAN</td>
</tr>
</table>

Named examples are file types, not integrations; droidlan receives whatever the phone's share sheet or file picker hands it.

## 🔒 Security profile

These scripts assume a trusted LAN. Both `pc_ftp_server.py` and `upload_server.py` listen on `0.0.0.0`, so anyone on the same network who can reach your PC's IP can upload to the configured directory while the server is running. The FTP server uses random credentials by default, but FTP itself is plaintext. Anyone sniffing the LAN sees the password and the file contents. Don't run any of this on public Wi-Fi.

## ⭐ Star history

<!--
Restore this line when api.star-history.com recovers from its outage:
[![Star History Chart](https://api.star-history.com/svg?repos=wranngle/droidlan&type=Date)](https://www.star-history.com/#wranngle/droidlan&Date)
-->

[![GitHub stars](https://img.shields.io/github/stars/wranngle/droidlan?style=social)](https://www.star-history.com/#wranngle/droidlan&Date)

[**View the interactive star history**](https://www.star-history.com/#wranngle/droidlan&Date), drawn live even while star-history's image API is down.

## License

MIT, see [LICENSE](LICENSE).
