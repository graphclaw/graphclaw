"""tests.test_api.test_approval_routes — Tests for /app/v1/approvals endpoints.

Description
-----------
Verifies that the approvals endpoints list pending tasks and correctly
transition them to COMPLETE or CANCELLED status.

Design Patterns
---------------
- ``app.dependency_overrides``: ``require_auth`` is replaced with a stub that
  returns a fixed user_id.
- Direct stub manipulation: Tests seed ``_pending_approvals`` directly so they
  do not depend on a real task graph.
- ``TestClient``: Synchronous ASGI test client.

Dependencies
------------
- fastapi.testclient: TestClient.
- graphclaw.api.router: app_router.
- graphclaw.api.approvals: _pending_approvals (test stub access).
- graphclaw.auth.middleware: require_auth.
- fastapi: FastAPI (third-party).
- pytest: test runner.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphclaw.api import approvals as approvals_module
from graphclaw.api.router import app_router
from graphclaw.auth.middleware import require_auth

_TEST_USER = "test-user-approvals-001"


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


def _seed_approval(task_id: str) -> dict:
    entry = {
        "task_id": task_id,
        "description": f"Approve tool call for task {task_id}",
        "tool_name": "post_message",
        "tool_args": {"channel": "#general", "text": "hello"},
        "status": "APPROVAL",
    }
    approvals_module._pending_approvals.setdefault(_TEST_USER, []).append(entry)
    return entry


@pytest.fixture(autouse=True)
def clear_approvals():
    """Reset the stub storage before and after each test."""
    approvals_module._pending_approvals.pop(_TEST_USER, None)
    yield
    approvals_module._pending_approvals.pop(_TEST_USER, None)


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
    _seed_approval("TASK-001")
    _seed_approval("TASK-002")
    response = client.get("/app/v1/approvals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    task_ids = {t["task_id"] for t in data}
    assert "TASK-001" in task_ids
    assert "TASK-002" in task_ids


def test_list_approvals_excludes_non_approval_status(client: TestClient) -> None:
    """GET /app/v1/approvals only returns tasks with APPROVAL status."""
    _seed_approval("TASK-A")
    # Manually add a COMPLETE task — should not appear
    approvals_module._pending_approvals.setdefault(_TEST_USER, []).append(
        {
            "task_id": "TASK-B",
            "description": "already done",
            "tool_name": "list_events",
            "tool_args": {},
            "status": "COMPLETE",
        }
    )
    response = client.get("/app/v1/approvals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "TASK-A"


def test_list_approvals_requires_auth(no_auth_client: TestClient) -> None:
    """GET /app/v1/approvals returns 401/403 without a Bearer token."""
    response = no_auth_client.get("/app/v1/approvals")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /app/v1/approvals/{task_id}/approve
# ---------------------------------------------------------------------------


def test_approve_task_sets_complete(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve transitions task to COMPLETE."""
    _seed_approval("TASK-APPROVE-001")
    response = client.post("/app/v1/approvals/TASK-APPROVE-001/approve")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "TASK-APPROVE-001"
    assert data["status"] == "COMPLETE"
    assert data["ok"] is True


def test_approve_task_not_found(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve returns 404 for unknown task."""
    response = client.post("/app/v1/approvals/TASK-NONEXISTENT/approve")
    assert response.status_code == 404


def test_approve_task_updates_stored_status(client: TestClient) -> None:
    """Approving a task actually updates the stored status."""
    _seed_approval("TASK-STATUS-CHK")
    client.post("/app/v1/approvals/TASK-STATUS-CHK/approve")
    tasks = approvals_module._pending_approvals.get(_TEST_USER, [])
    task = next((t for t in tasks if t["task_id"] == "TASK-STATUS-CHK"), None)
    assert task is not None
    assert task["status"] == "COMPLETE"


# ---------------------------------------------------------------------------
# POST /app/v1/approvals/{task_id}/deny
# ---------------------------------------------------------------------------


def test_deny_task_sets_cancelled(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny transitions task to CANCELLED."""
    _seed_approval("TASK-DENY-001")
    response = client.post("/app/v1/approvals/TASK-DENY-001/deny")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "TASK-DENY-001"
    assert data["status"] == "CANCELLED"
    assert data["ok"] is True


def test_deny_task_not_found(client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny returns 404 for unknown task."""
    response = client.post("/app/v1/approvals/TASK-NONEXISTENT/deny")
    assert response.status_code == 404


def test_deny_task_updates_stored_status(client: TestClient) -> None:
    """Denying a task actually updates the stored status."""
    _seed_approval("TASK-DENY-STATUS-CHK")
    client.post("/app/v1/approvals/TASK-DENY-STATUS-CHK/deny")
    tasks = approvals_module._pending_approvals.get(_TEST_USER, [])
    task = next(
        (t for t in tasks if t["task_id"] == "TASK-DENY-STATUS-CHK"), None
    )
    assert task is not None
    assert task["status"] == "CANCELLED"


def test_approve_requires_auth(no_auth_client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/approve returns 401/403 without Bearer token."""
    response = no_auth_client.post("/app/v1/approvals/TASK-X/approve")
    assert response.status_code in (401, 403)


def test_deny_requires_auth(no_auth_client: TestClient) -> None:
    """POST /app/v1/approvals/{id}/deny returns 401/403 without Bearer token."""
    response = no_auth_client.post("/app/v1/approvals/TASK-X/deny")
    assert response.status_code in (401, 403)
