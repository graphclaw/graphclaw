"""Integration tests for GraphRepository and dependency queries.

These tests require a running Postgres + Apache AGE instance.
Run with::

    pytest tests/test_db/ -m integration

The test database must have:
- The ``age`` extension installed
- The ``graphclaw`` property graph created:
    ``SELECT create_graph('graphclaw');``

The DSN is read from the ``TEST_DATABASE_URL`` environment variable, falling
back to the default Docker Compose service address.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from graphclaw.db.connection import create_pool, get_connection
from graphclaw.db.graph_repository import GraphRepository
from graphclaw.db.queries.dependencies import (
    get_downstream_dependents,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw_test",
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Minimal stub node models (no dependency on graphclaw.models)
# ---------------------------------------------------------------------------


@dataclass
class _StubNode:
    """Minimal node stub that satisfies GraphRepository.create_node()."""

    id: str
    node_type: str
    title: str
    state: str = "PENDING"
    estimated_effort_hours: float = 4.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def model_dump(self, *, mode: str = "json") -> dict:  # noqa: ARG002
        return {
            "id": self.id,
            "node_type": self.node_type,
            "title": self.title,
            "state": self.state,
            "estimated_effort_hours": self.estimated_effort_hours,
            "created_at": self.created_at,
        }


def _make_task(
    title: str = "Test Task",
    state: str = "PENDING",
    effort: float = 4.0,
) -> _StubNode:
    uid = uuid.uuid4().hex[:6].upper()
    return _StubNode(
        id=f"TSK-TS-{uid}-ATM",
        node_type="TaskAtomic",
        title=title,
        state=state,
        estimated_effort_hours=effort,
    )


def _make_goal(title: str = "Test Goal") -> _StubNode:
    uid = uuid.uuid4().hex[:8].upper()
    return _StubNode(
        id=f"GOAL-{uid}",
        node_type="GoalNode",
        title=title,
        state="ACTIVE",
        estimated_effort_hours=0.0,
    )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use SelectorEventLoop on Windows for psycopg async compatibility."""
    import sys

    if sys.platform == "win32":
        import asyncio

        return asyncio.WindowsSelectorEventLoopPolicy()
    return None


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Shared connection pool for the test session."""
    pool = await create_pool(dsn=TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="session")
async def repo(db_pool):
    """GraphRepository instance shared across tests in the session."""
    return GraphRepository(pool=db_pool)


# ---------------------------------------------------------------------------
# Per-test graph cleanup
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def clean_graph(db_pool):
    """Delete all vertices (and their edges) before each test."""
    async with get_connection(db_pool) as conn:
        await conn.execute(
            "SELECT * FROM cypher('graphclaw', $$ MATCH (n) DETACH DELETE n $$) as (v agtype)"
        )
    yield


# ---------------------------------------------------------------------------
# Tests: Node CRUD
# ---------------------------------------------------------------------------


class TestCreateAndRetrieveNode:
    """Verify that a node can be inserted and then retrieved by id."""

    async def test_create_returns_properties(self, repo: GraphRepository) -> None:
        task = _make_task(title="Write tests")
        result = await repo.create_node(task)
        assert result["id"] == task.id

    async def test_get_node_returns_inserted_data(self, repo: GraphRepository) -> None:
        task = _make_task(title="Implement feature")
        await repo.create_node(task)

        fetched = await repo.get_node(task.id)

        assert fetched is not None
        assert fetched["id"] == task.id
        assert fetched["title"] == "Implement feature"

    async def test_get_node_missing_returns_none(self, repo: GraphRepository) -> None:
        result = await repo.get_node("NONEXISTENT-ID")
        assert result is None

    async def test_update_node_changes_property(self, repo: GraphRepository) -> None:
        task = _make_task(title="Original title")
        await repo.create_node(task)

        await repo.update_node(task.id, {"title": "Updated title"})
        fetched = await repo.get_node(task.id)

        assert fetched is not None
        assert fetched["title"] == "Updated title"

    async def test_delete_node_removes_vertex(self, repo: GraphRepository) -> None:
        task = _make_task(title="To be deleted")
        await repo.create_node(task)

        await repo.delete_node(task.id)
        fetched = await repo.get_node(task.id)

        assert fetched is None


# ---------------------------------------------------------------------------
# Tests: Edge CRUD
# ---------------------------------------------------------------------------


class TestEdgeCreation:
    """Verify that directed edges can be created between two nodes."""

    async def test_create_edge_and_retrieve(self, repo: GraphRepository) -> None:
        task_a = _make_task(title="Task A")
        task_b = _make_task(title="Task B")
        await repo.create_node(task_a)
        await repo.create_node(task_b)

        edge = await repo.create_edge(
            source_id=task_b.id,
            target_id=task_a.id,
            edge_type="DEPENDS_ON",
        )
        # Edge dict may be empty (no extra properties set).
        assert isinstance(edge, dict)

    async def test_get_edges_outgoing(self, repo: GraphRepository) -> None:
        task_a = _make_task(title="Blocker")
        task_b = _make_task(title="Dependent")
        await repo.create_node(task_a)
        await repo.create_node(task_b)
        await repo.create_edge(
            source_id=task_b.id,
            target_id=task_a.id,
            edge_type="DEPENDS_ON",
        )

        edges = await repo.get_edges(task_b.id, direction="out", edge_type="DEPENDS_ON")

        assert len(edges) >= 1
        end_ids = [e["_end_id"] for e in edges]
        assert task_a.id in end_ids

    async def test_create_edge_with_properties(self, repo: GraphRepository) -> None:
        task_a = _make_task(title="Parent goal task")
        goal = _make_goal(title="Sprint Goal")
        await repo.create_node(task_a)
        await repo.create_node(goal)

        edge = await repo.create_edge(
            source_id=task_a.id,
            target_id=goal.id,
            edge_type="PART_OF",
            properties={"weight": 1},
        )
        assert isinstance(edge, dict)


# ---------------------------------------------------------------------------
# Tests: list_nodes with filters
# ---------------------------------------------------------------------------


class TestListNodes:
    """Verify list_nodes returns the correct subset of vertices."""

    async def test_list_all_by_label(self, repo: GraphRepository) -> None:
        t1 = _make_task(title="Task One")
        t2 = _make_task(title="Task Two")
        await repo.create_node(t1)
        await repo.create_node(t2)

        results = await repo.list_nodes(label="TaskAtomic")

        ids = [r["id"] for r in results]
        assert t1.id in ids
        assert t2.id in ids

    async def test_list_with_state_filter(self, repo: GraphRepository) -> None:
        active_task = _make_task(title="Active Task", state="ACTIVE")
        pending_task = _make_task(title="Pending Task", state="PENDING")
        await repo.create_node(active_task)
        await repo.create_node(pending_task)

        results = await repo.list_nodes(label="TaskAtomic", filters={"state": "ACTIVE"})

        ids = [r["id"] for r in results]
        assert active_task.id in ids
        assert pending_task.id not in ids

    async def test_list_nodes_empty_when_none_match(self, repo: GraphRepository) -> None:
        results = await repo.list_nodes(label="TaskAtomic", filters={"state": "NONEXISTENT_STATE"})
        assert results == []


# ---------------------------------------------------------------------------
# Tests: downstream dependent query
# ---------------------------------------------------------------------------


class TestDownstreamDependents:
    """Verify get_downstream_dependents follows DEPENDS_ON edges correctly."""

    async def test_direct_dependent(self, repo: GraphRepository, db_pool: Any) -> None:
        """A task that directly depends on the anchor should appear."""
        blocker = _make_task(title="Blocker")
        dependent = _make_task(title="Direct Dependent")
        await repo.create_node(blocker)
        await repo.create_node(dependent)
        # dependent -> blocker
        await repo.create_edge(
            source_id=dependent.id,
            target_id=blocker.id,
            edge_type="DEPENDS_ON",
        )

        results = await get_downstream_dependents(db_pool, blocker.id)

        ids = [r["id"] for r in results]
        assert dependent.id in ids

    async def test_transitive_dependent(self, repo: GraphRepository, db_pool: Any) -> None:
        """A task two hops away should also appear (recursive traversal)."""
        root = _make_task(title="Root blocker")
        middle = _make_task(title="Middle task")
        leaf = _make_task(title="Leaf dependent")
        await repo.create_node(root)
        await repo.create_node(middle)
        await repo.create_node(leaf)

        # middle depends on root; leaf depends on middle
        await repo.create_edge(middle.id, root.id, "DEPENDS_ON")
        await repo.create_edge(leaf.id, middle.id, "DEPENDS_ON")

        results = await get_downstream_dependents(db_pool, root.id)

        ids = [r["id"] for r in results]
        assert middle.id in ids
        assert leaf.id in ids

    async def test_no_dependents(self, repo: GraphRepository, db_pool: Any) -> None:
        """A standalone node should have no dependents."""
        task = _make_task(title="Standalone")
        await repo.create_node(task)

        results = await get_downstream_dependents(db_pool, task.id)

        assert results == []

    async def test_anchor_not_in_results(self, repo: GraphRepository, db_pool: Any) -> None:
        """The queried node itself must not appear in the results."""
        anchor = _make_task(title="Anchor")
        child = _make_task(title="Child")
        await repo.create_node(anchor)
        await repo.create_node(child)
        await repo.create_edge(child.id, anchor.id, "DEPENDS_ON")

        results = await get_downstream_dependents(db_pool, anchor.id)

        ids = [r["id"] for r in results]
        assert anchor.id not in ids
