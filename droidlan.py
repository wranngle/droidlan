#!/usr/bin/env python3
"""droidlan umbrella CLI.

Single entry point that wraps the three standalone server scripts as
subcommands:

    droidlan sideload  -> sideload_server.py (host APK over HTTP)
    droidlan pc-ftp    -> pc_ftp_server.py   (FTP server receiving from phone)
    droidlan upload    -> upload_server.py   (browser-form HTTP upload)

Any flags after the subcommand are forwarded verbatim to the underlying
script so existing invocations work unchanged (e.g.
``droidlan pc-ftp --port 2121 --user phone``).
"""

import argparse
import runpy
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

SUBCOMMANDS = {
    "sideload": ("sideload_server.py", "Host an APK over HTTP for phone-side install."),
    "pc-ftp": ("pc_ftp_server.py", "Run an FTP server on the PC to receive files."),
    "upload": ("upload_server.py", "Browser-form HTTP upload server."),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="droidlan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Pass --help after a subcommand to see its own flags "
               "(e.g. `droidlan pc-ftp --help`).",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=list(SUBCOMMANDS.keys()),
        metavar="<command>",
        help="\n".join(f"{name}: {desc}" for name, (_, desc) in SUBCOMMANDS.items()),
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the underlying script.",
    )
    return parser


def dispatch(command: str, forwarded: list[str]) -> int:
    script_name, _ = SUBCOMMANDS[command]
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        print(f"droidlan: missing backing script: {script_path}", file=sys.stderr)
        return 2
    saved_argv = sys.argv[:]
    sys.argv = [str(script_path), *forwarded]
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved_argv
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return dispatch(args.command, args.args)


if __name__ == "__main__":
    sys.exit(main())
