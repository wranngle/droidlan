"""End-to-end encryption for droidlan transfers.

The flow: the PC server prints a QR code (round-1 feature) that the phone
scans. The QR payload includes a high-entropy seed; both sides feed the
seed into ``derive_key()`` to obtain the same 256-bit symmetric key.
Files traverse the network as ``Envelope`` frames sealed with AES-GCM, so
anyone sniffing the LAN -- including the access-point operator -- sees
only ciphertext plus a public nonce.

Key derivation: HKDF-SHA256 over the QR seed with a fixed protocol salt
and ``info`` tag, so the same seed yields the same key regardless of
client implementation.
"""

from .envelope import Envelope, WrongKeyError
from .envelope import open as open_envelope
from .envelope import seal
from .keys import (
    derive_key,
    generate_seed,
    qr_payload_for_seed,
    seed_from_qr_payload,
)

__all__ = [
    "derive_key",
    "generate_seed",
    "qr_payload_for_seed",
    "seed_from_qr_payload",
    "Envelope",
    "seal",
    "open_envelope",
    "WrongKeyError",
]
