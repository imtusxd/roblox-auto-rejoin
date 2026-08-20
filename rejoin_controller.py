"""Orchestrates one account's full watch/relaunch cycle:

  check cookie is still valid -> launch -> wait for a new RobloxPlayerBeta.exe
  pid -> locate its log file -> wait for the "Joining game" line -> apply
  window layout + resource policy -> keep polling the log for a disconnect
  -> once disconnected longer than no_connection_timeout_seconds (or the
  process just exits on its own), kill it and loop back around to relaunch
  with a fresh ticket.

Each account runs as its own independent thread so one account's relaunch
never blocks another's, and launches across accounts are staggered
(stagger_launch_seconds apart) *and* capped by a max_concurrent_launches
semaphore rather than firing all at once - this machine is already
confirmed resource-constrained running backend + eldorado-bot + Roblox +
Chrome together, so a thundering herd of simultaneous Roblox launches is
exactly the kind of contention to avoid.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

import accounts
import cookie_check
import disconnect_watcher as dw
import launcher
import process_manager
import roblox_auth
import share_link
import window_manager
from accounts import Account
from webhook import WebhookNotifier

logger = logging.getLogger("rejoin_controller")


@dataclasses.dataclass
class ControllerConfig:
    place_id: str
    handle_exe_path: str
    potassium_path: str
    cookies_path: str = ""
    # Where accounts.mark_cookie_dead appends confirmed-dead cookies. Blank
    # skips the file (the account still stops being watched either way -
    # this only controls whether an audit trail is written).
    dead_cookies_path: str = ""
    no_connection_timeout_seconds: float = 120.0
    join_timeout_seconds: float = 90.0
    poll_interval_seconds: float = 3.0
    log_lookup_retries: int = 20
    log_lookup_retry_delay_seconds: float = 1.5
    pid_lookup_timeout_seconds: float = 30.0
    error_retry_seconds: float = 15.0
    max_concurrent_launches: int = 3

    arrange_windows: bool = True
    windows_per_row: int = 10
    window_width: int = 300
    window_height: int = 200
    minimize_after_seconds: float = 5.0

    process_priority: str = "below_normal"
    cpu_affinity_core_count: int = 0

    check_cookie_before_launch: bool = True


class AccountStatus:
    STARTING = "Starting"
    CHECKING_COOKIE = "Checking cookie"
    INVALID_COOKIE = "Invalid cookie"
    LAUNCHING = "Launching"
    WAITING_FOR_JOIN = "Waiting to join"
    ONLINE = "Online"
    DISCONNECTED = "Disconnected"
    RELAUNCHING = "Relaunching"
    ERROR = "Error"
    STOPPED = "Stopped"
    # Terminal state: cookie confirmed invalid, moved to dead_cookies_path,
    # and this account's watch loop has exited for good - distinct from
    # STOPPED (user-requested) and INVALID_COOKIE (not currently used as a
    # resting state; kept as a status constant for callers that still want
    # to report a transient checking-cookie failure).
    DEAD = "Dead (cookie invalid)"


class AccountDead(Exception):  # noqa: N818 - matches AuthError's naming, not an "Error" here
    """Raised internally once a cookie is confirmed invalid and has been
    handed to accounts.mark_cookie_dead. Caught by _run_account to stop
    that account's loop for good instead of retrying a cookie that, unlike
    a network hiccup, will never become valid again on its own."""


@dataclasses.dataclass
class AccountRuntime:
    account: Account
    status: str = AccountStatus.STARTING
    detail: str = ""
    pid: Optional[int] = None
    # Consecutive launch failures ("process never appeared", "timed out
    # waiting to join", an unhandled exception, ...) since the last
    # successful join. Drives exponential backoff (_backoff_seconds) -
    # every failed launch attempt still fetches a fresh auth ticket first
    # (2 HTTP requests to auth.roblox.com), and retrying that at a fixed
    # short interval across several accounts stuck failing at once is
    # exactly the kind of sustained request rate that gets a cookie
    # rate-limited (HTTP 429) rather than actually fixing anything faster.
    consecutive_failures: int = 0


OnUpdate = Callable[[AccountRuntime], None]
OnLog = Callable[[str], None]


class RejoinController:
    def __init__(
        self,
        config: ControllerConfig,
        on_update: Optional[OnUpdate] = None,
        on_log: Optional[OnLog] = None,
        notifier: Optional[WebhookNotifier] = None,
    ) -> None:
        self.config = config
        self.on_update = on_update or (lambda runtime: None)
        self.on_log = on_log or (lambda message: None)
        self.notifier = notifier
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._launch_slots = threading.Semaphore(max(1, config.max_concurrent_launches))

    def start(self, accounts: list[Account], stagger_seconds: float) -> None:
        self._stop_event.clear()
        self._threads = []
        if self.notifier:
            self.notifier.start()
            self.notifier.notify(f"Started watching {len(accounts)} account(s).")
        for i, account in enumerate(accounts):
            delay = i * stagger_seconds
            thread = threading.Thread(
                target=self._run_account,
                args=(account, delay),
                daemon=True,
                name=f"rejoin-{account.label}",
            )
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.notifier:
            self.notifier.stop()

    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    # -- per-account loop -------------------------------------------------

    def _log(self, account: Account, message: str) -> None:
        self.on_log(f"[{account.label}] {message}")

    def _notify(self, account: Account, message: str) -> None:
        if self.notifier:
            self.notifier.notify(f"[{account.label}] {message}")

    _MAX_BACKOFF_SECONDS = 300.0  # 5 minutes - a hard ceiling regardless of streak length

    def _backoff_seconds(self, runtime: AccountRuntime) -> float:
        """error_retry_seconds, doubled per consecutive failure and capped
        at _MAX_BACKOFF_SECONDS - so a brief hiccup still retries promptly
        (1x, 2x, 4x, ... error_retry_seconds) but a sustained failure
        streak backs off instead of hammering auth.roblox.com with a fresh
        ticket request on a fixed short interval forever."""
        multiplier = 2 ** min(runtime.consecutive_failures, 8)  # cap the exponent, not just the result
        return min(self.config.error_retry_seconds * multiplier, self._MAX_BACKOFF_SECONDS)

    def _wait_after_failure(self, runtime: AccountRuntime) -> bool:
        """Increments the failure streak and waits the backoff for it.
        Returns True if the controller was stopped during the wait (same
        convention as threading.Event.wait) so callers can bail out early."""
        runtime.consecutive_failures += 1
        wait_seconds = self._backoff_seconds(runtime)
        self._log(
            runtime.account,
            f"retry #{runtime.consecutive_failures} in {wait_seconds:.0f}s",
        )
        return self._stop_event.wait(wait_seconds)

    def _run_account(self, account: Account, initial_delay: float) -> None:
        runtime = AccountRuntime(account=account)
        if self._stop_event.wait(initial_delay):
            return

        session = requests.Session()

        while not self._stop_event.is_set():
            try:
                self._launch_and_watch(account, runtime, session)
            except AccountDead:
                # Terminal - _handle_dead_cookie already set runtime.status
                # to DEAD and fired on_update/notify. Leave that state as
                # the final one instead of overwriting it with STOPPED
                # below, and don't loop back around to retry.
                return
            except Exception as exc:  # noqa: BLE001 - keep the watch loop alive
                logger.exception("Unhandled error watching %s", account.label)
                runtime.status = AccountStatus.ERROR
                runtime.detail = str(exc)
                self._log(account, f"error: {exc}")
                self._notify(account, f"error: {exc}")
                self.on_update(runtime)
                self._wait_after_failure(runtime)

        runtime.status = AccountStatus.STOPPED
        runtime.detail = ""
        self.on_update(runtime)

    def _launch_and_watch(self, account: Account, runtime: AccountRuntime, session) -> None:
        if self.config.check_cookie_before_launch:
            runtime.status = AccountStatus.CHECKING_COOKIE
            runtime.detail = "Checking cookie validity"
            self.on_update(runtime)

            result = cookie_check.check_cookie(session, account.cookie)
            if result.status == cookie_check.CookieStatus.INVALID:
                self._handle_dead_cookie(account, runtime, result)
                raise AccountDead()
            # UNKNOWN (network hiccup) falls through and tries to launch
            # anyway - a failed cookie check isn't proof the cookie is bad.

        with self._launch_slots:
            if self._stop_event.is_set():
                return
            self._do_launch(account, runtime, session)

    def _handle_dead_cookie(self, account: Account, runtime: AccountRuntime, result) -> None:
        """Moves account.cookie to config.dead_cookies_path (if set) and
        marks this account DEAD for good. Only call this on a *confirmed*
        invalid cookie (cookie_check.CookieStatus.INVALID) - never on
        UNKNOWN, where the check itself failed rather than proved the
        cookie dead.
        """
        reason = result.detail or "Cookie invalid/expired"
        moved = False
        if self.config.dead_cookies_path and self.config.cookies_path:
            try:
                moved = accounts.mark_cookie_dead(
                    Path(self.config.cookies_path),
                    Path(self.config.dead_cookies_path),
                    account.cookie,
                    reason=reason,
                )
            except OSError as exc:
                self._log(account, f"could not write to dead-cookies file: {exc}")

        runtime.status = AccountStatus.DEAD
        runtime.detail = reason
        runtime.pid = None
        suffix = " - moved to dead-cookies file" if moved else ""
        self._log(account, f"cookie confirmed dead ({reason}){suffix}, no longer watching")
        self._notify(account, f"cookie confirmed dead, removing from rotation: {reason}{suffix}")
        self.on_update(runtime)

    def _do_launch(self, account: Account, runtime: AccountRuntime, session) -> None:
        runtime.status = AccountStatus.LAUNCHING
        runtime.detail = "Fetching auth ticket"
        self.on_update(runtime)
        self._log(account, "fetching auth ticket")

        auth = roblox_auth.fetch_auth_ticket(session, account.cookie)

        # A per-account place id / private server (parsed from its
        # cookies.txt line - see accounts.parse_cookie_line) overrides the
        # run's default, so different accounts can farm different
        # games/servers in the same run.
        place_id = account.place_id or self.config.place_id
        target_desc = f"place {place_id}"

        private_server_code = account.private_server
        if private_server_code and accounts.is_unsupported_share_link(private_server_code):
            # Roblox's newer share-link format - see
            # accounts.is_unsupported_share_link. Resolved into the real,
            # redeemable access code via share_link.resolve_share_link
            # (confirmed live 2026-08-20 - see that module's own
            # docstring) using the SAME csrf token already fetched above
            # for the auth ticket, no extra round trip. Only falls back to
            # the public server (the original safety net) if resolution
            # itself fails - an expired/invalid invite, or this account
            # genuinely has no access to it - rather than for every single
            # launch the way it unconditionally used to.
            try:
                resolved = share_link.resolve_share_link(
                    session, account.cookie, private_server_code, auth.csrf_token
                )
            except share_link.ShareLinkError as exc:
                self._log(account, f"could not resolve private server share link, joining public server instead: {exc}")
                private_server_code = None
                target_desc += " (private server override skipped - share link did not resolve)"
            else:
                if resolved.place_id != place_id:
                    self._log(
                        account,
                        f"note: private server's real place ({resolved.place_id}) differs from "
                        f"configured place_id ({place_id}) - launching with place_id as configured",
                    )
                private_server_code = resolved.access_code
                target_desc += f" (private server {private_server_code}, resolved from share link)"
        elif private_server_code:
            target_desc += f" (private server {private_server_code})"

        existing_pids = launcher.list_roblox_pids()
        tracker_id = launcher.launch_via_protocol(
            auth.ticket, place_id, private_server_code=private_server_code
        )
        launcher.ensure_potassium_running(self.config.potassium_path)
        self._log(
            account,
            f"launched {target_desc} (browserTrackerId={tracker_id}), waiting for process",
        )

        runtime.status = AccountStatus.WAITING_FOR_JOIN
        runtime.detail = "Waiting for Roblox process"
        self.on_update(runtime)

        pid = self._wait_for_new_pid(existing_pids)
        if pid is None:
            runtime.status = AccountStatus.ERROR
            runtime.detail = "Roblox process never appeared"
            self._log(account, "process never appeared")
            self._notify(account, "Roblox process never appeared after launch")
            self.on_update(runtime)
            self._wait_after_failure(runtime)
            return
        runtime.pid = pid
        self._log(account, f"process pid={pid}")

        process_manager.apply_resource_policy(
            pid,
            process_manager.ResourcePolicy(
                priority=self.config.process_priority,
                cpu_affinity_core_count=self.config.cpu_affinity_core_count,
            ),
        )

        runtime.detail = "Locating log file"
        self.on_update(runtime)
        log_path = self._wait_for_log_path(pid)
        if log_path is None:
            runtime.status = AccountStatus.ERROR
            runtime.detail = "Could not locate Roblox log file"
            self._log(account, "could not locate log file")
            self._notify(account, "could not locate Roblox log file")
            self.on_update(runtime)
            self._wait_after_failure(runtime)
            return

        tailer = dw.LogTailer(log_path)

        runtime.detail = f"Waiting to join ({log_path.name})"
        self.on_update(runtime)

        join_deadline = time.monotonic() + self.config.join_timeout_seconds
        while not tailer.state.is_connected and time.monotonic() < join_deadline:
            if self._stop_event.wait(self.config.poll_interval_seconds):
                return
            tailer.poll()

        if not tailer.state.is_connected:
            runtime.status = AccountStatus.ERROR
            runtime.detail = "Timed out waiting to join the game"
            self._log(account, "join timeout")
            self._notify(account, "timed out waiting to join the game")
            self.on_update(runtime)
            launcher.kill_process_by_pid(pid)
            self._wait_after_failure(runtime)
            return

        runtime.consecutive_failures = 0  # a successful join clears any backoff streak
        runtime.status = AccountStatus.ONLINE
        runtime.detail = "Connected"
        self._log(account, "connected")
        self.on_update(runtime)

        if self.config.arrange_windows:
            threading.Thread(
                target=self._arrange_window,
                args=(account, pid),
                daemon=True,
            ).start()

        self._watch_until_relaunch_needed(account, runtime, tailer, pid)

    def _arrange_window(self, account: Account, pid: int) -> None:
        """Runs in a background thread, re-enforcing the grid position (and
        eventually minimizing) for a while after connecting rather than
        moving the window just once.

        A single MoveWindow call right at "Joining game" isn't enough -
        confirmed live: that log line fires while Roblox is still on its
        loading screen, and it resizes/re-styles its own window (sometimes
        replacing the hwnd entirely) once the 3D scene finishes loading,
        silently undoing a one-shot resize. Re-applying periodically for a
        window after connecting rides out that churn instead of racing it.
        """
        rect = window_manager.grid_position(
            account.index,
            self.config.windows_per_row,
            self.config.window_width,
            self.config.window_height,
        )

        start = time.monotonic()
        enforce_until = start + max(self.config.minimize_after_seconds, 10.0) + 20.0
        minimize_at = start + self.config.minimize_after_seconds
        minimized = False

        while time.monotonic() < enforce_until:
            if self._stop_event.wait(2.0):
                return
            if not launcher.is_pid_alive(pid):
                return

            hwnd = window_manager.find_window_for_pid(pid)
            if hwnd is None:
                continue

            window_manager.move_window(hwnd, rect)

            if not minimized and self.config.minimize_after_seconds > 0 and time.monotonic() >= minimize_at:
                window_manager.minimize_window(hwnd)
                minimized = True

    def _watch_until_relaunch_needed(self, account, runtime, tailer, pid) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.config.poll_interval_seconds):
                return
            tailer.poll()

            if not launcher.is_pid_alive(pid):
                runtime.status = AccountStatus.DISCONNECTED
                runtime.detail = "Roblox process exited"
                self._log(account, "process exited, will relaunch")
                self._notify(account, "Roblox process exited unexpectedly, relaunching")
                self.on_update(runtime)
                return

            if tailer.state.is_connected:
                if runtime.status != AccountStatus.ONLINE:
                    runtime.consecutive_failures = 0
                    runtime.status = AccountStatus.ONLINE
                    runtime.detail = "Connected"
                    self._log(account, "reconnected")
                    self.on_update(runtime)
                continue

            seconds_down = tailer.state.seconds_since_disconnect() or 0.0
            runtime.status = AccountStatus.DISCONNECTED
            runtime.detail = f"Disconnected {seconds_down:.0f}s ago"
            self.on_update(runtime)

            if seconds_down > self.config.no_connection_timeout_seconds:
                runtime.status = AccountStatus.RELAUNCHING
                runtime.detail = f"No connection for {seconds_down:.0f}s, relaunching"
                self._log(account, f"no connection for {seconds_down:.0f}s, relaunching")
                self._notify(account, f"no connection for {seconds_down:.0f}s, relaunching")
                self.on_update(runtime)
                launcher.kill_process_by_pid(pid)
                return

    # -- helpers ------------------------------------------------------

    def _wait_for_new_pid(self, existing_pids: set[int]) -> Optional[int]:
        deadline = time.monotonic() + self.config.pid_lookup_timeout_seconds
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return None
            new_pids = launcher.list_roblox_pids() - existing_pids
            if new_pids:
                return max(new_pids)  # newest launch = highest new pid
            if self._stop_event.wait(1.0):
                return None
        return None

    def _wait_for_log_path(self, pid: int) -> Optional[Path]:
        for _ in range(self.config.log_lookup_retries):
            if self._stop_event.is_set():
                return None
            path = dw.find_log_path(self.config.handle_exe_path, pid)
            if path is not None and path.exists():
                return path
            if self._stop_event.wait(self.config.log_lookup_retry_delay_seconds):
                return None
        return None
