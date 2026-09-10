from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_files_exist() -> None:
    unix = ROOT / "gpsync.sh"
    win = ROOT / "gpsync.cmd"
    py = ROOT / "run.py"
    assert unix.is_file()
    assert win.is_file()
    assert py.is_file()
    text = unix.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "-m gpsync" in text
    assert "\r" not in text
    cmd = win.read_text(encoding="utf-8")
    assert "-m gpsync" in cmd
    assert ".venv" in cmd
    run_py = py.read_text(encoding="utf-8")
    assert '"-m", "gpsync"' in run_py or "-m gpsync" in run_py
    assert "\r" not in run_py.split("\n", 1)[0]


def test_unix_launcher_help() -> None:
    script = ROOT / "gpsync.sh"
    python = ROOT / ".venv" / "bin" / "python"
    if os.name == "nt" or not python.exists():
        return
    proc = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "download" in proc.stdout
    assert "status" in proc.stdout


def test_run_py_help() -> None:
    python = ROOT / ".venv" / "bin" / "python"
    runner = ROOT / "run.py"
    if not python.exists() or not runner.exists():
        return
    if os.name == "nt":
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        if not python.exists():
            return
    proc = subprocess.run(
        [str(python), str(runner), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "download" in proc.stdout
