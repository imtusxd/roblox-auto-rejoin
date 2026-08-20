"""Resolves Roblox's newer "share link" private-server format
(roblox.com/share?code=...&type=Server, and the raw code=...&type=Server
shorthand accounts.parse_cookie_line leaves untouched - see
accounts.is_unsupported_share_link) into the legacy-style access code
launcher.PRIVATE_SERVER_LAUNCHER_URL actually needs.

Confirmed LIVE (2026-08-20), against a real VIP server share link and a
real account cookie on this machine:

    POST https://apis.roblox.com/sharelinks/v1/resolve-link
    {"linkId": <code>, "linkType": "Server"}

- Accepts the SAME X-CSRF-TOKEN roblox_auth.fetch_auth_ticket already
  fetches for the auth-ticket flow - no separate CSRF round trip needed.
- Returns `privateServerInviteData.linkCode` - confirmed to be the real,
  redeemable access code: its own `placeId` in that same response matched
  this account's configured place_id in cookies.txt exactly, confirming
  linkCode is the genuine private-server access code for that exact game,
  equivalent to what an old-style "Copy Link" privateServerLinkCode value
  used to be - PlaceLauncher.ashx's RequestPrivateGame (see launcher.py)
  accepts it as `accessCode` the same way.

This is what was silently missing before: rejoin_controller.py used to
detect this newer share-link shape and just fall back to a PUBLIC server
rather than attempt an unresolvable launch - which is why accounts were
never actually reaching the VIP server, only logging a one-line notice
about it (easy to miss in a GUI-only workflow). This resolves the link
first instead, so the fallback only fires when resolution genuinely
fails (expired/invalid code, account lacks access, network error) rather
than for every single launch.
"""
from __future__ import annotations

import dataclasses
import re

RESOLVE_LINK_URL = "https://apis.roblox.com/sharelinks/v1/resolve-link"

_CODE_RE = re.compile(r"code=([a-f0-9-]+)")


class ShareLinkError(RuntimeError):
    """Raised when a share link can't be resolved (no code= found, a
    non-200/non-JSON response, or a response missing the expected
    privateServerInviteData shape - e.g. an expired invite, or the
    account genuinely has no access to that private server). Callers
    must catch this and fall back to a public server, same safety net
    rejoin_controller.py already used before this module existed."""


@dataclasses.dataclass(frozen=True)
class ResolvedPrivateServer:
    access_code: str
    place_id: str
    """The place this private server actually belongs to, per Roblox's own
    resolution - callers may use this to sanity-check (not necessarily
    override) whatever place_id they were about to launch with; a mismatch
    means the private server override and the configured place_id
    disagree about which game this is, worth surfacing rather than
    silently launching into a server that will never contain this
    private server."""


def extract_link_id(share_value: str) -> str | None:
    """Pulls the bare code out of either the full
    https://www.roblox.com/share?code=XXXX&type=Server URL or the
    code=XXXX&type=Server shorthand accounts.py's cookies.txt parsing
    leaves untouched (see accounts.is_unsupported_share_link). None if no
    code=... is found at all - e.g. a value that's neither shape."""
    match = _CODE_RE.search(share_value)
    return match.group(1) if match else None


def resolve_share_link(session, security_cookie: str, share_value: str, csrf_token: str) -> ResolvedPrivateServer:
    """Raises ShareLinkError on anything that isn't a clean success.

    `session`/`security_cookie`/`csrf_token` follow roblox_auth.py's own
    injectable-session convention - pass the same session and csrf_token
    already obtained from roblox_auth.fetch_auth_ticket for this account,
    no extra CSRF fetch needed (confirmed live: the auth-ticket flow's
    token is accepted here too)."""
    link_id = extract_link_id(share_value)
    if not link_id:
        raise ShareLinkError(f"Could not find a code= value in share link: {share_value!r}")

    try:
        response = session.post(
            RESOLVE_LINK_URL,
            cookies={".ROBLOSECURITY": security_cookie},
            headers={"X-CSRF-TOKEN": csrf_token, "Content-Type": "application/json"},
            json={"linkId": link_id, "linkType": "Server"},
        )
    except Exception as exc:
        raise ShareLinkError(f"resolve-link request failed: {exc}") from exc

    if response.status_code != 200:
        raise ShareLinkError(
            f"resolve-link returned {response.status_code}: {getattr(response, 'text', '')[:200]}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise ShareLinkError(f"resolve-link response was not JSON: {exc}") from exc

    invite = data.get("privateServerInviteData") if isinstance(data, dict) else None
    if not invite or not invite.get("linkCode") or not invite.get("placeId"):
        raise ShareLinkError(
            f"resolve-link response missing privateServerInviteData.linkCode/placeId: {data!r}"
        )

    return ResolvedPrivateServer(access_code=str(invite["linkCode"]), place_id=str(invite["placeId"]))
