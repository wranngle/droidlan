"""Tests for log.py — ECS-shaped JSONL transfer events."""

from __future__ import annotations

import importlib
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import log as log_module  # noqa: E402

ISO_8601_Z = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)

REQUIRED_TOP_KEYS = ("@timestamp", "event", "service")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _assert_ecs_shape(event: dict, *, service: str, action: str) -> None:
    for key in REQUIRED_TOP_KEYS:
        assert key in event, f"missing top-level key {key!r}"
    assert ISO_8601_Z.match(event["@timestamp"]), event["@timestamp"]
    assert event["event"]["kind"] == "event"
    assert event["event"]["action"] == action
    assert event["event"]["dataset"] == f"droidlan.{service}"
    assert event["service"]["name"] == service


# ---------- core log_event shape ----------


def test_log_event_writes_ecs_shaped_line(tmp_path: Path) -> None:
    out = tmp_path / "logs" / "droidlan.jsonl"
    log_module.log_event("pc_ftp_server", "start", path=out, server={"host": "10.0.0.1", "port": 2121})

    lines = _read_jsonl(out)
    assert len(lines) == 1
    _assert_ecs_shape(lines[0], service="pc_ftp_server", action="start")
    assert lines[0]["server"] == {"host": "10.0.0.1", "port": 2121}


def test_log_event_rejects_unknown_action(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        log_module.log_event("upload_server", "garbage", path=tmp_path / "x.jsonl")


def test_log_event_appends_not_truncates(tmp_path: Path) -> None:
    out = tmp_path / "droidlan.jsonl"
    log_module.log_event("upload_server", "start", path=out, file={"path": "a.bin"})
    log_module.log_event("upload_server", "complete", path=out, file={"path": "a.bin", "size": 42})

    lines = _read_jsonl(out)
    assert len(lines) >= 2, "transfer must produce at least 2 lines (start + complete)"
    _assert_ecs_shape(lines[0], service="upload_server", action="start")
    _assert_ecs_shape(lines[1], service="upload_server", action="complete")
    assert lines[1]["file"]["size"] == 42


def test_log_event_drops_none_fields(tmp_path: Path) -> None:
    out = tmp_path / "droidlan.jsonl"
    log_module.log_event("sideload_server", "error", path=out, error={"message": "boom"}, dir=None)
    event = _read_jsonl(out)[0]
    assert "dir" not in event
    assert event["error"] == {"message": "boom"}


def test_log_event_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "deep" / "droidlan.jsonl"
    log_module.log_event("pc_ftp_server", "start", path=out)
    assert out.exists()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "env.jsonl"
    monkeypatch.setenv("DROIDLAN_LOG_PATH", str(target))
    log_module.log_event("pc_ftp_server", "complete")
    assert target.exists()
    assert len(_read_jsonl(target)) == 1


# ---------- transfer-shaped scenario the spec calls out ----------


def test_transfer_produces_start_and_complete(tmp_path: Path) -> None:
    """A transfer produces at least 2 lines (start + complete) with required ECS fields."""
    out = tmp_path / "droidlan.jsonl"
    log_module.log_event("upload_server", "start", path=out, http={"content_length": 1024})
    log_module.log_event(
        "upload_server",
        "complete",
        path=out,
        file={"path": str(tmp_path / "incoming" / "photo.jpg"), "size": 1024},
    )

    lines = _read_jsonl(out)
    assert len(lines) >= 2
    assert lines[0]["event"]["action"] == "start"
    assert lines[-1]["event"]["action"] == "complete"
    for line in lines:
        _assert_ecs_shape(line, service="upload_server", action=line["event"]["action"])


# ---------- end-to-end through upload_server's POST handler ----------


def _make_multipart_body(filename: str, content: bytes, boundary: str = "BNDRY") -> bytes:
    cr = b"\r\n"
    return (
        f"--{boundary}{cr.decode()}"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"{cr.decode()}'
        f"Content-Type: application/octet-stream{cr.decode()}{cr.decode()}"
    ).encode() + content + cr + f"--{boundary}--{cr.decode()}".encode()


def test_upload_server_e2e_writes_start_and_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "logs" / "droidlan.jsonl"
    monkeypatch.setenv("DROIDLAN_LOG_PATH", str(log_path))

    # Reload modules so they see the patched env on import.
    for name in ("upload_server", "log"):
        sys.modules.pop(name, None)
    importlib.import_module("log")
    upload_server = importlib.import_module("upload_server")

    upload_dir = tmp_path / "incoming"
    upload_dir.mkdir()
    upload_server.UPLOAD_DIR = upload_dir
    upload_server.MAX_BYTES = 1024 * 1024

    payload = b"hello droidlan"
    body = _make_multipart_body("greeting.txt", payload)
    boundary = "BNDRY"

    class _FakeRfile(io.BytesIO):
        pass

    class _Handler(upload_server.UploadHandler):
        def __init__(self) -> None:
            # Bypass BaseHTTPRequestHandler.__init__ which needs a socket.
            self.headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            }
            self.rfile = _FakeRfile(body)
            self.wfile = io.BytesIO()
            self._sent: list[tuple[int, str]] = []

        def send_response(self, code: int, message: str | None = None) -> None:  # type: ignore[override]
            self._sent.append((code, message or ""))

        def send_header(self, *args, **kwargs) -> None:  # type: ignore[override]
            return None

        def end_headers(self) -> None:  # type: ignore[override]
            return None

        def send_error(self, code: int, message: str | None = None, *args, **kwargs) -> None:  # type: ignore[override]
            self._sent.append((code, message or ""))

    handler = _Handler()
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        handler.do_POST()

    assert (upload_dir / "greeting.txt").read_bytes() == payload
    lines = _read_jsonl(log_path)
    assert len(lines) >= 2, lines
    actions = [line["event"]["action"] for line in lines]
    assert "start" in actions and "complete" in actions
    complete = next(line for line in lines if line["event"]["action"] == "complete")
    assert complete["file"]["size"] == len(payload)
    assert complete["file"]["path"].endswith("greeting.txt")
    for line in lines:
        _assert_ecs_shape(line, service="upload_server", action=line["event"]["action"])
