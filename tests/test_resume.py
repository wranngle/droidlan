"""End-to-end tests for resumable chunked transfers (stdlib only).

Spec (round-2 §13.2): upload a 10 MB fixture, interrupt after 5 MB,
resume; assert only the remaining 5 MB hit the wire and final hash ==
source.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

# Make repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from droidlan.transfer import chunked


CHUNK = 1 * 1024 * 1024  # 1 MiB per chunk
FIXTURE_BYTES = 10 * 1024 * 1024  # 10 MiB total


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_file(tmp_path: Path) -> Path:
    """Deterministic 10 MiB payload (per-byte pattern so we never just see zeroes)."""
    src = tmp_path / "payload.bin"
    # 256-byte repeating pattern; size is exact, content is non-trivial.
    pattern = bytes(range(256))
    repeats = FIXTURE_BYTES // len(pattern)
    with src.open("wb") as f:
        for _ in range(repeats):
            f.write(pattern)
    assert src.stat().st_size == FIXTURE_BYTES
    return src


@pytest.fixture
def server(tmp_path: Path):
    """Spin up the chunked transfer server on an ephemeral port."""
    state = tmp_path / "state"
    final = tmp_path / "final"
    state.mkdir()
    final.mkdir()

    # Bind on ephemeral port to avoid collisions during parallel test runs.
    srv = chunked.serve(state, final, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # Tiny readiness probe so the test never races the listener.
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    try:
        yield {"url": f"http://127.0.0.1:{port}",
               "state": state, "final": final, "port": port}
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# Unit-ish tests for the helpers
# ---------------------------------------------------------------------------


def test_safe_name_accepts_simple_names():
    assert chunked.safe_name("foo.bin") == "foo.bin"
    assert chunked.safe_name("a_b-c.1") == "a_b-c.1"


def test_safe_name_rejects_path_traversal():
    with pytest.raises(ValueError):
        chunked.safe_name("../etc/passwd")
    with pytest.raises(ValueError):
        chunked.safe_name("a/b")
    with pytest.raises(ValueError):
        chunked.safe_name("")


def test_checkpoint_roundtrip(tmp_path: Path):
    ckpt = chunked.Checkpoint(name="payload.bin", total=100, offset=50,
                              started_at=time.time())
    chunked.save_checkpoint(tmp_path, ckpt)
    loaded = chunked.load_checkpoint(tmp_path, "payload.bin")
    assert loaded == ckpt


def test_load_checkpoint_missing_returns_none(tmp_path: Path):
    assert chunked.load_checkpoint(tmp_path, "nope") is None


# ---------------------------------------------------------------------------
# E2E — the spec'd resume behavior
# ---------------------------------------------------------------------------


class _CountingSendingSocket:
    """No-op placeholder; we count bytes by snapshotting log offsets instead."""
    pass


def _bytes_received(state_dir: Path, final_dir: Path, name: str) -> int:
    """Bytes durably on the server side, whether mid-transfer or finalized."""
    part = state_dir / f"{name}.part"
    final = final_dir / name
    if final.exists():
        return final.stat().st_size
    if part.exists():
        return part.stat().st_size
    return 0


def test_resume_only_sends_remaining_bytes_and_hashes_match(
    fixture_file: Path, server, tmp_path: Path, monkeypatch
):
    """The spec'd central promise of this feature.

    Strategy:
      1. Upload first 5 chunks (5 MiB) via `max_chunks=5` to simulate a
         mid-transfer interruption.
      2. Verify: checkpoint says 5 MiB received, no final file yet.
      3. Resume with a fresh `upload_in_chunks` call and a byte-counting
         wrapper around `urllib.request.urlopen` so we can prove only
         5 MiB of payload bytes crossed the wire on the second call.
      4. Verify: final file exists, SHA256 == source, server reports
         complete=True.
    """
    src = fixture_file
    name = src.name
    source_sha = hashlib.sha256(src.read_bytes()).hexdigest()

    # --- Phase 1: partial upload (5 chunks of 1 MiB) ---
    chunked.upload_in_chunks(
        src, server["url"], name=name, chunk_size=CHUNK, max_chunks=5,
    )

    assert _bytes_received(server["state"], server["final"], name) == 5 * CHUNK
    ckpt = chunked.load_checkpoint(server["state"], name)
    assert ckpt is not None
    assert ckpt.offset == 5 * CHUNK
    assert ckpt.total == FIXTURE_BYTES
    assert not (server["final"] / name).exists(), "must not finalize early"

    # --- Phase 2: byte-counting resume ---
    original_open = urllib.request.urlopen
    wire_bytes = {"upload": 0}

    def counting_open(req_or_url, *args, **kwargs):
        # Only count POST /upload chunk payloads. The resume probe is GET.
        if isinstance(req_or_url, urllib.request.Request):
            if (req_or_url.method == "POST"
                    and "/upload" in req_or_url.full_url):
                data = req_or_url.data or b""
                wire_bytes["upload"] += len(data)
        return original_open(req_or_url, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", counting_open)

    result = chunked.upload_in_chunks(
        src, server["url"], name=name, chunk_size=CHUNK,
    )

    # Exactly the remaining 5 MiB of payload bytes hit the wire.
    assert wire_bytes["upload"] == 5 * CHUNK, (
        f"resume sent {wire_bytes['upload']} bytes; expected {5 * CHUNK}"
    )
    assert result.get("complete") is True
    assert result.get("sha256") == source_sha

    # Final file matches source byte-for-byte.
    final_path = server["final"] / name
    assert final_path.exists()
    assert final_path.stat().st_size == FIXTURE_BYTES
    assert hashlib.sha256(final_path.read_bytes()).hexdigest() == source_sha

    # Checkpoint is cleaned up post-finalize.
    assert chunked.load_checkpoint(server["state"], name) is None
    assert not (server["state"] / f"{name}.part").exists()


def test_resume_probe_reports_offset(fixture_file: Path, server):
    """GET /resume returns the current durable offset."""
    src = fixture_file
    name = src.name
    chunked.upload_in_chunks(src, server["url"], name=name,
                             chunk_size=CHUNK, max_chunks=3)
    probe_url = f"{server['url']}/resume?name={name}"
    with urllib.request.urlopen(probe_url, timeout=5) as resp:
        body = json.loads(resp.read().decode())
    assert body["received"] == 3 * CHUNK
    assert body["complete"] is False
    assert body["total"] == FIXTURE_BYTES


def test_offset_mismatch_rejected(fixture_file: Path, server):
    """If a client sends a chunk at the wrong offset the server says 409."""
    src = fixture_file
    name = src.name
    # First send one chunk to advance the offset to 1 MiB.
    chunked.upload_in_chunks(src, server["url"], name=name,
                             chunk_size=CHUNK, max_chunks=1)

    # Now hand-craft a chunk at the wrong offset (3 MiB) and expect 409.
    bogus = b"x" * CHUNK
    req = urllib.request.Request(
        f"{server['url']}/upload?name={name}",
        data=bogus, method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {3 * CHUNK}-{4 * CHUNK - 1}/{FIXTURE_BYTES}",
            "Content-Length": str(CHUNK),
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        pytest.fail("expected HTTPError 409")
    except urllib.error.HTTPError as e:
        assert e.code == 409
        body = json.loads(e.read().decode())
        assert body["received"] == 1 * CHUNK


def test_sha256_mismatch_is_caught_at_finalize(tmp_path: Path, server,
                                               monkeypatch):
    """If the assembled file does not match the declared hash, server returns 422."""
    src = tmp_path / "small.bin"
    src.write_bytes(b"a" * (2 * CHUNK))

    # Upload first half normally.
    chunked.upload_in_chunks(src, server["url"], name=src.name,
                             chunk_size=CHUNK, max_chunks=1,
                             verify_sha256=False)

    # Send the final chunk with a wrong sha header.
    last = b"a" * CHUNK
    req = urllib.request.Request(
        f"{server['url']}/upload?name={src.name}",
        data=last, method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {CHUNK}-{2 * CHUNK - 1}/{2 * CHUNK}",
            "Content-Length": str(CHUNK),
            "X-Droidlan-Sha256": "0" * 64,
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        pytest.fail("expected HTTPError 422")
    except urllib.error.HTTPError as e:
        assert e.code == 422


def test_log_events_emit_to_ecs_channel(fixture_file: Path, server,
                                        tmp_path: Path, monkeypatch):
    """Resume + complete events land in the ECS-shaped JSONL log channel.

    Cites round-1 PR #4 (structured ECS JSONL transfer logging) — chunked
    transfer events emit through the same `DROIDLAN_LOG_PATH` channel so
    a single tail can watch every transfer subsystem.
    """
    log_path = tmp_path / "logs" / "droidlan.jsonl"
    monkeypatch.setenv("DROIDLAN_LOG_PATH", str(log_path))

    chunked.upload_in_chunks(fixture_file, server["url"],
                             name=fixture_file.name,
                             chunk_size=CHUNK, max_chunks=2)
    chunked.upload_in_chunks(fixture_file, server["url"],
                             name=fixture_file.name, chunk_size=CHUNK)

    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l]
    actions = {ln["event"]["action"] for ln in lines}
    # We expect at least: start, resume, chunk, complete.
    assert "start" in actions
    assert "resume" in actions
    assert "complete" in actions
    for ln in lines:
        # ECS shape sanity.
        assert ln["event"]["dataset"] == "droidlan.transfer"
        assert ln["event"]["kind"] == "event"
        assert ln["service"]["name"] == "chunked_transfer"
        assert "@timestamp" in ln
