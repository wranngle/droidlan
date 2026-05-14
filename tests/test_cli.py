"""Tests for the droidlan umbrella CLI (`droidlan.py`)."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import droidlan as cli  # noqa: E402


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def test_help_lists_three_subcommands() -> None:
    rc, stdout, _ = _run_cli(["--help"])
    assert rc == 0
    for sub in ("sideload", "pc-ftp", "upload"):
        assert sub in stdout, f"--help missing subcommand {sub}\n{stdout}"


def test_no_args_prints_help() -> None:
    rc, stdout, _ = _run_cli([])
    assert rc == 0
    assert "sideload" in stdout and "pc-ftp" in stdout and "upload" in stdout


@pytest.mark.parametrize("subcmd,script", [
    ("sideload", "sideload_server.py"),
    ("pc-ftp", "pc_ftp_server.py"),
    ("upload", "upload_server.py"),
])
def test_subcommand_dispatches_to_correct_script(subcmd: str, script: str) -> None:
    captured: dict[str, object] = {}

    def fake_run_path(path: str, run_name: str = "__main__") -> None:
        captured["path"] = path
        captured["argv"] = sys.argv[:]
        raise SystemExit(0)

    with patch.object(cli, "runpy") as mock_runpy:
        mock_runpy.run_path.side_effect = fake_run_path
        rc, _, _ = _run_cli([subcmd, "--port", "9999"])

    assert rc == 0
    assert captured["path"].endswith(script), captured
    forwarded = captured["argv"]
    assert forwarded[1:] == ["--port", "9999"], forwarded


def test_subcommand_propagates_nonzero_exit() -> None:
    def fake_run_path(path: str, run_name: str = "__main__") -> None:
        raise SystemExit(7)

    with patch.object(cli, "runpy") as mock_runpy:
        mock_runpy.run_path.side_effect = fake_run_path
        rc, _, _ = _run_cli(["upload", "--bogus"])

    assert rc == 7


def test_missing_backing_script_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SCRIPT_DIR", tmp_path)
    rc, _, stderr = _run_cli(["sideload"])
    assert rc == 2
    assert "missing backing script" in stderr


def test_unknown_subcommand_errors() -> None:
    rc, _, _ = _run_cli(["bogus-cmd"])
    assert rc != 0


def test_subcommands_table_matches_files_on_disk() -> None:
    for _, (script_name, _) in cli.SUBCOMMANDS.items():
        assert (REPO_ROOT / script_name).exists(), f"missing {script_name}"
