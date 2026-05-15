"""End-to-end encryption: contract tests.

These assert the central promise of the feature: a file sealed under a
QR-derived key round-trips byte-identical on the right key, and decryption
fails on a wrong key. Everything else (KDF, wire format, AAD binding) is
verified through that lens, not as isolated property tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from droidlan.crypto import (
    Envelope,
    WrongKeyError,
    derive_key,
    generate_seed,
    open_envelope,
    qr_payload_for_seed,
    seal,
    seed_from_qr_payload,
)
from droidlan.crypto.envelope import MAGIC
from droidlan.transfer.encrypted import open_to_path, seal_file


# ---------- key derivation ----------------------------------------------------


def test_derive_key_is_deterministic_for_a_seed() -> None:
    seed = b"\x01" * 32
    assert derive_key(seed) == derive_key(seed)
    assert len(derive_key(seed)) == 32


def test_different_seeds_produce_different_keys() -> None:
    assert derive_key(b"\x01" * 32) != derive_key(b"\x02" * 32)


def test_qr_payload_round_trip() -> None:
    seed = generate_seed()
    payload = qr_payload_for_seed("192.168.1.50", 8443, seed)
    assert payload.startswith("droidlan://192.168.1.50:8443?k=")
    assert seed_from_qr_payload(payload) == seed


def test_seed_from_qr_payload_rejects_foreign_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        seed_from_qr_payload("https://example.com?k=abc")


def test_seed_from_qr_payload_rejects_missing_key_param() -> None:
    with pytest.raises(ValueError, match="missing"):
        seed_from_qr_payload("droidlan://host:1?other=1")


# ---------- envelope wire format ---------------------------------------------


def test_envelope_starts_with_magic_so_strangers_can_be_rejected() -> None:
    key = derive_key(generate_seed())
    env = seal(key, "photo.jpg", b"hello bytes")
    raw = env.to_bytes()
    assert raw.startswith(MAGIC)


def test_envelope_round_trips_through_bytes() -> None:
    key = derive_key(generate_seed())
    env = seal(key, "report.pdf", b"\x00\x01\x02\x03" * 256)
    rebuilt = Envelope.from_bytes(env.to_bytes())
    assert rebuilt == env


# ---------- the central promise: plaintext round-trip ------------------------


def test_plaintext_round_trip_on_matching_key(tmp_path: Path) -> None:
    seed = generate_seed()
    payload = qr_payload_for_seed("10.0.0.5", 8080, seed)

    # Sender side (PC).
    sender_key = derive_key(seed)
    src = tmp_path / "souvenir.bin"
    plaintext = os.urandom(1024 * 64)  # 64 KiB
    src.write_bytes(plaintext)
    raw = seal_file(sender_key, src)

    # Receiver side (phone) — only the QR payload travels between them.
    receiver_seed = seed_from_qr_payload(payload)
    receiver_key = derive_key(receiver_seed)
    dest_dir = tmp_path / "incoming"
    written = open_to_path(receiver_key, raw, dest_dir)

    assert written == dest_dir / "souvenir.bin"
    assert written.read_bytes() == plaintext


# ---------- attacker-middle: wrong key MUST fail -----------------------------


def test_decryption_fails_with_wrong_key(tmp_path: Path) -> None:
    good_key = derive_key(generate_seed())
    bad_key = derive_key(generate_seed())
    assert good_key != bad_key

    src = tmp_path / "secret.txt"
    src.write_bytes(b"the QR was for someone else")
    raw = seal_file(good_key, src)

    with pytest.raises(WrongKeyError):
        open_to_path(bad_key, raw, tmp_path / "intruder")
    # And nothing leaked to disk under the attacker's path.
    assert not (tmp_path / "intruder" / "secret.txt").exists()


def test_tampered_ciphertext_fails(tmp_path: Path) -> None:
    key = derive_key(generate_seed())
    env = seal(key, "x.bin", b"original payload")
    raw = bytearray(env.to_bytes())
    raw[-1] ^= 0x01  # flip last byte of the GCM tag
    with pytest.raises(WrongKeyError):
        open_envelope(key, Envelope.from_bytes(bytes(raw)))


def test_tampered_filename_fails(tmp_path: Path) -> None:
    key = derive_key(generate_seed())
    env = seal(key, "real.bin", b"original payload")
    tampered = Envelope(
        filename="fake.bin", nonce=env.nonce, ciphertext=env.ciphertext
    )
    with pytest.raises(WrongKeyError):
        open_envelope(key, tampered)
