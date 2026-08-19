"""Persisted settings, same flat key/value shape YummyWebPlayer's own
config.json already uses for familiarity. Lives next to the .exe/script
and is created with sane defaults on first run.
"""
from __future__ import annotations

import dataclasses
import json
import secrets
from pathlib import Path

from paths import app_dir, bundled_resource_dir

CONFIG_PATH = app_dir() / "config.json"
COOKIES_PATH = app_dir() / "cookies.txt"
DEAD_COOKIES_PATH = app_dir() / "dead_cookies.txt"

# GAG2's place id, same default YummyWebPlayer's own config.json already
# points at on this machine.
DEFAULT_PLACE_ID = "16732694052"


@dataclasses.dataclass
class AppConfig:
    # Default place id, used by any cookies.txt line that doesn't specify
    # its own (see accounts.parse_cookie_line) - i.e. this is a fallback,
    # not "the" game, once any line carries a per-account override.
    place_id: str = DEFAULT_PLACE_ID
    # Optional place id -> friendly display name, shown in the GUI's Game
    # column instead of a bare numeric id. Not exposed as its own Settings
    # row (a free-form id->name map doesn't fit the label/entry grid the
    # other fields use) - add entries by editing config.json directly.
    known_games: dict[str, str] = dataclasses.field(
        default_factory=lambda: {DEFAULT_PLACE_ID: "Grow a Garden 2"}
    )
    cookies_path: str = str(COOKIES_PATH)
    # Confirmed-dead cookies (see accounts.mark_cookie_dead) are pulled out
    # of cookies_path and appended here instead of being retried forever -
    # mirrors YummyWebPlayer's own switched/deadcookie.txt. Blank = keep the
    # dead-cookie stop behavior but skip writing an audit file.
    dead_cookies_path: str = str(DEAD_COOKIES_PATH)
    # bundled_resource_dir(), not app_dir() - handle.exe is a read-only
    # resource bundled INTO the .exe (PyInstaller --add-data), extracted
    # to a fresh temp folder each run, not something meant to persist next
    # to the .exe the way config.json/cookies.txt do.
    handle_exe_path: str = str(bundled_resource_dir() / "handle.exe")
    potassium_path: str = str(
        Path(r"C:\Users\Admin\Desktop\New folder (2)\OpXOyuApWKTlFzrV (1)\Potassium.exe")
    )
    no_connection_timeout_seconds: float = 120.0
    join_timeout_seconds: float = 90.0
    poll_interval_seconds: float = 3.0
    stagger_launch_seconds: float = 60.0
    max_concurrent_launches: int = 3

    # Window layout (mirrors Yummy's Windows Per Rows / Fixed Size / Delay Minimize)
    arrange_windows: bool = True
    windows_per_row: int = 10
    window_width: int = 300
    window_height: int = 200
    minimize_after_seconds: float = 5.0

    # Resource management (mirrors Yummy's Set Priority Low / Set Affinity)
    process_priority: str = "below_normal"  # "normal" | "below_normal" | "low"
    cpu_affinity_core_count: int = 0  # 0 = leave affinity untouched

    # Discord webhook (mirrors Yummy's Webhook Url / Webhook Delay Send [M])
    webhook_url: str = ""
    webhook_batch_seconds: float = 60.0

    # Cookie liveness check (mirrors Yummy's Thread Check Cookie). A
    # confirmed-invalid cookie is moved to dead_cookies_path and that
    # account stops being watched for good - see accounts.mark_cookie_dead
    # - so there's no "recheck interval" to configure here anymore.
    check_cookie_before_launch: bool = True

    # FPS cap applied to EVERY Roblox window (see fps_control.py) - there's
    # no safe way to cap windows individually, this is one shared value.
    # 0 = leave uncapped (Roblox's own default). Only affects windows
    # launched AFTER this is applied, not ones already running.
    target_fps: int = 0

    # Control API (see api_server.py) - lets a separate dashboard (e.g. the
    # website-cloner Next.js app) view live status and start/stop/manage
    # accounts over the network. User's own explicit, deliberate choices:
    # bound to 0.0.0.0 (reachable from other machines on the LAN, not just
    # this one) and protected by a mandatory api_key header - see
    # api_server.py's own module docstring for the full security posture,
    # including why no endpoint ever accepts/returns a raw cookie.
    api_enabled: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8765
    # Blank only ever appears in a hand-edited config.json - load_config
    # below fills this in with a fresh random key (and re-saves) the
    # moment it sees one, so in practice this is never blank at runtime.
    # Never regenerated automatically after that first time: anything
    # already using the key (the dashboard) would silently break.
    api_key: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        defaults = cls()
        known = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return dataclasses.replace(defaults, **filtered)


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        config = AppConfig()
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            config = AppConfig.from_dict(data)
        except Exception:
            config = AppConfig()

    if not config.api_key:
        # First run, or a hand-edited config.json that blanked it out -
        # either way, api_server.py must never actually start with an
        # empty key (that would mean "no auth at all" on a LAN-reachable
        # control API). Generated once and persisted immediately, not
        # regenerated on every load.
        config = dataclasses.replace(config, api_key=secrets.token_urlsafe(32))
        save_config(config, path)

    return config


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(config.to_dict(), indent=4), encoding="utf-8")
