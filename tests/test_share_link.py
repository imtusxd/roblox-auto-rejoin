import pytest

import share_link


class FakeResponse:
    def __init__(self, status_code: int, body: object = None, text: str = "", raise_on_json: bool = False) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    def post(self, url, cookies=None, headers=None, json=None):
        self.calls.append({"url": url, "cookies": cookies, "headers": headers, "json": json})
        if self._raise_exc:
            raise self._raise_exc
        return self._response


# -- extract_link_id ---------------------------------------------------


def test_extract_link_id_from_full_share_url():
    value = "https://www.roblox.com/share?code=8177d15ce9d50c4d9ad6ae513e6d33fd&type=Server"
    assert share_link.extract_link_id(value) == "8177d15ce9d50c4d9ad6ae513e6d33fd"


def test_extract_link_id_from_bare_shorthand():
    # accounts.parse_cookie_line's sv= token leaves the value exactly this
    # shape when it's already missing the roblox.com/share? prefix.
    value = "code=8177d15ce9d50c4d9ad6ae513e6d33fd&type=Server"
    assert share_link.extract_link_id(value) == "8177d15ce9d50c4d9ad6ae513e6d33fd"


def test_extract_link_id_returns_none_when_no_code_present():
    assert share_link.extract_link_id("not-a-share-link-at-all") is None


# -- resolve_share_link ---------------------------------------------------


def test_resolve_share_link_happy_path():
    # Real shape confirmed live 2026-08-20 against a real VIP server link.
    body = {
        "privateServerInviteData": {
            "status": "Valid",
            "ownerUserId": 10721411473,
            "universeId": 66654135,
            "privateServerId": 4112196490,
            "linkCode": "03116228470955786049590174383559",
            "placeId": 142823291,
        }
    }
    session = FakeSession(FakeResponse(200, body))

    result = share_link.resolve_share_link(session, "cookie-value", "code=abc123&type=Server", "csrf-token")

    assert result.access_code == "03116228470955786049590174383559"
    assert result.place_id == "142823291"
    call = session.calls[0]
    assert call["url"] == share_link.RESOLVE_LINK_URL
    assert call["cookies"] == {".ROBLOSECURITY": "cookie-value"}
    assert call["headers"]["X-CSRF-TOKEN"] == "csrf-token"
    assert call["json"] == {"linkId": "abc123", "linkType": "Server"}


def test_resolve_share_link_raises_when_no_code_in_value():
    session = FakeSession()
    with pytest.raises(share_link.ShareLinkError, match="code="):
        share_link.resolve_share_link(session, "cookie-value", "garbage", "csrf-token")
    assert session.calls == []  # never even attempted the request


def test_resolve_share_link_raises_on_non_200():
    session = FakeSession(FakeResponse(404, text="not found"))
    with pytest.raises(share_link.ShareLinkError, match="404"):
        share_link.resolve_share_link(session, "cookie-value", "code=abc&type=Server", "csrf-token")


def test_resolve_share_link_raises_on_non_json_body():
    session = FakeSession(FakeResponse(200, raise_on_json=True))
    with pytest.raises(share_link.ShareLinkError, match="not JSON"):
        share_link.resolve_share_link(session, "cookie-value", "code=abc&type=Server", "csrf-token")


def test_resolve_share_link_raises_when_invite_data_missing():
    """E.g. an expired/invalid invite, or a different link type entirely -
    Roblox's own response just omits privateServerInviteData rather than
    erroring, so this must be checked explicitly."""
    session = FakeSession(FakeResponse(200, {"privateServerInviteData": None}))
    with pytest.raises(share_link.ShareLinkError, match="privateServerInviteData"):
        share_link.resolve_share_link(session, "cookie-value", "code=abc&type=Server", "csrf-token")


def test_resolve_share_link_raises_when_link_code_missing():
    session = FakeSession(FakeResponse(200, {"privateServerInviteData": {"placeId": 123}}))
    with pytest.raises(share_link.ShareLinkError, match="privateServerInviteData"):
        share_link.resolve_share_link(session, "cookie-value", "code=abc&type=Server", "csrf-token")


def test_resolve_share_link_raises_on_request_exception():
    session = FakeSession(raise_exc=ConnectionError("network down"))
    with pytest.raises(share_link.ShareLinkError, match="network down"):
        share_link.resolve_share_link(session, "cookie-value", "code=abc&type=Server", "csrf-token")
