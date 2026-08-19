import webhook


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeSession:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.status_code)


def test_build_payload_joins_messages_with_newlines():
    payload = webhook.build_payload(["first", "second"])
    assert payload == {"content": "first\nsecond"}


def test_build_payload_truncates_very_long_content():
    messages = ["x" * 5000]
    payload = webhook.build_payload(messages)
    assert len(payload["content"]) < 5000
    assert payload["content"].startswith("...(truncated)...")


def test_send_webhook_returns_false_without_url_or_messages():
    session = FakeSession()
    assert webhook.send_webhook(session, "", ["hi"]) is False
    assert webhook.send_webhook(session, "https://discord.example/hook", []) is False
    assert session.calls == []


def test_send_webhook_posts_and_reports_success():
    session = FakeSession(status_code=204)
    ok = webhook.send_webhook(session, "https://discord.example/hook", ["one", "two"])
    assert ok is True
    assert session.calls[0]["json"] == {"content": "one\ntwo"}


def test_send_webhook_reports_failure_on_error_status():
    session = FakeSession(status_code=500)
    assert webhook.send_webhook(session, "https://discord.example/hook", ["oops"]) is False


def test_notifier_flush_sends_batched_pending_messages():
    session = FakeSession(status_code=200)
    notifier = webhook.WebhookNotifier(
        "https://discord.example/hook", batch_seconds=60, session=session
    )
    notifier.notify("event one")
    notifier.notify("event two")

    notifier._flush()  # exercise the flush logic directly, no background thread needed

    assert session.calls[0]["json"] == {"content": "event one\nevent two"}
    assert notifier._pending == []


def test_notifier_notify_is_noop_without_url():
    session = FakeSession()
    notifier = webhook.WebhookNotifier("", batch_seconds=60, session=session)
    notifier.notify("should not be queued")
    assert notifier._pending == []
