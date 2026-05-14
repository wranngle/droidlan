"""Progress-tracker tests.

Asserts the ProgressTracker fires its callback at every threshold step as
bytes accumulate, and that a 100MB-equivalent transfer fed in realistic
chunk sizes produces well above the ≥10 progress updates the feature spec
calls for.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from any cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upload_server import ProgressTracker, render_progress  # noqa: E402


def _collect():
    events: list[tuple[int, int, int]] = []
    return events, lambda done, total, pct: events.append((done, total, pct))


def test_default_thresholds_fire_at_each_decile():
    events, sink = _collect()
    total = 1000
    tracker = ProgressTracker(total=total, on_update=sink)
    # Feed in 100-byte chunks; each chunk crosses exactly one 10% threshold.
    for _ in range(10):
        tracker.update(100)
    pcts = [pct for _, _, pct in events]
    # Expect 11 events: 0%, 10%, 20%, ..., 100%.
    assert pcts == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    # Final byte count must equal the announced total.
    assert events[-1][0] == total


def test_one_hundred_megabyte_transfer_yields_at_least_ten_updates():
    """The headline acceptance: a 100MB upload must surface ≥10 updates."""
    events, sink = _collect()
    total = 100 * 1024 * 1024  # 100 MiB
    tracker = ProgressTracker(total=total, on_update=sink)
    chunk = 64 * 1024
    sent = 0
    while sent < total:
        tracker.update(min(chunk, total - sent))
        sent += chunk
    tracker.finish()
    assert len(events) >= 10, f"expected >=10 progress events, got {len(events)}"
    # The progression must be monotonic.
    pcts = [pct for _, _, pct in events]
    assert pcts == sorted(pcts)
    # The first event represents the start; the last must be 100%.
    assert pcts[0] == 0
    assert pcts[-1] == 100


def test_callback_fires_at_explicit_byte_thresholds():
    """Each callback's reported byte count must hit the corresponding decile."""
    events, sink = _collect()
    total = 1_000_000
    tracker = ProgressTracker(total=total, on_update=sink)
    # Stream in exactly 1% chunks; the tracker should still only fire on
    # the decile boundaries, not on every chunk.
    chunk = total // 100
    for _ in range(100):
        tracker.update(chunk)
    # Map pct -> (bytes_done, total) for direct threshold inspection.
    by_pct = {pct: (done, t) for done, t, pct in events}
    for expected_pct in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        assert expected_pct in by_pct, f"missing threshold {expected_pct}%"
        done, t = by_pct[expected_pct]
        assert t == total
        # bytes_done at threshold p% must be at least p% of total.
        assert done >= expected_pct * total // 100


def test_custom_step_pct():
    events, sink = _collect()
    tracker = ProgressTracker(total=100, on_update=sink, step_pct=25)
    for _ in range(100):
        tracker.update(1)
    pcts = [pct for _, _, pct in events]
    assert pcts == [0, 25, 50, 75, 100]


def test_finish_emits_terminal_event_only_once():
    events, sink = _collect()
    tracker = ProgressTracker(total=1000, on_update=sink)
    tracker.update(500)
    tracker.finish()
    tracker.finish()  # idempotent
    # Last event must be 100%, and only one 100% event total.
    pcts = [pct for _, _, pct in events]
    assert pcts[-1] == 100
    assert pcts.count(100) == 1


def test_update_rejects_negative_n():
    tracker = ProgressTracker(total=100, on_update=lambda *_: None)
    with pytest.raises(ValueError):
        tracker.update(-1)


def test_invalid_step_pct_rejected():
    with pytest.raises(ValueError):
        ProgressTracker(total=100, on_update=lambda *_: None, step_pct=0)
    with pytest.raises(ValueError):
        ProgressTracker(total=100, on_update=lambda *_: None, step_pct=101)


def test_render_progress_writes_to_non_tty_stream():
    buf = io.StringIO()
    render_progress("file.bin", 50, 100, 50, stream=buf)
    output = buf.getvalue()
    assert "50%" in output
    assert "file.bin" in output
    # Non-TTY path must terminate with newline so log readers don't merge lines.
    assert output.endswith("\n")
