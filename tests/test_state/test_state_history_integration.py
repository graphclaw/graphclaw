# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for O-SM-03: state_history persisted and retrievable.

Verifies that after POST /tasks/{id}/transition:
  1. The task's state_history is non-empty when read back.
  2. GET /tasks/{id}/state-history returns the history as dicts, not raw JSON strings.
  3. Multiple transitions accumulate correctly in state_history.
  4. Cascade-activated tasks (O-SM-02 path) also have state_history written.

Run with::

    pytest tests/test_state/test_state_history_integration.py -m integration
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from graphclaw.api.deps import get_graph_store, get_state_machine
from graphclaw.api.state import router as state_router
from graphclaw.auth.middleware import require_auth
from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.state.machine import StateMachine

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
_TEST_USER = "USER-sm03-int-test"
_TEST_ASSIGNEE = "USER-sm03-assignee-int-test"
_TEST_UNRELATED = "USER-sm03-unrelated-int-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pool():
    p = await create_pool(TEST_DSN)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    return AgeGraphStore(pool)


@pytest.fixture
def auth_user() -> dict[str, str]:
    return {"id": _TEST_USER}


@pytest.fixture
def app(repo: AgeGraphStore, auth_user: dict[str, str]):
    """Minimal FastAPI app wired to the real AgeGraphStore and real StateMachine."""
    sm = StateMachine()
    test_app = FastAPI()
    test_app.include_router(state_router)

    test_app.dependency_overrides[require_auth] = lambda: auth_user["id"]
    test_app.dependency_overrides[get_graph_store] = lambda: repo
    test_app.dependency_overrides[get_state_machine] = lambda: sm
    return test_app


@pytest_asyncio.fixture
async def client(app: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_task(repo: AgeGraphStore, state: TaskState = TaskState.PENDING) -> str:
    tid = generate_task_id("SM", TaskType.ATOMIC)
    task = TaskNode(
        id=tid,
        title="sm03 state history test",
        description="sm03 integration test task",
        task_type=TaskType.ATOMIC,
        created_by=_TEST_USER,
        owned_by=_TEST_USER,
        state=state,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repo.create_node(task)
    return tid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStateHistoryPersisted:
    async def test_state_history_written_after_transition(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """After POST /transition, the task's state_history must have one entry."""
        tid = await _create_task(repo)
        try:
            resp = await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "ACTIVE", "reason": "sm03 test"},
            )
            assert resp.status_code == 200, resp.text

            # Read back and check state_history
            raw = await repo.get_node(tid)
            assert raw is not None

            # state_history is stored as list of JSON strings; _deserialize handles it
            from graphclaw.api.state import _deserialize_task_fields

            task_data = _deserialize_task_fields(raw)
            history = task_data.get("state_history", [])
            assert len(history) == 1, f"Expected 1 history entry, got {len(history)}: {history}"
            entry = history[0]
            assert isinstance(entry, dict), f"Expected dict, got {type(entry)}: {entry}"
            assert entry.get("from_state") == "PENDING"
            assert entry.get("to_state") == "ACTIVE"
        finally:
            await repo.delete_node(tid)

    async def test_multiple_transitions_accumulate_in_history(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """Multiple transitions must each append to state_history, not overwrite."""
        tid = await _create_task(repo)
        try:
            await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "ACTIVE", "reason": "first"},
            )
            await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "COMPLETE", "reason": "second"},
            )

            from graphclaw.api.state import _deserialize_task_fields

            raw = await repo.get_node(tid)
            task_data = _deserialize_task_fields(raw)
            history = task_data.get("state_history", [])
            assert len(history) == 2, f"Expected 2 history entries, got {len(history)}: {history}"
            assert history[0].get("to_state") == "ACTIVE"
            assert history[1].get("to_state") == "COMPLETE"
        finally:
            await repo.delete_node(tid)

    async def test_get_state_history_endpoint_returns_dicts_not_strings(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """GET /tasks/{id}/state-history must return parsed dicts, not raw JSON strings."""
        tid = await _create_task(repo)
        try:
            await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "ACTIVE", "reason": "endpoint test"},
            )

            resp = await client.get(f"/tasks/{tid}/state-history")
            assert resp.status_code == 200, resp.text

            data = resp.json()
            entries = data.get("entries", [])
            assert len(entries) == 1, f"Expected 1 entry, got {len(entries)}: {entries}"
            entry = entries[0]
            assert isinstance(entry, dict), f"Expected dict entry, got {type(entry)}: {entry}"
            # Verify the entry has the expected fields
            assert "from_state" in entry, f"Missing from_state in {entry}"
            assert "to_state" in entry, f"Missing to_state in {entry}"
            assert entry["from_state"] == "PENDING"
            assert entry["to_state"] == "ACTIVE"
            assert entry.get("reason") == "endpoint test"
        finally:
            await repo.delete_node(tid)

    async def test_cascade_activated_task_has_state_history(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """After cascade activation (O-SM-02), the activated task has a history entry."""
        # Create the predecessor (PENDING → will become ACTIVE then COMPLETE)
        pred_id = generate_task_id("SM", TaskType.ATOMIC)
        pred = TaskNode(
            id=pred_id,
            title="predecessor",
            description="sm03 predecessor",
            task_type=TaskType.ATOMIC,
            created_by=_TEST_USER,
            owned_by=_TEST_USER,
            state=TaskState.ACTIVE,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await repo.create_node(pred)

        # Create the dependent (INACTIVE_PENDING, waiting on pred via DEPENDS_ON)
        dep_id = generate_task_id("SM", TaskType.ATOMIC)
        dep = TaskNode(
            id=dep_id,
            title="dependent",
            description="sm03 dependent",
            task_type=TaskType.ATOMIC,
            created_by=_TEST_USER,
            owned_by=_TEST_USER,
            state=TaskState.INACTIVE_PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await repo.create_node(dep)
        # dep -[DEPENDS_ON]-> pred
        await repo.create_edge(dep_id, pred_id, "DEPENDS_ON", {})

        try:
            # Complete the predecessor; cascade should activate the dependent
            resp = await client.post(
                f"/tasks/{pred_id}/transition",
                json={"target_state": "COMPLETE", "reason": "cascade test"},
            )
            assert resp.status_code == 200, resp.text

            # The dependent task should now be ACTIVE and have a history entry
            from graphclaw.api.state import _deserialize_task_fields

            raw_dep = await repo.get_node(dep_id)
            assert raw_dep is not None
            dep_data = _deserialize_task_fields(raw_dep)

            assert dep_data.get("state") == "ACTIVE", (
                f"Expected dependent to be ACTIVE, got {dep_data.get('state')}"
            )
            history = dep_data.get("state_history", [])
            assert len(history) >= 1, (
                f"Expected cascade history entry, got {len(history)}: {history}"
            )
            cascade_entry = history[-1]
            assert cascade_entry.get("to_state") == "ACTIVE"
        finally:
            await repo.delete_node(pred_id)
            await repo.delete_node(dep_id)

    async def test_assignee_can_transition_task(
        self,
        client: AsyncClient,
        repo: AgeGraphStore,
        auth_user: dict[str, str],
    ):
        """Assigned users are authorized to perform transitions."""
        tid = generate_task_id("SM", TaskType.ATOMIC)
        task = TaskNode(
            id=tid,
            title="assignee transition",
            description="assignee authorization path",
            task_type=TaskType.ATOMIC,
            created_by=_TEST_USER,
            owned_by=_TEST_USER,
            assigned_to=_TEST_ASSIGNEE,
            state=TaskState.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await repo.create_node(task)

        try:
            auth_user["id"] = _TEST_ASSIGNEE
            resp = await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "ACTIVE", "reason": "assignee authorization"},
            )
            assert resp.status_code == 200, resp.text
        finally:
            auth_user["id"] = _TEST_USER
            await repo.delete_node(tid)

    async def test_unrelated_user_gets_403_on_transition(
        self,
        client: AsyncClient,
        repo: AgeGraphStore,
        auth_user: dict[str, str],
    ):
        """Users who are neither owner nor assignee cannot transition the task."""
        tid = await _create_task(repo)
        try:
            auth_user["id"] = _TEST_UNRELATED
            resp = await client.post(
                f"/tasks/{tid}/transition",
                json={"target_state": "ACTIVE", "reason": "unauthorized"},
            )
            assert resp.status_code == 403, resp.text
        finally:
            auth_user["id"] = _TEST_USER
            await repo.delete_node(tid)
