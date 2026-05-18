# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for O-SM-01 and O-SM-02: cascade wiring from the API layer.

Tests verify that after POST /tasks/{id}/transition → COMPLETE:

1. (O-SM-02) INACTIVE_PENDING tasks that declare a DEPENDS_ON dependency on the
   completed task are transitioned to ACTIVE and persisted in AGE.
2. (O-SM-01) A COMPOSITE parent whose all children are now COMPLETE is
   auto-completed and its updated state is persisted in AGE.
3. An AND-gate COMPOSITE parent stays ACTIVE when only one of many children
   completes.

All tests run against a real PostgreSQL + Apache AGE instance.

Run with::

    pytest tests/test_state/test_cascade_integration.py -m integration

The DSN is read from TEST_DATABASE_URL (falls back to Docker Compose default).
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
from graphclaw.models.enums import GateType, TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import CompositeMetadata
from graphclaw.state.cascade import activate_next_in_chain

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
_TEST_USER = "USER-cascade-int-test"


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
def app(repo):
    """Minimal FastAPI app wired directly to the real AgeGraphStore.

    Only mounts the state router so we avoid needing all other ``app.state``
    objects (scoring engine, storage client, etc.).
    """
    test_app = FastAPI()
    test_app.include_router(state_router)  # prefix=/tasks
    test_app.dependency_overrides[require_auth] = lambda: _TEST_USER
    test_app.dependency_overrides[get_graph_store] = lambda: repo
    test_app.dependency_overrides[get_state_machine] = lambda: __import__(
        "graphclaw.state.machine", fromlist=["StateMachine"]
    ).StateMachine()
    return test_app


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _store_task(repo: AgeGraphStore, task: TaskNode) -> None:
    """Persist a TaskNode to AGE using create_node."""

    class _Wrapper:
        def model_dump(self, **kw):
            return task.model_dump(mode="json")

    await repo.create_node(_Wrapper())


# ---------------------------------------------------------------------------
# Direct cascade function tests (O-SM-02: activate_next_in_chain)
# ---------------------------------------------------------------------------


class TestActivateNextInChainDirect:
    """Tests that call activate_next_in_chain directly against the real DB.

    These are faster than going through the HTTP layer and give precise
    coverage of the cascade logic independent of the API endpoint.
    """

    @pytest.mark.asyncio
    async def test_inactive_pending_dependent_becomes_active(self, repo: AgeGraphStore):
        """INACTIVE_PENDING task depending on a completed task is activated."""
        task_a = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Predecessor",
            description="Completes first",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_b = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Waiting Dependent",
            description="Waits for A",
            state=TaskState.INACTIVE_PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, task_a)
        await _store_task(repo, task_b)
        # (task_b)-[:DEPENDS_ON]->(task_a)
        await repo.create_edge(task_b.id, task_a.id, "DEPENDS_ON", {})

        try:
            # Simulate task_a reaching COMPLETE
            task_a.state = TaskState.COMPLETE
            activated = await activate_next_in_chain(task_a, repo)

            # Persist the activated tasks (as the API endpoint does)
            for act in activated:
                await repo.update_node(act.id, act.model_dump(mode="json"))

            # Verify B is ACTIVE in the real DB
            raw_b = await repo.get_node(task_b.id)
            assert raw_b is not None
            assert raw_b["state"] == TaskState.ACTIVE.value, (
                f"Expected ACTIVE, got {raw_b['state']}"
            )
            assert len(activated) == 1
            assert activated[0].id == task_b.id
        finally:
            await repo.delete_node(task_a.id)
            await repo.delete_node(task_b.id)

    @pytest.mark.asyncio
    async def test_already_active_task_not_re_activated(self, repo: AgeGraphStore):
        """A task that is already ACTIVE is not returned as activated."""
        task_a = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Pred",
            description="",
            state=TaskState.COMPLETE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_b = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Already Active",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, task_a)
        await _store_task(repo, task_b)
        await repo.create_edge(task_b.id, task_a.id, "DEPENDS_ON", {})

        try:
            activated = await activate_next_in_chain(task_a, repo)
            assert activated == [], "ACTIVE task should not be re-activated"
        finally:
            await repo.delete_node(task_a.id)
            await repo.delete_node(task_b.id)

    @pytest.mark.asyncio
    async def test_multiple_dependents_all_activated(self, repo: AgeGraphStore):
        """All INACTIVE_PENDING dependents are activated when predecessor completes."""
        task_a = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Shared Predecessor",
            description="",
            state=TaskState.COMPLETE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_b = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Dep B",
            description="",
            state=TaskState.INACTIVE_PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_c = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Dep C",
            description="",
            state=TaskState.INACTIVE_PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, task_a)
        await _store_task(repo, task_b)
        await _store_task(repo, task_c)
        await repo.create_edge(task_b.id, task_a.id, "DEPENDS_ON", {})
        await repo.create_edge(task_c.id, task_a.id, "DEPENDS_ON", {})

        try:
            activated = await activate_next_in_chain(task_a, repo)
            for act in activated:
                await repo.update_node(act.id, act.model_dump(mode="json"))

            activated_ids = {a.id for a in activated}
            assert task_b.id in activated_ids
            assert task_c.id in activated_ids

            # Verify in DB
            for dep_id in (task_b.id, task_c.id):
                raw = await repo.get_node(dep_id)
                assert raw["state"] == TaskState.ACTIVE.value
        finally:
            await repo.delete_node(task_a.id)
            await repo.delete_node(task_b.id)
            await repo.delete_node(task_c.id)


# ---------------------------------------------------------------------------
# HTTP endpoint tests (O-SM-01 + O-SM-02 via POST /tasks/{id}/transition)
# ---------------------------------------------------------------------------


class TestCascadeViaTransitionEndpoint:
    """End-to-end tests that POST to /tasks/{id}/transition and verify
    cascade side-effects are persisted to the real AGE database."""

    @pytest.mark.asyncio
    async def test_chain_activation_via_api(self, app: FastAPI, repo: AgeGraphStore):
        """Completing task A via the API activates an INACTIVE_PENDING task B."""
        task_a = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="A",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_b = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="B",
            description="",
            state=TaskState.INACTIVE_PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, task_a)
        await _store_task(repo, task_b)
        await repo.create_edge(task_b.id, task_a.id, "DEPENDS_ON", {})

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/tasks/{task_a.id}/transition",
                    json={"target_state": "COMPLETE", "reason": "integration test"},
                )
            assert resp.status_code == 200, resp.text

            # task_a itself should be COMPLETE
            raw_a = await repo.get_node(task_a.id)
            assert raw_a["state"] == TaskState.COMPLETE.value

            # task_b should have been activated by cascade
            raw_b = await repo.get_node(task_b.id)
            assert raw_b is not None
            assert raw_b["state"] == TaskState.ACTIVE.value, (
                f"Expected B to be ACTIVE after cascade, got {raw_b['state']}"
            )
        finally:
            await repo.delete_node(task_a.id)
            await repo.delete_node(task_b.id)

    @pytest.mark.asyncio
    async def test_composite_parent_autocompletes_when_all_children_done(
        self, app: FastAPI, repo: AgeGraphStore
    ):
        """When the last child completes, an AND-gate composite parent auto-completes."""
        parent = TaskNode(
            id=generate_task_id("CI", TaskType.COMPOSITE),
            task_type=TaskType.COMPOSITE,
            title="Parent",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
            type_metadata=CompositeMetadata(
                completion_gate=GateType.AND,
                auto_complete_on_children=True,
            ),
        )
        child1 = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Child 1 (already done)",
            description="",
            state=TaskState.COMPLETE,  # already complete
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        child2 = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Child 2 (last one)",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, parent)
        await _store_task(repo, child1)
        await _store_task(repo, child2)
        # Wire children → parent via PART_OF
        await repo.create_edge(child1.id, parent.id, "PART_OF", {})
        await repo.create_edge(child2.id, parent.id, "PART_OF", {})

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/tasks/{child2.id}/transition",
                    json={"target_state": "COMPLETE", "reason": "last child done"},
                )
            assert resp.status_code == 200, resp.text

            # Composite parent should have been auto-completed
            raw_parent = await repo.get_node(parent.id)
            assert raw_parent is not None
            assert raw_parent["state"] == TaskState.COMPLETE.value, (
                f"Parent should be COMPLETE after all children done, got {raw_parent['state']}"
            )
        finally:
            await repo.delete_node(child2.id)
            await repo.delete_node(child1.id)
            await repo.delete_node(parent.id)

    @pytest.mark.asyncio
    async def test_composite_parent_stays_active_with_incomplete_children(
        self, app: FastAPI, repo: AgeGraphStore
    ):
        """AND-gate composite parent is NOT completed when a child still remains."""
        parent = TaskNode(
            id=generate_task_id("CI", TaskType.COMPOSITE),
            task_type=TaskType.COMPOSITE,
            title="Parent Partial",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
            type_metadata=CompositeMetadata(
                completion_gate=GateType.AND,
                auto_complete_on_children=True,
            ),
        )
        child1 = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Child 1",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        child2 = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="Child 2 (still pending)",
            description="",
            state=TaskState.ACTIVE,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, parent)
        await _store_task(repo, child1)
        await _store_task(repo, child2)
        await repo.create_edge(child1.id, parent.id, "PART_OF", {})
        await repo.create_edge(child2.id, parent.id, "PART_OF", {})

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/tasks/{child1.id}/transition",
                    json={"target_state": "COMPLETE", "reason": "one done"},
                )
            assert resp.status_code == 200, resp.text

            # Parent must still be ACTIVE — child2 is not done
            raw_parent = await repo.get_node(parent.id)
            assert raw_parent is not None
            assert raw_parent["state"] == TaskState.ACTIVE.value, (
                f"Parent should stay ACTIVE (AND-gate, child2 incomplete), "
                f"got {raw_parent['state']}"
            )
        finally:
            await repo.delete_node(child1.id)
            await repo.delete_node(child2.id)
            await repo.delete_node(parent.id)

    @pytest.mark.asyncio
    async def test_non_complete_transition_does_not_trigger_cascade(
        self, app: FastAPI, repo: AgeGraphStore
    ):
        """Transitioning to ACTIVE (not COMPLETE) leaves dependents unchanged."""
        task_a = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="A — not completing",
            description="",
            state=TaskState.PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )
        task_b = TaskNode(
            id=generate_task_id("CI", TaskType.ATOMIC),
            task_type=TaskType.ATOMIC,
            title="B — should stay INACTIVE_PENDING",
            description="",
            state=TaskState.INACTIVE_PENDING,
            owned_by=_TEST_USER,
            created_at=_now(),
            updated_at=_now(),
        )

        await _store_task(repo, task_a)
        await _store_task(repo, task_b)
        await repo.create_edge(task_b.id, task_a.id, "DEPENDS_ON", {})

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/tasks/{task_a.id}/transition",
                    json={"target_state": "ACTIVE", "reason": "just activating"},
                )
            assert resp.status_code == 200, resp.text

            # B should still be INACTIVE_PENDING — no cascade for non-COMPLETE
            raw_b = await repo.get_node(task_b.id)
            assert raw_b["state"] == TaskState.INACTIVE_PENDING.value, (
                f"B should stay INACTIVE_PENDING, got {raw_b['state']}"
            )
        finally:
            await repo.delete_node(task_a.id)
            await repo.delete_node(task_b.id)
