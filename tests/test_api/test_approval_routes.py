# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_api.test_approval_routes — Tests for /app/v1/approvals endpoints.

Description
-----------
Verifies that the approvals endpoints list pending APPROVAL tasks and correctly
drive them to COMPLETE (approve) or CANCELLED (deny) via the state machine.

Design Patterns
---------------
- ``app.dependency_overrides``: Both ``require_auth`` and ``get_graph_store``
  are overridden with fakes so no real database is needed.
- ``FakeGraphStore``: In-memory graph store seeded with real ``TaskNode``
  objects, replacing the previous module-level ``_pending_approvals`` dict.
- Real StateMachine: The actual ``StateMachine`` runs during tests, validating
  that transition chains work correctly.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.deps: get_graph_store.
- graphclaw.auth.middleware: require_auth.
- graphclaw.models.base: generate_task_id.
- graphclaw.models.enums: TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- tests.test_api.conftest: FakeGraphStore.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api.deps import get_graph_store
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth
from graphclaw.models.base import generate_task_id, utcnow
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from tests.test_api.conftest import FakeGraphStore

_TEST_USER = "test-user-approvals-001"

# ---------------------------------------------------------------------------
# Module-level fake store (reset per test by fixture)
# ---------------------------------------------------------------------------

_fake_store = FakeGraphStore()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(app_router)

    async def _fake_auth() -> str:
        return _TEST_USER

    async def _fake_store_dep():
        return _fake_store

    app.dependency_overrides[require_auth] = _fake_auth
    app.dependency_overrides[get_graph_store] = _fake_store_dep
    return app


def _seed_approval() -> str:
    """Create a real TaskNode in PENDING state and return its task_id."""
    import asyncio

    now = utcnow()
    task_id = generate_task_id("TST", TaskType.APPROVAL)
    task = TaskNode(
        id=task_id,
        task_type=TaskType.APPROVAL,
        title="Approve MCP tool: test_tool on test_server",
        description="Please approve this tool call",
        assigned_to=_TEST_USER,
        owned_by=_TEST_USER,
        created_by=_TEST_USER,
        state=TaskState.PENDING,
        created_at=now,
        updated_at=now,
        version=0,
    )
    # Synchronously seed the fake store
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_fake_store.create_node(task))
    finally:
        loop.close()
    return task_id


@pytest.fixture(autouse=True)
def clear_approvals():
    """Reset the fake store before and after each test."""
    _fake_store.clear()
    yield
    _fake_store.clear()


@pytest.fixture()
def client():
    return TestClient(_make_app())


@pytest.fixture()
def no_auth_client():
    app = FastAPI()
    app.include_router(app_router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /app/v1/approvals
# ---------------------------------------------------------------------------


def test_list_approvals_empty(client: TestClient) -> None:
    """GET /app/v1/approvals returns an empty list when no tasks are pending."""
    response = client.get("/app/v1/approvals")
    assert response.status_code == 200
    assert response.json() == []


def test_list_approvals_returns_pending(client: TestClient) -> None:
    """GET /app/v1/approvals returns seeded APPROVAL tasks."""
    id1 = _seed_approval()
    id2 = _seed_approval()
    response = client.get("/app/v1/approvals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    task_ids = {t["task_id"] for t in data}
    assert id1 in task_ids
    assert id2 in task_ids


def test_list_approvals_requires_auth(no_auth_client: TestClient) -> None:
    """GET /app/v1/approvals returns 401/403 without a Bearer token."""
    response = no_auth_client.get("/app/v1/approvals")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /app/v1/approvals/{task_id}/approve
# ---------------------------------------------------------------------------


def test_approve_task_sets_complete(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve transitions task to COMPLETE."""
    task_id = _seed_approval()
    response = client.post(f"/app/v1/approvals/{task_id}/approve")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "COMPLETE"
    assert data["ok"] is True


def test_approve_task_not_found(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve returns 404 for unknown task."""
    response = client.post("/app/v1/approvals/TSK-XX-000001-APR/approve")
    assert response.status_code == 404


def test_approve_task_updates_stored_state(client: TestClient) -> None:
    """Approving a task persists the COMPLETE state in the store."""
    import asyncio

    task_id = _seed_approval()
    client.post(f"/app/v1/approvals/{task_id}/approve")

    loop = asyncio.new_event_loop()
    try:
        node = loop.run_until_complete(_fake_store.get_node(task_id))
    finally:
        loop.close()

    assert node is not None
    assert node["state"] == "COMPLETE"


# ---------------------------------------------------------------------------
# POST /app/v1/approvals/{task_id}/deny
# ---------------------------------------------------------------------------


def test_deny_task_sets_cancelled(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny transitions task to CANCELLED."""
    task_id = _seed_approval()
    response = client.post(f"/app/v1/approvals/{task_id}/deny")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "CANCELLED"
    assert data["ok"] is True


def test_deny_task_not_found(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny returns 404 for unknown task."""
    response = client.post("/app/v1/approvals/TSK-XX-000001-APR/deny")
    assert response.status_code == 404


def test_deny_task_updates_stored_state(client: TestClient) -> None:
    """Denying a task persists the CANCELLED state in the store."""
    import asyncio

    task_id = _seed_approval()
    client.post(f"/app/v1/approvals/{task_id}/deny")

    loop = asyncio.new_event_loop()
    try:
        node = loop.run_until_complete(_fake_store.get_node(task_id))
    finally:
        loop.close()

    assert node is not None
    assert node["state"] == "CANCELLED"


def test_approve_requires_auth(no_auth_client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve returns 401/403 without Bearer token."""
    response = no_auth_client.post("/app/v1/approvals/TSK-XX-000001-APR/approve")
    assert response.status_code in (401, 403)


def test_deny_requires_auth(no_auth_client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny returns 401/403 without Bearer token."""
    response = no_auth_client.post("/app/v1/approvals/TSK-XX-000001-APR/deny")
    assert response.status_code in (401, 403)
