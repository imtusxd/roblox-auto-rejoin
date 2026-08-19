"""Launches Roblox from an auth ticket, and makes sure Potassium is
resident afterwards so its autoexec folder re-injects the trade script into
the freshly (re)joined client.

`launch_via_protocol` builds the same `roblox-player:` protocol URI
roblox.com itself hands the browser off to when you click "Play", then
resolves it the same way a browser would (`os.startfile` on Windows).
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import subprocess
import time
import urllib.parse
from pathlib import Path

# Roblox refuses to open more than one game window at a time: on launch it
# tries to claim a mutex named ROBLOX_singletonMutex, and if something else
# already holds it, it steps aside instead of closing/refocusing whatever
# window currently owns it. This is the exact technique RBX Alt Manager's
# AccountManager.UpdateMultiRoblox uses (`new Mutex(true,
# "ROBLOX_singletonMutex")` + `WaitOne(TimeSpan.Zero, true)`), ported here
# via ctypes since Python has no built-in named-mutex primitive. Without
# this, launching account #2 kills account #1's client - confirmed live on
# this machine before adding the fix.
_SINGLETON_MUTEX_NAME = "ROBLOX_singletonMutex"
_WAIT_OBJECT_0 = 0
_mutex_handle: int | None = None


def enable_multi_instance() -> bool:
    """Claim the singleton mutex once, near app startup, before launching
    any Roblox client. The handle is held for the lifetime of this
    process - closing/exiting the app releases it automatically. Safe to
    call more than once (no-ops if already held)."""
    global _mutex_handle
    if _mutex_handle is not None:
        return True

    try:
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError):
        return False  # Not on Windows - nothing to do.

    handle = kernel32.CreateMutexW(None, True, _SINGLETON_MUTEX_NAME)
    if not handle:
        return False

    # Mirrors Mutex.WaitOne(TimeSpan.Zero, true) in AccountManager.cs -
    # confirm we actually hold it (a zero-timeout wait grants ownership
    # immediately or fails without blocking).
    result = kernel32.WaitForSingleObject(handle, 0)
    if result != _WAIT_OBJECT_0:
        kernel32.CloseHandle(handle)
        return False

    _mutex_handle = handle
    return True


PLACE_LAUNCHER_URL = (
    "https://assetgame.roblox.com/game/PlaceLauncher.ashx"
    "?request=RequestGame&browserTrackerId={browser_tracker_id}&placeId={place_id}"
    "&isPlayTogetherGame=false"
)
# Same PlaceLauncher.ashx endpoint, Roblox's other well-known request type
# for joining via a private/VIP server's access code (the "Copy Link"
# button on a server produces a URL carrying this same code as
# privateServerLinkCode - see accounts.extract_private_server_code, which
# pulls it back out so either a bare code or the full link works as input
# here). Not independently verified live yet the way the mutex/tracker-id
# fixes above were - if it stops working, Roblox may have deprecated this
# request type; RequestGame above is the one that's been confirmed.
PRIVATE_SERVER_LAUNCHER_URL = (
    "https://assetgame.roblox.com/game/PlaceLauncher.ashx"
    "?request=RequestPrivateGame&browserTrackerId={browser_tracker_id}&placeId={place_id}"
    "&accessCode={access_code}"
)


def new_browser_tracker_id() -> int:
    """Roblox uses browserTrackerId to distinguish between concurrent
    launch "sessions". Launching every account with the same tracker id (as
    an earlier version of this code did, hardcoding 0) makes Roblox treat
    every launch after the first as belonging to that same session: it
    starts with `--launch-to-tray` and idles forever waiting to hand off to
    "itself", never creating a real game window or even opening its log
    file - confirmed live on this machine (a stuck process's own log file
    couldn't even be located). RBX Alt Manager's own launch code
    (Account.cs) assigns each account a unique, persistent
    `BrowserTrackerID` for exactly this reason.
    """
    return random.randint(100_000_000, 999_999_999)


def build_launch_uri(
    ticket: str, place_id: str, browser_tracker_id: int, private_server_code: str | None = None
) -> str:
    launch_time = int(time.time() * 1000)
    if private_server_code:
        placelauncherurl = PRIVATE_SERVER_LAUNCHER_URL.format(
            place_id=place_id,
            browser_tracker_id=browser_tracker_id,
            access_code=private_server_code,
        )
    else:
        placelauncherurl = PLACE_LAUNCHER_URL.format(
            place_id=place_id, browser_tracker_id=browser_tracker_id
        )
    encoded_placelauncherurl = urllib.parse.quote(placelauncherurl, safe="")
    return (
        "roblox-player:1"
        "+launchmode:play"
        f"+gameinfo:{ticket}"
        f"+launchtime:{launch_time}"
        f"+placelauncherurl:{encoded_placelauncherurl}"
        f"+browsertrackerid:{browser_tracker_id}"
        "+robloxLocale:en_us"
        "+gameLocale:en_us"
        "+channel:"
        "+LaunchExp:InApp"
    )


def launch_via_protocol(
    ticket: str,
    place_id: str,
    browser_tracker_id: int | None = None,
    private_server_code: str | None = None,
) -> int:
    """Returns the browser_tracker_id actually used (a fresh one is
    generated if not given) so the caller can log/track it."""
    if browser_tracker_id is None:
        browser_tracker_id = new_browser_tracker_id()
    uri = build_launch_uri(ticket, place_id, browser_tracker_id, private_server_code)
    os.startfile(uri)  # type: ignore[attr-defined]  # Windows-only, resolves like a browser would
    return browser_tracker_id


def is_process_running(exe_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return exe_name.lower() in result.stdout.lower()
    except Exception:
        # Can't confirm either way - assume it's running rather than risk
        # spawning a duplicate Potassium instance.
        return True


def ensure_potassium_running(potassium_path: str) -> None:
    """No-op if Potassium is already resident - it's confirmed to
    autoexec-inject into any Roblox process without needing to be
    relaunched itself, so this only starts it up if it isn't running at
    all (e.g. first run, or it crashed)."""
    if not potassium_path:
        return

    exe_name = Path(potassium_path).name
    if is_process_running(exe_name):
        return

    try:
        subprocess.Popen([potassium_path], cwd=str(Path(potassium_path).parent))
    except Exception:
        pass


def kill_process_by_pid(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _is_real_game_client(command_line: str | None) -> bool:
    """Roblox launches a second, helper process alongside every game
    client - it has an empty or "\\??\\"-prefixed command line. RBX Alt
    Manager's own RobloxWatcher.CheckProcesses filters this out by
    requiring "-t "/"-j " launch args (its own launch path invokes
    RobloxPlayerBeta.exe directly with those flags), but confirmed live on
    this machine, launching via the `roblox-player:` protocol URI (as this
    tool does) instead re-execs RobloxPlayerBeta.exe with the *entire* URI
    as a single argument - `...+launchmode:play+gameinfo:...` - not
    `-t`/`-j` flags at all. Accept either shape so this keeps working
    whichever way a given Roblox install ends up invoking the client.
    """
    if not command_line:
        return False
    if command_line.startswith("\\??\\"):
        return False
    return (
        "-t " in command_line
        or "-j " in command_line
        or "gameinfo:" in command_line
        or "launchmode:play" in command_line
    )


def list_roblox_pids() -> set[int]:
    """Only the real game client process for each running Roblox instance
    - see `_is_real_game_client` for why the naive "every process named
    RobloxPlayerBeta.exe" list is wrong."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='RobloxPlayerBeta.exe'\" "
                "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return set()

    raw = result.stdout.strip()
    if not raw:
        return set()

    try:
        data = json.loads(raw)
    except Exception:
        return set()

    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        return set()

    pids: set[int] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not _is_real_game_client(entry.get("CommandLine")):
            continue
        pid = entry.get("ProcessId")
        if pid is not None:
            try:
                pids.add(int(pid))
            except (TypeError, ValueError):
                continue
    return pids


def is_pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return str(pid) in result.stdout
    except Exception:
        # Assume alive on lookup failure to avoid a false relaunch storm.
        return True
