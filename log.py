"""Structured JSONL logging for droidlan transfer events.

Each event is written as one JSON line to ``logs/droidlan.jsonl`` (path
configurable). The shape follows Elastic Common Schema (ECS) so the
files compose with the rest of the wranngle telemetry stack.

Required ECS fields per line:
  - @timestamp   ISO-8601 UTC string
  - event.kind   always "event"
  - event.action one of {"start", "complete", "error"}
  - event.dataset "droidlan.<service>"
  - service.name the calling script (e.g. "pc_ftp_server")
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path("logs") / "droidlan.jsonl"

_VALID_ACTIONS = {"start", "complete", "error"}

_write_lock = threading.Lock()


def _resolve_log_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("DROIDLAN_LOG_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_LOG_PATH


def log_event(
    service: str,
    action: str,
    *,
    path: Path | str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one ECS-shaped event line to the JSONL log.

    Returns the dict that was written so callers can inspect or test it.
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {_VALID_ACTIONS}, got {action!r}")

    event: dict[str, Any] = {
        "@timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": {
            "kind": "event",
            "action": action,
            "dataset": f"droidlan.{service}",
        },
        "service": {"name": service},
    }
    for key, value in fields.items():
        if value is None:
            continue
        event[key] = value

    target = _resolve_log_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":"), default=str)
    with _write_lock:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return event
