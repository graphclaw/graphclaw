# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ScoringEngine — full pipeline and weight application.

These tests exercise score_task() with known inputs and verify the
final_score calculation, factor breakdowns, and modifiers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import (
    GoalPriority,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import TaskNode
from graphclaw.scoring.cache import ScoreCache
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_task(
    task_type: TaskType = TaskType.ATOMIC,
    state: TaskState = TaskState.ACTIVE,
    deadline_days: float | None = 5.0,
    effort_days: float = 1.0,
    on_critical_path: bool = False,
) -> TaskNode:
    task = TaskNode(
        id=generate_task_id("TS", task_type),
        task_type=task_type,
        title="Scored Task",
        description="A task being scored",
        state=state,
        created_at=_now(),
        updated_at=_now(),
        on_critical_path=on_critical_path,
    )
    if deadline_days is not None:
        task.timeline.deadline = _now() + timedelta(days=deadline_days)
    task.timeline.estimated_effort_days = effort_days
    return task


def _empty_context(**overrides) -> ScoringContext:
    return ScoringContext(**overrides)


# ---------------------------------------------------------------------------
# Basic scoring
# ---------------------------------------------------------------------------


class TestScoreTask:
    def test_returns_score_explanation(self):
        engine = ScoringEngine()
        task = _make_task()
        ctx = _empty_context()
        result = engine.score_task(task, ctx)
        assert result.node_id == task.id
        assert result.final_score >= 0.0
        assert len(result.factors) == 7

    def test_all_factors_present(self):
        engine = ScoringEngine()
        task = _make_task()
        ctx = _empty_context()
        result = engine.score_task(task, ctx)
        factor_names = {f.factor_name for f in result.factors}
        expected = {
            "timeline_urgency",
            "dependency_weight",
            "critical_path",
            "blocker",
            "human_override",
            "resource_risk",
            "constraint_pressure",
        }
        assert factor_names == expected

    def test_weights_sum_to_1(self):
        engine = ScoringEngine()
        assert (
            engine.w1 + engine.w2 + engine.w3 + engine.w4 + engine.w5 + engine.w6 + engine.w7
        ) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Weight application
# ---------------------------------------------------------------------------


class TestWeightApplication:
    def test_weighted_scores_match_raw_times_weight(self):
        engine = ScoringEngine()
        task = _make_task()
        ctx = _empty_context()
        result = engine.score_task(task, ctx)
        for factor in result.factors:
            assert factor.weighted_score == pytest.approx(
                factor.raw_score * factor.weight, abs=1e-9
            )

    def test_final_score_is_sum_of_weighted(self):
        """For a non-critical-path task, final_score = sum of weighted_scores."""
        engine = ScoringEngine()
        task = _make_task(on_critical_path=False)
        ctx = _empty_context()
        result = engine.score_task(task, ctx)
        expected = sum(f.weighted_score for f in result.factors)
        assert result.final_score == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# Critical path multiplier
# ---------------------------------------------------------------------------


class TestCriticalPathMultiplier:
    def test_cp_p1_score_higher_than_off_cp(self):
        """Critical path effect is captured via F3 factor, not a post-multiplier."""
        engine = ScoringEngine()
        task_cp = _make_task(on_critical_path=True)
        task_off = _make_task(on_critical_path=False)
        ctx_cp = _empty_context(task_goal_priority={task_cp.id: GoalPriority.P1})
        ctx_off = _empty_context(task_goal_priority={task_off.id: GoalPriority.P1})
        score_cp = engine.score_task(task_cp, ctx_cp).final_score
        score_off = engine.score_task(task_off, ctx_off).final_score
        assert score_cp > score_off

    def test_cp_final_score_equals_weighted_sum(self):
        """No post-multiplier: final_score is always the plain weighted sum."""
        engine = ScoringEngine()
        task = _make_task(on_critical_path=True)
        ctx = _empty_context(task_goal_priority={task.id: GoalPriority.P1})
        result = engine.score_task(task, ctx)
        expected = sum(f.weighted_score for f in result.factors)
        assert result.final_score == pytest.approx(expected, abs=1e-9)

    def test_no_critical_path_multiplier_modifier(self):
        """The critical_path_multiplier modifier no longer exists."""
        engine = ScoringEngine()
        task = _make_task(on_critical_path=True)
        ctx = _empty_context(task_goal_priority={task.id: GoalPriority.P1})
        result = engine.score_task(task, ctx)
        cp_mods = [m for m in result.modifiers if m.modifier_type == "critical_path_multiplier"]
        assert len(cp_mods) == 0

    def test_off_cp_no_modifier(self):
        engine = ScoringEngine()
        task = _make_task(on_critical_path=False)
        ctx = _empty_context()
        result = engine.score_task(task, ctx)
        cp_mods = [m for m in result.modifiers if m.modifier_type == "critical_path_multiplier"]
        assert len(cp_mods) == 0


# ---------------------------------------------------------------------------
# Cache integration
# ---------------------------------------------------------------------------


class TestCacheIntegration:
    def test_cache_hit_returns_same_result(self):
        cache = ScoreCache()
        engine = ScoringEngine(cache=cache)
        task = _make_task()
        ctx = _empty_context()
        result1 = engine.score_task(task, ctx)
        result2 = engine.score_task(task, ctx)
        assert result1.final_score == result2.final_score
        assert cache.size == 1

    def test_cache_invalidation_forces_rescore(self):
        cache = ScoreCache()
        engine = ScoringEngine(cache=cache)
        task = _make_task()
        ctx = _empty_context()
        result1 = engine.score_task(task, ctx)
        cache.invalidate(task.id)
        assert cache.size == 0
        result2 = engine.score_task(task, ctx)
        assert cache.size == 1


# ---------------------------------------------------------------------------
# Context influence
# ---------------------------------------------------------------------------


class TestContextInfluence:
    def test_high_dependencies_increase_score(self):
        engine = ScoringEngine()
        task_low = _make_task()
        task_high = _make_task()
        ctx_low = _empty_context(
            task_direct_dependents={task_low.id: 0},
            task_transitive_dependents={task_low.id: 0},
        )
        ctx_high = _empty_context(
            task_direct_dependents={task_high.id: 10},
            task_transitive_dependents={task_high.id: 20},
        )
        score_low = engine.score_task(task_low, ctx_low).final_score
        score_high = engine.score_task(task_high, ctx_high).final_score
        assert score_high > score_low

    def test_hard_blocker_increases_score(self):
        engine = ScoringEngine()
        task_none = _make_task()
        task_hard = _make_task()
        ctx_none = _empty_context(task_blocker_type={task_none.id: "NONE"})
        ctx_hard = _empty_context(task_blocker_type={task_hard.id: "HARD"})
        score_none = engine.score_task(task_none, ctx_none).final_score
        score_hard = engine.score_task(task_hard, ctx_hard).final_score
        assert score_hard > score_none
