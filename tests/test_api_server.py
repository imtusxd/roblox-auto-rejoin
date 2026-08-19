from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import api_server

API_KEY = "test-secret-key"


class FakeAppState:
    def __init__(self) -> None:
        self.watching = False
        self.accounts = [
            {
                "index": 0,
                "label": "SolaraStore01",
                "status": "Online",
                "detail": "Connected",
                "pid": 1234,
                "place_id": None,
                "private_server": None,
            },
            {
                "index": 1,
                "label": "SolaraStore02",
                "status": "Idle",
                "detail": "",
                "pid": None,
                "place_id": "16732694052",
                "private_server": "abc123",
            },
        ]
        self.config: dict[str, Any] = {
            "place_id": "16732694052",
            "api_key": API_KEY,
            "no_connection_timeout_seconds": 120.0,
        }
        self.logs: list[str] = ["line one", "line two", "line three"]
        self.start_calls = 0
        self.stop_calls = 0

    def get_api_key(self) -> str:
        return API_KEY

    def get_accounts_status(self) -> list[dict[str, Any]]:
        return self.accounts

    def is_watching(self) -> bool:
        return self.watching

    def request_start(self) -> None:
        self.start_calls += 1
        self.watching = True

    def request_stop(self) -> None:
        self.stop_calls += 1
        self.watching = False

    def remove_account(self, index: int) -> bool:
        for account in self.accounts:
            if account["index"] == index:
                self.accounts.remove(account)
                return True
        return False

    def set_account_target(self, index: int, place_id, private_server) -> bool:
        for account in self.accounts:
            if account["index"] == index:
                account["place_id"] = place_id
                account["private_server"] = private_server
                return True
        return False

    def get_config(self) -> dict[str, Any]:
        return dict(self.config)

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.config.update(patch)
        return dict(self.config)

    def get_recent_logs(self, limit: int) -> list[str]:
        return self.logs[-limit:]


def _client() -> tuple[TestClient, FakeAppState]:
    state = FakeAppState()
    app = api_server.build_app(state)
    return TestClient(app), state


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_health_requires_no_auth():
    client, _ = _client()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_protected_route_rejects_missing_key():
    client, _ = _client()
    response = client.get("/api/accounts")
    assert response.status_code == 401


def test_protected_route_rejects_wrong_key():
    client, _ = _client()
    response = client.get("/api/accounts", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_list_accounts_returns_status_without_cookies():
    client, _ = _client()
    response = client.get("/api/accounts", headers=_auth())
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["label"] == "SolaraStore01"
    assert "cookie" not in data[0]


def test_watch_start_and_stop_call_through_to_state():
    client, state = _client()

    start_resp = client.post("/api/watch/start", headers=_auth())
    assert start_resp.status_code == 200
    assert start_resp.json() == {"watching": True}
    assert state.start_calls == 1

    status_resp = client.get("/api/watch/status", headers=_auth())
    assert status_resp.json() == {"watching": True}

    stop_resp = client.post("/api/watch/stop", headers=_auth())
    assert stop_resp.status_code == 200
    assert stop_resp.json() == {"watching": False}
    assert state.stop_calls == 1


def test_delete_account_removes_it():
    client, state = _client()
    response = client.delete("/api/accounts/0", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {"removed": True}
    assert len(state.accounts) == 1


def test_delete_account_404_when_not_found():
    client, _ = _client()
    response = client.delete("/api/accounts/999", headers=_auth())
    assert response.status_code == 404


def test_set_account_target_updates_place_id_and_private_server():
    client, state = _client()
    response = client.put(
        "/api/accounts/0/target",
        headers=_auth(),
        json={"place_id": "999", "private_server": "code-xyz"},
    )
    assert response.status_code == 200
    assert response.json() == {"updated": True}
    assert state.accounts[0]["place_id"] == "999"
    assert state.accounts[0]["private_server"] == "code-xyz"


def test_set_account_target_404_when_not_found():
    client, _ = _client()
    response = client.put(
        "/api/accounts/999/target", headers=_auth(), json={"place_id": "1"}
    )
    assert response.status_code == 404


def test_get_config_redacts_api_key():
    client, _ = _client()
    response = client.get("/api/config", headers=_auth())
    assert response.status_code == 200
    data = response.json()
    assert "api_key" not in data
    assert data["place_id"] == "16732694052"


def test_update_config_applies_patch_and_still_redacts_key():
    client, state = _client()
    response = client.put(
        "/api/config", headers=_auth(), json={"no_connection_timeout_seconds": 60.0}
    )
    assert response.status_code == 200
    data = response.json()
    assert "api_key" not in data
    assert data["no_connection_timeout_seconds"] == 60.0
    assert state.config["no_connection_timeout_seconds"] == 60.0


def test_update_config_cannot_rotate_the_api_key_through_the_patch():
    client, state = _client()
    client.put("/api/config", headers=_auth(), json={"api_key": "attacker-supplied"})
    assert state.config["api_key"] == API_KEY  # unchanged


def test_get_logs_returns_recent_lines_respecting_limit():
    client, _ = _client()
    response = client.get("/api/logs?limit=2", headers=_auth())
    assert response.status_code == 200
    assert response.json() == ["line two", "line three"]


def test_cors_headers_present_for_cross_origin_requests():
    client, _ = _client()
    response = client.get(
        "/api/health", headers={"Origin": "http://localhost:3000"}
    )
    assert response.headers.get("access-control-allow-origin") == "*"
