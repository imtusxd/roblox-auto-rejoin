"""Window layout for launched Roblox clients - arrange them in a grid and
minimize them after a delay, matching YummyWebPlayer's own
"Windows Per Rows" + "Fixed Size" + "Delay Minimize" behavior.

The grid-position math (`grid_position`) is pure and unit-testable; the
actual window manipulation (`find_window_for_pid`, `move_window`,
`minimize_window`) is thin ctypes/user32 glue kept separate from it, same
split as `disconnect_watcher.py`'s classify_line vs. LogTailer.
"""
from __future__ import annotations

import ctypes
import dataclasses
from ctypes import wintypes

SW_MINIMIZE = 6


@dataclasses.dataclass(frozen=True)
class WindowRect:
    x: int
    y: int
    width: int
    height: int


def grid_position(index: int, columns: int, width: int, height: int) -> WindowRect:
    """Where the Nth window (0-based) should go in a left-to-right,
    top-to-bottom grid of `columns` windows per row, each `width` x
    `height` - the same layout Yummy's "Windows Per Rows" produces."""
    if columns <= 0:
        columns = 1
    row, col = divmod(index, columns)
    return WindowRect(x=col * width, y=row * height, width=width, height=height)


def find_window_for_pid(pid: int) -> int | None:
    """Enumerates top-level windows and returns the first one owned by
    `pid` that actually has a title (Roblox's real game window, not one of
    its invisible helper windows)."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        found.append(hwnd)
        return False  # stop enumeration, we found it

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def move_window(hwnd: int, rect: WindowRect) -> bool:
    try:
        return bool(
            ctypes.windll.user32.MoveWindow(
                hwnd, rect.x, rect.y, rect.width, rect.height, True
            )
        )
    except Exception:
        return False


def minimize_window(hwnd: int) -> bool:
    try:
        return bool(ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE))
    except Exception:
        return False
