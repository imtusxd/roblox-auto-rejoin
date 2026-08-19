import pytest

import roblox_auth


class FakeResponse:
    def __init__(self, status_code: int, headers: dict, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers
        self.text = text


class FakeSession:
    """Records each POST and answers with a scripted sequence of responses,
    mirroring the two-call CSRF dance without touching the network."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, cookies=None, headers=None, json=None):
        self.calls.append({"url": url, "cookies": cookies, "headers": headers, "json": json})
        return self._responses.pop(0)


def test_fetch_csrf_token_reads_header_from_403():
    session = FakeSession([FakeResponse(403, {"x-csrf-token": "abc123"})])

    token = roblox_auth.fetch_csrf_token(session, "cookie-value")

    assert token == "abc123"
    assert session.calls[0]["url"] == roblox_auth.AUTH_TICKET_URL
    assert session.calls[0]["cookies"] == {".ROBLOSECURITY": "cookie-value"}
    assert "X-CSRF-TOKEN" not in session.calls[0]["headers"]


def test_fetch_csrf_token_raises_on_unexpected_status():
    session = FakeSession([FakeResponse(200, {}, text="unexpected success")])

    with pytest.raises(roblox_auth.AuthError):
        roblox_auth.fetch_csrf_token(session, "cookie-value")


def test_fetch_csrf_token_raises_when_header_missing():
    session = FakeSession([FakeResponse(403, {})])

    with pytest.raises(roblox_auth.AuthError):
        roblox_auth.fetch_csrf_token(session, "cookie-value")


def test_fetch_auth_ticket_happy_path():
    session = FakeSession(
        [
            FakeResponse(403, {"x-csrf-token": "abc123"}),
            FakeResponse(200, {"rbx-authentication-ticket": "ticket-xyz"}),
        ]
    )

    result = roblox_auth.fetch_auth_ticket(session, "cookie-value")

    assert result.ticket == "ticket-xyz"
    assert result.csrf_token == "abc123"
    assert len(session.calls) == 2
    assert session.calls[1]["headers"]["X-CSRF-TOKEN"] == "abc123"


def test_fetch_auth_ticket_raises_when_ticket_header_missing():
    session = FakeSession(
        [
            FakeResponse(403, {"x-csrf-token": "abc123"}),
            FakeResponse(200, {}),
        ]
    )

    with pytest.raises(roblox_auth.AuthError):
        roblox_auth.fetch_auth_ticket(session, "cookie-value")


def test_fetch_auth_ticket_propagates_csrf_failure():
    session = FakeSession([FakeResponse(200, {}, text="bad cookie")])

    with pytest.raises(roblox_auth.AuthError):
        roblox_auth.fetch_auth_ticket(session, "cookie-value")

    # Should never have attempted the second call.
    assert len(session.calls) == 1
