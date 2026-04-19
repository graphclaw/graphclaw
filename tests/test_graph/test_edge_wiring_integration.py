"""Integration tests for O-DB-02: OWNED_BY and ASSIGNED_TO edges are created.

Verifies that after creating a task via:
 1. POST /tasks  (REST API)
 2. AgentLoop._tool_create_task()  (agent tool)

the following graph edges are present in AGE:
 - OWNED_BY:    task_id → user_id  (always)
 - ASSIGNED_TO: task_id → assignee_id  (when assignee_id is provided)

Run with::

    pytest tests/test_graph/test_edge_wiring_integration.py -m integration

The DSN is read from TEST_DATABASE_URL (falls back to Docker Compose default).
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
from graphclaw.models.nodes import TaskNode, UserNode

pytestmark = pytest.mark.integration

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)
_TEST_USER = "USER-edge-int-test"
_TEST_ASSIGNEE = "USER-edge-assignee-int-test"


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
    """Create stub UserNodes so OWNED_BY / ASSIGNED_TO edges can link to real nodes."""
    from graphclaw.models.nodes import UserNode

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
    """Minimal FastAPI app wired to the real AgeGraphStore."""
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


async def _edges_from(repo: AgeGraphStore, source_id: str) -> list[dict]:
    """Return all outgoing edges from source_id."""
    return await repo.get_edges(source_id, direction="out")


def _edge_types(edges: list[dict]) -> set[str]:
    return {e.get("_label", "") for e in edges}


def _edge_target(edge: dict) -> str | None:
    return edge.get("_end_id")


# ---------------------------------------------------------------------------
# REST API: POST /tasks
# ---------------------------------------------------------------------------


class TestCreateTaskEdgesViaRestAPI:
    async def test_owned_by_edge_created_on_task_creation(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """POST /tasks creates an OWNED_BY edge from task to the requesting user."""
        resp = await client.post(
            "/graph/tasks",
            json={"title": "Edge test task", "task_type": "ATOMIC"},
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]

        try:
            edges = await _edges_from(repo, task_id)
            assert "OWNED_BY" in _edge_types(edges), (
                f"Expected OWNED_BY edge from {task_id}, got: {_edge_types(edges)}"
            )
            owned_by_edge = next(
                e for e in edges if e.get("_label") == "OWNED_BY"
            )
            assert _edge_target(owned_by_edge) == _TEST_USER
        finally:
            await repo.delete_node(task_id)

    async def test_no_assigned_to_edge_when_no_assignee(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """POST /tasks without assignee_id must NOT create an ASSIGNED_TO edge."""
        resp = await client.post(
            "/graph/tasks",
            json={"title": "No assignee task", "task_type": "ATOMIC"},
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]

        try:
            edges = await _edges_from(repo, task_id)
            assert "ASSIGNED_TO" not in _edge_types(edges), (
                f"Unexpected ASSIGNED_TO edge found for task {task_id}"
            )
        finally:
            await repo.delete_node(task_id)

    async def test_assigned_to_edge_created_when_assignee_provided(
        self, client: AsyncClient, repo: AgeGraphStore
    ):
        """POST /tasks with assignee_id creates an ASSIGNED_TO edge to the assignee."""
        resp = await client.post(
            "/graph/tasks",
            json={
                "title": "Assigned task",
                "task_type": "ATOMIC",
                "assignee_id": _TEST_ASSIGNEE,
            },
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]

        try:
            edges = await _edges_from(repo, task_id)
            edge_types = _edge_types(edges)
            assert "OWNED_BY" in edge_types, f"Missing OWNED_BY edge for {task_id}"
            assert "ASSIGNED_TO" in edge_types, f"Missing ASSIGNED_TO edge for {task_id}"

            assigned_edge = next(
                e for e in edges if e.get("_label") == "ASSIGNED_TO"
            )
            assert _edge_target(assigned_edge) == _TEST_ASSIGNEE
        finally:
            await repo.delete_node(task_id)


# ---------------------------------------------------------------------------
# AgentLoop._tool_create_task
# ---------------------------------------------------------------------------


class TestCreateTaskEdgesViaAgentLoop:
    async def test_owned_by_edge_created_by_agent_tool(self, repo: AgeGraphStore):
        """AgentLoop._tool_create_task creates OWNED_BY edge when it creates a task."""
        from unittest.mock import AsyncMock, MagicMock

        from graphclaw.agent.loop import AgentLoop

        loop = _make_agent_loop(repo)

        args = {"title": "Agent task", "task_type": "ATOMIC"}
        result = await loop._tool_create_task(user_id=_TEST_USER, args=args)

        task_id = result["task_id"]
        try:
            edges = await _edges_from(repo, task_id)
            assert "OWNED_BY" in _edge_types(edges), (
                f"Expected OWNED_BY edge from {task_id}, got {_edge_types(edges)}"
            )
        finally:
            await repo.delete_node(task_id)

    async def test_assigned_to_edge_created_by_agent_tool(self, repo: AgeGraphStore):
        """AgentLoop._tool_create_task creates ASSIGNED_TO edge when assigned_to is given."""
        loop = _make_agent_loop(repo)

        args = {
            "title": "Assigned agent task",
            "task_type": "ATOMIC",
            "assigned_to": _TEST_ASSIGNEE,
        }
        result = await loop._tool_create_task(user_id=_TEST_USER, args=args)

        task_id = result["task_id"]
        try:
            edges = await _edges_from(repo, task_id)
            edge_types = _edge_types(edges)
            assert "OWNED_BY" in edge_types, f"Missing OWNED_BY for {task_id}"
            assert "ASSIGNED_TO" in edge_types, f"Missing ASSIGNED_TO for {task_id}"

            assigned_edge = next(
                e for e in edges if e.get("_label") == "ASSIGNED_TO"
            )
            assert _edge_target(assigned_edge) == _TEST_ASSIGNEE
        finally:
            await repo.delete_node(task_id)

    async def test_assigned_to_field_set_on_tasknode(self, repo: AgeGraphStore):
        """AgentLoop._tool_create_task sets assigned_to property on the TaskNode."""
        loop = _make_agent_loop(repo)

        args = {
            "title": "Assignee property task",
            "task_type": "ATOMIC",
            "assigned_to": _TEST_ASSIGNEE,
        }
        result = await loop._tool_create_task(user_id=_TEST_USER, args=args)
        task_id = result["task_id"]

        try:
            raw = await repo.get_node(task_id)
            assert raw is not None
            assert raw.get("assigned_to") == _TEST_ASSIGNEE
        finally:
            await repo.delete_node(task_id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_agent_loop(repo: AgeGraphStore):
    """Build a minimal AgentLoop with only the graph repo wired up."""
    from unittest.mock import AsyncMock, MagicMock

    from graphclaw.agent.loop import AgentLoop
    from graphclaw.scoring.engine import ScoringEngine
    from graphclaw.state.machine import StateMachine

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=MagicMock(content="OK"))
    mock_storage = MagicMock()
    mock_storage.write = AsyncMock()
    mock_storage.read = AsyncMock(return_value=b"")
    mock_scoring = MagicMock(spec=ScoringEngine)
    mock_scoring.score_task = AsyncMock(return_value=None)
    mock_sm = MagicMock(spec=StateMachine)

    loop = AgentLoop(
        graph_repo=repo,
        llm_client=mock_llm,
        storage_client=mock_storage,
        scoring_engine=mock_scoring,
        state_machine=mock_sm,
    )
    return loop
