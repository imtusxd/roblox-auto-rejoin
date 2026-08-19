"""Pure port of RBX Alt Manager's Account.cs GetCSRFToken/GetAuthTicket flow.

No filesystem or process access here - everything takes an injectable HTTP
session object (anything with a `.post(url, cookies=..., headers=...)`
method returning an object with `.status_code`/`.headers`/`.text`, e.g. a
`requests.Session`) so this is easy to unit test against mocked responses.

Flow (verified against Account.cs, ~line 112-164):
  1. POST https://auth.roblox.com/v1/authentication-ticket/ with the
     .ROBLOSECURITY cookie and NO CSRF header -> Roblox always answers 403
     to this first call, but the 403 response carries an x-csrf-token
     header with a fresh token.
  2. POST the same URL again, this time with X-CSRF-TOKEN set to that
     token -> a successful response carries an rbx-authentication-ticket
     header, which is the one-time launch ticket handed to the Roblox
     client via the roblox-player: protocol URI.
"""
from __future__ import annotations

import dataclasses

AUTH_TICKET_URL = "https://auth.roblox.com/v1/authentication-ticket/"

# Any real, existing game page works as the Referer - Roblox only checks
# that one is present. Overridable per call if a specific game is wanted.
DEFAULT_REFERER = "https://www.roblox.com/games/16732694052/"


class AuthError(RuntimeError):
    """Raised when the cookie -> ticket flow fails at any step (expired
    cookie, unexpected status code, missing header, etc.)."""


@dataclasses.dataclass(frozen=True)
class AuthResult:
    ticket: str
    csrf_token: str


def _cookie_jar(security_cookie: str) -> dict[str, str]:
    return {".ROBLOSECURITY": security_cookie}


def fetch_csrf_token(session, security_cookie: str, referer: str = DEFAULT_REFERER) -> str:
    """Step 1: POST with no CSRF token, expect a 403 carrying x-csrf-token.

    Roblox now rejects this POST with 415 UnsupportedMediaType unless a
    Content-Type: application/json header (and a JSON body, even an empty
    one) is present - RBX Alt Manager's older RestSharp-based Account.cs
    didn't need this, but Roblox's gateway started enforcing it since.
    Confirmed against the live endpoint with a real cookie before adding
    this - the request 415'd without it and succeeded with it.
    """
    response = session.post(
        AUTH_TICKET_URL,
        cookies=_cookie_jar(security_cookie),
        headers={"Referer": referer, "Content-Type": "application/json"},
        json={},
    )

    if response.status_code != 403:
        raise AuthError(
            f"Expected 403 while fetching CSRF token, got {response.status_code}: "
            f"{getattr(response, 'text', '')[:200]}"
        )

    token = response.headers.get("x-csrf-token")
    if not token:
        raise AuthError("403 response did not include an x-csrf-token header")

    return token


def fetch_auth_ticket(session, security_cookie: str, referer: str = DEFAULT_REFERER) -> AuthResult:
    """Step 2: POST again with X-CSRF-TOKEN, read rbx-authentication-ticket.

    Raises AuthError (including a cookie that's simply invalid/expired -
    step 1 requires the cookie to be accepted before Roblox even bothers
    responding with a CSRF token) rather than returning a sentinel, so
    callers can't accidentally treat a failed launch as a successful one.
    """
    csrf_token = fetch_csrf_token(session, security_cookie, referer)

    response = session.post(
        AUTH_TICKET_URL,
        cookies=_cookie_jar(security_cookie),
        headers={
            "Referer": referer,
            "X-CSRF-TOKEN": csrf_token,
            "Content-Type": "application/json",
        },
        json={},
    )

    ticket = response.headers.get("rbx-authentication-ticket")
    if not ticket:
        raise AuthError(
            f"No rbx-authentication-ticket header in response (status "
            f"{response.status_code}): {getattr(response, 'text', '')[:200]}"
        )

    return AuthResult(ticket=ticket, csrf_token=csrf_token)
