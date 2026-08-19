"""Caps (or uncaps) the frame rate for every Roblox window on this
machine - one shared value, not per-account/per-window. There is no safe
way to give two simultaneously-running Roblox windows two different FPS
caps without patching each process's memory directly (what community "FPS
unlocker" tools do) - fragile, breaks on every Roblox update, and not
something this project re-implements. This instead uses the officially-
supported client config file every Roblox install already reads on
startup: %LOCALAPPDATA%\\Roblox\\ClientSettings\\ClientAppSettings.json,
DFIntTaskSchedulerTargetFps - the same well-documented FastFlag legitimate
players already use to raise/lower their own FPS cap (nothing exploit-y
about it, it's a standard client setting).

Important limitation: this file is only ever read by a Roblox process at
ITS OWN startup - changing it has no effect on windows already running.
Apply it (see rejoin_controller/gui.py's call site, right before Start
watching) before launching, not while accounts are already online.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FPS_FLAG_NAME = "DFIntTaskSchedulerTargetFps"


def client_settings_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return Path(local_app_data) / "Roblox" / "ClientSettings" / "ClientAppSettings.json"


def _read_existing(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Missing file, corrupt JSON, whatever - start fresh rather than
        # crash. Anything already in there we can't parse would be lost,
        # but a corrupt ClientAppSettings.json isn't something worth
        # preserving anyway (Roblox itself won't have been reading it
        # correctly either).
        return {}


def apply_target_fps(target_fps: int, path: Path | None = None) -> bool:
    """target_fps <= 0 removes the flag entirely (uncapped - Roblox's own
    default, usually 60). Merges into whatever's already in the file
    rather than overwriting it, so any other FastFlag a user has set by
    hand survives. Returns whether the write succeeded - best-effort,
    never raises, since a failed FPS cap is a resource-usage nicety, not a
    correctness requirement (same convention as process_manager.py's
    apply_resource_policy)."""
    target_path = path or client_settings_path()

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        settings = _read_existing(target_path)

        if target_fps > 0:
            settings[FPS_FLAG_NAME] = target_fps
        else:
            settings.pop(FPS_FLAG_NAME, None)

        target_path.write_text(json.dumps(settings, indent=4), encoding="utf-8")
        return True
    except Exception:
        return False
