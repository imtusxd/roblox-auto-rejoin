"""Loads the account list from a plain cookie-list file, mirroring
YummyWebPlayer's own cookie.txt convention: one .ROBLOSECURITY value per
line, comments (#...) and blank lines ignored. Add more accounts by adding
more lines - no hardcoded cap on how many this tool can watch.

Each line may carry extra whitespace-separated tokens after the cookie:

    <cookie>
    <cookie> <place id>
    <cookie> sv=<private server code or full link>
    <cookie> <place id> sv=<private server code or full link>

A bare all-digits token is a per-account place id override (falls back to
AppConfig.place_id / ControllerConfig.place_id, the "default game", when
absent). An `sv=` token is a per-account private server override (falls
back to a public server when absent) - its value can be either a bare
access code or a full "Copy Link" URL from Roblox, either works (see
extract_private_server_code). Together these let one run watch several
different games/servers at once instead of needing a separate copy of
this tool per game.
"""
from __future__ import annotations

import dataclasses
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Same call Account.cs's Account(Cookie) constructor makes to resolve a
# display name/user id for a raw cookie.
ACCOUNT_JSON_URL = "https://www.roblox.com/my/account/json"


@dataclasses.dataclass
class Account:
    index: int
    cookie: str
    username: str | None = None
    user_id: int | None = None
    # Per-account overrides, parsed from this account's cookies.txt line -
    # None means "use the run's default" (place_id) / "public server"
    # (private_server).
    place_id: str | None = None
    private_server: str | None = None

    @property
    def label(self) -> str:
        return self.username or f"Account #{self.index + 1}"


def extract_private_server_code(value: str) -> str:
    """`value` may already be a bare private-server access code, or a full
    URL copied from Roblox's "Copy Link" button
    (.../games/123/Game-Name?privateServerLinkCode=...) - either is
    accepted as input, so users don't have to manually dig the code out of
    a pasted link.

    Deliberately does NOT parse Roblox's newer roblox.com/share?code=...
    format - see is_unsupported_share_link. A value in that shape is
    passed through unchanged (not "extracted") specifically so it stays
    recognizable to that check at launch time, rather than looking like a
    normal, usable code.
    """
    if "privateServerLinkCode=" in value:
        query = urllib.parse.urlparse(value).query
        codes = urllib.parse.parse_qs(query).get("privateServerLinkCode")
        if codes:
            return codes[0]
    return value


def is_unsupported_share_link(value: str) -> bool:
    """True for Roblox's newer roblox.com/share?code=...&type=Server link
    format specifically (rolled out since Oct 2023) - unlike the older
    privateServerLinkCode format, Roblox has not published any API to
    resolve this format's code into actual join info; it's confirmed
    (Roblox devforum) to be for internal/client use, only resolved by
    opening the link in a real, already-logged-in browser. There is no
    known way to redeem it for a specific account's cookie the way this
    tool launches everything else, so passing it straight through as a
    PlaceLauncher accessCode (the old format's mechanism) just produces a
    launch that silently never opens a real game window - confirmed live
    on this machine. Callers should check this and fall back to a public
    server instead of attempting the launch with it.
    """
    return "type=Server" in value or "roblox.com/share" in value or "/share?code=" in value


def parse_cookie_line(raw_line: str) -> tuple[str, str | None, str | None] | None:
    """One line of cookies.txt -> (cookie, place_id_or_None,
    private_server_or_None), or None for a blank/comment line.

    A .ROBLOSECURITY value never contains whitespace (cookie-value syntax
    forbids raw spaces), so splitting on whitespace is unambiguous. Among
    the tokens after the cookie: one that's all digits is the place id
    override; one starting with "sv=" (case-insensitive) is the private
    server override. Every pre-existing cookies.txt line (just a bare
    cookie) is unaffected - both come back None.
    """
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split()
    cookie = parts[0]
    place_id: str | None = None
    private_server: str | None = None
    for token in parts[1:]:
        if token.lower().startswith("sv="):
            private_server = extract_private_server_code(token[3:])
        elif token.isdigit():
            place_id = token
    return cookie, place_id, private_server


def _format_cookie_line(cookie: str, place_id: str | None, private_server: str | None) -> str:
    """The inverse of parse_cookie_line - used by set_place_id/
    set_private_server so editing one override never silently drops the
    other one already set on the same line."""
    parts = [cookie]
    if place_id:
        parts.append(place_id)
    if private_server:
        parts.append(f"sv={private_server}")
    return " ".join(parts)


def load_cookies(path: Path) -> list[str]:
    if not path.exists():
        return []

    cookies: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_cookie_line(raw_line)
        if parsed is not None:
            cookies.append(parsed[0])
    return cookies


def load_accounts(path: Path) -> list[Account]:
    if not path.exists():
        return []

    loaded: list[Account] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_cookie_line(raw_line)
        if parsed is None:
            continue
        cookie, place_id, private_server = parsed
        loaded.append(
            Account(index=len(loaded), cookie=cookie, place_id=place_id, private_server=private_server)
        )
    return loaded


def resolve_account_info(session, account: Account, timeout: float = 10.0) -> None:
    """Best-effort - fills in username/user_id for display purposes only.
    Leaves the fields as None on any failure so the caller can still show a
    stable "Account #N" placeholder label instead of crashing the watch
    loop over what is purely cosmetic information.
    """
    try:
        response = session.get(
            ACCOUNT_JSON_URL,
            cookies={".ROBLOSECURITY": account.cookie},
            timeout=timeout,
        )
        if response.status_code != 200:
            return
        data = response.json()
    except Exception:
        return

    # Roblox's legacy endpoint has used both casings across versions.
    account.username = data.get("Name") or data.get("name") or data.get("UserName")
    raw_id = data.get("UserId") if "UserId" in data else data.get("Id") or data.get("id")
    if raw_id is not None:
        try:
            account.user_id = int(raw_id)
        except (TypeError, ValueError):
            pass


# Guards every read-modify-write of cookies.txt below (mark_cookie_dead,
# set_place_id, set_private_server): two calls racing - e.g. two accounts
# confirmed dead in the same poll cycle, or an edit landing mid-removal -
# must not clobber each other's changes to the file.
_cookies_file_lock = threading.Lock()


def _remove_cookie_line(cookies_path: Path, cookie: str) -> bool:
    """Shared removal logic for mark_cookie_dead/remove_account - finds
    `cookie`'s line (matched via parse_cookie_line, so it's found
    regardless of any place id / private server override on that line)
    and rewrites the file without it, preserving every other line
    verbatim and in order. Returns False (no write at all) if `cookie`
    isn't found. Caller must already hold _cookies_file_lock."""
    if not cookies_path.exists():
        return False

    raw_lines = cookies_path.read_text(encoding="utf-8-sig").splitlines()
    kept_lines: list[str] = []
    removed = False
    for raw_line in raw_lines:
        parsed = parse_cookie_line(raw_line)
        if not removed and parsed is not None and parsed[0] == cookie:
            removed = True
            continue
        kept_lines.append(raw_line)

    if not removed:
        return False

    cookies_path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
    return True


def remove_account(cookies_path: Path, cookie: str) -> bool:
    """Removes `cookie`'s line from cookies_path entirely - a deliberate,
    user-initiated "stop watching this account", NOT a liveness verdict
    (unlike mark_cookie_dead, this writes nothing to dead_cookies_path).
    Used by the API server's DELETE /api/accounts/{index} - the one
    account-management action allowed over the network (see api_server.py's
    own docstring for why adding a NEW cookie over the network is not)."""
    with _cookies_file_lock:
        return _remove_cookie_line(cookies_path, cookie)


def mark_cookie_dead(cookies_path: Path, dead_cookies_path: Path, cookie: str, reason: str = "") -> bool:
    """Pulls `cookie` out of `cookies_path` for good and appends it (with a
    UTC timestamp and `reason`) to `dead_cookies_path` - the equivalent of
    YummyWebPlayer's own switched/deadcookie.txt: once a .ROBLOSECURITY
    cookie is confirmed invalid it never becomes valid again on its own
    (logout/ban/password-reset all invalidate it permanently), so there's
    no point leaving it in rotation to be re-checked forever. Call this
    once, when `cookie_check.check_cookie` returns INVALID - not on
    UNKNOWN, which just means the check itself failed.

    `cookie` is matched against each line's parsed cookie part (via
    parse_cookie_line), not the raw line - so this still finds and removes
    the right line whether or not it carries place id / private server
    overrides.

    Returns True if `cookie` was found in `cookies_path` and moved, False
    if it wasn't there (e.g. already removed by a concurrent call, or the
    file doesn't exist).

    Only the first matching line is removed; every other line (including
    comments, blanks, and any overrides) is preserved verbatim and in
    order.
    """
    with _cookies_file_lock:
        if not _remove_cookie_line(cookies_path, cookie):
            return False

        dead_cookies_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        suffix = f" - {reason}" if reason else ""
        with dead_cookies_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {cookie}{suffix}\n")

        return True


def _rewrite_override(
    cookies_path: Path,
    cookie: str,
    apply: Callable[[str | None, str | None], tuple[str | None, str | None]],
) -> bool:
    """Shared read-modify-write for set_place_id/set_private_server: finds
    `cookie`'s line, calls `apply(place_id, private_server)` to get the new
    (place_id, private_server) pair, and rewrites just that line with
    _format_cookie_line - preserving every other line untouched. Returns
    False (no write at all) if `cookie` isn't found.
    """
    with _cookies_file_lock:
        if not cookies_path.exists():
            return False

        raw_lines = cookies_path.read_text(encoding="utf-8-sig").splitlines()
        new_lines: list[str] = []
        found = False
        for raw_line in raw_lines:
            parsed = parse_cookie_line(raw_line)
            if not found and parsed is not None and parsed[0] == cookie:
                found = True
                _cookie, place_id, private_server = parsed
                new_place_id, new_private_server = apply(place_id, private_server)
                new_lines.append(_format_cookie_line(cookie, new_place_id, new_private_server))
            else:
                new_lines.append(raw_line)

        if not found:
            return False

        cookies_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True


def set_place_id(cookies_path: Path, cookie: str, place_id: str | None) -> bool:
    """Rewrites `cookie`'s line to carry `place_id` as its place id
    override (or drops it if `place_id` is None/blank), leaving any
    private server override on that same line untouched. Used by the
    GUI's per-row/bulk editors so an override can be set without
    hand-editing a file full of long, sensitive cookie values.
    """
    return _rewrite_override(
        cookies_path, cookie, lambda _old_place_id, private_server: (place_id, private_server)
    )


def set_private_server(cookies_path: Path, cookie: str, private_server: str | None) -> bool:
    """Rewrites `cookie`'s line to carry `private_server` as its private
    server override (or drops it, falling back to a public server, if
    `private_server` is None/blank), leaving any place id override on that
    same line untouched. `private_server` may be a bare access code or a
    full "Copy Link" URL - see extract_private_server_code.
    """
    code = extract_private_server_code(private_server) if private_server else None
    return _rewrite_override(cookies_path, cookie, lambda place_id, _old: (place_id, code))
