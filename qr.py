"""Terminal QR code printer.

Encodes a URL as a scannable QR using Unicode half-block characters so the
output fits in roughly half the vertical space of a full-block rendering.
Prints the QR followed by the original URL so users can still copy/paste.
"""

from __future__ import annotations

import sys

import qrcode

_BLOCK_FULL = "██"
_BLOCK_UPPER = "▀▀"
_BLOCK_LOWER = "▄▄"
_BLOCK_EMPTY = "  "


def _render_half_block(matrix: list[list[bool]]) -> str:
    """Render a 2D bool matrix as half-block Unicode lines.

    Each terminal row encodes two matrix rows: the upper half-block is the
    even row, the lower half-block is the odd row. Each cell is doubled
    horizontally to keep modules square in typical terminal fonts.
    """
    rows = len(matrix)
    width = len(matrix[0]) if rows else 0
    lines: list[str] = []
    for y in range(0, rows, 2):
        parts: list[str] = []
        for x in range(width):
            top = matrix[y][x]
            bot = matrix[y + 1][x] if y + 1 < rows else False
            if top and bot:
                parts.append(_BLOCK_FULL)
            elif top and not bot:
                parts.append(_BLOCK_UPPER)
            elif not top and bot:
                parts.append(_BLOCK_LOWER)
            else:
                parts.append(_BLOCK_EMPTY)
        lines.append("".join(parts))
    return "\n".join(lines)


def render_qr(url: str, *, border: int = 2) -> str:
    """Return a terminal-printable QR string for *url* (no trailing newline)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    matrix = [[bool(cell) for cell in row] for row in qr.get_matrix()]
    return _render_half_block(matrix)


def print_qr(url: str, *, file=None) -> None:
    """Print a terminal QR encoding *url*, then the URL on the next line."""
    stream = file or sys.stdout
    print(render_qr(url), file=stream)
    print(url, file=stream)
