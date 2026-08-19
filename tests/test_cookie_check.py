import cookie_check


class FakeResponse:
    def __init__(self, status_code: int, json_data=None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class FakeSession:
    def __init__(self, response=None, exception=None) -> None:
        self._response = response
        self._exception = exception

    def get(self, url, cookies=None, timeout=None):
        if self._exception is not None:
            raise self._exception
        return self._response


def test_check_cookie_valid_returns_username_and_id():
    session = FakeSession(FakeResponse(200, {"name": "SolaraStore01", "id": 106921911}))
    result = cookie_check.check_cookie(session, "cookie-value")

    assert result.status == cookie_check.CookieStatus.VALID
    assert result.username == "SolaraStore01"
    assert result.user_id == 106921911


def test_check_cookie_invalid_on_401():
    session = FakeSession(FakeResponse(401))
    result = cookie_check.check_cookie(session, "cookie-value")

    assert result.status == cookie_check.CookieStatus.INVALID


def test_check_cookie_unknown_on_unexpected_status():
    session = FakeSession(FakeResponse(429))
    result = cookie_check.check_cookie(session, "cookie-value")

    assert result.status == cookie_check.CookieStatus.UNKNOWN


def test_check_cookie_unknown_on_network_error():
    session = FakeSession(exception=ConnectionError("boom"))
    result = cookie_check.check_cookie(session, "cookie-value")

    assert result.status == cookie_check.CookieStatus.UNKNOWN
    assert "boom" in result.detail
