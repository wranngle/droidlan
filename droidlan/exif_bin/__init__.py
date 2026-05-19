"""EXIF auto-bin: route incoming photos into ``YYYY/MM/`` subfolders by
``DateTimeOriginal``.

After uploads land flat in ``./incoming/`` (via ``upload_server.py`` or
``pc_ftp_server.py``), a phone-camera roll can accumulate hundreds of
unsorted JPEGs. This module reads the EXIF timestamp out of each image and
moves it under ``<root>/<YYYY>/<MM>/<original-name>``.

Stdlib-only. Files lacking a parseable ``DateTimeOriginal`` are dropped in
``<root>/unknown/`` so nothing is silently lost.

The module reuses the progress-tracker idiom established in PR #3
(``ProgressTracker`` for streaming transfers): a per-file callback fires
every ``step_pct`` of total work plus a guaranteed 100% terminal event, so
the binning pass renders the same progress UX as a transfer.
"""

from .binner import (
    DEFAULT_STEP_PCT,
    UNKNOWN_BUCKET,
    BinResult,
    ExifReadError,
    ProgressEvent,
    bin_directory,
    bin_file,
    read_exif_datetime,
)

__all__ = [
    "DEFAULT_STEP_PCT",
    "UNKNOWN_BUCKET",
    "BinResult",
    "ExifReadError",
    "ProgressEvent",
    "bin_directory",
    "bin_file",
    "read_exif_datetime",
]
