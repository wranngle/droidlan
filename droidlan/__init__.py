"""droidlan: zero-config LAN transfer toolkit between phone and PC.

This package directory shadows the sibling ``droidlan.py`` umbrella CLI on
``sys.path``. To keep ``import droidlan`` exposing the CLI's ``main`` /
``SUBCOMMANDS`` / ``runpy`` symbols (and to keep ``patch.object(droidlan,
\"runpy\")`` interception working), we load the script in-place and replace
this package module in ``sys.modules`` with the loaded script module.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

_script = _Path(__file__).resolve().parent.parent / "droidlan.py"
_spec = _ilu.spec_from_file_location(__name__, _script)
_mod = _ilu.module_from_spec(_spec)
_mod.__path__ = __path__  # type: ignore[attr-defined]
_sys.modules[__name__] = _mod
_spec.loader.exec_module(_mod)
