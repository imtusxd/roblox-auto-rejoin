"""Crash/fault logging - the equivalent of YummyWebPlayer's own
log/fault.log, which this tool didn't have until an app crash (silent
under pythonw.exe: stderr goes nowhere, so an uncaught exception anywhere
just vanishes) left two accounts' Roblox clients stuck with nothing
watching them and no trace of why the watcher itself had died.

install() wires three separate places an exception can escape to
un-observed and routes all of them to the same log file:
  - sys.excepthook       - an uncaught exception on the main thread
  - threading.excepthook - an uncaught exception on any background thread
                            (each account's watch loop already has its own
                            try/except per rejoin_controller.py, but this
                            is the backstop for anything that isn't -
                            resolve_account_info's thread, webhook's
                            sender thread, window_manager's arrange thread)
  - Tk.report_callback_exception - an uncaught exception raised inside a
    widget callback (button command, `.after()` callback like
    _drain_queue) - Tkinter catches these itself and would otherwise just
    print to stderr, which is exactly the "vanishes under pythonw" case
    that left this crash with no trace.
"""
from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime, timezone

from paths import app_dir

LOG_DIR = app_dir() / "log"
CRASH_LOG_PATH = LOG_DIR / "crash.log"


def _write(source: str, exc_type, exc_value, exc_tb) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with CRASH_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{timestamp}] unhandled exception ({source})\n{formatted}")
    except Exception:
        pass  # logging the crash must never itself be able to crash the app


def install(root=None) -> None:
    """Call once, near app startup. `root` (the Tk instance), if given,
    also gets its report_callback_exception overridden - pass it once
    it's been created."""

    def _sys_hook(exc_type, exc_value, exc_tb):
        _write("main thread", exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_hook

    def _thread_hook(args) -> None:
        _write(f"thread '{args.thread.name}'", args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook

    if root is not None:
        def _tk_hook(exc_type, exc_value, exc_tb):
            _write("tkinter callback", exc_type, exc_value, exc_tb)

        root.report_callback_exception = _tk_hook
