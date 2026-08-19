"""Cookie validity check, run before every launch attempt - matches
YummyWebPlayer's "Thread Check Cookie" idea: know a cookie is dead/expired
*before* burning a launch cycle on it instead of only finding out when the
auth-ticket POST fails.

Roblox doesn't expose a single "is this account banned" endpoint that
works uniformly, but `users.roblox.com/v1/users/authenticated` is the
cheapest reliable signal for "is this cookie still logged in at all": 200
means valid (and hands back the username/id for free), 401 means the
cookie is expired/invalidated - which is also what happens to a
terminated account's cookie. We don't try to distinguish "banned" from
"expired" beyond that; both mean "don't bother launching this one".
"""
from __future__ import annotations

import dataclasses

AUTHENTICATED_URL = "https://users.roblox.com/v1/users/authenticated"


class CookieStatus:
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"  # network error, rate limit, etc - not a verdict either way


@dataclasses.dataclass(frozen=True)
class CookieCheckResult:
    status: str
    username: str | None = None
    user_id: int | None = None
    detail: str = ""


def check_cookie(session, cookie: str, timeout: float = 10.0) -> CookieCheckResult:
    try:
        response = session.get(
            AUTHENTICATED_URL,
            cookies={".ROBLOSECURITY": cookie},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - a network hiccup isn't a verdict
        return CookieCheckResult(status=CookieStatus.UNKNOWN, detail=str(exc))

    if response.status_code == 200:
        try:
            data = response.json()
        except Exception:
            return CookieCheckResult(status=CookieStatus.VALID)
        return CookieCheckResult(
            status=CookieStatus.VALID,
            username=data.get("name"),
            user_id=data.get("id"),
        )

    if response.status_code == 401:
        return CookieCheckResult(
            status=CookieStatus.INVALID, detail="Cookie expired or account logged out"
        )

    return CookieCheckResult(
        status=CookieStatus.UNKNOWN, detail=f"Unexpected status {response.status_code}"
    )
