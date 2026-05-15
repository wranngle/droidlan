#!/usr/bin/env python3
"""Generate three deterministic test JPEGs with hand-crafted EXIF DateTime tags.

We avoid Pillow on purpose: the binner's parser is stdlib-only, and the
fixtures should not need a third-party dep to be regenerated. Each output
is a syntactically valid baseline JPEG with a minimal APP1 EXIF segment
whose DateTimeOriginal tag holds the date encoded in the filename.

This file is run manually when fixtures need regenerating. It is not part
of the test suite — the committed .jpg files are the source of truth.
"""

import struct
from pathlib import Path

SAMPLES = [
    ("photo-2023-04.jpg", "2023:04:15 09:30:00"),
    ("photo-2024-11.jpg", "2024:11:02 14:22:10"),
    ("photo-2025-01.jpg", "2025:01:31 23:59:59"),
]

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
APP1_MARKER = b"\xff\xe1"
SOS_MARKER = b"\xff\xda"
DQT_MARKER = b"\xff\xdb"
SOF0_MARKER = b"\xff\xc0"
DHT_MARKER = b"\xff\xc4"


def build_exif_segment(datetime_str: str) -> bytes:
    """Return the full APP1 segment bytes (marker + length + payload)."""
    assert len(datetime_str) == 19, f"datetime must be 19 chars: {datetime_str!r}"
    ascii_value = datetime_str.encode("ascii") + b"\x00"  # NUL-terminated

    # TIFF header (big-endian) + IFD0 with two entries: DateTime and an
    # ExifIFDPointer to a sub-IFD that has DateTimeOriginal. We use the
    # top-level DateTime tag too so even a minimal parser finds it.
    bo = b"MM"  # big-endian
    magic = struct.pack(">H", 0x002A)
    # Layout offsets are all relative to the start of the TIFF header.
    # IFD0 starts at offset 8.
    ifd0_offset = struct.pack(">I", 8)

    # IFD0: 2 entries (DateTime, ExifIFDPointer) + next-IFD offset (0).
    # Each entry is 12 bytes. ASCII values longer than 4 bytes are stored
    # out-of-line; our 20-byte datetime string lives after IFD0 + sub-IFD.
    #
    # Layout from TIFF header start:
    #   0..7   : header
    #   8      : IFD0 count (2 entries)
    #   10..21 : entry 1 (DateTime, ASCII, 20 bytes, offset=46)
    #   22..33 : entry 2 (ExifIFDPointer, LONG, 1, value=38)
    #   34..37 : next IFD offset (0)
    #   38     : Sub-IFD count (1 entry)
    #   40..51 : sub-entry (DateTimeOriginal, ASCII, 20 bytes, offset=66)
    #   52..55 : next IFD offset (0)
    #   56..75 : (DateTime value, 20 bytes)   <-- 46 if we collapse layout
    # Simpler: put both ASCII values consecutively after the sub-IFD and
    # let the offsets reflect that.

    ifd0_count = struct.pack(">H", 2)
    # Datetime ASCII string (20 bytes incl NUL) lives at offset 60.
    entry_datetime = (
        struct.pack(">H", 0x0132)        # tag: DateTime
        + struct.pack(">H", 2)           # type: ASCII
        + struct.pack(">I", 20)          # count: 20 bytes
        + struct.pack(">I", 60)          # value offset
    )
    entry_exif_ptr = (
        struct.pack(">H", 0x8769)        # tag: ExifIFDPointer
        + struct.pack(">H", 4)           # type: LONG
        + struct.pack(">I", 1)           # count: 1
        + struct.pack(">I", 38)          # value: offset to sub-IFD
    )
    next_ifd0 = struct.pack(">I", 0)

    sub_ifd_count = struct.pack(">H", 1)
    sub_entry_dto = (
        struct.pack(">H", 0x9003)        # tag: DateTimeOriginal
        + struct.pack(">H", 2)           # ASCII
        + struct.pack(">I", 20)          # 20 bytes
        + struct.pack(">I", 80)          # value offset (60+20)
    )
    next_sub = struct.pack(">I", 0)

    # Now assemble:
    tiff = (
        bo + magic + ifd0_offset                 # 0..7
        + ifd0_count                             # 8..9
        + entry_datetime                         # 10..21
        + entry_exif_ptr                         # 22..33
        + next_ifd0                              # 34..37
        + sub_ifd_count                          # 38..39
        + sub_entry_dto                          # 40..51
        + next_sub                               # 52..55
    )
    # Pad to offset 60.
    assert len(tiff) == 56, f"tiff prefix wrong size: {len(tiff)}"
    tiff += b"\x00" * (60 - len(tiff))
    tiff += ascii_value                          # 60..79 (20 bytes)
    tiff += ascii_value                          # 80..99 (20 bytes) — DateTimeOriginal value

    payload = b"Exif\x00\x00" + tiff
    seg_len = len(payload) + 2  # +2 for the length field itself
    return APP1_MARKER + struct.pack(">H", seg_len) + payload


def build_minimal_jpeg(exif_segment: bytes) -> bytes:
    """Return a 1x1 grayscale JPEG with the given APP1 segment inserted.

    The body bytes are a minimal baseline JPEG payload without any APP
    segments and without the leading SOI — we prepend SOI ourselves so
    the APP1 EXIF segment lands immediately after it (where parsers look).
    """
    body = bytes.fromhex(
        "ffdb004300"
        "0804040504040505050506060606060606"
        "0707080807070906090a0a09090a0a0c0c"
        "0c0c0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e"
        "0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e"
        "0e0e0e0e0e0e0e0e"
        "ffc0000b08000100010101110000"
        "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
        "ffc400b5100002010303020403050504040000017d010203000411051221314106"
        "13516107227114328191a1082342b1c11552d1f02433627282090a161718191a25"
        "262728292a3435363738393a434445464748494a535455565758595a636465666768"
        "696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8"
        "a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5"
        "e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"
        "ffda0008010100003f00fb"
        "ffd9"
    )
    return JPEG_SOI + exif_segment + body


def main() -> None:
    here = Path(__file__).parent
    for name, dt in SAMPLES:
        seg = build_exif_segment(dt)
        jpeg = build_minimal_jpeg(seg)
        (here / name).write_bytes(jpeg)
        print(f"wrote {name} ({len(jpeg)} bytes, EXIF={dt})")


if __name__ == "__main__":
    main()
