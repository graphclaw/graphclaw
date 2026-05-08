# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for O-DB-01: TaskNode stored under type-specific AGE labels.

Verifies that create_node() routes each TaskType to the correct AGE vertex label
(e.g. TaskAtomic, TaskDelegated, TaskFollowUp) rather than the generic TaskNode.

Run with::

    pytest tests/test_graph/test_task_type_labels_integration.py -m integration -v
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import TaskType
from graphclaw.models.nodes import TaskNode

pytestmark = pytest.mark.integration

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(task_type: TaskType) -> TaskNode:
    initials = task_type.value[:2]  # e.g. "AT", "DL", "FO"
    return TaskNode(
        id=generate_task_id(initials, task_type),
        title=f"Test {task_type.value.capitalize()} task",
        description=f"Integration test for O-DB-01 ({task_type.value})",
        task_type=task_type,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


async def _get_label_for_node(repo: AgeGraphStore, node_id: str) -> str | None:
    """Return the AGE vertex label for the given node id."""
    from graphclaw.db.age.connection import get_connection
    from graphclaw.db.age.utils import GRAPH_NAME, _escape

    eid = _escape(node_id)
    async with get_connection(repo._pool) as conn:
        result = await conn.execute(
            f"""
            SELECT * FROM cypher('{GRAPH_NAME}', $$
                MATCH (n {{id: '{eid}'}})
                RETURN label(n)
            $$) as (lbl agtype)
            """
        )
        row = await result.fetchone()
    if row is None:
        return None
    # AGE returns the label as a quoted string like '"TaskAtomic"'
    raw = str(row[0])
    return raw.strip('"')


# ---------------------------------------------------------------------------
# Tests: one per task type
# ---------------------------------------------------------------------------

# Map from TaskType → expected AGE label (matches init-db.sql)
_EXPECTED_LABELS = {
    TaskType.ATOMIC: "TaskAtomic",
    TaskType.COMPOSITE: "TaskComposite",
    TaskType.DELEGATED: "TaskDelegated",
    TaskType.FOLLOWUP: "TaskFollowUp",
    TaskType.APPROVAL: "TaskApproval",
    TaskType.MILESTONE: "TaskMilestone",
    TaskType.REVIEW: "TaskReview",
    TaskType.RECURRING: "TaskRecurring",
    TaskType.DECISION: "TaskDecision",
    TaskType.CHECKIN: "TaskCheckin",
    TaskType.RESEARCH: "TaskResearch",
}


@pytest.mark.parametrize("task_type,expected_label", list(_EXPECTED_LABELS.items()))
async def test_task_stored_under_correct_label(
    repo: AgeGraphStore, task_type: TaskType, expected_label: str
):
    """Each TaskType must be stored under its type-specific AGE label."""
    task = _make_task(task_type)
    await repo.create_node(task)
    try:
        label = await _get_label_for_node(repo, task.id)
        assert label == expected_label, (
            f"{task_type.value}: expected label={expected_label!r}, got={label!r}"
        )
    finally:
        await repo.delete_node(task.id)


async def test_generic_task_node_label_no_longer_used(repo: AgeGraphStore):
    """No task should be stored under the generic 'TaskNode' label after the fix."""
    task = _make_task(TaskType.ATOMIC)
    await repo.create_node(task)
    try:
        label = await _get_label_for_node(repo, task.id)
        assert label != "TaskNode", (
            "Task was stored under generic 'TaskNode' label — fix not applied"
        )
    finally:
        await repo.delete_node(task.id)
