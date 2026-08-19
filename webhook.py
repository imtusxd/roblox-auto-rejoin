"""Discord webhook notifications, batched like YummyWebPlayer's own
Webhook Url + Webhook Delay Send [M]: individual events are queued and
flushed as one combined message every `batch_seconds` instead of firing
one HTTP request per event - a relaunch loop on a flaky connection could
otherwise spam a Discord channel (and get itself rate-limited) fast.
"""
from __future__ import annotations

import threading

import requests

_MAX_CONTENT_LENGTH = 1900  # Discord's own cap is 2000; leave headroom.


def build_payload(messages: list[str]) -> dict:
    """Pure - turns a batch of queued lines into a Discord webhook payload."""
    content = "\n".join(messages)
    if len(content) > _MAX_CONTENT_LENGTH:
        content = "...(truncated)...\n" + content[-_MAX_CONTENT_LENGTH:]
    return {"content": content}


def send_webhook(session, url: str, messages: list[str]) -> bool:
    if not url or not messages:
        return False
    try:
        response = session.post(url, json=build_payload(messages), timeout=10)
        return response.status_code < 300
    except Exception:
        return False


class WebhookNotifier:
    """Background batched sender - call `notify(message)` from any thread;
    a flush timer sends everything queued since the last flush, at most
    once every `batch_seconds`."""

    def __init__(self, url: str, batch_seconds: float, session=None) -> None:
        self.url = url
        self.batch_seconds = max(batch_seconds, 1.0)
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.url or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="webhook-notifier")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def notify(self, message: str) -> None:
        if not self.url:
            return
        with self._lock:
            self._pending.append(message)

    def _run(self) -> None:
        while not self._stop_event.wait(self.batch_seconds):
            self._flush()
        self._flush()  # final flush on stop, so nothing queued gets dropped

    def _flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            batch = self._pending
            self._pending = []
        send_webhook(self._session, self.url, batch)
