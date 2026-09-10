#!/usr/bin/env python3
"""Run gpsync with this project's .venv. You do not need to activate it."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _venv_pythons():
    scripts = ROOT / ".venv" / "Scripts" / "python.exe"
    unix = ROOT / ".venv" / "bin" / "python"
    unix3 = ROOT / ".venv" / "bin" / "python3"
    if os.name == "nt":
        return [scripts]
    return [unix, unix3, scripts]


def _same_python(path: Path) -> bool:
    try:
        return Path(sys.executable).resolve() == path.resolve()
    except OSError:
        return False


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    found = False
    for python in _venv_pythons():
        if not python.exists():
            continue
        found = True
        if not _same_python(python):
            os.execv(str(python), [str(python), "-m", "gpsync"] + args)
        break
    if not found:
        sys.stderr.write(
            "No project .venv found. From this folder, once:\n"
            "  python3 -m venv .venv && .venv/bin/pip install -e . "
            "&& .venv/bin/playwright install chromium\n"
            "Windows: .\\gpsync.cmd (uses .venv or WSL)\n"
        )
        return 1
    from gpsync.cli import main as cli_main

    return cli_main(args or None)


if __name__ == "__main__":
    raise SystemExit(main())
