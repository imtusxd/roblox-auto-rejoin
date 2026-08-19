# Roblox Auto-Rejoin

Standalone desktop tool that watches every account's Roblox client, detects
a real disconnect (by tailing the client's own native log file - not an
in-game heartbeat script), and automatically relaunches + rejoins the game.
Fully independent from `backend/` and `eldorado-bot/` - this tool only
launches/watches Roblox; the trade/delivery script itself keeps running
because it lives in **Potassium's autoexec folder** and re-injects itself
on every fresh inject.

## How it works

1. `cookies.txt` holds one `.ROBLOSECURITY` value per line (same convention
   as `YummyWebPlayer/cookie.txt`) - add or remove accounts by editing that
   file, no fixed cap. A line can carry extra whitespace-separated tokens
   after the cookie: a bare number is a per-account place id override
   (`<cookie> <place id>`), and `sv=<code or link>` is a per-account
   private server override (`<cookie> sv=<code or link>`, or both
   together, in either order) - e.g. watch some accounts on public Blox
   Fruits servers and others on a specific Grow a Garden 2 private server,
   in the same run. A line with no override falls back to the default
   `place_id` / a public server. `sv=` accepts either a bare access code
   or the full URL from Roblox's "Copy Link" button - the code is pulled
   out of the link automatically (`accounts.extract_private_server_code`).
   The GUI's Game column shows where each row is actually going (resolved
   through `known_games` in `config.json` for a friendly name when one is
   set, otherwise the raw numeric id, plus a 🔒 SV flag when a private
   server is set) - **double-click a Game cell**, or tick several rows'
   checkbox column and use **"Set Place ID / Server for selected..."**, to
   edit these without hand-editing a file full of long, sensitive cookie
   values.
2. For each account, `roblox_auth.py` exchanges the cookie for a one-time
   launch ticket (the same CSRF-then-ticket dance
   `Roblox-Account-Manager`'s `Account.cs` uses).
3. `launcher.py` opens the `roblox-player:` protocol URI with that ticket
   (same thing your browser does when you click "Play" on roblox.com), and
   makes sure Potassium is running so its autoexec folder re-injects the
   trade script.
4. `disconnect_watcher.py` finds the new `RobloxPlayerBeta.exe`'s log file
   via Sysinternals `handle.exe` and tails it, watching for the two native
   log lines that mean "joined" and "disconnected" (ported from
   `Roblox-Account-Manager`'s `RobloxProcess.cs`).
5. `rejoin_controller.py` runs one independent loop per account: launch ->
   wait to join -> keep watching -> once disconnected longer than the
   configured timeout (or the process just exits), kill it and relaunch
   with a fresh ticket. Accounts are launched staggered, not all at once,
   since this machine also runs the delivery backend/bot alongside Roblox.
   Before every launch (if `check_cookie_before_launch` is on) it also
   checks the cookie is still valid via `cookie_check.py` - a *confirmed*
   invalid cookie (never just a network hiccup) is moved out of
   `cookies.txt` into `dead_cookies.txt` by `accounts.mark_cookie_dead`
   and that account stops being watched for good, the same way
   `YummyWebPlayer/switched/deadcookie.txt` retires a dead cookie instead
   of retrying it forever - a `.ROBLOSECURITY` cookie doesn't come back on
   its own once invalidated.
6. `gui.py` is the desktop window: account grid with live status, a
   Start/Stop button, a settings dialog, and a log feed. Closing the
   window (the X button) while anything is being watched just minimizes
   it instead of exiting - the rejoin loop only exists as long as this
   process is alive, so closing it would otherwise silently stop
   auto-rejoin for every account with no warning. Click Stop first to
   actually allow the window to close.

Every `tasklist`/`powershell`/`handle.exe` subprocess call above
(`launcher.list_roblox_pids`, `is_pid_alive`, `disconnect_watcher.find_log_path`,
...) runs with `CREATE_NO_WINDOW` - without it, each one briefly flashes
its own console window, and since several of these run every few seconds
for as long as an account is online, that adds up to a lot of flicker.

A launch attempt that keeps failing (process never appears, times out
waiting to join, ...) backs off exponentially per account
(`error_retry_seconds`, doubling per consecutive failure, capped at 5
minutes) instead of retrying on a fixed short interval - every attempt
fetches a fresh auth ticket first, and hammering that on a tight loop
across several accounts stuck failing at once is the kind of sustained
request rate that gets a cookie rate-limited (HTTP 429) rather than
actually recovering any faster. The streak resets to zero on the next
successful join.

`fps_control.py` can cap every Roblox window's frame rate to reduce
CPU/GPU load when running many accounts at once (Settings -> "Target FPS,
ALL windows") - applied right before Start watching by writing Roblox's
own `%LOCALAPPDATA%\Roblox\ClientSettings\ClientAppSettings.json`
(`DFIntTaskSchedulerTargetFps`), the same officially-supported client
setting real players already use to adjust their own FPS cap. This is
**one shared value for every window**, not per-account - there's no safe
way to cap two simultaneously-running Roblox windows differently without
patching each process's memory directly (what community "FPS unlocker"
tools do), which is fragile and out of scope here. It also only affects
windows launched *after* it's applied - already-running windows keep
whatever cap they started with. Leave it at 0 to never touch the file at
all (a cap set some other way is left alone).

## Running it

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

First run creates `config.json` with defaults (see `app_config.py`) -
adjust the Place ID, `handle.exe` path (a copy is already bundled next to
this README), and Potassium path from the Settings dialog if needed.

`handle.exe` requires its Sysinternals EULA to be accepted once:

```
handle.exe -accepteula
```

## Tests

```
.venv\Scripts\python -m pytest
```

Covers the two pure/isolatable pieces: `roblox_auth.py`'s CSRF/ticket
header parsing (mocked HTTP, no real network) and `disconnect_watcher.py`'s
regex matching + log-tailing (including `find_log_path`'s `handle.exe`
output parsing, mocked). The real Roblox launch/rejoin cycle can't be
meaningfully unit tested - smoke-test it by hand: start watching one real
account, force-kill its Roblox window, and confirm the status column flips
to "Disconnected" and then relaunches after the configured timeout.
