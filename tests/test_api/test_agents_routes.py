"""tests.test_api.test_agents_routes — Tests for /app/v1/agents/* endpoints.

Covers GET/POST/PATCH/DELETE /agents, GET /agents/{id}/versions,
and POST /agents/{id}/test.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_storage_client
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

from tests.test_api.conftest import FakeStorageClient

_TEST_USER = "USER-test-agents-001"


def _make_app(
    storage: FakeStorageClient | None = None,
) -> tuple[FastAPI, FakeStorageClient]:
    if storage is None:
        storage = FakeStorageClient()

    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_storage() -> FakeStorageClient:
        return storage

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_storage_client] = _fake_storage
    return app, storage


# ---------------------------------------------------------------------------
# GET /app/v1/agents
# ---------------------------------------------------------------------------


def test_list_agents_empty_for_new_user() -> None:
    """GET /agents returns [] for a user with no definitions."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/agents")
    assert response.status_code == 200
    assert response.json() == []


def test_list_agents_returns_created_definitions() -> None:
    """GET /agents returns all created agent definitions."""
    app, _ = _make_app()
    client = TestClient(app)
    client.post("/app/v1/agents", json={"name": "Agent Alpha"})
    client.post("/app/v1/agents", json={"name": "Agent Beta"})
    response = client.get("/app/v1/agents")
    assert response.status_code == 200
    names = [a["name"] for a in response.json()]
    assert "Agent Alpha" in names
    assert "Agent Beta" in names


# ---------------------------------------------------------------------------
# POST /app/v1/agents
# ---------------------------------------------------------------------------


def test_create_agent_returns_201() -> None:
    """POST /agents creates a definition and returns 201."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/app/v1/agents",
        json={"name": "My Agent", "description": "Test agent", "tags": ["test"]},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My Agent"
    assert data["agent_id"].startswith("AGT-")
    assert data["version"] == "1"


def test_create_agent_persists_to_storage() -> None:
    """POST /agents writes the definition to storage."""
    app, storage = _make_app()
    client = TestClient(app)
    response = client.post("/app/v1/agents", json={"name": "Persisted Agent"})
    agent_id = response.json()["agent_id"]
    path = f"agents/{_TEST_USER}/definitions/{agent_id}.json"
    assert path in storage._data


# ---------------------------------------------------------------------------
# GET /app/v1/agents/{id}
# ---------------------------------------------------------------------------


def test_get_agent_returns_definition() -> None:
    """GET /agents/{id} returns the created definition."""
    app, _ = _make_app()
    client = TestClient(app)
    created = client.post("/app/v1/agents", json={"name": "Fetch Me"}).json()
    agent_id = created["agent_id"]
    response = client.get(f"/app/v1/agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Fetch Me"


def test_get_agent_not_found_returns_404() -> None:
    """GET /agents/{id} returns 404 for unknown agent."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/agents/AGT-nonexistent")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /app/v1/agents/{id}
# ---------------------------------------------------------------------------


def test_patch_agent_updates_name() -> None:
    """PATCH /agents/{id} updates the agent name."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "Old Name"}).json()["agent_id"]
    response = client.patch(f"/app/v1/agents/{agent_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


def test_patch_agent_increments_version() -> None:
    """PATCH /agents/{id} bumps the version number."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "Version Test"}).json()["agent_id"]
    response = client.patch(f"/app/v1/agents/{agent_id}", json={"name": "Updated"})
    assert response.json()["version"] == "2"


def test_patch_agent_not_found_returns_404() -> None:
    """PATCH /agents/{id} returns 404 for unknown agent."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.patch("/app/v1/agents/AGT-ghost", json={"name": "Ghost"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /app/v1/agents/{id}
# ---------------------------------------------------------------------------


def test_delete_agent_returns_204() -> None:
    """DELETE /agents/{id} returns 204 after deletion."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "To Delete"}).json()["agent_id"]
    response = client.delete(f"/app/v1/agents/{agent_id}")
    assert response.status_code == 204


def test_delete_agent_removes_from_storage() -> None:
    """DELETE /agents/{id} removes the definition from storage."""
    app, storage = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "To Delete"}).json()["agent_id"]
    client.delete(f"/app/v1/agents/{agent_id}")
    path = f"agents/{_TEST_USER}/definitions/{agent_id}.json"
    assert path not in storage._data


def test_delete_agent_then_get_returns_404() -> None:
    """GET /agents/{id} returns 404 after the agent is deleted."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "Deleted"}).json()["agent_id"]
    client.delete(f"/app/v1/agents/{agent_id}")
    response = client.get(f"/app/v1/agents/{agent_id}")
    assert response.status_code == 404


def test_delete_agent_not_found_returns_404() -> None:
    """DELETE /agents/{id} returns 404 for unknown agent."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.delete("/app/v1/agents/AGT-missing")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /app/v1/agents/{id}/versions
# ---------------------------------------------------------------------------


def test_list_versions_empty_before_any_patch() -> None:
    """GET /agents/{id}/versions returns [] when no patches have been made."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "No Versions"}).json()["agent_id"]
    response = client.get(f"/app/v1/agents/{agent_id}/versions")
    assert response.status_code == 200
    assert response.json() == []


def test_list_versions_shows_snapshot_after_patch() -> None:
    """GET /agents/{id}/versions returns one entry after a PATCH."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "Has Version"}).json()["agent_id"]
    client.patch(f"/app/v1/agents/{agent_id}", json={"name": "Updated"})
    response = client.get(f"/app/v1/agents/{agent_id}/versions")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["agent_id"] == agent_id


def test_list_versions_not_found_returns_404() -> None:
    """GET /agents/{id}/versions returns 404 for unknown agent."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/agents/AGT-missing/versions")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /app/v1/agents/{id}/test
# ---------------------------------------------------------------------------


def test_test_agent_returns_ok() -> None:
    """POST /agents/{id}/test returns status=ok for a valid agent."""
    app, _ = _make_app()
    client = TestClient(app)
    agent_id = client.post("/app/v1/agents", json={"name": "Testable"}).json()["agent_id"]
    response = client.post(f"/app/v1/agents/{agent_id}/test")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["agent_id"] == agent_id


def test_test_agent_not_found_returns_404() -> None:
    """POST /agents/{id}/test returns 404 for unknown agent."""
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post("/app/v1/agents/AGT-ghost/test")
    assert response.status_code == 404
