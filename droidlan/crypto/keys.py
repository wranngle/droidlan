"""QR-seed -> symmetric-key derivation.

The PC generates a random 256-bit seed at server start, encodes it into
the QR payload alongside the connection URL, and prints both as a QR
code. The phone scans, decodes the payload, and runs HKDF-SHA256 over
the seed to obtain the same 32-byte AES-GCM key.

Wire format for the QR payload::

    droidlan://<host>:<port>?k=<base64url-seed>

The ``k`` parameter is the seed; ``derive_key()`` is deterministic over
the seed, so both endpoints agree without ever transmitting the key.
"""

from __future__ import annotations

import base64
import secrets
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SEED_BYTES = 32
KEY_BYTES = 32

# Fixed across the protocol so two endpoints derive matching keys.
_HKDF_SALT = b"droidlan.e2ee.v1"
_HKDF_INFO = b"droidlan aes-gcm transfer key"


def generate_seed() -> bytes:
    """Return a cryptographically random 32-byte seed for one session."""
    return secrets.token_bytes(SEED_BYTES)


def derive_key(seed: bytes) -> bytes:
    """HKDF-SHA256 the seed into a 32-byte AES-GCM key.

    Deterministic: same seed -> same key on every endpoint.
    """
    if len(seed) < 16:
        raise ValueError(f"seed must be >= 16 bytes, got {len(seed)}")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return hkdf.derive(seed)


def qr_payload_for_seed(host: str, port: int, seed: bytes) -> str:
    """Encode the QR-printable connect string for a seed."""
    enc = base64.urlsafe_b64encode(seed).rstrip(b"=").decode("ascii")
    return f"droidlan://{host}:{port}?k={enc}"


def seed_from_qr_payload(payload: str) -> bytes:
    """Inverse of :func:`qr_payload_for_seed`.

    Raises ``ValueError`` on a malformed payload so the caller can show
    a "QR scan failed" message instead of decrypting with garbage.
    """
    parsed = urlparse(payload)
    if parsed.scheme != "droidlan":
        raise ValueError(f"expected droidlan:// scheme, got {parsed.scheme!r}")
    params = parse_qs(parsed.query)
    encoded = params.get("k", [None])[0]
    if not encoded:
        raise ValueError("QR payload missing 'k' (seed) parameter")
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"QR seed is not valid base64url: {exc}") from exc
