"""Behavior tests for droidlan.sync.

Central promise: a two-way sync that never silently loses an edit.
Concurrent edits surface to ``conflicts.jsonl``; one-sided edits
propagate; deletions propagate; nothing is destroyed under concurrent
divergence.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from droidlan.sync import (
    DEFAULT_CONFLICT_LEDGER,
    DEFAULT_STATE_FILE,
    load_state,
    sync,
)


def _read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TwoWaySyncContract(unittest.TestCase):
    # ----- central promise: no edit is silently lost -----

    def test_concurrent_divergent_edit_is_recorded_in_ledger(self):
        """Edit on both sides between syncs -> ledger entry, both files retained."""
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "doc.txt").write_text("shared baseline")
            sync(a, b)
            (a / "doc.txt").write_text("alpha-side edit")
            (b / "doc.txt").write_text("bravo-side edit")
            result = sync(a, b, origin_a="laptop", origin_b="phone")

            self.assertEqual(len(result.conflicts), 1)
            ledger = _read_ledger(a / DEFAULT_CONFLICT_LEDGER)
            self.assertEqual(len(ledger), 1)
            entry = ledger[0]
            self.assertEqual(entry["file"]["path"], "doc.txt")
            self.assertIn(entry["file"]["winner"], {"laptop", "phone"})
            self.assertNotEqual(
                entry["file"]["a"]["hash"], entry["file"]["b"]["hash"]
            )

    # ----- happy-path propagation -----

    def test_one_sided_edit_propagates(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "f.txt").write_text("v1")
            sync(a, b)
            (a / "f.txt").write_text("v2")
            sync(a, b)
            self.assertEqual((b / "f.txt").read_text(), "v2")

    def test_new_file_on_b_propagates_to_a(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (b / "fresh.txt").write_text("hello from b")
            sync(a, b)
            self.assertEqual((a / "fresh.txt").read_text(), "hello from b")

    def test_nested_path_propagates(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "deep").mkdir()
            (a / "deep" / "leaf.txt").write_text("nested")
            sync(a, b)
            self.assertTrue((b / "deep" / "leaf.txt").exists())
            self.assertEqual((b / "deep" / "leaf.txt").read_text(), "nested")

    # ----- deletion propagation -----

    def test_deletion_on_a_propagates_to_b(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "doomed.txt").write_text("delete me")
            sync(a, b)
            self.assertTrue((b / "doomed.txt").exists())
            (a / "doomed.txt").unlink()
            sync(a, b)
            self.assertFalse((b / "doomed.txt").exists())

    # ----- conflict semantics -----

    def test_coincidental_identical_edit_is_not_a_conflict(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "f.txt").write_text("baseline")
            sync(a, b)
            (a / "f.txt").write_text("agreed")
            (b / "f.txt").write_text("agreed")
            result = sync(a, b)
            self.assertEqual(result.conflicts, [])
            self.assertFalse((a / DEFAULT_CONFLICT_LEDGER).exists())

    def test_lamport_clock_advances_on_each_write(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "f.txt").write_text("v1")
            sync(a, b)
            (a / "f.txt").write_text("v2")
            sync(a, b)
            state = load_state(a / DEFAULT_STATE_FILE)
            self.assertGreaterEqual(state["f.txt"]["clock"], 2)

    def test_conflict_ledger_is_jsonl_with_ecs_shape(self):
        """Ledger lines parse as JSON and carry the ECS fields we cite from #4."""
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "x").write_text("base")
            sync(a, b)
            (a / "x").write_text("a-edit")
            (b / "x").write_text("b-edit")
            sync(a, b)
            lines = (a / DEFAULT_CONFLICT_LEDGER).read_text().splitlines()
            self.assertTrue(lines)
            for raw in lines:
                entry = json.loads(raw)
                self.assertIn("@timestamp", entry)
                self.assertEqual(entry["event"]["kind"], "event")
                self.assertEqual(entry["event"]["action"], "conflict")
                self.assertEqual(entry["event"]["dataset"], "droidlan.sync")
                self.assertEqual(entry["service"]["name"], "droidlan.sync")

    # ----- idempotency / dry-run-shaped invariant -----

    def test_repeated_sync_with_no_changes_is_a_noop(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "k.txt").write_text("stable")
            sync(a, b)
            result = sync(a, b)
            self.assertEqual(result.total_changes(), 0)

    # ----- state file isolation -----

    def test_state_file_is_not_synced_to_peer(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            (a / "f.txt").write_text("v1")
            sync(a, b)
            self.assertFalse((b / DEFAULT_STATE_FILE).exists())


if __name__ == "__main__":
    unittest.main()
