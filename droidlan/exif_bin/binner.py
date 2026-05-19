"""File-binning core: move JPEGs into ``YYYY/MM/`` folders by EXIF date.

Public surface:
    - ``bin_file(src, root)``                 -> BinResult
    - ``bin_directory(src_dir, root, ...)``   -> list[BinResult]
    - ``read_exif_datetime(path)``            -> ExifDateTime  (re-exported)

The progress reporting interface mirrors PR #3's ``ProgressTracker``:
a callback receives ``ProgressEvent`` at every ``step_pct`` of files
processed plus a guaranteed terminal 100% event, so the binner shares the
same UX story as the transfer scripts.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .exif_parser import ExifDateTime, ExifReadError, read_exif_datetime as _read

DEFAULT_STEP_PCT = 10
UNKNOWN_BUCKET = "unknown"
JPEG_SUFFIXES = frozenset({".jpg", ".jpeg", ".JPG", ".JPEG"})


@dataclass(frozen=True)
class ProgressEvent:
    """One progress tick.

    ``done`` and ``total`` are file counts (not bytes); the binner's unit of
    work is a file move, not a chunk. ``pct`` is integer 0-100.
    """

    done: int
    total: int
    pct: int
    current: str


@dataclass(frozen=True)
class BinResult:
    """Outcome of binning a single file."""

    src: Path
    dest: Path
    bucket: str  # "YYYY/MM" or "unknown"
    reason: Optional[str]  # None on success; ExifReadError message otherwise


def read_exif_datetime(path: Path) -> ExifDateTime:
    """Re-export so callers can import from one module."""
    return _read(path)


def _resolve_destination(src: Path, root: Path, when: Optional[ExifDateTime]) -> Path:
    if when is None:
        bucket = root / UNKNOWN_BUCKET
    else:
        bucket = root / f"{when.year:04d}" / f"{when.month:02d}"
    bucket.mkdir(parents=True, exist_ok=True)

    dest = bucket / src.name
    if not dest.exists() or dest.resolve() == src.resolve():
        return dest

    # Collision: append "-1", "-2", ... before the suffix. This is preferable
    # to overwriting; binning the same roll twice should be idempotent for
    # already-binned files and non-destructive for genuine name clashes.
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 10_000):
        candidate = bucket / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many name collisions in {bucket}")


def bin_file(src: Path, root: Path) -> BinResult:
    """Move ``src`` under ``root/YYYY/MM/`` based on its EXIF date.

    On EXIF parse failure the file is moved to ``root/unknown/`` so the
    caller can deal with it manually; nothing is dropped on the floor.
    """
    when: Optional[ExifDateTime] = None
    reason: Optional[str] = None
    try:
        when = _read(src)
    except ExifReadError as exc:
        reason = str(exc)

    dest = _resolve_destination(src, root, when)
    if dest.resolve() == src.resolve():
        # Already in the right bucket; idempotent no-op.
        bucket = UNKNOWN_BUCKET if when is None else f"{when.year:04d}/{when.month:02d}"
        return BinResult(src=src, dest=dest, bucket=bucket, reason=reason)

    shutil.move(str(src), str(dest))
    bucket = UNKNOWN_BUCKET if when is None else f"{when.year:04d}/{when.month:02d}"
    return BinResult(src=src, dest=dest, bucket=bucket, reason=reason)


def _iter_jpegs(src_dir: Path) -> Iterable[Path]:
    for path in sorted(src_dir.iterdir()):
        if path.is_file() and path.suffix in JPEG_SUFFIXES:
            yield path


def _default_progress(event: ProgressEvent) -> None:
    width = 30
    filled = int(width * event.pct / 100)
    bar = "#" * filled + "-" * (width - filled)
    line = f"[{bar}] {event.pct:3d}%  {event.done}/{event.total}  {event.current}"
    if sys.stdout.isatty():
        end = "\n" if event.pct >= 100 else "\r"
        sys.stdout.write(line + end)
    else:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


def bin_directory(
    src_dir: Path,
    root: Path,
    *,
    step_pct: int = DEFAULT_STEP_PCT,
    progress: Optional[Callable[[ProgressEvent], None]] = _default_progress,
) -> list[BinResult]:
    """Bin every JPEG in ``src_dir`` into ``root/YYYY/MM/`` subfolders.

    ``progress`` fires at every ``step_pct`` of files processed plus a
    terminal 100% event (cf. PR #3's ProgressTracker contract). Pass
    ``progress=None`` to silence it.
    """
    if not 1 <= step_pct <= 100:
        raise ValueError(f"step_pct must be in [1,100], got {step_pct}")

    files = list(_iter_jpegs(src_dir))
    total = len(files)
    if total == 0:
        if progress is not None:
            progress(ProgressEvent(done=0, total=0, pct=100, current=""))
        return []

    results: list[BinResult] = []
    last_pct_emitted = -1
    for idx, src in enumerate(files, start=1):
        result = bin_file(src, root)
        results.append(result)

        pct = int(idx * 100 / total)
        # Emit on threshold crossings and always for the terminal file.
        crossed_threshold = pct // step_pct > last_pct_emitted // step_pct if last_pct_emitted >= 0 else True
        if progress is not None and (crossed_threshold or idx == total):
            progress(ProgressEvent(done=idx, total=total, pct=pct, current=src.name))
            last_pct_emitted = pct

    # Guarantee a 100% terminal event even if the loop didn't naturally hit it.
    if progress is not None and last_pct_emitted < 100:
        progress(ProgressEvent(done=total, total=total, pct=100, current=files[-1].name))

    return results
