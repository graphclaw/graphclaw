# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for user-scoped DB query helpers.

Requires a running PostgreSQL + Apache AGE instance.

Run with::

    pytest tests/test_db/test_user_scope_integration.py -m integration

The DSN is read from TEST_DATABASE_URL (falls back to Docker Compose default).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------

TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://graphclaw:graphclaw_dev@localhost:5432/graphclaw",
)


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_stub(props: dict, label: str) -> object:
    """Create a minimal node stub whose label is visible to _resolve_label."""
    return type("_Stub", (), {"model_dump": lambda self, **kw: props, "node_type": label})()


def _task_props(user_id: str, title: str = "Test Task") -> dict:
    uid = uuid.uuid4().hex[:8]
    return {
        "id": f"TSK-TU-{uid}-AT",
        "task_type": "ATOMIC",
        "title": title,
        "description": "Integration test task",
        "state": "ACTIVE",
        "owned_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _goal_props(user_id: str, title: str = "Test Goal") -> dict:
    uid = uuid.uuid4().hex[:8]
    return {
        "id": f"GOAL-{uid}",
        "title": title,
        "description": "Integration test goal",
        "state": "ACTIVE",
        "priority": "P1",
        "owned_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _user_props(user_id: str) -> dict:
    return {
        "id": user_id,
        "name": "Integration User",
        "email": f"{user_id.lower()}@example.com",
        "created_at": _now(),
        "updated_at": _now(),
    }


# ---------------------------------------------------------------------------
# list_nodes_by_user
# ---------------------------------------------------------------------------


class TestListNodesByUser:
    @pytest.mark.asyncio
    async def test_returns_only_owned_tasks(self, repo):
        user_a = f"usr-a-{uuid.uuid4().hex[:6]}"
        user_b = f"usr-b-{uuid.uuid4().hex[:6]}"

        props_a = _task_props(user_a, "Alice Task")
        props_b = _task_props(user_b, "Bob Task")

        node_a = await repo.create_node(_node_stub(props_a, "TaskNode"))
        node_b = await repo.create_node(_node_stub(props_b, "TaskNode"))

        try:
            results_a = await repo.list_nodes_by_user("TaskNode", user_a)
            ids_a = {r["id"] for r in results_a}

            assert props_a["id"] in ids_a
            assert props_b["id"] not in ids_a
        finally:
            await repo.delete_node(props_a["id"])
            await repo.delete_node(props_b["id"])

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_user(self, repo):
        results = await repo.list_nodes_by_user("TaskNode", "usr-nobody-xyz")
        assert results == []

    @pytest.mark.asyncio
    async def test_works_with_goal_label(self, repo):
        user_id = f"usr-g-{uuid.uuid4().hex[:6]}"
        props = _goal_props(user_id)

        await repo.create_node(_node_stub(props, "GoalNode"))

        try:
            results = await repo.list_nodes_by_user("GoalNode", user_id)
            ids = {r["id"] for r in results}
            assert props["id"] in ids
        finally:
            await repo.delete_node(props["id"])

    @pytest.mark.asyncio
    async def test_multiple_tasks_same_user(self, repo):
        user_id = f"usr-multi-{uuid.uuid4().hex[:6]}"
        task1 = _task_props(user_id, "Task One")
        task2 = _task_props(user_id, "Task Two")

        await repo.create_node(_node_stub(task1, "TaskNode"))
        await repo.create_node(_node_stub(task2, "TaskNode"))

        try:
            results = await repo.list_nodes_by_user("TaskNode", user_id)
            ids = {r["id"] for r in results}
            assert task1["id"] in ids
            assert task2["id"] in ids
        finally:
            await repo.delete_node(task1["id"])
            await repo.delete_node(task2["id"])

    @pytest.mark.asyncio
    async def test_task_visible_via_owned_by_edge(self, repo):
        user_id = f"USER-edge-{uuid.uuid4().hex[:6]}"
        user = _user_props(user_id)
        task = _task_props("legacy-placeholder", "Edge-owned Task")
        task.pop("owned_by", None)

        await repo.create_node(_node_stub(user, "UserNode"))
        await repo.create_node(_node_stub(task, "TaskNode"))
        await repo.create_edge(task["id"], user_id, "OWNED_BY")

        try:
            results = await repo.list_nodes_by_user("TaskNode", user_id)
            ids = {r["id"] for r in results}
            assert task["id"] in ids
        finally:
            await repo.delete_node(task["id"])
            await repo.delete_node(user_id)

    @pytest.mark.asyncio
    async def test_task_not_duplicated_when_edge_and_property_exist(self, repo):
        user_id = f"USER-dupe-{uuid.uuid4().hex[:6]}"
        user = _user_props(user_id)
        task = _task_props(user_id, "Dual-owned Task")

        await repo.create_node(_node_stub(user, "UserNode"))
        await repo.create_node(_node_stub(task, "TaskNode"))
        await repo.create_edge(task["id"], user_id, "OWNED_BY")

        try:
            results = await repo.list_nodes_by_user("TaskNode", user_id)
            matching = [r for r in results if r.get("id") == task["id"]]
            assert len(matching) == 1
        finally:
            await repo.delete_node(task["id"])
            await repo.delete_node(user_id)


# ---------------------------------------------------------------------------
# list_nodes_for_goal
# ---------------------------------------------------------------------------


class TestListNodesForGoal:
    @pytest.mark.asyncio
    async def test_returns_tasks_linked_via_part_of(self, repo):
        user_id = f"usr-goal-{uuid.uuid4().hex[:6]}"
        goal = _goal_props(user_id)
        task = _task_props(user_id, "Goal Sub-Task")

        await repo.create_node(_node_stub(goal, "GoalNode"))
        await repo.create_node(_node_stub(task, "TaskNode"))
        await repo.create_edge(task["id"], goal["id"], "PART_OF")

        try:
            results = await repo.list_nodes_for_goal(goal["id"])
            ids = {r["id"] for r in results}
            assert task["id"] in ids
        finally:
            await repo.delete_node(task["id"])
            await repo.delete_node(goal["id"])

    @pytest.mark.asyncio
    async def test_returns_empty_for_goal_with_no_tasks(self, repo):
        user_id = f"usr-empty-{uuid.uuid4().hex[:6]}"
        goal = _goal_props(user_id, "Lonely Goal")

        await repo.create_node(_node_stub(goal, "GoalNode"))

        try:
            results = await repo.list_nodes_for_goal(goal["id"])
            assert results == []
        finally:
            await repo.delete_node(goal["id"])

    @pytest.mark.asyncio
    async def test_does_not_return_tasks_from_other_goals(self, repo):
        user_id = f"usr-isolation-{uuid.uuid4().hex[:6]}"
        goal_a = _goal_props(user_id, "Goal A")
        goal_b = _goal_props(user_id, "Goal B")
        task_a = _task_props(user_id, "Task for A")
        task_b = _task_props(user_id, "Task for B")

        for props in [goal_a, goal_b]:
            await repo.create_node(_node_stub(props, "GoalNode"))
        for props in [task_a, task_b]:
            await repo.create_node(_node_stub(props, "TaskNode"))
        await repo.create_edge(task_a["id"], goal_a["id"], "PART_OF")
        await repo.create_edge(task_b["id"], goal_b["id"], "PART_OF")

        try:
            results = await repo.list_nodes_for_goal(goal_a["id"])
            ids = {r["id"] for r in results}
            assert task_a["id"] in ids
            assert task_b["id"] not in ids
        finally:
            for props in [goal_a, goal_b, task_a, task_b]:
                await repo.delete_node(props["id"])

    @pytest.mark.asyncio
    async def test_nonexistent_goal_returns_empty(self, repo):
        results = await repo.list_nodes_for_goal("GOAL-DOESNOTEXIST-XYZ")
        assert results == []
