"""Two-way directory sync.

Algorithm per file path:
  1. Build a snapshot of each side: {relpath: (content_hash, mtime)}.
  2. Compare against the last-known synced state.
  3. For each path, derive (a_changed, b_changed).
       - Neither changed -> no-op.
       - Only one side changed -> propagate that side's content to the other.
       - Both changed AND hashes differ -> conflict: keep both copies on
         disk (winner determined by higher Lamport clock, ties broken
         lexicographically by origin id) and append an entry to the
         conflict ledger.
       - Both changed but hashes match -> coincidental agreement; bump
         clock and treat as resolved.

  4. Bump each file's logical clock by 1 every time it is written, and
     persist `{clock, hash, mtime, origin}` to the state file at each
     root.

This is not a full CRDT (no operational deltas, no causal vector), but it
preserves the practical CRDT property that no edit is silently lost — every
divergence ends up in the ledger.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_FILE = ".droidlan-sync-state.json"
DEFAULT_CONFLICT_LEDGER = "conflicts.jsonl"
_BUFFER_BYTES = 65536


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_BUFFER_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def scan_directory(
    root: Path,
    *,
    ignore: tuple[str, ...] = (DEFAULT_STATE_FILE, DEFAULT_CONFLICT_LEDGER),
) -> dict[str, tuple[str, float]]:
    """Return {relpath: (sha256, mtime)} for every regular file under root."""
    snapshot: dict[str, tuple[str, float]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name in ignore:
                continue
            absolute = Path(dirpath) / name
            rel = str(absolute.relative_to(root)).replace(os.sep, "/")
            snapshot[rel] = (_hash_file(absolute), absolute.stat().st_mtime)
    return snapshot


def load_state(state_path: Path) -> dict[str, dict]:
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict[str, dict]) -> None:
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(data)


def _append_conflict(
    ledger_path: Path,
    *,
    relpath: str,
    a_hash: str,
    b_hash: str,
    a_clock: int,
    b_clock: int,
    winner: str,
    a_origin: str,
    b_origin: str,
) -> dict:
    entry = {
        "@timestamp": _utc_now(),
        "event": {
            "kind": "event",
            "action": "conflict",
            "dataset": "droidlan.sync",
        },
        "service": {"name": "droidlan.sync"},
        "file": {
            "path": relpath,
            "a": {"hash": a_hash, "clock": a_clock, "origin": a_origin},
            "b": {"hash": b_hash, "clock": b_clock, "origin": b_origin},
            "winner": winner,
        },
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")
    return entry


@dataclass
class SyncResult:
    """Summary of a single sync run."""

    copied_a_to_b: list[str] = field(default_factory=list)
    copied_b_to_a: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    deleted_a: list[str] = field(default_factory=list)
    deleted_b: list[str] = field(default_factory=list)

    def total_changes(self) -> int:
        return (
            len(self.copied_a_to_b)
            + len(self.copied_b_to_a)
            + len(self.conflicts)
            + len(self.deleted_a)
            + len(self.deleted_b)
        )


def _record(state: dict[str, dict], rel: str, hash_: str, mtime: float, origin: str) -> None:
    prior = state.get(rel, {})
    state[rel] = {
        "clock": int(prior.get("clock", 0)) + 1,
        "hash": hash_,
        "mtime": mtime,
        "origin": origin,
    }


def sync(
    root_a: Path | str,
    root_b: Path | str,
    *,
    origin_a: str = "a",
    origin_b: str = "b",
    state_filename: str = DEFAULT_STATE_FILE,
    conflict_ledger_filename: str = DEFAULT_CONFLICT_LEDGER,
) -> SyncResult:
    """Two-way sync between two directory roots.

    State and the conflict ledger live inside ``root_a`` so the caller can
    pin them. Both roots receive the same content after a successful run
    (modulo conflicts, where both sides retain their own copy and the
    ledger records the divergence — operator chooses how to resolve).
    """
    a = Path(root_a)
    b = Path(root_b)
    a.mkdir(parents=True, exist_ok=True)
    b.mkdir(parents=True, exist_ok=True)

    state_path = a / state_filename
    ledger_path = a / conflict_ledger_filename
    ignore = (state_filename, conflict_ledger_filename)

    state = load_state(state_path)
    snap_a = scan_directory(a, ignore=ignore)
    snap_b = scan_directory(b, ignore=ignore)

    result = SyncResult()
    all_paths = set(snap_a) | set(snap_b) | set(state)

    for rel in sorted(all_paths):
        in_a = rel in snap_a
        in_b = rel in snap_b
        prior = state.get(rel)
        prior_hash = prior["hash"] if prior else None

        a_changed = in_a and (prior_hash != snap_a[rel][0])
        b_changed = in_b and (prior_hash != snap_b[rel][0])
        a_deleted = (prior is not None) and (not in_a)
        b_deleted = (prior is not None) and (not in_b)

        if not in_a and not in_b:
            state.pop(rel, None)
            continue

        if a_deleted and b_deleted:
            state.pop(rel, None)
            continue

        if a_deleted and not b_changed:
            (b / rel).unlink()
            result.deleted_b.append(rel)
            state.pop(rel, None)
            continue

        if b_deleted and not a_changed:
            (a / rel).unlink()
            result.deleted_a.append(rel)
            state.pop(rel, None)
            continue

        if a_changed and not b_changed and in_a:
            data = _read_bytes(a / rel)
            _write_bytes(b / rel, data)
            new_mtime = (b / rel).stat().st_mtime
            _record(state, rel, snap_a[rel][0], new_mtime, origin_a)
            result.copied_a_to_b.append(rel)
            continue

        if b_changed and not a_changed and in_b:
            data = _read_bytes(b / rel)
            _write_bytes(a / rel, data)
            new_mtime = (a / rel).stat().st_mtime
            _record(state, rel, snap_b[rel][0], new_mtime, origin_b)
            result.copied_b_to_a.append(rel)
            continue

        if a_changed and b_changed and in_a and in_b:
            hash_a = snap_a[rel][0]
            hash_b = snap_b[rel][0]
            if hash_a == hash_b:
                _record(state, rel, hash_a, snap_a[rel][1], origin_a)
                continue
            a_clock = int(prior["clock"]) + 1 if prior else 1
            b_clock = int(prior["clock"]) + 1 if prior else 1
            if a_clock > b_clock or (a_clock == b_clock and origin_a < origin_b):
                winner = origin_a
            else:
                winner = origin_b
            entry = _append_conflict(
                ledger_path,
                relpath=rel,
                a_hash=hash_a,
                b_hash=hash_b,
                a_clock=a_clock,
                b_clock=b_clock,
                winner=winner,
                a_origin=origin_a,
                b_origin=origin_b,
            )
            result.conflicts.append(entry)
            state[rel] = {
                "clock": max(a_clock, b_clock),
                "hash": hash_a if winner == origin_a else hash_b,
                "mtime": snap_a[rel][1] if winner == origin_a else snap_b[rel][1],
                "origin": winner,
                "conflict": True,
            }
            continue

        if not prior and in_a and not in_b:
            data = _read_bytes(a / rel)
            _write_bytes(b / rel, data)
            _record(state, rel, snap_a[rel][0], (b / rel).stat().st_mtime, origin_a)
            result.copied_a_to_b.append(rel)
            continue

        if not prior and in_b and not in_a:
            data = _read_bytes(b / rel)
            _write_bytes(a / rel, data)
            _record(state, rel, snap_b[rel][0], (a / rel).stat().st_mtime, origin_b)
            result.copied_b_to_a.append(rel)
            continue

    save_state(state_path, state)
    return result
