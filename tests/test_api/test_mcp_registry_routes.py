"""tests.test_api.test_mcp_registry_routes — Tests for /app/v1/mcp-servers endpoints.

Description
-----------
Verifies that the MCP registry endpoints correctly list, register, update,
and deregister MCP servers for authenticated users.

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_auth`` is replaced with a stub that
  returns a fixed user_id.
- Direct stub manipulation: Tests clear ``_mcp_servers`` via an ``autouse``
  fixture to ensure isolation.
- ``TestClient``: Synchronous ASGI test client.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.mcp_registry: _mcp_servers (test stub access).
- graphclaw.auth.middleware: require_auth.
- fastapi: FastAPI (third-party).
- pytest: test runner.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api import mcp_registry as mcp_module
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

_TEST_USER = "test-user-mcp-001"

_REGISTER_PAYLOAD = {
    "name": "My GitHub",
    "transport": "http",
    "endpoint_url": "https://api.github.com",
    "trust_tier": "GATED",
    "scope": ["read_issues", "create_issue"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    app.dependency_overrides[require_auth] = _fake_auth
    return app


@pytest.fixture(autouse=True)
def clear_mcp_servers():
    """Reset the stub storage before and after each test."""
    mcp_module._mcp_servers.pop(_TEST_USER, None)
    yield
    mcp_module._mcp_servers.pop(_TEST_USER, None)


@pytest.fixture()
def client():
    return TestClient(_make_app())


@pytest.fixture()
def no_auth_client():
    app = FastAPI()
    app.include_router(app_router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /app/v1/mcp-servers
# ---------------------------------------------------------------------------


def test_list_mcp_servers_empty(client: TestClient) -> None:
    """GET /app/v1/mcp-servers returns an empty list for a new user."""
    response = client.get("/app/v1/mcp-servers")
    assert response.status_code == 200
    assert response.json() == []


def test_list_mcp_servers_returns_registered(client: TestClient) -> None:
    """GET /app/v1/mcp-servers returns previously registered servers."""
    client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD)
    response = client.get("/app/v1/mcp-servers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "My GitHub"


def test_list_mcp_servers_requires_auth(no_auth_client: TestClient) -> None:
    """GET /app/v1/mcp-servers returns 401/403 without a Bearer token."""
    response = no_auth_client.get("/app/v1/mcp-servers")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /app/v1/mcp-servers
# ---------------------------------------------------------------------------


def test_register_mcp_server_returns_201(client: TestClient) -> None:
    """POST /app/v1/mcp-servers returns HTTP 201 with the new server entry."""
    response = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My GitHub"
    assert data["transport"] == "http"
    assert data["trust_tier"] == "GATED"
    assert data["enabled"] is True
    assert data["server_id"].startswith("MCP-")


def test_register_mcp_server_requires_auth(no_auth_client: TestClient) -> None:
    """POST /app/v1/mcp-servers returns 401/403 without a Bearer token."""
    response = no_auth_client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD)
    assert response.status_code in (401, 403)


def test_register_mcp_server_stores_scope(client: TestClient) -> None:
    """POST /app/v1/mcp-servers persists declared scopes."""
    response = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD)
    assert response.status_code == 201
    data = response.json()
    assert "read_issues" in data["scope"]


# ---------------------------------------------------------------------------
# PATCH /app/v1/mcp-servers/{server_id}
# ---------------------------------------------------------------------------


def test_patch_mcp_server_trust_tier(client: TestClient) -> None:
    """PATCH /app/v1/mcp-servers/{id} updates the trust tier."""
    reg = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD).json()
    server_id = reg["server_id"]
    response = client.patch(
        f"/app/v1/mcp-servers/{server_id}", json={"trust_tier": "AUTO"}
    )
    assert response.status_code == 200
    assert response.json()["trust_tier"] == "AUTO"


def test_patch_mcp_server_enabled_flag(client: TestClient) -> None:
    """PATCH /app/v1/mcp-servers/{id} can disable a server."""
    reg = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD).json()
    server_id = reg["server_id"]
    response = client.patch(
        f"/app/v1/mcp-servers/{server_id}", json={"enabled": False}
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_patch_mcp_server_not_found(client: TestClient) -> None:
    """PATCH /app/v1/mcp-servers/{id} returns 404 for unknown server."""
    response = client.patch(
        "/app/v1/mcp-servers/MCP-nonexistent", json={"trust_tier": "AUTO"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /app/v1/mcp-servers/{server_id}
# ---------------------------------------------------------------------------


def test_delete_mcp_server_returns_204(client: TestClient) -> None:
    """DELETE /app/v1/mcp-servers/{id} returns HTTP 204 on success."""
    reg = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD).json()
    server_id = reg["server_id"]
    response = client.delete(f"/app/v1/mcp-servers/{server_id}")
    assert response.status_code == 204


def test_delete_mcp_server_removes_from_list(client: TestClient) -> None:
    """After DELETE, the server no longer appears in the list."""
    reg = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD).json()
    server_id = reg["server_id"]
    client.delete(f"/app/v1/mcp-servers/{server_id}")
    list_resp = client.get("/app/v1/mcp-servers")
    assert list_resp.status_code == 200
    server_ids = [s["server_id"] for s in list_resp.json()]
    assert server_id not in server_ids


def test_delete_mcp_server_not_found(client: TestClient) -> None:
    """DELETE /app/v1/mcp-servers/{id} returns 404 for unknown server."""
    response = client.delete("/app/v1/mcp-servers/MCP-nonexistent")
    assert response.status_code == 404


def test_delete_mcp_server_requires_auth(no_auth_client: TestClient) -> None:
    """DELETE /app/v1/mcp-servers/{id} returns 401/403 without a Bearer token."""
    response = no_auth_client.delete("/app/v1/mcp-servers/MCP-abc123")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /app/v1/mcp-servers/{server_id}
# ---------------------------------------------------------------------------


def test_get_mcp_server_by_id(client: TestClient) -> None:
    """GET /app/v1/mcp-servers/{id} returns the registered server details."""
    reg = client.post("/app/v1/mcp-servers", json=_REGISTER_PAYLOAD).json()
    server_id = reg["server_id"]
    response = client.get(f"/app/v1/mcp-servers/{server_id}")
    assert response.status_code == 200
    assert response.json()["server_id"] == server_id
    assert response.json()["name"] == "My GitHub"


def test_get_mcp_server_not_found(client: TestClient) -> None:
    """GET /app/v1/mcp-servers/{id} returns 404 for unknown server."""
    response = client.get("/app/v1/mcp-servers/MCP-nonexistent")
    assert response.status_code == 404
