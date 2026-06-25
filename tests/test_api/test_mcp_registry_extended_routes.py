# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_mcp_registry_extended_routes — Wave 5 MCP registry tests.

Covers:
- GET /mcp-servers/{id}/tools  (graceful degradation when server unreachable)
- GET /mcp-approvals
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store, get_mcp_registry
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.mcp.registry import MCPRegistry
from graphclaw.models.base import utcnow
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode
from tests.test_api.conftest import FakeGraphStore, FakeStorageClient

_TEST_USER = "USER-test-mcp-ext-001"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app() -> tuple[FastAPI, FakeGraphStore, MCPRegistry]:
    fake_store = FakeGraphStore()
    fake_storage = FakeStorageClient()
    registry = MCPRegistry(storage_client=fake_storage)

    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_mcp_registry() -> MCPRegistry:
        return registry

    async def _fake_store() -> FakeGraphStore:
        return fake_store

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_mcp_registry] = _fake_mcp_registry
    app.dependency_overrides[get_graph_store] = _fake_store
    return app, fake_store, registry


async def _seed_server(registry: MCPRegistry, server_id: str) -> MCPServerNode:
    """Register an MCPServerNode into the fake registry (storage-backed)."""
    now = utcnow()
    node = MCPServerNode(
        id=server_id,
        name="Test MCP Server",
        transport=MCPTransport.HTTP,
        endpoint_url="http://localhost:9999/mcp",
        trust_tier=TrustTier.GATED,
        scope=["read"],
        enabled=True,
        created_at=now,
        updated_at=now,
        version=0,
    )
    await registry.register(_TEST_USER, node)
    return node


# ---------------------------------------------------------------------------
# GET /app/v1/mcp-servers/{id}/tools
# ---------------------------------------------------------------------------


def test_list_tools_not_found_returns_404() -> None:
    """GET /mcp-servers/{id}/tools returns 404 for unknown server."""
    app, _, _reg = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/mcp-servers/MCP-nonexistent/tools")
    assert response.status_code == 404


def test_list_tools_unreachable_server_returns_empty() -> None:
    """GET /mcp-servers/{id}/tools returns [] when server is unreachable."""
    import asyncio

    app, _, registry = _make_app()

    # Seed the server into the registry (storage-backed)
    asyncio.run(_seed_server(registry, "MCP-testtools001"))
    client = TestClient(app)
    # The server at localhost:9999 won't be reachable — should degrade to []
    response = client.get("/app/v1/mcp-servers/MCP-testtools001/tools")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# GET /app/v1/mcp-approvals
# ---------------------------------------------------------------------------


def test_list_mcp_approvals_empty_returns_list() -> None:
    """GET /mcp-approvals returns [] when no approvals are pending."""
    app, _, _reg = _make_app()
    client = TestClient(app)
    response = client.get("/app/v1/mcp-approvals")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_mcp_approvals_returns_pending_tasks() -> None:
    """GET /mcp-approvals returns pending APPROVAL tasks for the user."""

    app, store, _reg = _make_app()

    # Seed an APPROVAL task
    now = utcnow()
    store._nodes["TSK-TST-0001-APR"] = {
        "id": "TSK-TST-0001-APR",
        "task_type": "APPROVAL",
        "state": "PENDING",
        "assigned_to": _TEST_USER,
        "title": "Approve MCP tool call",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    client = TestClient(app)
    response = client.get("/app/v1/mcp-approvals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "TSK-TST-0001-APR"
    assert data[0]["state"] == "PENDING"
