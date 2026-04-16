"""Tests for GraphClaw CLI commands.

Uses Typer's CliRunner to invoke commands with a mocked database layer.
All DB interactions are patched so no live Postgres connection is required.

Patch strategy
--------------
CLI commands now use ``graphclaw.cli._shared.cli_pool``, which imports
``create_pool`` and ``AgeGraphStore`` at module load time:

    from graphclaw.db.connection import create_pool
    from graphclaw.db.age import AgeGraphStore

To intercept these calls from tests, we patch the names as they exist in the
``_shared`` module's namespace (where the ``from ... import`` bound them):

    patch("graphclaw.cli._shared.create_pool", ...)
    patch("graphclaw.cli._shared.AgeGraphStore", ...)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from graphclaw.cli.main import app
from graphclaw.models.base import generate_goal_id, generate_task_id
from graphclaw.models.enums import (
    GoalPriority,
    GoalState,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import GoalNode, GoalProgress, GoalTimeline, TaskNode
from graphclaw.models.scoring import (
    ActionQueueEntry,
    ScoreExplanation,
    ScoreFactor,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture helpers
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
        description="A test task",
        state=state,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_goal(title: str = "Test Goal") -> GoalNode:
    return GoalNode(
        id=generate_goal_id(),
        title=title,
        description="A test goal",
        state=GoalState.ACTIVE,
        priority=GoalPriority.P1,
        timeline=GoalTimeline(),
        progress=GoalProgress(),
        created_at=_now(),
        updated_at=_now(),
    )


def _make_queue_entry(task: TaskNode, rank: int = 1) -> ActionQueueEntry:
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
        final_score=0.75,
        rank=rank,
        factors=[factor],
        summary=f"Task '{task.title}' scored 0.750.",
    )
    return ActionQueueEntry(
        node_id=task.id,
        final_score=0.75,
        rank=rank,
        recommended_action="EXECUTE_TASK",
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# task list — CLI runner tests
# ---------------------------------------------------------------------------


class TestTaskList:
    def test_task_list_asyncio_run_called(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "list"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_task_list_with_state_filter(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "list", "--state", "ACTIVE"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_task_list_state_flag_short(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "list", "-s", "IN_PROGRESS"])
            assert result.exit_code == 0

    def test_task_list_exception_exits_nonzero(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected error")
            result = runner.invoke(app, ["task", "list"])
            assert result.exit_code != 0


# ---------------------------------------------------------------------------
# task show — CLI runner tests
# ---------------------------------------------------------------------------


class TestTaskShow:
    def test_task_show_delegates_to_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "show", "TSK-TS-1234-ATM"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_task_show_requires_task_id(self):
        result = runner.invoke(app, ["task", "show"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# task create — CLI runner tests
# ---------------------------------------------------------------------------


class TestTaskCreate:
    def test_task_create_delegates_to_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(
                app, ["task", "create", "--title", "My Task", "--type", "ATOMIC"]
            )
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_task_create_requires_title(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "create"])
            # Missing --title should fail.
            assert result.exit_code != 0


# ---------------------------------------------------------------------------
# task transition — CLI runner tests
# ---------------------------------------------------------------------------


class TestTaskTransition:
    def test_task_transition_delegates_to_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["task", "transition", "TSK-TS-0001-ATM", "IN_PROGRESS"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_task_transition_requires_both_args(self):
        result = runner.invoke(app, ["task", "transition", "TSK-TS-0001-ATM"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# agent score — CLI runner tests
# ---------------------------------------------------------------------------


class TestAgentScore:
    def test_agent_score_calls_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["agent", "score"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_agent_score_top_n_option(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["agent", "score", "--top", "3"])
            assert result.exit_code == 0

    def test_agent_run_calls_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["agent", "run"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_agent_briefing_calls_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["agent", "briefing"])
            assert result.exit_code == 0
            mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# graph stats / query — CLI runner tests
# ---------------------------------------------------------------------------


class TestGraphCommands:
    def test_graph_stats_calls_asyncio_run(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(app, ["graph", "stats"])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_graph_query_requires_cypher_arg(self):
        result = runner.invoke(app, ["graph", "query"])
        assert result.exit_code != 0

    def test_graph_query_with_cypher(self):
        with patch("graphclaw.cli._shared.run_async") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(
                app,
                ["graph", "query", "--dangerous-allow-raw", "MATCH (n) RETURN n LIMIT 1"],
            )
            assert result.exit_code == 0

    def test_graph_query_without_dangerous_flag_exits_nonzero(self):
        result = runner.invoke(app, ["graph", "query", "MATCH (n) RETURN n LIMIT 1"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Integration-style: async helpers with fully mocked DB
# ---------------------------------------------------------------------------
#
# CLI commands now use graphclaw.cli._shared.cli_pool(), which bound
# ``create_pool`` and ``AgeGraphStore`` into the _shared module's namespace
# at import time.  We patch them there so that cli_pool() returns our mocks.

# Shared patch targets for all CLI DB tests.
_PATCH_CREATE_POOL = "graphclaw.cli._shared.create_pool"
_PATCH_GRAPH_REPO = "graphclaw.cli._shared.AgeGraphStore"


def _make_db_mocks(task_list=None, goal_list=None, node=None):
    """Build (pool_mock, repo_mock) with canned responses."""
    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()
    mock_repo = AsyncMock()
    if task_list is not None:
        mock_repo.list_nodes = AsyncMock(return_value=task_list)
    if node is not None:
        mock_repo.get_node = AsyncMock(return_value=node)
    return mock_pool, mock_repo


class TestListTasksAsyncHelper:
    """Tests the _list_tasks_async coroutine with a mocked database."""

    @pytest.mark.asyncio
    async def test_list_tasks_calls_list_nodes(self):
        task = _make_task(title="My Task")
        task_props = task.model_dump(mode="json")
        mock_pool, mock_repo = _make_db_mocks(task_list=[task_props])

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.task_commands import _list_tasks_async

                    await _list_tasks_async(None)

        mock_repo.list_nodes.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tasks_with_state_filter_passes_filter(self):
        mock_pool, mock_repo = _make_db_mocks(task_list=[])

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.task_commands import _list_tasks_async

                    await _list_tasks_async("ACTIVE")

        # Verify the state filter was passed through.
        call_kwargs = mock_repo.list_nodes.call_args
        assert call_kwargs is not None

    @pytest.mark.asyncio
    async def test_list_tasks_empty_does_not_raise(self):
        mock_pool, mock_repo = _make_db_mocks(task_list=[])

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.task_commands import _list_tasks_async

                    await _list_tasks_async(None)  # Should not raise.

    @pytest.mark.asyncio
    async def test_list_tasks_no_db_url_exits(self):
        import click

        with patch.dict("os.environ", {}, clear=True):
            from graphclaw.cli.task_commands import _list_tasks_async

            with pytest.raises((SystemExit, click.exceptions.Exit)):
                await _list_tasks_async(None)


class TestShowTaskAsyncHelper:
    @pytest.mark.asyncio
    async def test_show_task_found_calls_get_node(self):
        task = _make_task()
        task_props = task.model_dump(mode="json")
        mock_pool, mock_repo = _make_db_mocks(node=task_props)

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.task_commands import _show_task_async

                    await _show_task_async(task.id)

        mock_repo.get_node.assert_called_once_with(task.id)

    @pytest.mark.asyncio
    async def test_show_task_not_found_exits_with_code_1(self):
        import click

        mock_pool, mock_repo = _make_db_mocks(node=None)

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.task_commands import _show_task_async

                    with pytest.raises((SystemExit, click.exceptions.Exit)):
                        await _show_task_async("TSK-XX-0001-ATM")


class TestAgentScoreAsyncHelper:
    @pytest.mark.asyncio
    async def test_score_async_calls_run_cycle(self):
        task = _make_task()
        entry = _make_queue_entry(task, rank=1)

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_loop = AsyncMock()
        mock_loop.run_cycle = AsyncMock(return_value=[entry])

        with patch(
            "graphclaw.cli.agent_commands._build_agent_loop",
            new=AsyncMock(return_value=(mock_pool, mock_loop)),
        ):
            from graphclaw.cli.agent_commands import _score_async

            await _score_async(None)

        mock_loop.run_cycle.assert_called_once()

    @pytest.mark.asyncio
    async def test_score_async_empty_queue_does_not_raise(self):
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_loop = AsyncMock()
        mock_loop.run_cycle = AsyncMock(return_value=[])

        with patch(
            "graphclaw.cli.agent_commands._build_agent_loop",
            new=AsyncMock(return_value=(mock_pool, mock_loop)),
        ):
            from graphclaw.cli.agent_commands import _score_async

            await _score_async(None)  # Should not raise.

    @pytest.mark.asyncio
    async def test_run_cycle_async_calls_run_cycle(self):
        task = _make_task()
        entry = _make_queue_entry(task, rank=1)

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_loop = AsyncMock()
        mock_loop.run_cycle = AsyncMock(return_value=[entry])

        with patch(
            "graphclaw.cli.agent_commands._build_agent_loop",
            new=AsyncMock(return_value=(mock_pool, mock_loop)),
        ):
            from graphclaw.cli.agent_commands import _run_cycle_async

            await _run_cycle_async(None)

        mock_loop.run_cycle.assert_called_once()

    @pytest.mark.asyncio
    async def test_briefing_async_calls_generate_briefing(self):
        task = _make_task()
        entry = _make_queue_entry(task, rank=1)

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_loop = AsyncMock()
        mock_loop.run_cycle = AsyncMock(return_value=[entry])
        mock_loop.generate_briefing = AsyncMock(return_value="briefing text")

        with patch(
            "graphclaw.cli.agent_commands._build_agent_loop",
            new=AsyncMock(return_value=(mock_pool, mock_loop)),
        ):
            from graphclaw.cli.agent_commands import _briefing_async

            await _briefing_async(top_n=5)

        mock_loop.generate_briefing.assert_called_once_with([entry], top_n=5)


class TestGraphStatsAsyncHelper:
    @pytest.mark.asyncio
    async def test_stats_async_queries_all_labels(self):
        task = _make_task()
        task_props = task.model_dump(mode="json")
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.list_nodes = AsyncMock(
            side_effect=lambda label, **kw: [task_props] if label == "TaskNode" else []
        )

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}):
            with patch(
                _PATCH_CREATE_POOL,
                new=AsyncMock(return_value=mock_pool),
            ):
                with patch(
                    _PATCH_GRAPH_REPO,
                    return_value=mock_repo,
                ):
                    from graphclaw.cli.graph_commands import _stats_async

                    await _stats_async()

        # Should have queried multiple labels.
        assert mock_repo.list_nodes.call_count > 1

    @pytest.mark.asyncio
    async def test_stats_async_no_db_url_exits(self):
        import click

        with patch.dict("os.environ", {}, clear=True):
            from graphclaw.cli.graph_commands import _stats_async

            with pytest.raises((SystemExit, click.exceptions.Exit)):
                await _stats_async()
