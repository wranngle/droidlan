"""Minimal stdlib-only EXIF reader.

Scope: extract the timestamp tags needed to bin a JPEG into a YYYY/MM
folder. We deliberately do not parse the whole EXIF spec — only enough
of the JPEG/TIFF structure to find ``DateTimeOriginal`` (0x9003) inside
the EXIF sub-IFD, with a fallback to the top-level ``DateTime`` (0x0132).

JPEG layout this walks:
    SOI (FFD8) ...markers... APP1 (FFE1) [len][Exif\\0\\0][TIFF header]
    TIFF: byte-order marker (II/MM) + magic 0x002A + offset to IFD0
    IFD0: count + entries; one entry may be ExifIFDPointer (0x8769).
    Exif sub-IFD: contains DateTimeOriginal (0x9003).

EXIF datetime format is fixed: ``YYYY:MM:DD HH:MM:SS`` (19 ASCII bytes +
NUL). We only parse the year and month — that is all the binner needs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

JPEG_SOI = b"\xff\xd8"
APP1_MARKER = b"\xff\xe1"
EXIF_HEADER = b"Exif\x00\x00"

TAG_DATETIME = 0x0132
TAG_EXIF_IFD_POINTER = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004

# TIFF type 2 = ASCII string. Other types exist but the datetime tags are
# always type 2, so we don't bother decoding the rest.
TIFF_TYPE_ASCII = 2


class ExifReadError(ValueError):
    """Raised when a file is not a parseable JPEG with usable EXIF."""


@dataclass(frozen=True)
class ExifDateTime:
    """Year + month extracted from an EXIF datetime tag.

    We do not surface the full timestamp because the binner only needs the
    YYYY/MM components, and pretending to return a full ``datetime`` would
    over-promise (EXIF strings have no timezone).
    """

    year: int
    month: int

    @classmethod
    def parse(cls, raw: str) -> "ExifDateTime":
        # EXIF spec: "YYYY:MM:DD HH:MM:SS" — fixed positions. Some cameras
        # write garbage (all-zeros for unset clocks); reject that as unknown
        # rather than producing 0000/00.
        if len(raw) < 10 or raw[4] != ":" or raw[7] != ":":
            raise ExifReadError(f"malformed EXIF datetime: {raw!r}")
        try:
            year = int(raw[0:4])
            month = int(raw[5:7])
        except ValueError as exc:
            raise ExifReadError(f"non-numeric EXIF datetime: {raw!r}") from exc
        if year < 1970 or year > 9999 or month < 1 or month > 12:
            raise ExifReadError(f"out-of-range EXIF datetime: {raw!r}")
        return cls(year=year, month=month)


def read_exif_datetime(path: Path) -> ExifDateTime:
    """Return the ``DateTimeOriginal`` for a JPEG, or raise ``ExifReadError``.

    Falls back to ``DateTimeDigitized`` then top-level ``DateTime`` so we
    still bin photos that omit the original tag (some Android camera apps).
    """
    raw = _find_datetime_string(path)
    return ExifDateTime.parse(raw)


def _find_datetime_string(path: Path) -> str:
    with path.open("rb") as fh:
        data = fh.read(2)
        if data != JPEG_SOI:
            raise ExifReadError(f"{path}: not a JPEG (no SOI marker)")

        # Walk JPEG markers looking for APP1 with the Exif header. Bail after
        # a generous cap so a corrupted/huge file can't hang the scanner.
        for _ in range(64):
            marker = fh.read(2)
            if len(marker) < 2:
                raise ExifReadError(f"{path}: truncated before APP1")
            seg_len_bytes = fh.read(2)
            if len(seg_len_bytes) < 2:
                raise ExifReadError(f"{path}: truncated segment length")
            seg_len = struct.unpack(">H", seg_len_bytes)[0]
            if seg_len < 2:
                raise ExifReadError(f"{path}: bogus segment length {seg_len}")

            payload = fh.read(seg_len - 2)
            if len(payload) < seg_len - 2:
                raise ExifReadError(f"{path}: truncated segment payload")

            if marker == APP1_MARKER and payload.startswith(EXIF_HEADER):
                return _parse_tiff(payload[len(EXIF_HEADER):], path)

            # Stop searching once we hit the start-of-scan; EXIF only lives
            # in the pre-image metadata segments.
            if marker == b"\xff\xda":
                break

    raise ExifReadError(f"{path}: no EXIF APP1 segment found")


def _parse_tiff(buf: bytes, path: Path) -> str:
    if len(buf) < 8:
        raise ExifReadError(f"{path}: TIFF header truncated")
    bo = buf[:2]
    if bo == b"II":
        endian = "<"
    elif bo == b"MM":
        endian = ">"
    else:
        raise ExifReadError(f"{path}: unknown TIFF byte order {bo!r}")

    magic = struct.unpack(endian + "H", buf[2:4])[0]
    if magic != 0x002A:
        raise ExifReadError(f"{path}: bad TIFF magic 0x{magic:04x}")

    ifd0_offset = struct.unpack(endian + "I", buf[4:8])[0]
    ifd0 = _read_ifd(buf, ifd0_offset, endian, path)

    if TAG_DATETIME_ORIGINAL in ifd0:
        return ifd0[TAG_DATETIME_ORIGINAL]
    if TAG_DATETIME_DIGITIZED in ifd0:
        return ifd0[TAG_DATETIME_DIGITIZED]

    exif_ptr = ifd0.get(TAG_EXIF_IFD_POINTER)
    if exif_ptr is not None:
        sub = _read_ifd(buf, exif_ptr, endian, path)
        if TAG_DATETIME_ORIGINAL in sub:
            return sub[TAG_DATETIME_ORIGINAL]
        if TAG_DATETIME_DIGITIZED in sub:
            return sub[TAG_DATETIME_DIGITIZED]

    if TAG_DATETIME in ifd0:
        return ifd0[TAG_DATETIME]

    raise ExifReadError(f"{path}: no datetime tag in EXIF")


def _read_ifd(buf: bytes, offset: int, endian: str, path: Path) -> dict[int, object]:
    """Decode one IFD into ``{tag: value}``. Only ASCII tags + the sub-IFD
    pointer (LONG) are decoded — we ignore everything else by design.
    """
    if offset + 2 > len(buf):
        raise ExifReadError(f"{path}: IFD offset {offset} past EOF")
    count = struct.unpack(endian + "H", buf[offset:offset + 2])[0]
    out: dict[int, object] = {}
    entry_base = offset + 2
    for i in range(count):
        entry = buf[entry_base + i * 12: entry_base + (i + 1) * 12]
        if len(entry) < 12:
            raise ExifReadError(f"{path}: truncated IFD entry {i}")
        tag, dtype, dcount = struct.unpack(endian + "HHI", entry[:8])
        value_field = entry[8:12]

        if tag == TAG_EXIF_IFD_POINTER:
            out[tag] = struct.unpack(endian + "I", value_field)[0]
            continue

        if dtype != TIFF_TYPE_ASCII:
            continue

        if dcount <= 4:
            raw = value_field[:dcount]
        else:
            value_offset = struct.unpack(endian + "I", value_field)[0]
            if value_offset + dcount > len(buf):
                raise ExifReadError(f"{path}: ASCII value past EOF")
            raw = buf[value_offset: value_offset + dcount]

        text = raw.rstrip(b"\x00").decode("ascii", errors="replace")
        out[tag] = text

    return out
