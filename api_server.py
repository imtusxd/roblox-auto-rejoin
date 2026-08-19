"""LAN-reachable control API so a separate dashboard (e.g. the
website-cloner Next.js app) can view live status and start/stop/manage
accounts without touching the desktop GUI directly.

Security posture (deliberate, user-confirmed choices - do not loosen
without re-confirming):
  - Every route except /api/health requires a matching `X-API-Key` header.
    The key lives in AppConfig.api_key, auto-generated on first run (see
    app_config.py) so it's never blank by default.
  - This is a control-plane API for REAL Roblox accounts with real
    money-value items - anyone who has the key can start/stop watching
    and remove accounts. Anyone on the LAN can reach it at all (the user
    explicitly chose 0.0.0.0 over 127.0.0.1-only for remote viewing from
    another machine) - the API key is the only thing standing between
    "anyone on this network" and "can control these accounts", so treat
    it like a password, not a convenience token.
  - There is NO endpoint that accepts or returns a raw .ROBLOSECURITY
    cookie value, in either direction, ever. Accounts can only be
    removed (DELETE) or have their place id / private server override
    edited (PUT .../target) - never added. A brand new cookie must still
    be typed into cookies.txt by hand, on the machine itself - see
    accounts.remove_account's own docstring for why this line is drawn
    here specifically (user's explicit choice, weighing convenience
    against a cookie ever touching the network in any form).

`AppStatePort` decouples this module from gui.py/Tkinter entirely (same
split as every other module in this project - disconnect_watcher.py's
classify_line vs. LogTailer, launcher.py's build_launch_uri vs.
launch_via_protocol, ...) so the actual route logic is independently
testable via FastAPI's TestClient against a fake state, without a Tk
event loop anywhere nearby.
"""
from __future__ import annotations

from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class AppStatePort(Protocol):
    def get_api_key(self) -> str: ...

    def get_accounts_status(self) -> list[dict[str, Any]]: ...
    """One dict per account: index, label, status, detail, pid, place_id,
    private_server - deliberately NEVER the cookie itself."""

    def is_watching(self) -> bool: ...
    def request_start(self) -> None: ...
    def request_stop(self) -> None: ...

    def remove_account(self, index: int) -> bool: ...
    def set_account_target(
        self, index: int, place_id: str | None, private_server: str | None
    ) -> bool: ...

    def get_config(self) -> dict[str, Any]: ...
    """Must NOT include api_key - see _redact_config, applied on every
    response regardless of what this returns, as a second layer."""

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]: ...

    def get_recent_logs(self, limit: int) -> list[str]: ...


def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Belt-and-suspenders - even if a future AppStatePort implementation
    accidentally includes api_key in get_config()'s return value, it never
    leaves this process. The key is a secret; it has no business being
    echoed back over the same API it protects."""
    return {k: v for k, v in config.items() if k != "api_key"}


class TargetUpdate(BaseModel):
    place_id: str | None = None
    private_server: str | None = None


class ConfigPatch(BaseModel):
    model_config = {"extra": "allow"}  # any AppConfig field may be patched


def build_app(state: AppStatePort) -> FastAPI:
    app = FastAPI(title="roblox-auto-rejoin control API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        expected = state.get_api_key()
        if not expected or x_api_key != expected:
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/accounts", dependencies=[Depends(require_api_key)])
    def list_accounts() -> list[dict[str, Any]]:
        return state.get_accounts_status()

    @app.get("/api/watch/status", dependencies=[Depends(require_api_key)])
    def watch_status() -> dict[str, bool]:
        return {"watching": state.is_watching()}

    @app.post("/api/watch/start", dependencies=[Depends(require_api_key)])
    def watch_start() -> dict[str, bool]:
        state.request_start()
        return {"watching": True}

    @app.post("/api/watch/stop", dependencies=[Depends(require_api_key)])
    def watch_stop() -> dict[str, bool]:
        state.request_stop()
        return {"watching": False}

    @app.delete("/api/accounts/{index}", dependencies=[Depends(require_api_key)])
    def delete_account(index: int) -> dict[str, bool]:
        removed = state.remove_account(index)
        if not removed:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"removed": True}

    @app.put("/api/accounts/{index}/target", dependencies=[Depends(require_api_key)])
    def set_account_target(index: int, body: TargetUpdate) -> dict[str, bool]:
        ok = state.set_account_target(index, body.place_id or None, body.private_server or None)
        if not ok:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"updated": True}

    @app.get("/api/config", dependencies=[Depends(require_api_key)])
    def get_config() -> dict[str, Any]:
        return _redact_config(state.get_config())

    @app.put("/api/config", dependencies=[Depends(require_api_key)])
    def update_config(patch: ConfigPatch) -> dict[str, Any]:
        data = patch.model_dump(exclude_unset=True)
        data.pop("api_key", None)  # the key can't be rotated through this endpoint
        updated = state.update_config(data)
        return _redact_config(updated)

    @app.get("/api/logs", dependencies=[Depends(require_api_key)])
    def get_logs(limit: int = 200) -> list[str]:
        return state.get_recent_logs(limit)

    return app


def run_server(state: AppStatePort, host: str, port: int) -> None:
    """Blocking - call this inside a background thread, never on the
    Tkinter main thread (see gui.py's own wiring)."""
    import uvicorn

    app = build_app(state)
    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).run()
