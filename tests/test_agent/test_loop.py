"""Tests for AgentLoop — scoring cycle, scoring context, and briefing.

All database calls are mocked via AsyncMock so no live DB is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import (
    GateType,
    GoalPriority,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import (
    ActionQueueEntry,
    ScoreExplanation,
    ScoreFactor,
)
from graphclaw.models.type_metadata import CompositeMetadata
from graphclaw.scoring.engine import ScoringContext, ScoringEngine
from graphclaw.state.machine import StateMachine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_task(
    task_type: TaskType = TaskType.ATOMIC,
    state: TaskState = TaskState.ACTIVE,
    title: str = "Test Task",
) -> TaskNode:
    return TaskNode(
        id=generate_task_id("TS", task_type),
        task_type=task_type,
        title=title,
        description="A loop test task",
        state=state,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_queue_entry(task: TaskNode, rank: int = 1, score: float = 0.75) -> ActionQueueEntry:
    factor = ScoreFactor(
        factor_name="timeline_urgency",
        raw_score=0.8,
        weight=0.25,
        weighted_score=0.2,
        plain_english="Deadline in 3 days",
    )
    explanation = ScoreExplanation(
        node_id=task.id,
        scored_at=_now(),
        final_score=score,
        rank=rank,
        factors=[factor],
        summary=f"Task '{task.title}' scored {score:.3f}.",
    )
    return ActionQueueEntry(
        node_id=task.id,
        final_score=score,
        rank=rank,
        recommended_action="EXECUTE_TASK",
        explanation=explanation,
    )


def _make_loop(mock_repo=None, mock_engine=None) -> tuple[AgentLoop, Any, Any]:
    repo = mock_repo or AsyncMock()
    engine = mock_engine or MagicMock(spec=ScoringEngine)
    sm = StateMachine()
    loop = AgentLoop(graph_repo=repo, scoring_engine=engine, state_machine=sm)
    return loop, repo, engine


# ---------------------------------------------------------------------------
# AgentLoop.run_cycle
# ---------------------------------------------------------------------------


class TestRunCycle:
    @pytest.mark.asyncio
    async def test_run_cycle_returns_empty_when_no_tasks(self):
        loop, repo, engine = _make_loop()
        repo.list_nodes = AsyncMock(return_value=[])

        queue = await loop.run_cycle()

        assert queue == []
        repo.list_nodes.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cycle_returns_sorted_queue(self):
        task1 = _make_task(title="Task A")
        task2 = _make_task(title="Task B")

        entry1 = _make_queue_entry(task1, rank=1, score=0.9)
        entry2 = _make_queue_entry(task2, rank=2, score=0.4)

        loop, repo, engine = _make_loop()
        # list_nodes returns serialised dicts
        repo.list_nodes = AsyncMock(
            return_value=[
                task1.model_dump(mode="json"),
                task2.model_dump(mode="json"),
            ]
        )
        # get_edges returns nothing for all context lookups
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)

        # Make score_all an async mock that returns our canned queue.
        engine.score_all = AsyncMock(return_value=[entry1, entry2])

        queue = await loop.run_cycle()

        assert len(queue) == 2
        assert queue[0].rank == 1
        assert queue[0].final_score > queue[1].final_score

    @pytest.mark.asyncio
    async def test_run_cycle_filters_terminal_states(self):
        """COMPLETE and CANCELLED tasks should be excluded from the cycle."""
        active = _make_task(state=TaskState.ACTIVE)
        complete = _make_task(state=TaskState.COMPLETE)
        cancelled = _make_task(state=TaskState.CANCELLED)

        loop, repo, engine = _make_loop()
        repo.list_nodes = AsyncMock(
            return_value=[
                active.model_dump(mode="json"),
                complete.model_dump(mode="json"),
                cancelled.model_dump(mode="json"),
            ]
        )
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)

        captured_tasks: list[list[TaskNode]] = []

        async def _capture_score_all(tasks, context):
            captured_tasks.append(list(tasks))
            return [_make_queue_entry(t, i + 1) for i, t in enumerate(tasks)]

        engine.score_all = _capture_score_all

        await loop.run_cycle()

        # Only the ACTIVE task should reach the engine.
        assert len(captured_tasks) == 1
        assert len(captured_tasks[0]) == 1
        assert captured_tasks[0][0].id == active.id

    @pytest.mark.asyncio
    async def test_run_cycle_db_failure_returns_empty(self):
        """If list_nodes fails, run_cycle should return empty queue gracefully."""
        loop, repo, engine = _make_loop()
        repo.list_nodes = AsyncMock(side_effect=Exception("DB down"))

        queue = await loop.run_cycle()

        assert queue == []

    @pytest.mark.asyncio
    async def test_run_cycle_user_scope_uses_list_nodes_by_user(self):
        task = _make_task(title="Scoped Task")
        entry = _make_queue_entry(task, rank=1, score=0.8)

        loop, repo, engine = _make_loop()
        repo.list_nodes_by_user = AsyncMock(return_value=[task.model_dump(mode="json")])
        repo.list_nodes = AsyncMock(return_value=[])
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)
        engine.score_all = AsyncMock(return_value=[entry])

        queue = await loop.run_cycle(user_id="USER-test")

        assert len(queue) == 1
        repo.list_nodes_by_user.assert_called_once_with("TaskNode", "USER-test")
        repo.list_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_cycle_heartbeat_uses_cached_queue_when_clean(self):
        task = _make_task(title="Cached Task")
        cached_entry = _make_queue_entry(task, rank=1, score=0.93)

        loop, repo, engine = _make_loop()
        loop._score_cache_dirty = False
        loop._last_queue_by_scope["__all__"] = [cached_entry]
        repo.list_nodes = AsyncMock(side_effect=AssertionError("list_nodes should not be called"))

        queue = await loop.run_cycle(trigger_source="heartbeat")

        assert queue == [cached_entry]
        repo.list_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_cycle_logs_trigger_source(self):
        task = _make_task(title="Triggered Task")
        entry = _make_queue_entry(task, rank=1, score=0.84)

        loop, repo, engine = _make_loop()
        repo.list_nodes = AsyncMock(return_value=[task.model_dump(mode="json")])
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)
        engine.score_all = AsyncMock(return_value=[entry])

        loop._logger = MagicMock()

        await loop.run_cycle(trigger_source="on_demand")

        assert loop._logger.log.call_count == 1
        kwargs = loop._logger.log.call_args.kwargs
        assert kwargs["trigger_source"] == "on_demand"


# ---------------------------------------------------------------------------
# AgentLoop.build_scoring_context
# ---------------------------------------------------------------------------


class TestBuildScoringContext:
    @pytest.mark.asyncio
    async def test_context_defaults_when_no_edges(self):
        task = _make_task()
        loop, repo, engine = _make_loop()
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)

        ctx = await loop.build_scoring_context([task])

        assert isinstance(ctx, ScoringContext)
        assert ctx.task_goal_priority[task.id] == GoalPriority.P3
        assert ctx.task_direct_dependents[task.id] == 0
        assert ctx.task_transitive_dependents[task.id] == 0
        assert ctx.task_blocker_type[task.id] == "NONE"
        assert ctx.task_constraints[task.id] == []

    @pytest.mark.asyncio
    async def test_context_picks_up_goal_priority(self):
        task = _make_task()
        loop, repo, engine = _make_loop()

        goal_node = {
            "id": "GOAL-test-001",
            "priority": "P1",
            "state": "ACTIVE",
            "title": "Big Goal",
            "description": "",
            "created_at": _now().isoformat(),
            "updated_at": _now().isoformat(),
        }

        async def _mock_get_edges(node_id, direction, edge_type):
            if edge_type == "APPLIES_TO" and node_id == task.id:
                return [{"_end_id": "GOAL-test-001"}]
            return []

        async def _mock_get_node(node_id):
            if node_id == "GOAL-test-001":
                return goal_node
            return None

        repo.get_edges = AsyncMock(side_effect=_mock_get_edges)
        repo.get_node = AsyncMock(side_effect=_mock_get_node)

        ctx = await loop.build_scoring_context([task])

        assert ctx.task_goal_priority[task.id] == GoalPriority.P1

    @pytest.mark.asyncio
    async def test_context_counts_direct_dependents(self):
        task = _make_task()
        loop, repo, engine = _make_loop()

        # Simulate 2 inbound DEPENDS_ON edges.
        async def _mock_get_edges(node_id, direction, edge_type):
            if edge_type == "DEPENDS_ON" and direction == "in":
                return [{"_start_id": "other-1"}, {"_start_id": "other-2"}]
            return []

        repo.get_edges = AsyncMock(side_effect=_mock_get_edges)
        repo.get_node = AsyncMock(return_value=None)

        ctx = await loop.build_scoring_context([task])

        assert ctx.task_direct_dependents[task.id] == 2
        assert ctx.task_transitive_dependents[task.id] == 2

    @pytest.mark.asyncio
    async def test_context_detects_hard_blocker(self):
        task = _make_task()
        loop, repo, engine = _make_loop()

        async def _mock_get_edges(node_id, direction, edge_type):
            if edge_type == "BLOCKS" and direction == "out":
                return [{"_end_id": "other-task", "strength": "HARD"}]
            return []

        repo.get_edges = AsyncMock(side_effect=_mock_get_edges)
        repo.get_node = AsyncMock(return_value=None)

        ctx = await loop.build_scoring_context([task])

        assert ctx.task_blocker_type[task.id] == "HARD"

    @pytest.mark.asyncio
    async def test_context_resource_reliability_from_node(self):
        task = _make_task()
        loop, repo, engine = _make_loop()

        resource_props = {
            "id": "RES-test",
            "reliability": {"overall_score": 0.6},
            "capacity": {"load_factor": 0.75},
            "current_risk": {
                "capacity_risk": "HIGH",
                "delivery_risk": "MEDIUM",
                "responsiveness_risk": "LOW",
            },
        }

        async def _mock_get_edges(node_id, direction, edge_type):
            if edge_type == "ASSIGNED_TO" and direction == "out":
                return [{"_end_id": "RES-test"}]
            return []

        async def _mock_get_node(node_id):
            if node_id == "RES-test":
                return resource_props
            return None

        repo.get_edges = AsyncMock(side_effect=_mock_get_edges)
        repo.get_node = AsyncMock(side_effect=_mock_get_node)

        ctx = await loop.build_scoring_context([task])

        assert ctx.task_resource_reliability[task.id] == pytest.approx(0.6)
        assert ctx.task_resource_load_factor[task.id] == pytest.approx(0.75)
        # HIGH=1.0, MEDIUM=0.5, LOW=0.0 → average = 0.5
        assert ctx.task_resource_risk_signals[task.id] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_context_graph_repo_is_set(self):
        task = _make_task()
        loop, repo, engine = _make_loop()
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)

        ctx = await loop.build_scoring_context([task])

        assert ctx.graph_repo is repo

    @pytest.mark.asyncio
    async def test_context_multiple_tasks(self):
        tasks = [_make_task(title=f"Task {i}") for i in range(3)]
        loop, repo, engine = _make_loop()
        repo.get_edges = AsyncMock(return_value=[])
        repo.get_node = AsyncMock(return_value=None)

        ctx = await loop.build_scoring_context(tasks)

        for task in tasks:
            assert task.id in ctx.task_goal_priority
            assert task.id in ctx.task_direct_dependents


# ---------------------------------------------------------------------------
# AgentLoop.generate_briefing
# ---------------------------------------------------------------------------


class TestGenerateBriefing:
    @pytest.mark.asyncio
    async def test_briefing_includes_rank_and_action(self):
        task = _make_task(title="Top Priority Task")
        entry = _make_queue_entry(task, rank=1, score=0.9)

        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing([entry], top_n=5)

        assert "#1" in briefing
        assert task.id in briefing
        assert "EXECUTE_TASK" in briefing

    @pytest.mark.asyncio
    async def test_briefing_empty_queue(self):
        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing([])

        assert "No actionable tasks" in briefing

    @pytest.mark.asyncio
    async def test_briefing_respects_top_n(self):
        tasks = [_make_task(title=f"Task {i}") for i in range(10)]
        entries = [
            _make_queue_entry(t, rank=i + 1, score=1.0 - i * 0.1) for i, t in enumerate(tasks)
        ]

        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing(entries, top_n=3)

        # Only first 3 should appear in briefing.
        assert "#1" in briefing
        assert "#3" in briefing
        assert "#4" not in briefing

    @pytest.mark.asyncio
    async def test_briefing_contains_score(self):
        task = _make_task()
        entry = _make_queue_entry(task, rank=1, score=0.852)

        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing([entry])

        assert "0.852" in briefing

    @pytest.mark.asyncio
    async def test_briefing_contains_autonomy_level(self):
        task = _make_task()
        entry = _make_queue_entry(task, rank=1)
        # Default autonomy is SUGGEST.

        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing([entry])

        assert "SUGGEST" in briefing

    @pytest.mark.asyncio
    async def test_briefing_includes_total_queue_size(self):
        tasks = [_make_task() for _ in range(5)]
        entries = [_make_queue_entry(t, rank=i + 1) for i, t in enumerate(tasks)]

        loop, repo, engine = _make_loop()
        briefing = await loop.generate_briefing(entries, top_n=2)

        assert "5" in briefing  # total queue size


class TestGraphSummaryScoping:
    @pytest.mark.asyncio
    async def test_build_graph_summary_triggers_user_scoped_cycle(self):
        task = _make_task(title="Summary Task")
        entry = _make_queue_entry(task, rank=1, score=0.91)

        loop, repo, engine = _make_loop()
        repo.list_nodes_by_user = AsyncMock(return_value=[])
        loop.run_cycle = AsyncMock(return_value=[entry])
        loop._fetch_active_tasks = AsyncMock(return_value=[task])

        summary = await loop._build_graph_summary("USER-summary")

        loop.run_cycle.assert_called_once_with(user_id="USER-summary", trigger_source="on_demand")
        assert "Top Priority Tasks" in summary
        assert task.id in summary


class TestSmartRetrievalBehaviors:
    @pytest.mark.asyncio
    async def test_list_tasks_uses_scored_queue_order_without_goal_scope(self):
        user_id = "USER-smart"
        task_a = _make_task(title="Task A")
        task_b = _make_task(title="Task B")
        task_a.owned_by = user_id
        task_b.owned_by = user_id

        entry_a = _make_queue_entry(task_a, rank=1, score=0.95)
        entry_b = _make_queue_entry(task_b, rank=2, score=0.40)

        loop, repo, _engine = _make_loop()
        repo.list_nodes_by_user = AsyncMock(
            return_value=[task_b.model_dump(mode="json"), task_a.model_dump(mode="json")]
        )
        loop._last_queue_by_scope[user_id] = [entry_a, entry_b]

        result = await loop._tool_list_tasks(user_id, {"limit": 10})
        ids = [t["id"] for t in result["tasks"]]

        assert ids[:2] == [task_a.id, task_b.id]

    @pytest.mark.asyncio
    async def test_update_task_state_invalidates_scoped_queue_cache(self):
        user_id = "USER-cache"
        loop, repo, _engine = _make_loop()
        existing = _make_task(state=TaskState.ACTIVE, title="Cached")
        repo.get_node = AsyncMock(return_value=existing.model_dump(mode="json"))
        repo.update_node = AsyncMock()

        seed_entry = _make_queue_entry(_make_task(title="Cached"), rank=1, score=0.8)
        loop._last_queue_by_scope[user_id] = [seed_entry]
        loop._last_queue_by_scope["__all__"] = [seed_entry]

        await loop._tool_update_task_state(
            user_id,
            {"task_id": "TSK-CACHE-001", "new_state": "IN_PROGRESS", "reason": "test"},
        )

        assert user_id not in loop._last_queue_by_scope
        assert "__all__" not in loop._last_queue_by_scope

    @pytest.mark.asyncio
    async def test_update_task_state_runs_completion_cascade(self):
        parent = TaskNode(
            id=generate_task_id("TS", TaskType.COMPOSITE),
            task_type=TaskType.COMPOSITE,
            title="Parent",
            description="Composite parent",
            state=TaskState.ACTIVE,
            created_at=_now(),
            updated_at=_now(),
            type_metadata=CompositeMetadata(
                completion_gate=GateType.AND,
                auto_complete_on_children=True,
            ),
        )
        child = _make_task(state=TaskState.IN_PROGRESS, title="Child")
        dependent = _make_task(state=TaskState.INACTIVE_PENDING, title="Dependent")

        nodes = {
            parent.id: parent.model_dump(mode="json"),
            child.id: child.model_dump(mode="json"),
            dependent.id: dependent.model_dump(mode="json"),
        }

        async def _get_node(node_id: str):
            return nodes.get(node_id)

        async def _get_edges(node_id: str, direction: str, edge_type: str):
            if node_id == child.id and direction == "in" and edge_type == "DEPENDS_ON":
                return [{"_start_id": dependent.id}]
            if node_id == child.id and direction == "out" and edge_type == "PART_OF":
                return [{"_end_id": parent.id}]
            if node_id == parent.id and direction == "in" and edge_type == "PART_OF":
                return [{"_start_id": child.id}]
            return []

        async def _update_node(node_id: str, payload: dict[str, Any]):
            existing = dict(nodes.get(node_id, {}))
            existing.update(payload)
            nodes[node_id] = existing
            return True

        loop, repo, _engine = _make_loop()
        repo.get_node = AsyncMock(side_effect=_get_node)
        repo.get_edges = AsyncMock(side_effect=_get_edges)
        repo.update_node = AsyncMock(side_effect=_update_node)

        result = await loop._tool_update_task_state(
            "USER-123",
            {"task_id": child.id, "new_state": "COMPLETE", "reason": "finished"},
        )

        assert result["status"] == "updated"

        persisted = [(call.args[0], call.args[1]) for call in repo.update_node.await_args_list]
        updated_ids = [node_id for node_id, _ in persisted]
        assert child.id in updated_ids
        assert dependent.id in updated_ids
        assert parent.id in updated_ids

        dependent_payload = next(payload for node_id, payload in persisted if node_id == dependent.id)
        assert dependent_payload["state"] == TaskState.ACTIVE.value

        parent_payload = next(payload for node_id, payload in persisted if node_id == parent.id)
        assert parent_payload["state"] == TaskState.COMPLETE.value


# ---------------------------------------------------------------------------
# AgentLoop constructor / wiring
# ---------------------------------------------------------------------------


class TestAgentLoopConstructor:
    def test_constructor_stores_dependencies(self):
        repo = AsyncMock()
        engine = MagicMock(spec=ScoringEngine)
        sm = StateMachine()

        loop = AgentLoop(graph_repo=repo, scoring_engine=engine, state_machine=sm)

        assert loop._repo is repo
        assert loop._engine is engine
        assert loop._sm is sm
