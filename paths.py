"""Where persisted files vs. bundled resources actually live - matters
once this app is packaged into a standalone .exe (PyInstaller onefile).

PyInstaller onefile extracts the frozen script to a fresh temporary
directory on EVERY run and sets sys.frozen/sys._MEIPASS accordingly -
`Path(__file__).resolve().parent` inside a frozen app resolves into that
temp directory, not next to the .exe. Anything using that for a file
meant to persist between runs (config.json, cookies.txt, dead_cookies.txt,
log/crash.log) would silently lose all of it the moment the process
exits and the temp directory gets cleaned up - confirmed exactly the kind
of bug that only shows up after packaging, never in a normal `py main.py`
dev run, so worth getting right up front rather than discovering it
after shipping the .exe to another machine.
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory persisted, user-editable files should live in - next to
    the actual .exe when frozen, next to this script otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_resource_dir() -> Path:
    """Directory for read-only resources bundled INTO the .exe itself
    (currently just handle.exe) - PyInstaller onefile extracts these to a
    fresh temp folder (sys._MEIPASS) on every run, unlike app_dir()."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent
