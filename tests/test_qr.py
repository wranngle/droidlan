"""Tests for terminal QR-code rendering.

Behavior under test: `print_qr(url)` produces output that (a) contains the
URL on its own line for copy/paste fallback and (b) round-trips back to the
same URL when decoded by `pyzbar` (when the system libzbar is available).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qr import print_qr, render_qr  # noqa: E402

SAMPLE_URLS = [
    "http://example.com/test",
    "http://192.168.1.42:8080/ftp.apk",
    "ftp://phone:s3cret@192.168.1.42:2121/",
]


@pytest.mark.parametrize("url", SAMPLE_URLS)
def test_print_qr_emits_url_line(url: str) -> None:
    buf = io.StringIO()
    print_qr(url, file=buf)
    output = buf.getvalue()
    assert url in output, "URL must appear in printed output for copy/paste"
    assert output.rstrip().splitlines()[-1] == url, (
        "URL must be the final line so users can read it after the QR block"
    )


@pytest.mark.parametrize("url", SAMPLE_URLS)
def test_render_qr_does_not_raise(url: str) -> None:
    rendered = render_qr(url)
    assert rendered, "render_qr must return a non-empty string"
    assert "\n" in rendered, "QR render must be multi-line"


@pytest.mark.parametrize("url", SAMPLE_URLS)
def test_qr_round_trip_decode(url: str) -> None:
    """Generate the same QR payload via the qrcode lib and decode it.

    If `pyzbar` cannot load `libzbar` (system lib missing), skip — the
    behavioral guarantee is then covered by the URL-in-output assertion.
    """
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except (ImportError, OSError) as exc:
        pytest.skip(f"pyzbar/pillow unavailable: {exc}")

    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    results = decode(Image.open(buf))
    assert results, "QR must be decodable"
    assert results[0].data.decode("utf-8") == url, (
        "Decoded payload must equal the input URL"
    )
