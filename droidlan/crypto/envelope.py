"""AES-GCM envelope: the on-wire encrypted frame.

Wire format (big-endian, single contiguous byte string)::

    | magic (4B = b"DLE1") | flags (1B) | filename_len (2B) |
    | nonce (12B) | filename (UTF-8, filename_len bytes) |
    | ciphertext (variable) || tag (16B, appended by AES-GCM)

``magic`` lets a receiver reject foreign payloads cheaply. ``nonce`` is
random per envelope (96-bit, AES-GCM standard). ``filename`` rides
inside the authenticated-data field so a tampered name fails open.

Decryption with the wrong key raises :class:`WrongKeyError` (a subclass
of ``ValueError``); callers should surface a "QR mismatch / replay
attempt" message instead of writing the garbled output to disk.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"DLE1"
NONCE_BYTES = 12
_HEADER_FMT = ">4sBH"  # magic, flags, filename_len
_HEADER_LEN = struct.calcsize(_HEADER_FMT)
_FLAGS_DEFAULT = 0x00


class WrongKeyError(ValueError):
    """Raised when the AES-GCM auth tag fails to verify."""


@dataclass(frozen=True)
class Envelope:
    """Decoded view of a sealed transfer frame."""

    filename: str
    nonce: bytes
    ciphertext: bytes  # includes the 16-byte GCM tag

    def to_bytes(self) -> bytes:
        fname = self.filename.encode("utf-8")
        if len(fname) > 0xFFFF:
            raise ValueError("filename exceeds 65535 bytes")
        if len(self.nonce) != NONCE_BYTES:
            raise ValueError(f"nonce must be {NONCE_BYTES} bytes")
        header = struct.pack(_HEADER_FMT, MAGIC, _FLAGS_DEFAULT, len(fname))
        return header + self.nonce + fname + self.ciphertext

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Envelope":
        if len(raw) < _HEADER_LEN + NONCE_BYTES:
            raise ValueError("envelope too short for header")
        magic, _flags, fname_len = struct.unpack(_HEADER_FMT, raw[:_HEADER_LEN])
        if magic != MAGIC:
            raise ValueError(f"bad magic: expected {MAGIC!r}, got {magic!r}")
        nonce = raw[_HEADER_LEN : _HEADER_LEN + NONCE_BYTES]
        fname_start = _HEADER_LEN + NONCE_BYTES
        fname_end = fname_start + fname_len
        if len(raw) < fname_end + 16:
            raise ValueError("envelope truncated before ciphertext+tag")
        filename = raw[fname_start:fname_end].decode("utf-8")
        ciphertext = raw[fname_end:]
        return cls(filename=filename, nonce=nonce, ciphertext=ciphertext)


def seal(key: bytes, filename: str, plaintext: bytes) -> Envelope:
    """Encrypt ``plaintext`` under ``key`` into a fresh envelope.

    The filename is bound into AES-GCM's associated-data slot, so any
    tampering with it during transit causes :func:`open` to raise
    :class:`WrongKeyError`.
    """
    if len(key) != 32:
        raise ValueError(f"key must be 32 bytes (got {len(key)})")
    nonce = secrets.token_bytes(NONCE_BYTES)
    aead = AESGCM(key)
    aad = filename.encode("utf-8")
    ciphertext = aead.encrypt(nonce, plaintext, aad)
    return Envelope(filename=filename, nonce=nonce, ciphertext=ciphertext)


def open(key: bytes, envelope: Envelope) -> bytes:  # noqa: A001 — mirrors `seal`
    """Verify+decrypt ``envelope`` under ``key``.

    Raises :class:`WrongKeyError` if the key, nonce, ciphertext, or
    bound filename has been tampered with.
    """
    if len(key) != 32:
        raise ValueError(f"key must be 32 bytes (got {len(key)})")
    aead = AESGCM(key)
    aad = envelope.filename.encode("utf-8")
    try:
        return aead.decrypt(envelope.nonce, envelope.ciphertext, aad)
    except InvalidTag as exc:
        raise WrongKeyError("AES-GCM auth tag verification failed") from exc
