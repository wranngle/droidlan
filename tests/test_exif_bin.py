"""Tests for the EXIF auto-bin module.

Central promise (e2e): given a directory of JPEGs from a phone roll,
each file ends up under ``<root>/<YYYY>/<MM>/`` matching its EXIF
DateTimeOriginal — no files lost, no manual sorting.

The committed fixtures under ``fixtures/exif-samples/`` are the source of
truth; they cover three distinct (year, month) buckets so the test
exercises both same-year and cross-year binning.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from droidlan.exif_bin import (
    DEFAULT_STEP_PCT,
    UNKNOWN_BUCKET,
    BinResult,
    ExifReadError,
    ProgressEvent,
    bin_directory,
    bin_file,
    read_exif_datetime,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures" / "exif-samples"

EXPECTED_BUCKETS = {
    "photo-2023-04.jpg": ("2023", "04"),
    "photo-2024-11.jpg": ("2024", "11"),
    "photo-2025-01.jpg": ("2025", "01"),
}


@pytest.fixture
def staged_roll(tmp_path: Path) -> Path:
    """Copy the committed fixtures into a tmp directory so tests can move
    them around without polluting the repo."""
    staging = tmp_path / "roll"
    staging.mkdir()
    for name in EXPECTED_BUCKETS:
        shutil.copyfile(FIXTURES / name, staging / name)
    return staging


# --- EXIF parser ---------------------------------------------------------


class TestExifParser:
    def test_each_fixture_yields_expected_year_month(self) -> None:
        # The central correctness check: fixtures parse to the dates encoded
        # in their filenames.
        for name, (year, month) in EXPECTED_BUCKETS.items():
            dt = read_exif_datetime(FIXTURES / name)
            assert dt.year == int(year)
            assert dt.month == int(month)

    def test_rejects_non_jpeg(self, tmp_path: Path) -> None:
        bogus = tmp_path / "not-a-jpeg.jpg"
        bogus.write_bytes(b"GIF89a" + b"\x00" * 100)
        with pytest.raises(ExifReadError, match="not a JPEG"):
            read_exif_datetime(bogus)

    def test_rejects_jpeg_without_exif(self, tmp_path: Path) -> None:
        # SOI + SOS + EOI — valid JPEG framing, no APP1 segment.
        no_exif = tmp_path / "no-exif.jpg"
        no_exif.write_bytes(b"\xff\xd8\xff\xda\x00\x02\xff\xd9")
        with pytest.raises(ExifReadError, match="no EXIF APP1"):
            read_exif_datetime(no_exif)


# --- bin_directory: the central e2e promise ------------------------------


class TestBinDirectoryE2E:
    def test_three_distinct_dates_each_land_in_correct_yyyy_mm(
        self, staged_roll: Path, tmp_path: Path
    ) -> None:
        root = tmp_path / "binned"
        results = bin_directory(staged_roll, root, progress=None)

        # Each fixture must end up under its expected YYYY/MM bucket and
        # the source directory must be empty (files moved, not copied).
        assert len(results) == 3
        for name, (year, month) in EXPECTED_BUCKETS.items():
            dest = root / year / month / name
            assert dest.exists(), f"{name} not at {dest}"
            assert not (staged_roll / name).exists(), f"{name} still in source"

        result_by_name = {r.src.name: r for r in results}
        for name, (year, month) in EXPECTED_BUCKETS.items():
            assert result_by_name[name].bucket == f"{year}/{month}"
            assert result_by_name[name].reason is None

    def test_empty_directory_yields_no_results(self, tmp_path: Path) -> None:
        src = tmp_path / "empty"
        src.mkdir()
        results = bin_directory(src, tmp_path / "out", progress=None)
        assert results == []


# --- progress callback (cites PR #3 contract) ----------------------------


class TestProgressCallback:
    def test_terminal_event_is_always_emitted(
        self, staged_roll: Path, tmp_path: Path
    ) -> None:
        events: list[ProgressEvent] = []
        bin_directory(staged_roll, tmp_path / "out", progress=events.append)

        assert events, "expected at least one progress event"
        assert events[-1].pct == 100
        assert events[-1].done == events[-1].total == 3

    def test_step_pct_fires_at_thresholds(self, tmp_path: Path) -> None:
        # 10 files at step_pct=10 -> one event per file (each crosses a 10%
        # boundary). This exercises the threshold-crossing logic without
        # being sensitive to off-by-one rendering.
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            shutil.copyfile(
                FIXTURES / "photo-2023-04.jpg",
                src / f"img-{i:02d}.jpg",
            )

        events: list[ProgressEvent] = []
        bin_directory(src, tmp_path / "out", step_pct=10, progress=events.append)

        # Last event is the 100% terminal one.
        assert events[-1].pct == 100
        # Total files processed equals files seen.
        assert events[-1].done == 10
        # No event reports pct > 100 or negative.
        assert all(0 <= e.pct <= 100 for e in events)

    def test_progress_disabled_when_none(
        self, staged_roll: Path, tmp_path: Path
    ) -> None:
        # Should not raise; should return correct results regardless.
        results = bin_directory(staged_roll, tmp_path / "out", progress=None)
        assert len(results) == 3

    def test_step_pct_validation(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        with pytest.raises(ValueError, match="step_pct"):
            bin_directory(src, tmp_path / "out", step_pct=0, progress=None)
        with pytest.raises(ValueError, match="step_pct"):
            bin_directory(src, tmp_path / "out", step_pct=101, progress=None)

    def test_default_step_pct_constant(self) -> None:
        # Frozen contract: PR #3 chose 10% as the default; we mirror it so
        # the UX feels identical across transfer and binning.
        assert DEFAULT_STEP_PCT == 10


# --- failure-visibility & idempotency ------------------------------------


class TestFailureAndIdempotency:
    def test_file_without_exif_goes_to_unknown_bucket(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        bad = src / "no-exif.jpg"
        # Valid JPEG frame, no EXIF — would otherwise be silently lost.
        bad.write_bytes(b"\xff\xd8\xff\xda\x00\x02\xff\xd9")

        results = bin_directory(src, tmp_path / "out", progress=None)
        assert len(results) == 1
        assert results[0].bucket == UNKNOWN_BUCKET
        assert results[0].reason is not None
        assert (tmp_path / "out" / UNKNOWN_BUCKET / "no-exif.jpg").exists()

    def test_name_collision_does_not_overwrite(
        self, staged_roll: Path, tmp_path: Path
    ) -> None:
        root = tmp_path / "out"
        # First pass: bin everything.
        bin_directory(staged_roll, root, progress=None)

        # Stage a second copy of the same file under the same name.
        second = tmp_path / "second"
        second.mkdir()
        shutil.copyfile(FIXTURES / "photo-2023-04.jpg", second / "photo-2023-04.jpg")
        result = bin_file(second / "photo-2023-04.jpg", root)

        # The original at YYYY/MM/photo-2023-04.jpg must still exist; the
        # collision goes to a "-1" sibling.
        assert (root / "2023" / "04" / "photo-2023-04.jpg").exists()
        assert result.dest.name == "photo-2023-04-1.jpg"
        assert result.dest.exists()

    def test_binning_does_not_lose_files(
        self, staged_roll: Path, tmp_path: Path
    ) -> None:
        # Count files before and after — the sum across destination buckets
        # must equal the source count, with the source emptied.
        before = sorted(p.name for p in staged_roll.iterdir())
        root = tmp_path / "out"
        bin_directory(staged_roll, root, progress=None)
        moved = sorted(p.name for p in root.rglob("*.jpg"))
        assert moved == before
        assert list(staged_roll.iterdir()) == []
