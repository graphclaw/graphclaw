"""Integration tests for O-SCR-02: ScoreExplanation persisted to AGE.

Verifies that after score_task() and score_all():
  1. task.computed_priority is updated in-memory by score_task()
  2. task.last_scored_at and task.scoring.* are updated in-memory
  3. score_all() persists computed_priority, last_scored_at, and scoring block to AGE
  4. Reading the task back from AGE shows the persisted score data

Run with::

    pytest tests/test_scoring/test_score_persistence_integration.py -m integration
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from graphclaw.db.age.connection import create_pool
from graphclaw.db.age.repository import AgeGraphStore
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_task(state: TaskState = TaskState.ACTIVE) -> TaskNode:
    task = TaskNode(
        id=generate_task_id("SC", TaskType.ATOMIC),
        task_type=TaskType.ATOMIC,
        title="Scoring persistence test task",
        description="Integration test for O-SCR-02",
        state=state,
        created_at=_now(),
        updated_at=_now(),
    )
    task.timeline.deadline = _now() + timedelta(days=3)
    return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScoreTaskUpdatesInMemory:
    async def test_score_task_updates_computed_priority(self):
        """score_task() must set task.computed_priority to the final score."""
        engine = ScoringEngine()
        task = _make_task()
        context = ScoringContext()

        assert task.scoring.computed_priority == 0.0, "Should start at 0.0"
        expl = engine.score_task(task, context)

        assert task.scoring.computed_priority == expl.final_score, (
            f"task.scoring.computed_priority ({task.scoring.computed_priority}) "
            f"must equal expl.final_score ({expl.final_score})"
        )
        assert task.scoring.computed_priority > 0.0, "Scored task should have non-zero priority"

    async def test_score_task_updates_last_scored_at(self):
        """score_task() must set task.last_scored_at to a recent datetime."""
        engine = ScoringEngine()
        task = _make_task()
        context = ScoringContext()

        before = _now()
        engine.score_task(task, context)
        after = _now()

        assert task.scoring.last_scored_at is not None
        assert before <= task.scoring.last_scored_at <= after

    async def test_score_task_updates_scoring_block_factors(self):
        """score_task() must populate all factor fields on task.scoring."""
        engine = ScoringEngine()
        task = _make_task()
        context = ScoringContext()

        engine.score_task(task, context)

        # timeline_urgency should be > 0 because deadline is 3 days out
        assert task.scoring.timeline_urgency > 0.0, (
            "timeline_urgency should be non-zero with a 3-day deadline"
        )
        assert task.scoring.score_reasoning is not None


class TestScoreAllPersistsToAGE:
    async def test_score_all_persists_computed_priority(self, repo: AgeGraphStore):
        """score_all() must write computed_priority into the scoring block in AGE."""

        from graphclaw.api.state import _deserialize_task_fields

        task = _make_task()
        await repo.create_node(task)

        try:
            engine = ScoringEngine()
            context = ScoringContext(graph_repo=repo)
            await engine.score_all([task], context)

            raw = await repo.get_node(task.id)
            assert raw is not None
            data = _deserialize_task_fields(raw)
            scoring = data.get("scoring")
            assert scoring is not None, "scoring block must be persisted"
            persisted_priority = scoring.get("computed_priority", 0.0)
            assert float(persisted_priority) > 0.0, (
                f"scoring.computed_priority must be > 0, got {persisted_priority}"
            )
            assert float(persisted_priority) == pytest.approx(task.scoring.computed_priority)
        finally:
            await repo.delete_node(task.id)

    async def test_score_all_persists_last_scored_at(self, repo: AgeGraphStore):
        """score_all() must write last_scored_at inside the scoring block to AGE."""
        from graphclaw.api.state import _deserialize_task_fields

        task = _make_task()
        await repo.create_node(task)

        try:
            before = _now()
            engine = ScoringEngine()
            context = ScoringContext(graph_repo=repo)
            await engine.score_all([task], context)

            raw = await repo.get_node(task.id)
            assert raw is not None
            data = _deserialize_task_fields(raw)
            scoring = data.get("scoring", {})
            scored_at_raw = scoring.get("last_scored_at")
            assert scored_at_raw is not None, "scoring.last_scored_at must be persisted"
        finally:
            await repo.delete_node(task.id)

    async def test_score_all_persists_scoring_block(self, repo: AgeGraphStore):
        """score_all() must write the scoring sub-block (timeline_urgency etc.) to AGE."""

        from graphclaw.api.state import _deserialize_task_fields

        task = _make_task()
        await repo.create_node(task)

        try:
            engine = ScoringEngine()
            context = ScoringContext(graph_repo=repo)
            await engine.score_all([task], context)

            raw = await repo.get_node(task.id)
            assert raw is not None
            data = _deserialize_task_fields(raw)
            scoring = data.get("scoring")
            assert scoring is not None, "scoring block must be persisted"
            assert isinstance(scoring, dict), f"scoring must be a dict, got {type(scoring)}"
            assert scoring.get("timeline_urgency", 0.0) > 0.0, (
                "timeline_urgency should be > 0 in persisted scoring block"
            )
            assert scoring.get("computed_priority", 0.0) > 0.0
        finally:
            await repo.delete_node(task.id)

    async def test_completed_task_not_scored(self, repo: AgeGraphStore):
        """score_all() must skip COMPLETE tasks — their computed_priority stays 0."""
        task = _make_task(state=TaskState.COMPLETE)
        await repo.create_node(task)

        try:
            engine = ScoringEngine()
            context = ScoringContext(graph_repo=repo)
            await engine.score_all([task], context)

            raw = await repo.get_node(task.id)
            assert raw is not None
            # COMPLETE tasks are excluded from scoring — priority should remain 0.0
            assert float(raw.get("computed_priority", 0.0)) == pytest.approx(0.0), (
                "COMPLETE task should not have computed_priority updated"
            )
        finally:
            await repo.delete_node(task.id)
