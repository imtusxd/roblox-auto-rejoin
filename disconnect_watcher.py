"""Per-account Roblox client log watcher.

Ported from RBX Alt Manager's RobloxProcess.cs (ReadLogFile/WaitForLogPath):
locates the log file for a given PID via Sysinternals handle.exe, tails new
bytes as they're appended, and matches each new line against two regex
patterns to detect connect/disconnect events. This is far more robust than
relying on an in-game Lua heartbeat file (YummyWebPlayer's checkyummy.lua
approach) because the native client log line lands whether or not any
executor script survives the disconnect.

The pure line-matching logic (`classify_line`, `WatcherState`) is kept
separate from the file/process I/O (`find_log_path`, `LogTailer`) so it can
be unit tested against captured sample log lines without touching a real
Roblox log file or spawning handle.exe.
"""
from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import time
from pathlib import Path

# Verbatim from RobloxProcess.cs's ReadLogFile - a real client log line
# looks like:
#   2026-08-19T07:51:34.123Z,1,ClientAppSettings ! Joining game '...' place 16732694052 at 1.2.3.4
CONNECTED_PATTERN = re.compile(
    r"\[FLog::Output\] ! Joining game '[\w+\-]{36}' place \d+ at [\d+.]+"
)
DISCONNECTED_PATTERN = re.compile(
    r"\[FLog::Network\] Sending disconnect with reason: (\d+)"
)

# Verbatim from RobloxProcess.cs's WaitForLogPath, matched against
# handle.exe's own "-p {pid}" output.
LOG_PATH_PATTERN = re.compile(
    r"\w+: File.+(\w+:.+\\logs\\)([\d+.]+_\w+_Player_\w+_last\.log)"
)


class LineEvent:
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NONE = "none"


def classify_line(line: str) -> str:
    """Pure function: does this one log line signal a connect or disconnect
    event? No side effects, no I/O - safe to unit test directly."""
    if CONNECTED_PATTERN.search(line):
        return LineEvent.CONNECTED
    if DISCONNECTED_PATTERN.search(line):
        return LineEvent.DISCONNECTED
    return LineEvent.NONE


@dataclasses.dataclass
class WatcherState:
    is_connected: bool = False
    disconnected_at: float | None = None
    last_position: int = 0

    def apply_line(self, line: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        event = classify_line(line)
        if event == LineEvent.CONNECTED:
            self.is_connected = True
            self.disconnected_at = None
        elif event == LineEvent.DISCONNECTED:
            self.is_connected = False
            self.disconnected_at = now

    def seconds_since_disconnect(self, now: float | None = None) -> float | None:
        if self.is_connected or self.disconnected_at is None:
            return None
        now = time.time() if now is None else now
        return now - self.disconnected_at


def find_log_path(handle_exe_path: str, pid: int) -> Path | None:
    """Same `-p {pid}` call RobloxProcess.cs's WaitForLogPath makes.

    Requires the Sysinternals handle.exe EULA to already be accepted (same
    HKCU\\SOFTWARE\\Sysinternals\\Handle\\EulaAccepted=1 registry key
    RobloxWatcher.IsHandleEulaAccepted checks for - run
    `handle.exe -accepteula` once by hand before using this tool).
    """
    try:
        result = subprocess.run(
            [handle_exe_path, "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None

    match = LOG_PATH_PATTERN.search(result.stdout)
    if not match:
        return None

    parent_dir = match.group(1)
    filename = match.group(2)

    if "?" in parent_dir:
        # handle.exe mangles non-ASCII usernames in the path it prints out -
        # fall back to the well-known logs folder location instead.
        parent_dir = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "logs")

    return Path(parent_dir) / filename


class LogTailer:
    """Wraps one account's log file: repeatedly reads newly appended bytes
    and feeds each new line into a WatcherState."""

    def __init__(self, log_path: Path, state: WatcherState | None = None) -> None:
        self.log_path = log_path
        self.state = state or WatcherState()

    def poll(self) -> None:
        try:
            size = self.log_path.stat().st_size
        except OSError:
            return

        if size <= self.state.last_position:
            return

        try:
            with self.log_path.open("rb") as handle:
                handle.seek(self.state.last_position)
                chunk = handle.read(size - self.state.last_position)
        except OSError:
            return

        self.state.last_position = size
        text = chunk.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            self.state.apply_line(line)
