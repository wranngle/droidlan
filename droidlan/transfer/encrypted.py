"""File-level helpers that seal/open a path under a derived key.

These are thin enough to inline at the server route, but pulled out so
unit tests cover the full round-trip without spinning up an HTTP stack.
"""

from __future__ import annotations

from pathlib import Path

from ..crypto import open_envelope, seal
from ..crypto.envelope import Envelope


def seal_file(key: bytes, path: Path) -> bytes:
    """Read ``path`` and return the serialized AES-GCM envelope bytes."""
    plaintext = Path(path).read_bytes()
    envelope = seal(key, Path(path).name, plaintext)
    return envelope.to_bytes()


def open_to_path(key: bytes, raw: bytes, dest_dir: Path) -> Path:
    """Decrypt ``raw`` under ``key`` and write to ``dest_dir/<filename>``.

    Returns the path written. Raises ``WrongKeyError`` on tag failure;
    in that case nothing is written.
    """
    envelope = Envelope.from_bytes(raw)
    plaintext = open_envelope(key, envelope)
    dest = Path(dest_dir) / envelope.filename
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plaintext)
    return dest
