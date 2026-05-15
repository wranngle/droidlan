"""CRDT-ish two-way directory sync with conflict ledger.

Last-Writer-Wins by Lamport-style logical clock per file. Concurrent edits
(same parent vector but divergent content) are surfaced to a JSONL ledger
at ``conflicts.jsonl`` rather than silently clobbered.

The state file (`.droidlan-sync-state.json`) lives at the root of each
synced directory and stores ``{path: {clock, hash, mtime, origin}}``.
"""

from .core import (
    DEFAULT_CONFLICT_LEDGER,
    DEFAULT_STATE_FILE,
    SyncResult,
    load_state,
    save_state,
    scan_directory,
    sync,
)

__all__ = [
    "DEFAULT_CONFLICT_LEDGER",
    "DEFAULT_STATE_FILE",
    "SyncResult",
    "load_state",
    "save_state",
    "scan_directory",
    "sync",
]
