"""Integration tests for O-AGT-02: FollowUp auto-spawn on DELEGATED task creation.

Verifies that creating a DELEGATED task (via REST API or AgentLoop) automatically:
  1. Creates a FollowUp sibling task with state=INACTIVE_PENDING
  2. Wires a FOLLOW_UP_FOR edge from the FollowUp to the DELEGATED task
  3. Updates the DELEGATED task's type_metadata.follow_up_task_id
  4. Does NOT spawn a FollowUp for non-DELEGATED task types

Run with::

    pytest tests/test_agent/test_followup_spawn_integration.py -m integration
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from graphclaw.api.deps import get_graph_store
from graphclaw.api.graph import router as graph_router
from graphclaw.auth.middleware import require_auth
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.db.connection import create_pool
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import UserNode

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
_TEST_USER = "USER-agt02-int-test"
_TEST_ASSIGNEE = "USER-agt02-assignee-int-test"


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


@pytest_asyncio.fixture(autouse=True)
async def seed_user_nodes(repo: AgeGraphStore):
    """Create stub UserNodes so edges can link to real nodes."""
    for uid in (_TEST_USER, _TEST_ASSIGNEE):
        existing = await repo.get_node(uid)
        if existing is None:
            user = UserNode(
                id=uid,
                name=uid,
                email=f"{uid.lower().replace('-', '.')}@example.com",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            await repo.create_node(user)
    yield


@pytest.fixture
def app(repo: AgeGraphStore):
    test_app = FastAPI()
    test_app.include_router(graph_router)
    test_app.dependency_overrides[require_auth] = lambda: _TEST_USER
    test_app.dependency_overrides[get_graph_store] = lambda: repo
    return test_app


@pytest_asyncio.fixture
async def client(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _find_followup(repo: AgeGraphStore, delegated_id: str) -> dict | None:
    """Return the FollowUp node linked via FOLLOW_UP_FOR edge from delegated_id."""
    edges = await repo.get_edges(delegated_id, direction="in", edge_type="FOLLOW_UP_FOR")
    if not edges:
        return None
    followup_id = edges[0].get("_start_id")
    if not followup_id:
        return None
    return await repo.get_node(followup_id)


# ---------------------------------------------------------------------------
# REST API tests
# ---------------------------------------------------------------------------


class TestFollowUpSpawnViaRestAPI:
    async def test_delegated_task_spawns_followup(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """POST /graph/tasks with task_type=DELEGATED creates a FollowUp sibling."""
        resp = await client.post(
            "/graph/tasks",
            json={
                "title": "Delegated task",
                "task_type": "DELEGATED",
                "assignee_id": _TEST_ASSIGNEE,
            },
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        delegated_id = resp.json()["id"]

        try:
            followup = await _find_followup(repo, delegated_id)
            assert followup is not None, (
                f"No FollowUp task found linked to delegated task {delegated_id}"
            )
            assert followup.get("task_type") == "FOLLOWUP", (
                f"Expected task_type=FOLLOWUP, got {followup.get('task_type')}"
            )
            assert followup.get("state") == "INACTIVE_PENDING", (
                f"Expected state=INACTIVE_PENDING, got {followup.get('state')}"
            )
        finally:
            await repo.delete_node(delegated_id)
            followup = await _find_followup(repo, delegated_id)
            if followup:
                await repo.delete_node(followup["id"])

    async def test_delegated_task_follow_up_for_edge_exists(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """A FOLLOW_UP_FOR edge must exist: followup → delegated."""
        resp = await client.post(
            "/graph/tasks",
            json={"title": "Delegated for edge test", "task_type": "DELEGATED"},
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        delegated_id = resp.json()["id"]

        try:
            # Incoming FOLLOW_UP_FOR edges to delegated task
            edges = await repo.get_edges(delegated_id, direction="in", edge_type="FOLLOW_UP_FOR")
            assert len(edges) >= 1, (
                f"Expected FOLLOW_UP_FOR edge pointing to {delegated_id}, got: {edges}"
            )
        finally:
            edges = await repo.get_edges(delegated_id, direction="in", edge_type="FOLLOW_UP_FOR")
            for e in edges:
                fid = e.get("_start_id")
                if fid:
                    await repo.delete_node(fid)
            await repo.delete_node(delegated_id)

    async def test_atomic_task_does_not_spawn_followup(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """POST /graph/tasks with task_type=ATOMIC must NOT create a FollowUp."""
        resp = await client.post(
            "/graph/tasks",
            json={"title": "Atomic task", "task_type": "ATOMIC"},
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        atomic_id = resp.json()["id"]

        try:
            edges = await repo.get_edges(atomic_id, direction="in", edge_type="FOLLOW_UP_FOR")
            assert len(edges) == 0, (
                f"ATOMIC task should not have FOLLOW_UP_FOR edges, got: {edges}"
            )
        finally:
            await repo.delete_node(atomic_id)

    async def test_delegated_task_metadata_has_follow_up_id(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """The DELEGATED task's type_metadata.follow_up_task_id must be set."""
        from graphclaw.api.state import _deserialize_task_fields

        resp = await client.post(
            "/graph/tasks",
            json={"title": "Delegated meta test", "task_type": "DELEGATED"},
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        delegated_id = resp.json()["id"]

        try:
            raw = await repo.get_node(delegated_id)
            data = _deserialize_task_fields(raw)
            meta = data.get("type_metadata")
            assert meta is not None, "type_metadata should be set on delegated task"
            assert meta.get("follow_up_task_id") is not None, (
                f"type_metadata.follow_up_task_id must be set, got: {meta}"
            )
        finally:
            edges = await repo.get_edges(delegated_id, direction="in", edge_type="FOLLOW_UP_FOR")
            for e in edges:
                fid = e.get("_start_id")
                if fid:
                    await repo.delete_node(fid)
            await repo.delete_node(delegated_id)


# ---------------------------------------------------------------------------
# AgentLoop._tool_create_task tests
# ---------------------------------------------------------------------------


class TestFollowUpSpawnViaAgentLoop:
    async def test_delegated_task_spawns_followup_via_agent(self, repo: AgeGraphStore):
        """AgentLoop._tool_create_task with task_type=delegated spawns a FollowUp."""
        from graphclaw.agent.loop import AgentLoop
        from graphclaw.scoring.engine import ScoringEngine
        from graphclaw.state.machine import StateMachine
        from unittest.mock import AsyncMock, MagicMock

        loop = AgentLoop(
            graph_repo=repo,
            scoring_engine=MagicMock(spec=ScoringEngine),
            state_machine=MagicMock(spec=StateMachine),
        )

        result = await loop._tool_create_task(
            user_id=_TEST_USER,
            args={"title": "Agent delegated", "task_type": "delegated", "assigned_to": _TEST_ASSIGNEE},
        )
        delegated_id = result["task_id"]

        try:
            followup = await _find_followup(repo, delegated_id)
            assert followup is not None, (
                f"AgentLoop should auto-spawn FollowUp for {delegated_id}"
            )
            assert followup.get("task_type") == "FOLLOWUP"
            assert followup.get("state") == "INACTIVE_PENDING"
        finally:
            followup = await _find_followup(repo, delegated_id)
            if followup:
                await repo.delete_node(followup["id"])
            await repo.delete_node(delegated_id)
