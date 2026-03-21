"""tests.test_a2a.test_a2a_routes — FastAPI route tests for A2A endpoints.

Description
-----------
Tests for the A2A route handlers using FastAPI TestClient:

- POST /api/v1/task-update — Authenticated inbound task update.
- POST /api/v1/a2a/agents — Register a new agent (returns key_id + plaintext_key).
- DELETE /api/v1/a2a/agents/{key_id} — Revoke an agent key.

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_a2a_auth``, ``get_a2a_key_manager``,
  and ``get_broker`` are replaced with lightweight stubs so no real database or
  broker connection is needed.
- ``TestClient``: Synchronous ASGI test client.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.a2a.routes: a2a_router, task_update_router.
- graphclaw.a2a.middleware: require_a2a_auth, get_a2a_key_manager.
- graphclaw.gateway.deps: get_broker.
- pytest: test runner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.a2a.middleware import get_a2a_key_manager, require_a2a_auth
from graphclaw.a2a.models import A2AKeyRef
from graphclaw.a2a.routes import a2a_router, task_update_router
from graphclaw.auth.middleware import get_current_user_id
from graphclaw.gateway.deps import get_broker

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

_TEST_USER = "USER-test-routes-001"
_VALID_KEY = "wg_agent_validkeyfortest1234567890123456"
_FAKE_KEY_ID = "RES-fake-key-id-001"


def _make_mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.publish = AsyncMock(return_value=None)
    return broker


def _make_mock_key_manager(
    *,
    verify_returns: str | None = _TEST_USER,
    key_id: str = _FAKE_KEY_ID,
    plaintext: str = "wg_agent_returnedplaintextkey1234567890",
) -> MagicMock:
    """Return a MagicMock matching the A2AKeyManager interface."""
    km = MagicMock()

    # verify_key: used by require_a2a_auth
    km.verify_key = AsyncMock(return_value=verify_returns)

    # register_agent: returns (A2AKeyRef, plaintext_key)
    key_ref = A2AKeyRef(
        key_id=key_id,
        agent_name="TestAgent",
        user_id=_TEST_USER,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        resource_node_id=key_id,
    )
    km.register_agent = AsyncMock(return_value=(key_ref, plaintext))

    # revoke_key: succeeds silently
    km.revoke_key = AsyncMock(return_value=None)

    # rotate_key: returns (new_plaintext, new_hash)
    km.rotate_key = AsyncMock(return_value=("wg_agent_rotatedkey123456789012345678", "newhash"))

    # list_agents: returns a list with one active agent
    km.list_agents = AsyncMock(return_value=[key_ref])

    return km


def _make_app(
    *,
    mock_km: MagicMock | None = None,
    authenticated: bool = True,
) -> FastAPI:
    """Build a test FastAPI app with both A2A routers and dependency overrides."""
    app = FastAPI()
    app.include_router(a2a_router)
    app.include_router(task_update_router)

    fake_km = mock_km or _make_mock_key_manager()
    broker = _make_mock_broker()

    # Override broker
    app.dependency_overrides[get_broker] = lambda: broker

    # Override key manager singleton
    app.dependency_overrides[get_a2a_key_manager] = lambda: fake_km

    # Override platform JWT auth used by a2a management routes
    async def _fake_user_id() -> str:
        return _TEST_USER

    app.dependency_overrides[get_current_user_id] = _fake_user_id

    if authenticated:
        # Override A2A key auth to always succeed
        async def _fake_a2a_auth() -> str:
            return _TEST_USER

        app.dependency_overrides[require_a2a_auth] = _fake_a2a_auth

    return app


# ---------------------------------------------------------------------------
# POST /api/v1/task-update
# ---------------------------------------------------------------------------


class TestTaskUpdateEndpoint:
    """Tests for the POST /api/v1/task-update inbound endpoint."""

    _VALID_PAYLOAD = {
        "jsonrpc": "2.0",
        "method": "task.update",
        "params": {"task_id": "TASK-123", "status": "done"},
    }

    def test_task_update_valid_key(self) -> None:
        """POST /api/v1/task-update with authenticated caller returns 202."""
        client = TestClient(_make_app(authenticated=True))
        resp = client.post(
            "/api/v1/task-update",
            json=self._VALID_PAYLOAD,
            headers={"X-Agent-Api-Key": _VALID_KEY},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "message_id" in data

    def test_task_update_invalid_key(self) -> None:
        """POST /api/v1/task-update with an invalid key returns 403."""
        # Build app where require_a2a_auth is NOT overridden — uses real logic
        # which delegates to a mock key_manager that returns None for verify_key.
        km = _make_mock_key_manager(verify_returns=None)
        app = FastAPI()
        app.include_router(task_update_router)
        broker = _make_mock_broker()
        app.dependency_overrides[get_broker] = lambda: broker
        app.dependency_overrides[get_a2a_key_manager] = lambda: km
        # Do NOT override require_a2a_auth so it uses the real implementation.

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/task-update",
            json=self._VALID_PAYLOAD,
            headers={"X-Agent-Api-Key": "wg_agent_wrongkey00000000000000000000"},
        )
        assert resp.status_code == 403

    def test_task_update_missing_key(self) -> None:
        """POST /api/v1/task-update with no X-Agent-Api-Key header returns 403."""
        km = _make_mock_key_manager(verify_returns=None)
        app = FastAPI()
        app.include_router(task_update_router)
        broker = _make_mock_broker()
        app.dependency_overrides[get_broker] = lambda: broker
        app.dependency_overrides[get_a2a_key_manager] = lambda: km

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/task-update", json=self._VALID_PAYLOAD)
        assert resp.status_code == 403

    def test_task_update_oversized_body(self) -> None:
        """POST /api/v1/task-update with Content-Length > 512KB returns 413."""
        client = TestClient(_make_app(authenticated=True))
        oversized_bytes = str(512 * 1024 + 1)
        resp = client.post(
            "/api/v1/task-update",
            json=self._VALID_PAYLOAD,
            headers={
                "X-Agent-Api-Key": _VALID_KEY,
                "Content-Length": oversized_bytes,
            },
        )
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# POST /api/v1/a2a/agents  (register)
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Tests for POST /api/v1/a2a/agents."""

    def test_register_agent(self) -> None:
        """POST /api/v1/a2a/agents returns 201 with key_id and plaintext_key."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/a2a/agents",
            json={"agent_name": "TestAgent", "description": "CI bot"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "key_id" in data
        assert "plaintext_key" in data
        assert "agent_name" in data
        assert data["key_id"] == _FAKE_KEY_ID
        assert data["agent_name"] == "TestAgent"

    def test_register_agent_key_id_is_string(self) -> None:
        """The returned key_id should be a non-empty string."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/a2a/agents",
            json={"agent_name": "BotAlpha"},
        )
        assert resp.status_code == 201
        assert isinstance(resp.json()["key_id"], str)
        assert len(resp.json()["key_id"]) > 0

    def test_register_agent_plaintext_key_starts_with_prefix(self) -> None:
        """The returned plaintext_key must begin with 'wg_agent_'."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/a2a/agents",
            json={"agent_name": "PrefixCheckBot"},
        )
        assert resp.status_code == 201
        assert resp.json()["plaintext_key"].startswith("wg_agent_")


# ---------------------------------------------------------------------------
# DELETE /api/v1/a2a/agents/{key_id}  (revoke)
# ---------------------------------------------------------------------------


class TestRevokeAgent:
    """Tests for DELETE /api/v1/a2a/agents/{key_id}."""

    def test_revoke_agent(self) -> None:
        """DELETE /api/v1/a2a/agents/{key_id} returns 204 on success."""
        client = TestClient(_make_app())
        resp = client.delete(f"/api/v1/a2a/agents/{_FAKE_KEY_ID}")
        assert resp.status_code == 204

    def test_revoke_agent_not_found(self) -> None:
        """DELETE /api/v1/a2a/agents/{key_id} returns 404 when key_id is unknown."""
        km = _make_mock_key_manager()
        km.revoke_key = AsyncMock(side_effect=KeyError("not found"))
        client = TestClient(_make_app(mock_km=km), raise_server_exceptions=False)
        resp = client.delete("/api/v1/a2a/agents/RES-nonexistent")
        assert resp.status_code == 404
