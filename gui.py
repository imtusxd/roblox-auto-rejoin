"""Desktop window (tkinter/ttk, no extra native dependency needed on
Windows): an account grid with a live status column, Start/Stop watching,
a config panel, and a scrolling log feed - the same shape RAM's/WebRB's own
account-grid + log-panel UX already uses, scoped to only what this tool
does (watch + auto-rejoin, nothing else).

Visuals: a small dark ttk theme (`_setup_style`) plus per-status colored
row text and a live status summary, layered on top of the exact same
widgets/wiring as before - no new dependency, still pure tkinter/ttk.
"""
from __future__ import annotations

import dataclasses
import queue
import secrets
import threading
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import requests

import accounts as accounts_module
import api_server
import crash_log
import fps_control
import launcher
from accounts import Account, load_accounts, resolve_account_info
from app_config import AppConfig, load_config, save_config
from rejoin_controller import AccountRuntime, AccountStatus, ControllerConfig, RejoinController
from webhook import WebhookNotifier

APP_TITLE = "Roblox Auto-Rejoin"

# -- palette --------------------------------------------------------------
# One small dark palette, reused by name everywhere below instead of
# scattering hex literals through the widget code - change a look here and
# it applies everywhere at once.
BG = "#1a1b23"
PANEL = "#22232e"
PANEL_ALT = "#2a2c3a"
BORDER = "#383a4d"
TEXT = "#e7e7f1"
TEXT_MUTED = "#8d8fa8"
ACCENT = "#7c8cff"
ACCENT_ACTIVE = "#98a6ff"
SUCCESS = "#3ddc84"
WARNING = "#ffb020"
DANGER = "#ff5c6c"
INFO = "#5b9dff"
SUBTLE = "#6b6d84"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_HEADER = ("Segoe UI", 16, "bold")
FONT_SUBTITLE = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)

# status string -> (row text color, summary bucket emoji)
_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "Idle": (SUBTLE, "idle"),
    AccountStatus.STARTING: (INFO, "busy"),
    AccountStatus.CHECKING_COOKIE: (INFO, "busy"),
    AccountStatus.INVALID_COOKIE: (WARNING, "busy"),
    AccountStatus.LAUNCHING: (INFO, "busy"),
    AccountStatus.WAITING_FOR_JOIN: (INFO, "busy"),
    AccountStatus.ONLINE: (SUCCESS, "online"),
    AccountStatus.DISCONNECTED: (WARNING, "warn"),
    AccountStatus.RELAUNCHING: (INFO, "busy"),
    AccountStatus.ERROR: (DANGER, "error"),
    AccountStatus.STOPPED: (SUBTLE, "idle"),
    AccountStatus.DEAD: (SUBTLE, "dead"),
}
_BUCKET_ORDER = (
    ("online", "\U0001F7E2", "Online"),  # green circle
    ("busy", "\U0001F535", "Working"),  # blue circle
    ("warn", "\U0001F7E0", "Disconnected"),  # orange circle
    ("error", "\U0001F534", "Error"),  # red circle
    ("dead", "⚫", "Dead"),  # black circle
    ("idle", "⚪", "Idle"),  # white circle
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1000x660")
        self.root.minsize(760, 480)

        self.config: AppConfig = load_config()
        self.controller: RejoinController | None = None
        self.accounts: list[Account] = []
        self._update_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._row_by_index: dict[int, str] = {}
        self._account_by_row: dict[str, Account] = {}
        self._checked_rows: set[str] = set()

        # Plain-Python mirrors of what the Treeview/log widget show,
        # readable from any thread (api_server.py's own background thread
        # included) without touching a Tkinter widget - Tkinter itself is
        # not thread-safe, widgets may only be read/written on the main
        # thread. Each entry is replaced wholesale, never mutated
        # in-place, so a plain read from another thread can't observe a
        # half-written value under the GIL - no extra lock needed for
        # these two.
        self._status_snapshot: dict[int, dict[str, Any]] = {}
        self._log_buffer: "deque[str]" = deque(maxlen=1000)

        self._setup_style()
        self._build_widgets()
        if launcher.enable_multi_instance():
            self._append_log("Multi-instance mode enabled (ROBLOX_singletonMutex claimed).")
        else:
            self._append_log(
                "WARNING: could not claim the multi-instance mutex - "
                "only one account may stay online at a time. Close any "
                "other copy of this tool/RAM and restart."
            )
        self._append_log(
            "Tip: double-click an account's Game cell (or tick several and use "
            "'Set Place ID / Server for selected...') to set/clear its place id and private server."
        )
        self._start_api_server()
        self._load_accounts_async()
        self.root.after(150, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- style -------------------------------------------------------

    def _setup_style(self) -> None:
        """One dark ttk theme, built on "clam" (the only bundled theme that
        actually honors background/foreground colors - "vista"/"winnative"
        silently ignore most of them on Windows)."""
        self.root.configure(bg=BG)

        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=BG, foreground=TEXT, font=FONT, borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Header.TLabel", background=BG, foreground=TEXT, font=FONT_HEADER)
        style.configure("Subtitle.TLabel", background=BG, foreground=TEXT_MUTED, font=FONT_SUBTITLE)
        style.configure("Summary.TLabel", background=BG, foreground=TEXT_MUTED, font=FONT)

        style.configure(
            "TButton",
            background=PANEL_ALT,
            foreground=TEXT,
            padding=(12, 7),
            font=FONT,
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("disabled", PANEL), ("active", BORDER)],
            foreground=[("disabled", TEXT_MUTED)],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#14151f",
            padding=(12, 7),
            font=FONT_BOLD,
            relief="flat",
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", PANEL_ALT), ("active", ACCENT_ACTIVE)],
            foreground=[("disabled", TEXT_MUTED)],
        )

        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=27,
            borderwidth=0,
            font=FONT,
        )
        style.configure(
            "Treeview.Heading",
            background=PANEL_ALT,
            foreground=TEXT_MUTED,
            font=FONT_BOLD,
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Treeview.Heading",
            background=[("active", BORDER)],
        )
        style.map(
            "Treeview",
            background=[("selected", BORDER)],
            foreground=[("selected", TEXT)],
        )

        style.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="flat")
        style.configure("TLabelframe.Label", background=BG, foreground=TEXT_MUTED, font=FONT_BOLD)

        style.configure(
            "TEntry",
            fieldbackground=PANEL_ALT,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            padding=5,
        )
        style.map("TEntry", fieldbackground=[("disabled", PANEL)])

        style.configure(
            "TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=TEXT_MUTED,
            bordercolor=BORDER,
            padding=4,
        )
        style.map("TCombobox", fieldbackground=[("readonly", PANEL_ALT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BORDER)

        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])

        style.configure(
            "Vertical.TScrollbar",
            background=PANEL_ALT,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=TEXT_MUTED,
            relief="flat",
        )
        style.map("Vertical.TScrollbar", background=[("active", BORDER)])

    # -- layout -------------------------------------------------------

    def _build_widgets(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 14, 16, 6))
        header.pack(fill=tk.X)

        title_box = ttk.Frame(header)
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text=APP_TITLE, style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_box,
            text="Watches every account's Roblox client and auto-rejoins on disconnect",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W)

        self.summary_label = ttk.Label(header, text="", style="Summary.TLabel", anchor=tk.E)
        self.summary_label.pack(side=tk.RIGHT, anchor=tk.SE)

        toolbar = ttk.Frame(self.root, padding=(16, 4, 16, 10))
        toolbar.pack(fill=tk.X)

        self.start_button = ttk.Button(
            toolbar, text="▶  Start watching", style="Accent.TButton", command=self._on_start
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_button = ttk.Button(
            toolbar, text="■  Stop", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            toolbar, text="⟳  Reload accounts", command=self._load_accounts_async
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="⚙  Settings...", command=self._open_settings).pack(
            side=tk.LEFT
        )

        # Bulk-action bar: tick the checkbox column on however many rows,
        # then one action here applies to all of them at once - e.g. set
        # the same place id on 5 accounts without opening the per-row
        # dialog 5 times. More actions (private server, ...) can land here
        # later reusing the same _checked_rows selection.
        bulk_bar = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        bulk_bar.pack(fill=tk.X)
        ttk.Button(
            bulk_bar, text="Set Place ID / Server for selected...", command=self._on_bulk_set_launch_target
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.selection_label = ttk.Label(bulk_bar, text="0 selected", style="Subtitle.TLabel")
        self.selection_label.pack(side=tk.LEFT)

        table_frame = ttk.Frame(self.root, padding=(16, 0, 16, 10))
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("sel", "label", "game", "status", "detail", "pid")
        headings = {
            "sel": "☐",
            "label": "Account",
            "game": "Game",
            "status": "Status",
            "detail": "Detail",
            "pid": "PID",
        }
        widths = {"sel": 30, "label": 180, "game": 160, "status": 150, "detail": 280, "pid": 70}

        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(
                col, width=widths[col], anchor=(tk.CENTER if col == "sel" else tk.W),
                stretch=(col != "sel"),
            )
        # Clicking the "sel" header itself toggles every row at once - no
        # separate "select all" button needed.
        self.tree.heading("sel", command=self._toggle_select_all)
        tree_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        for status, (color, _bucket) in _STATUS_STYLE.items():
            self.tree.tag_configure(status, foreground=color)

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=(10, 6))
        log_frame.pack(fill=tk.BOTH, expand=False, padx=16, pady=(0, 14))
        self.log_text = tk.Text(
            log_frame,
            height=10,
            state=tk.DISABLED,
            wrap=tk.WORD,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=BORDER,
            relief="flat",
            font=FONT_MONO,
            padx=8,
            pady=6,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # -- accounts -------------------------------------------------------

    def _load_accounts_async(self) -> None:
        cookies_path = Path(self.config.cookies_path)
        self.accounts = load_accounts(cookies_path)

        self.tree.delete(*self.tree.get_children())
        self._row_by_index.clear()
        self._account_by_row.clear()
        self._checked_rows.clear()
        self._status_snapshot.clear()  # stale indices (removed/reordered accounts) must not linger
        self.tree.heading("sel", text="☐")
        for account in self.accounts:
            row_id = self.tree.insert(
                "",
                tk.END,
                values=("☐", account.label, self._game_label(account), "Idle", "", ""),
                tags=("Idle",),
            )
            self._row_by_index[account.index] = row_id
            self._account_by_row[row_id] = account
            self._status_snapshot[account.index] = {
                "index": account.index,
                "label": account.label,
                "status": "Idle",
                "detail": "",
                "pid": None,
                "place_id": account.place_id,
                "private_server": account.private_server,
            }
        self._update_summary()
        self._update_selection_label()

        if not self.accounts:
            self._append_log(
                f"No cookies found in {cookies_path}. One .ROBLOSECURITY value per line."
            )
            return

        self._append_log(f"Loaded {len(self.accounts)} account(s) from {cookies_path}")

        def resolve() -> None:
            session = requests.Session()
            for account in self.accounts:
                resolve_account_info(session, account)
                self._update_queue.put(("label", account))

        threading.Thread(target=resolve, daemon=True).start()

    def _game_label(self, account: Account) -> str:
        """What to show in the Game column: account.place_id if its
        cookies.txt line carried one, else the run's default place id -
        resolved through known_games for a friendly name when available,
        falling back to the bare numeric id otherwise. A private server
        override (if set) is flagged with a lock glyph rather than given
        its own column - the access code itself is long and not
        interesting to see at a glance."""
        place_id = account.place_id or self.config.place_id
        name = self.config.known_games.get(place_id)
        label = f"{name} ({place_id})" if name else place_id
        if account.private_server:
            label += "  🔒 SV"
        return label

    def _apply_place_id(self, account: Account, new_value: str | None) -> bool:
        """Shared by the single-row and bulk editors: writes `new_value`
        (already validated - digits or None) as `account`'s place id
        override, in cookies.txt and on the live Account object. Returns
        False (and leaves everything untouched) if the cookie could no
        longer be found in cookies.txt - e.g. it went dead moments ago.
        """
        updated = accounts_module.set_place_id(
            Path(self.config.cookies_path), account.cookie, new_value
        )
        if not updated:
            return False

        account.place_id = new_value
        self._sync_status_snapshot_overrides(account)
        row_id = self._row_by_index.get(account.index)
        if row_id is not None:
            self.tree.set(row_id, "game", self._game_label(account))
        return True

    def _sync_status_snapshot_overrides(self, account: Account) -> None:
        """_apply_place_id/_apply_private_server mutate the live Account
        object directly (see their own docstrings) - this keeps
        _status_snapshot (the plain-Python mirror api_server.py's
        AppStatePort reads from, safe from any thread) in sync with that
        same change, so GET /api/accounts reflects an edit immediately
        instead of only after the next _apply_runtime/_load_accounts_async
        happens to touch this account."""
        existing = self._status_snapshot.get(account.index)
        if existing is not None:
            self._status_snapshot[account.index] = {
                **existing,
                "place_id": account.place_id,
                "private_server": account.private_server,
            }

    def _apply_private_server(self, account: Account, new_value: str | None) -> bool:
        """Same idea as _apply_place_id but for the private-server
        override (accounts.set_private_server). `new_value` may be a bare
        access code or a full "Copy Link" URL - set_private_server pulls
        the code out either way."""
        updated = accounts_module.set_private_server(
            Path(self.config.cookies_path), account.cookie, new_value
        )
        if not updated:
            return False

        account.private_server = (
            accounts_module.extract_private_server_code(new_value) if new_value else None
        )
        self._sync_status_snapshot_overrides(account)
        row_id = self._row_by_index.get(account.index)
        if row_id is not None:
            self.tree.set(row_id, "game", self._game_label(account))
        return True

    def _prompt_launch_target(
        self, title_suffix: str, initial_place_id: str, initial_private_server: str
    ) -> tuple[str, str] | None:
        """One small dialog for both overrides at once (place id + private
        server), used by both the single-row and bulk editors - covers the
        common case of wanting to point an account (or a whole tick-boxed
        batch) at a specific game *and* server together in one prompt
        instead of two.

        Returns (place_id, private_server) - each an empty string if left
        blank - or None if cancelled. An invalid place id shows an error
        and also returns None rather than a partially-valid result.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Launch target")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=14)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=f"For: {title_suffix}", style="Subtitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10)
        )

        ttk.Label(body, text=f"Place ID (blank = default, {self.config.place_id})").grid(
            row=1, column=0, sticky=tk.W
        )
        place_var = tk.StringVar(value=initial_place_id)
        place_entry = ttk.Entry(body, textvariable=place_var, width=42)
        place_entry.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 10))

        ttk.Label(body, text="Private server link or access code (blank = public server)").grid(
            row=3, column=0, sticky=tk.W
        )
        sv_var = tk.StringVar(value=initial_private_server)
        ttk.Entry(body, textvariable=sv_var, width=42).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(2, 12)
        )

        result: dict[str, str] = {}

        def on_apply() -> None:
            place_id = place_var.get().strip()
            if place_id and not place_id.isdigit():
                messagebox.showerror(APP_TITLE, "Place id must be blank or a number.", parent=dialog)
                return
            result["place_id"] = place_id
            result["private_server"] = sv_var.get().strip()
            dialog.destroy()

        button_row = ttk.Frame(body)
        button_row.grid(row=5, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(button_row, text="Apply", style="Accent.TButton", command=on_apply).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)

        place_entry.focus_set()
        dialog.bind("<Return>", lambda _e: on_apply())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        dialog.wait_window()

        if "place_id" not in result:
            return None
        return result["place_id"], result["private_server"]

    def _describe_target(self, place_id: str, private_server: str) -> str:
        target = place_id or f"default ({self.config.place_id})"
        if private_server:
            target += " + private server"
        return target

    def _on_tree_double_click(self, event: tk.Event) -> None:
        """Double-clicking a row's Game cell prompts for that account's own
        place id / private server overrides (accounts.set_place_id /
        set_private_server), instead of requiring a hand-edit of
        cookies.txt - a file made mostly of long, sensitive cookie values
        that's easy to corrupt by hand.
        """
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#3":  # "game" is the 3rd column
            return

        row_id = self.tree.identify_row(event.y)
        account = self._account_by_row.get(row_id)
        if account is None:
            return

        prompted = self._prompt_launch_target(
            account.label, account.place_id or "", account.private_server or ""
        )
        if prompted is None:
            return
        new_place_id, new_private_server = prompted

        place_ok = self._apply_place_id(account, new_place_id or None)
        sv_ok = self._apply_private_server(account, new_private_server or None)
        if not (place_ok and sv_ok):
            messagebox.showerror(
                APP_TITLE,
                "Could not find this account's cookie in cookies.txt anymore "
                "(it may have just gone dead) - nothing was changed.",
            )
            return

        # Mutating the Account object in place is enough to affect an
        # already-running watch loop too - rejoin_controller re-reads
        # place_id/private_server from the same Account object on every
        # relaunch, not just at Start - so this takes effect on this
        # account's *next* relaunch, no restart needed.
        self._append_log(
            f"{account.label}: target set to "
            f"{self._describe_target(new_place_id, new_private_server)} "
            "- takes effect on its next launch/relaunch."
        )

    # -- multi-select (checkbox column) ------------------------------------

    def _on_tree_click(self, event: tk.Event) -> None:
        """A single click on the "sel" column toggles that row's checkbox.
        Anywhere else, let the click through to the Treeview's normal
        handling (row highlight, and _on_tree_double_click still fires
        separately on a second click)."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        self._toggle_row_check(row_id)

    def _toggle_row_check(self, row_id: str) -> None:
        if row_id in self._checked_rows:
            self._checked_rows.discard(row_id)
            self.tree.set(row_id, "sel", "☐")
        else:
            self._checked_rows.add(row_id)
            self.tree.set(row_id, "sel", "☑")
        self._update_selection_label()

    def _toggle_select_all(self) -> None:
        all_rows = self.tree.get_children()
        if not all_rows:
            return
        if len(self._checked_rows) >= len(all_rows):
            self._checked_rows.clear()
            glyph, header_glyph = "☐", "☐"
        else:
            self._checked_rows = set(all_rows)
            glyph, header_glyph = "☑", "☑"
        for row_id in all_rows:
            self.tree.set(row_id, "sel", glyph)
        self.tree.heading("sel", text=header_glyph)
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        self.selection_label.config(text=f"{len(self._checked_rows)} selected")
        total = len(self.tree.get_children())
        self.tree.heading("sel", text="☑" if total and len(self._checked_rows) >= total else "☐")

    def _on_bulk_set_launch_target(self) -> None:
        """Applies one place id / private server pair to every ticked
        account at once - the same edit _on_tree_double_click makes for a
        single row, just looped over the checkbox selection. Each
        account's own cookies.txt line is rewritten independently, so a
        partial failure (a cookie that went dead moments ago) only skips
        that one account rather than aborting the whole batch.
        """
        accounts_selected = [
            self._account_by_row[row_id]
            for row_id in self._checked_rows
            if row_id in self._account_by_row
        ]
        if not accounts_selected:
            messagebox.showinfo(
                APP_TITLE, "No accounts selected - tick the checkbox column first."
            )
            return

        labels = ", ".join(a.label for a in accounts_selected[:5])
        if len(accounts_selected) > 5:
            labels += f", +{len(accounts_selected) - 5} more"
        prompted = self._prompt_launch_target(
            f"{len(accounts_selected)} accounts ({labels})", "", ""
        )
        if prompted is None:
            return
        new_place_id, new_private_server = prompted

        applied = 0
        for account in accounts_selected:
            place_ok = self._apply_place_id(account, new_place_id or None)
            sv_ok = self._apply_private_server(account, new_private_server or None)
            if place_ok and sv_ok:
                applied += 1

        target = self._describe_target(new_place_id, new_private_server)
        self._append_log(
            f"Bulk: target set to {target} for {applied}/{len(accounts_selected)} "
            "selected account(s) - takes effect on each one's next launch/relaunch."
        )
        if applied < len(accounts_selected):
            messagebox.showwarning(
                APP_TITLE,
                f"{len(accounts_selected) - applied} account(s) could not be updated "
                "(their cookie may no longer be in cookies.txt).",
            )

    # -- controller wiring -------------------------------------------------------

    def _on_start(self) -> None:
        if not self.accounts:
            messagebox.showwarning(APP_TITLE, "No accounts loaded - add cookies first.")
            return

        # One shared FPS cap for every window this run launches (see
        # fps_control.py - there's no safe per-window equivalent). Only
        # affects windows launched from here on, not ones already open -
        # applying it right before Start watching is as close to "takes
        # effect for this run" as this mechanism allows. 0 (the default)
        # deliberately leaves ClientAppSettings.json untouched rather than
        # actively clearing the flag - a user who set an FPS cap some
        # other way shouldn't have this tool silently undo it just because
        # target_fps was never configured here.
        if self.config.target_fps > 0:
            if fps_control.apply_target_fps(self.config.target_fps):
                self._append_log(f"FPS cap set to {self.config.target_fps} for new launches.")
            else:
                self._append_log("WARNING: could not write ClientAppSettings.json for the FPS cap.")

        cfg = ControllerConfig(
            place_id=self.config.place_id,
            handle_exe_path=self.config.handle_exe_path,
            potassium_path=self.config.potassium_path,
            cookies_path=self.config.cookies_path,
            dead_cookies_path=self.config.dead_cookies_path,
            no_connection_timeout_seconds=self.config.no_connection_timeout_seconds,
            join_timeout_seconds=self.config.join_timeout_seconds,
            poll_interval_seconds=self.config.poll_interval_seconds,
            max_concurrent_launches=self.config.max_concurrent_launches,
            arrange_windows=self.config.arrange_windows,
            windows_per_row=self.config.windows_per_row,
            window_width=self.config.window_width,
            window_height=self.config.window_height,
            minimize_after_seconds=self.config.minimize_after_seconds,
            process_priority=self.config.process_priority,
            cpu_affinity_core_count=self.config.cpu_affinity_core_count,
            check_cookie_before_launch=self.config.check_cookie_before_launch,
        )
        notifier = (
            WebhookNotifier(self.config.webhook_url, self.config.webhook_batch_seconds)
            if self.config.webhook_url
            else None
        )
        self.controller = RejoinController(
            cfg,
            on_update=lambda runtime: self._update_queue.put(("runtime", runtime)),
            on_log=lambda message: self._update_queue.put(("log", message)),
            notifier=notifier,
        )
        self.controller.start(self.accounts, self.config.stagger_launch_seconds)

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self._append_log("Started watching.")

    def _on_stop(self) -> None:
        if self.controller:
            self.controller.stop()
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self._append_log("Stop requested.")

    def _on_close(self) -> None:
        """The rejoin loop only exists as long as this process is alive -
        there's no separate background service watching accounts. So the
        window itself closing (the X button) must not be treated as "the
        user is done", or the exact thing auto-rejoin exists to survive
        (an account going offline) happens to *every* account the moment
        someone clicks X, accidentally or not, with nothing left running
        to notice or fix it.

        While anything is being watched, X therefore just minimizes to
        the taskbar instead of exiting - the process, threads, and watch
        loops all keep running exactly as before, just with no window
        visible. Click Stop first (which is a deliberate, explicit
        action) to actually allow closing.
        """
        if self.controller and self.controller.is_running():
            self.root.iconify()
            self._append_log(
                "Window minimized - still watching in the background. "
                "Click Stop first, then close, to fully quit."
            )
            return

        if self.controller:
            self.controller.stop()
        self.root.destroy()

    # -- settings dialog -------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("580x620")
        dialog.configure(bg=BG)
        dialog.transient(self.root)

        # Scrollable body - the field list is long enough now (window
        # layout + resource + webhook + cookie-check sections) that it
        # doesn't comfortably fit a fixed-height dialog.
        canvas = tk.Canvas(dialog, highlightthickness=0, bg=BG)
        scrollbar = ttk.Scrollbar(dialog, orient=tk.VERTICAL, command=canvas.yview)
        body = ttk.Frame(canvas, padding=(4, 4))
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        button_bar = ttk.Frame(dialog, padding=10)
        button_bar.pack(side=tk.BOTTOM, fill=tk.X)

        fields: dict[str, tk.Variable] = {}
        row_counter = [0]

        def next_row() -> int:
            row_counter[0] += 1
            return row_counter[0]

        def add_section(title: str) -> None:
            row = next_row()
            sep = ttk.Frame(body, height=1, style="TFrame")
            if row > 1:
                sep.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=(10, 0))
                row = next_row()
            ttk.Label(body, text=title.upper(), foreground=ACCENT, font=FONT_BOLD).grid(
                row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(8, 4)
            )

        def add_row(label: str, key: str, browse: bool = False) -> None:
            row = next_row()
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            var = tk.StringVar(value=str(getattr(self.config, key)))
            ttk.Entry(body, textvariable=var, width=40).grid(
                row=row, column=1, sticky=tk.W, padx=4, pady=4
            )
            fields[key] = var
            if browse:
                def do_browse() -> None:
                    path = filedialog.askopenfilename()
                    if path:
                        var.set(path)

                ttk.Button(body, text="Browse...", command=do_browse).grid(
                    row=row, column=2, padx=4
                )

        def add_bool_row(label: str, key: str) -> None:
            row = next_row()
            var = tk.BooleanVar(value=bool(getattr(self.config, key)))
            ttk.Checkbutton(body, text=label, variable=var).grid(
                row=row, column=0, columnspan=2, sticky=tk.W, padx=8, pady=4
            )
            fields[key] = var

        def add_choice_row(label: str, key: str, choices: list[str]) -> None:
            row = next_row()
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            var = tk.StringVar(value=str(getattr(self.config, key)))
            ttk.Combobox(body, textvariable=var, values=choices, state="readonly", width=20).grid(
                row=row, column=1, sticky=tk.W, padx=4, pady=4
            )
            fields[key] = var

        add_section("General")
        add_row("Default Place ID (a cookies.txt line's own place id wins)", "place_id")
        add_row("Cookies file", "cookies_path", browse=True)
        add_row("Dead cookies file (blank = don't save)", "dead_cookies_path", browse=True)
        add_row("handle.exe path", "handle_exe_path", browse=True)
        add_row("Potassium.exe path", "potassium_path", browse=True)
        add_row("No-connection timeout (s)", "no_connection_timeout_seconds")
        add_row("Join timeout (s)", "join_timeout_seconds")
        add_row("Poll interval (s)", "poll_interval_seconds")
        add_row("Stagger launch (s)", "stagger_launch_seconds")
        add_row("Max concurrent launches", "max_concurrent_launches")

        add_section("Window layout")
        add_bool_row("Arrange windows in a grid", "arrange_windows")
        add_row("Windows per row", "windows_per_row")
        add_row("Window width", "window_width")
        add_row("Window height", "window_height")
        add_row("Minimize after (s)", "minimize_after_seconds")

        add_section("Resource management")
        add_choice_row("Process priority", "process_priority", ["normal", "below_normal", "low"])
        add_row("CPU affinity core count (0 = off)", "cpu_affinity_core_count")
        add_row("Target FPS, ALL windows (0 = uncapped)", "target_fps")

        add_section("Discord webhook")
        add_row("Webhook URL (blank = disabled)", "webhook_url")
        add_row("Batch send interval (s)", "webhook_batch_seconds")

        add_section("Cookie check")
        add_bool_row("Check cookie validity before each launch", "check_cookie_before_launch")

        add_section("Control API (restart required after changing host/port)")
        add_bool_row("Enable control API", "api_enabled")
        add_row("Host (0.0.0.0 = reachable on the LAN, 127.0.0.1 = this machine only)", "api_host")
        add_row("Port", "api_port")

        api_key_row = next_row()
        ttk.Label(body, text="API key").grid(row=api_key_row, column=0, sticky=tk.W, padx=8, pady=4)
        api_key_var = tk.StringVar(value=self.config.api_key)
        api_key_entry = ttk.Entry(body, textvariable=api_key_var, width=42, state="readonly")
        api_key_entry.grid(row=api_key_row, column=1, sticky=tk.W, padx=4, pady=4)

        def do_regenerate_key() -> None:
            if not messagebox.askyesno(
                APP_TITLE,
                "Generate a new API key? Anything already using the old key "
                "(the dashboard) will need it updated too - takes effect "
                "immediately, no restart needed.",
                parent=dialog,
            ):
                return
            self.config.api_key = secrets.token_urlsafe(32)
            save_config(self.config)
            api_key_var.set(self.config.api_key)

        ttk.Button(body, text="Regenerate...", command=do_regenerate_key).grid(
            row=api_key_row, column=2, padx=4
        )

        int_fields = {
            "windows_per_row",
            "window_width",
            "window_height",
            "max_concurrent_launches",
            "cpu_affinity_core_count",
            "target_fps",
            "api_port",
        }
        float_fields = {
            "no_connection_timeout_seconds",
            "join_timeout_seconds",
            "poll_interval_seconds",
            "stagger_launch_seconds",
            "minimize_after_seconds",
            "webhook_batch_seconds",
        }
        bool_fields = {"arrange_windows", "check_cookie_before_launch", "api_enabled"}

        def do_save() -> None:
            try:
                for key, var in fields.items():
                    if key in bool_fields:
                        setattr(self.config, key, bool(var.get()))
                    elif key in int_fields:
                        setattr(self.config, key, int(float(var.get())))
                    elif key in float_fields:
                        setattr(self.config, key, float(var.get()))
                    else:
                        setattr(self.config, key, var.get().strip())
            except ValueError as exc:
                messagebox.showerror(APP_TITLE, f"Invalid value: {exc}")
                return

            save_config(self.config)
            dialog.destroy()
            self._load_accounts_async()

        ttk.Button(button_bar, text="Save", style="Accent.TButton", command=do_save).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(button_bar, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=4)

    # -- update loop -------------------------------------------------------

    def _append_log(self, message: str) -> None:
        self._log_buffer.append(message)
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._update_queue.get_nowait()
                if kind == "runtime":
                    self._apply_runtime(payload)  # type: ignore[arg-type]
                elif kind == "label":
                    self._apply_label(payload)  # type: ignore[arg-type]
                elif kind == "log":
                    self._append_log(str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(150, self._drain_queue)

    def _apply_runtime(self, runtime: AccountRuntime) -> None:
        self._status_snapshot[runtime.account.index] = {
            "index": runtime.account.index,
            "label": runtime.account.label,
            "status": runtime.status,
            "detail": runtime.detail,
            "pid": runtime.pid,
            "place_id": runtime.account.place_id,
            "private_server": runtime.account.private_server,
        }

        row_id = self._row_by_index.get(runtime.account.index)
        if row_id is None:
            return
        self.tree.set(row_id, "status", runtime.status)
        self.tree.set(row_id, "detail", runtime.detail)
        self.tree.set(row_id, "pid", runtime.pid or "")
        self.tree.item(row_id, tags=(runtime.status,))
        self._update_summary()

    def _apply_label(self, account: Account) -> None:
        existing = self._status_snapshot.get(account.index)
        if existing is not None:
            self._status_snapshot[account.index] = {**existing, "label": account.label}

        row_id = self._row_by_index.get(account.index)
        if row_id is None:
            return
        self.tree.set(row_id, "label", account.label)

    # -- api_server.AppStatePort implementation --------------------------
    #
    # Every method below may be called from api_server.py's own background
    # thread (a separate uvicorn thread, not the Tk main thread) - see
    # _start_api_server. Read-only methods just return plain-Python state
    # (_status_snapshot/self.config/_log_buffer) that's already safe to
    # read from any thread (see the comment where _status_snapshot is
    # declared in __init__). Anything that touches a Tkinter widget or
    # needs a real success/failure result back synchronously goes through
    # _run_on_main_thread instead of being called directly - Tkinter
    # itself is not thread-safe, only the main thread may touch a widget.

    def _run_on_main_thread(self, func, timeout: float = 5.0):
        """Runs func() on the Tk main thread (via root.after, the standard
        thread-safe way to schedule Tkinter work from another thread) and
        blocks the CALLING thread until it completes, returning func()'s
        own return value or re-raising its exception. Needed wherever a
        caller off the main thread (api_server.py's routes) needs a real
        synchronous result - a bare `root.after(0, func)` alone is fire-
        and-forget, fine for request_start/request_stop but not for
        anything that must answer "did this actually work" (remove_account,
        set_account_target)."""
        result: dict[str, Any] = {}
        done = threading.Event()

        def runner() -> None:
            try:
                result["value"] = func()
            except Exception as exc:  # noqa: BLE001 - re-raised on the caller's own thread below
                result["error"] = exc
            finally:
                done.set()

        self.root.after(0, runner)
        if not done.wait(timeout):
            raise TimeoutError("Tkinter main thread did not respond in time")
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _find_account_by_index(self, index: int) -> Account | None:
        return next((a for a in self.accounts if a.index == index), None)

    def _start_api_server(self) -> None:
        if not self.config.api_enabled:
            self._append_log("Control API disabled (api_enabled=false in Settings).")
            return
        threading.Thread(
            target=api_server.run_server,
            args=(self, self.config.api_host, self.config.api_port),
            daemon=True,
            name="api-server",
        ).start()
        reachable = "this machine only" if self.config.api_host == "127.0.0.1" else "reachable on the LAN"
        self._append_log(
            f"Control API listening on http://{self.config.api_host}:{self.config.api_port} "
            f"({reachable}). API key is in Settings -> Control API."
        )

    # AppStatePort methods - see api_server.py's own AppStatePort Protocol
    # for what each one is required to do.

    def get_api_key(self) -> str:
        return self.config.api_key

    def get_accounts_status(self) -> list[dict[str, Any]]:
        return [self._status_snapshot[i] for i in sorted(self._status_snapshot)]

    def is_watching(self) -> bool:
        return self.controller is not None and self.controller.is_running()

    def request_start(self) -> None:
        # Fire-and-forget - _on_start already reports its own outcome via
        # log lines/the status table, both readable through this same
        # AppStatePort a moment later. No result to wait for here.
        self.root.after(0, self._on_start)

    def request_stop(self) -> None:
        self.root.after(0, self._on_stop)

    def remove_account(self, index: int) -> bool:
        def do_remove() -> bool:
            account = self._find_account_by_index(index)
            if account is None:
                return False
            removed = accounts_module.remove_account(Path(self.config.cookies_path), account.cookie)
            if removed:
                # Known limitation, same as editing cookies.txt by hand
                # while watching: a full reload resyncs self.accounts/the
                # Treeview/the status snapshot, but does NOT stop an
                # already-running watch thread for this account if one is
                # currently active - RejoinController has no per-account
                # cancel, only a global stop(). The account simply won't
                # be included in the NEXT Start watching run.
                self._load_accounts_async()
            return removed

        return self._run_on_main_thread(do_remove)

    def set_account_target(self, index: int, place_id: str | None, private_server: str | None) -> bool:
        def do_update() -> bool:
            account = self._find_account_by_index(index)
            if account is None:
                return False
            place_ok = self._apply_place_id(account, place_id)
            server_ok = self._apply_private_server(account, private_server)
            return place_ok or server_ok

        return self._run_on_main_thread(do_update)

    def get_config(self) -> dict[str, Any]:
        return self.config.to_dict()

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        def do_update() -> dict[str, Any]:
            known = {f.name for f in dataclasses.fields(self.config)}
            for key, value in patch.items():
                if key in known and key != "api_key":
                    setattr(self.config, key, value)
            save_config(self.config)
            return self.config.to_dict()

        return self._run_on_main_thread(do_update)

    def get_recent_logs(self, limit: int) -> list[str]:
        if limit <= 0:
            return []
        return list(self._log_buffer)[-limit:]

    def _update_summary(self) -> None:
        """Rebuilds the top-right "2 online, 1 disconnected, ..." line from
        the tree's current status column - cheap enough (one pass over
        however many accounts are loaded, at most a few hundred) to just
        recompute on every update instead of maintaining running counters
        that could drift out of sync."""
        counts = {bucket: 0 for bucket, _emoji, _label in _BUCKET_ORDER}
        total = 0
        for row_id in self.tree.get_children():
            total += 1
            status = self.tree.set(row_id, "status")
            _color, bucket = _STATUS_STYLE.get(status, (TEXT_MUTED, "idle"))
            counts[bucket] += 1

        if total == 0:
            self.summary_label.config(text="No accounts loaded")
            return

        parts = [
            f"{emoji} {counts[bucket]} {label}"
            for bucket, emoji, label in _BUCKET_ORDER
            if counts[bucket] > 0
        ]
        parts.append(f"• {total} total")
        self.summary_label.config(text="   ".join(parts))


def main() -> None:
    crash_log.install()  # main-thread/background-thread hooks first, before anything can run
    root = tk.Tk()
    crash_log.install(root)  # now also wire the Tk callback-exception hook
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
