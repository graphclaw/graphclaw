"""tests.test_agent.test_sub_agent_orchestration — Unit tests for Phase 5 sub-agent components.

Description
-----------
Tests for:
- AgentDispatchPlanner: topological sort, single-tier fallback, cycle handling
- BatchCoordinator: fan-in tier completion, DELEGATION_COMPLETE publishing
- SubAgentPool: job dispatch, semaphore throttling, batch registration
- AgentHealthMonitor: heartbeat recording, get_agent_health, timeout detection
- AgentLoop._tool_delegate_to_agent: AgentJobEvent publishing to AGENT_JOBS
- ResultCollector.process_agent_result: task node updates from sub-agent events

Design Patterns
---------------
- Arrange/Act/Assert with AsyncMock for all async collaborators.
- FakeBroker captures published messages without a real broker connection.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class FakeBroker:
    """Minimal in-memory broker stub for testing publish calls."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, queue: str, payload: str) -> None:
        self.published.append((queue, payload))

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# AgentDispatchPlanner
# ---------------------------------------------------------------------------


class TestAgentDispatchPlanner:
    """Unit tests for AgentDispatchPlanner.plan()."""

    @pytest.fixture
    def _planner(self):
        from graphclaw.agent.dispatch_planner import AgentDispatchPlanner
        from graphclaw.agent.sub_agent_runner import AgentJobEvent

        qe = AsyncMock()
        planner = AgentDispatchPlanner(query_engine=qe)
        return planner, qe, AgentJobEvent

    @pytest.mark.asyncio
    async def test_single_job_returns_single_tier(self, _planner):
        planner, qe, AgentJobEvent = _planner
        jobs = [
            AgentJobEvent(
                agent_id="agent-a",
                task_id="TSK-001",
                session_id="SES-1",
                parent_task_id=None,
                batch_id="",
                instructions="do it",
            )
        ]
        tiers = await planner.plan(jobs, session_id="SES-1")
        assert len(tiers) == 1
        assert len(tiers[0]) == 1
        assert tiers[0][0].task_id == "TSK-001"
        # No edge query needed for single job
        qe.get_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_edges_all_parallel(self, _planner):
        planner, qe, AgentJobEvent = _planner
        qe.get_edges = AsyncMock(return_value=[])

        jobs = [
            AgentJobEvent(
                agent_id="agent-a",
                task_id="TSK-001",
                session_id="SES-1",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="agent-b",
                task_id="TSK-002",
                session_id="SES-1",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
        ]
        tiers = await planner.plan(jobs, session_id="SES-1")
        assert len(tiers) == 1
        assert len(tiers[0]) == 2

    @pytest.mark.asyncio
    async def test_linear_dependency_produces_two_tiers(self, _planner):
        """A depends on B → tier 1: [B], tier 2: [A]."""
        planner, qe, AgentJobEvent = _planner

        async def fake_get_edges(node_id, direction, edge_type):
            # TSK-A DEPENDS_ON TSK-B
            if node_id == "TSK-A" and direction == "out":
                return [{"target_id": "TSK-B"}]
            return []

        qe.get_edges = fake_get_edges

        jobs = [
            AgentJobEvent(
                agent_id="agent-a",
                task_id="TSK-A",
                session_id="SES-1",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="agent-b",
                task_id="TSK-B",
                session_id="SES-1",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
        ]
        tiers = await planner.plan(jobs, session_id="SES-1")
        assert len(tiers) == 2
        tier_0_ids = {j.task_id for j in tiers[0]}
        tier_1_ids = {j.task_id for j in tiers[1]}
        assert "TSK-B" in tier_0_ids
        assert "TSK-A" in tier_1_ids

    @pytest.mark.asyncio
    async def test_independent_job_in_parallel_with_dependency_chain(self, _planner):
        """C independent, A depends on B → tier 1: [B, C], tier 2: [A]."""
        planner, qe, AgentJobEvent = _planner

        async def fake_get_edges(node_id, direction, edge_type):
            if node_id == "TSK-A" and direction == "out":
                return [{"target_id": "TSK-B"}]
            return []

        qe.get_edges = fake_get_edges

        jobs = [
            AgentJobEvent(
                agent_id="a",
                task_id="TSK-A",
                session_id="S",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="b",
                task_id="TSK-B",
                session_id="S",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="c",
                task_id="TSK-C",
                session_id="S",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
        ]
        tiers = await planner.plan(jobs, session_id="S")
        assert len(tiers) == 2
        tier_0_ids = {j.task_id for j in tiers[0]}
        tier_1_ids = {j.task_id for j in tiers[1]}
        assert {"TSK-B", "TSK-C"} == tier_0_ids
        assert {"TSK-A"} == tier_1_ids

    @pytest.mark.asyncio
    async def test_batch_ids_assigned_per_tier(self, _planner):
        """Each tier should have jobs with the same batch_id."""
        planner, qe, AgentJobEvent = _planner

        async def fake_get_edges(node_id, direction, edge_type):
            if node_id == "TSK-A" and direction == "out":
                return [{"target_id": "TSK-B"}]
            return []

        qe.get_edges = fake_get_edges

        jobs = [
            AgentJobEvent(
                agent_id="a",
                task_id="TSK-A",
                session_id="SES-ABC",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="b",
                task_id="TSK-B",
                session_id="SES-ABC",
                parent_task_id=None,
                batch_id="",
                instructions="",
            ),
        ]
        tiers = await planner.plan(jobs, session_id="SES-ABC")
        # All jobs in tier 0 share batch_id
        batch_ids_t0 = {j.batch_id for j in tiers[0]}
        batch_ids_t1 = {j.batch_id for j in tiers[1]}
        assert len(batch_ids_t0) == 1
        assert len(batch_ids_t1) == 1
        assert batch_ids_t0 != batch_ids_t1

    def test_topological_sort_cycle_remainder(self):
        """Cyclic dependencies should be appended as a final tier with a warning."""
        from graphclaw.agent.dispatch_planner import AgentDispatchPlanner

        # A depends on B, B depends on A — mutual cycle
        edges = [("TSK-A", "TSK-B"), ("TSK-B", "TSK-A")]
        tiers = AgentDispatchPlanner._topological_sort({"TSK-A", "TSK-B"}, edges)
        # Both stuck in cycle — should still appear (cycle remainder)
        all_ids = {tid for tier in tiers for tid in tier}
        assert "TSK-A" in all_ids
        assert "TSK-B" in all_ids


# ---------------------------------------------------------------------------
# BatchCoordinator
# ---------------------------------------------------------------------------


class TestBatchCoordinator:
    """Unit tests for BatchCoordinator fan-in logic."""

    @pytest.fixture
    def _coordinator(self):
        from graphclaw.agent.sub_agent_pool import BatchCoordinator

        broker = FakeBroker()
        coord = BatchCoordinator(broker=broker)
        return coord, broker

    @pytest.mark.asyncio
    async def test_not_done_until_all_complete(self, _coordinator):
        coord, broker = _coordinator
        coord.register_batch(batch_id="batch-1", count=3, session_id="SES-1", is_final_tier=True)

        done, _ = await coord.record_completion("batch-1")
        assert not done
        done, _ = await coord.record_completion("batch-1")
        assert not done
        done, _ = await coord.record_completion("batch-1")
        assert done

    @pytest.mark.asyncio
    async def test_final_tier_publishes_delegation_complete(self, _coordinator):
        coord, broker = _coordinator
        coord.register_batch(
            batch_id="batch-final", count=1, session_id="SES-X", is_final_tier=True
        )

        await coord.record_completion("batch-final")
        # Should have published DELEGATION_COMPLETE to TRIGGER_EVENTS
        assert len(broker.published) == 1
        queue, payload = broker.published[0]
        assert queue == "trigger_events"
        data = json.loads(payload)
        assert data["type"] == "DELEGATION_COMPLETE"
        assert data["session_id"] == "SES-X"

    @pytest.mark.asyncio
    async def test_non_final_tier_does_not_publish(self, _coordinator):
        coord, broker = _coordinator
        coord.register_batch(batch_id="batch-mid", count=1, session_id="SES-X", is_final_tier=False)

        await coord.record_completion("batch-mid")
        assert len(broker.published) == 0

    @pytest.mark.asyncio
    async def test_next_tier_jobs_returned_on_completion(self, _coordinator):
        from graphclaw.agent.sub_agent_runner import AgentJobEvent

        coord, broker = _coordinator
        next_jobs = [
            AgentJobEvent(
                agent_id="b",
                task_id="TSK-B",
                session_id="S",
                parent_task_id=None,
                batch_id="batch-2",
                instructions="",
            )
        ]
        coord.register_batch(
            batch_id="batch-1",
            count=1,
            session_id="S",
            next_tier_jobs=next_jobs,
            is_final_tier=False,
        )
        done, returned_jobs = await coord.record_completion("batch-1")
        assert done
        assert len(returned_jobs) == 1
        assert returned_jobs[0].task_id == "TSK-B"

    @pytest.mark.asyncio
    async def test_unknown_batch_returns_false(self, _coordinator):
        coord, _ = _coordinator
        done, jobs = await coord.record_completion("nonexistent-batch")
        assert not done
        assert jobs == []


# ---------------------------------------------------------------------------
# AgentHealthMonitor
# ---------------------------------------------------------------------------


class TestAgentHealthMonitor:
    """Unit tests for AgentHealthMonitor health tracking."""

    @pytest.fixture
    def _monitor(self):
        from graphclaw.agent.health_monitor import AgentHealthMonitor

        broker = FakeBroker()
        monitor = AgentHealthMonitor(
            broker=broker,
            state_machine=None,
            check_interval=30,
            heartbeat_timeout=300,
        )
        return monitor, broker

    def test_healthy_when_not_tracked(self, _monitor):
        monitor, _ = _monitor
        assert monitor.get_agent_health("unknown-agent") == "HEALTHY"

    def test_healthy_immediately_after_heartbeat(self, _monitor):
        monitor, _ = _monitor
        monitor.record_heartbeat("agent-1", "TSK-1", "SES-1")
        assert monitor.get_agent_health("agent-1") == "HEALTHY"

    def test_stale_after_60s(self, _monitor):
        from datetime import timedelta

        from graphclaw.models.base import utcnow

        monitor, _ = _monitor
        monitor.record_heartbeat("agent-1", "TSK-1", "SES-1")
        # Backdate the heartbeat to 90 seconds ago
        monitor._records["agent-1"].last_heartbeat = utcnow() - timedelta(seconds=90)
        assert monitor.get_agent_health("agent-1") == "STALE"

    def test_blocked_after_timeout(self, _monitor):
        from datetime import timedelta

        from graphclaw.models.base import utcnow

        monitor, _ = _monitor
        monitor.record_heartbeat("agent-1", "TSK-1", "SES-1")
        monitor._records["agent-1"].last_heartbeat = utcnow() - timedelta(seconds=400)
        assert monitor.get_agent_health("agent-1") == "BLOCKED"

    def test_remove_agent_clears_record(self, _monitor):
        monitor, _ = _monitor
        monitor.record_heartbeat("agent-1", "TSK-1", "SES-1")
        monitor.remove_agent("agent-1")
        assert monitor.get_agent_health("agent-1") == "HEALTHY"

    @pytest.mark.asyncio
    async def test_check_timeouts_marks_blocked_and_publishes(self, _monitor):
        from datetime import timedelta

        from graphclaw.models.base import utcnow

        monitor, broker = _monitor
        monitor.record_heartbeat("agent-slow", "TSK-SLOW", "SES-1")
        monitor._records["agent-slow"].last_heartbeat = utcnow() - timedelta(seconds=400)

        await monitor._check_timeouts()

        # Should publish BLOCKED event to AGENT_UPDATES
        assert len(broker.published) == 1
        queue, payload = broker.published[0]
        assert queue == "agent_updates"
        data = json.loads(payload)
        assert data["event_type"] == "blocked"
        assert data["agent_id"] == "agent-slow"

        # Agent removed from tracking after escalation
        assert "agent-slow" not in monitor._records

    @pytest.mark.asyncio
    async def test_check_timeouts_not_duplicate_escalation(self, _monitor):
        from datetime import timedelta

        from graphclaw.models.base import utcnow

        monitor, broker = _monitor
        monitor.record_heartbeat("agent-x", "TSK-X", "SES-1")
        monitor._records["agent-x"].last_heartbeat = utcnow() - timedelta(seconds=400)

        await monitor._check_timeouts()
        count_after_first = len(broker.published)
        # Agent removed after first escalation — second call should not publish
        await monitor._check_timeouts()
        assert len(broker.published) == count_after_first


# ---------------------------------------------------------------------------
# AgentLoop._tool_delegate_to_agent publishes to AGENT_JOBS
# ---------------------------------------------------------------------------


class TestAgentLoopDelegation:
    """Unit tests for AgentLoop._tool_delegate_to_agent publishing to AGENT_JOBS."""

    @pytest.fixture
    def _loop_with_broker(self):
        from graphclaw.agent.main_orchestrator import MainOrchestrator as AgentLoop
        from graphclaw.scoring.engine import ScoringEngine

        repo = AsyncMock()
        repo.get_node = AsyncMock(
            return_value={
                "id": "TSK-001",
                "title": "Test Task",
                "description": "desc",
                "state": "PENDING",
                "state_history": [],
                "parent_task_id": None,
            }
        )
        repo.update_node = AsyncMock(return_value=None)

        state_machine = MagicMock()
        broker = FakeBroker()

        loop = AgentLoop(
            graph_repo=repo,
            scoring_engine=ScoringEngine(),
            state_machine=state_machine,
            broker=broker,
        )
        loop._current_session_id = "SES-TEST-001"
        return loop, broker, repo

    @pytest.mark.asyncio
    async def test_publishes_agent_job_event_to_agent_jobs(self, _loop_with_broker):
        loop, broker, _ = _loop_with_broker
        result = await loop._tool_delegate_to_agent(
            user_id="user-1",
            args={"task_id": "TSK-001", "agent_id": "research-agent", "instructions": "research X"},
        )
        assert result["status"] == "delegated"
        # Job published to AGENT_JOBS queue
        assert len(broker.published) == 1
        queue, payload = broker.published[0]
        assert queue == "agent_jobs"
        job = json.loads(payload)
        assert job["agent_id"] == "research-agent"
        assert job["task_id"] == "TSK-001"
        assert job["session_id"] == "SES-TEST-001"

    @pytest.mark.asyncio
    async def test_batch_id_in_result(self, _loop_with_broker):
        loop, broker, _ = _loop_with_broker
        result = await loop._tool_delegate_to_agent(
            user_id="user-1",
            args={"task_id": "TSK-001", "agent_id": "agent-x", "instructions": ""},
        )
        assert "batch_id" in result
        assert result["batch_id"].startswith("batch-")

    @pytest.mark.asyncio
    async def test_delegate_persists_handoff_node_and_edge(self, _loop_with_broker):
        loop, _, repo = _loop_with_broker
        await loop._tool_delegate_to_agent(
            user_id="user-1",
            args={"task_id": "TSK-001", "agent_id": "agent-x", "instructions": "handoff ctx"},
        )

        repo.create_node.assert_called()
        handoff_node = repo.create_node.call_args.args[0]
        assert handoff_node.id.startswith("HND-")
        assert handoff_node.task_id == "TSK-001"
        assert handoff_node.to_owner == "agent-x"

        repo.create_edge.assert_called()
        edge_call = repo.create_edge.call_args
        assert edge_call.args[1] == "TSK-001"
        assert edge_call.args[2] == "REFERRED_BY"

    @pytest.mark.asyncio
    async def test_no_broker_does_not_raise(self, _loop_with_broker):
        loop, _, _ = _loop_with_broker
        loop._broker = None  # Remove broker
        # Should not raise — just warns
        result = await loop._tool_delegate_to_agent(
            user_id="user-1",
            args={"task_id": "TSK-001", "agent_id": "agent-y", "instructions": ""},
        )
        assert result["status"] == "delegated"

    @pytest.mark.asyncio
    async def test_task_not_found_returns_error(self, _loop_with_broker):
        loop, _, repo = _loop_with_broker
        repo.get_node = AsyncMock(return_value=None)
        result = await loop._tool_delegate_to_agent(
            user_id="user-1",
            args={"task_id": "MISSING", "agent_id": "agent-z", "instructions": ""},
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# ResultCollector.process_agent_result
# ---------------------------------------------------------------------------


class TestResultCollectorProcessAgentResult:
    """Unit tests for ResultCollector.process_agent_result."""

    @pytest.fixture
    def _collector(self):
        from graphclaw.agent.result_collector import ResultCollector
        from graphclaw.agent.sub_agent_runner import AgentUpdateEvent, AgentUpdateEventType

        repo = AsyncMock()
        repo.update_node = AsyncMock(return_value=None)

        worker_pool = MagicMock()
        worker_pool.get_worker_statuses = MagicMock(return_value=[])

        collector = ResultCollector(
            graph_repo=repo,
            worker_pool=worker_pool,
            user_id="user-1",
            agent_id="main",
        )
        return collector, repo, AgentUpdateEvent, AgentUpdateEventType

    @pytest.mark.asyncio
    async def test_updates_task_to_needs_review_on_completed(self, _collector):
        collector, repo, AgentUpdateEvent, AgentUpdateEventType = _collector
        event = AgentUpdateEvent(
            event_type=AgentUpdateEventType.COMPLETED,
            agent_id="agent-1",
            task_id="TSK-001",
            session_id="SES-1",
            message="Research complete",
            status="COMPLETED",
            batch_id="batch-abc",
        )
        await collector.process_agent_result(event)
        repo.update_node.assert_called_once()
        call_args = repo.update_node.call_args
        assert call_args[0][0] == "TSK-001"
        updates = call_args[0][1]
        assert updates["state"] == "NEEDS_REVIEW"
        assert "intelligence" in updates

    @pytest.mark.asyncio
    async def test_updates_task_to_blocked_on_failed(self, _collector):
        collector, repo, AgentUpdateEvent, AgentUpdateEventType = _collector
        event = AgentUpdateEvent(
            event_type=AgentUpdateEventType.BLOCKED,
            agent_id="agent-1",
            task_id="TSK-002",
            session_id="SES-1",
            message="failed",
            status="FAILED",
            batch_id="",
        )
        await collector.process_agent_result(event)
        call_args = repo.update_node.call_args
        updates = call_args[0][1]
        assert updates["state"] == "BLOCKED"

    @pytest.mark.asyncio
    async def test_no_message_still_updates(self, _collector):
        collector, repo, AgentUpdateEvent, AgentUpdateEventType = _collector
        event = AgentUpdateEvent(
            event_type=AgentUpdateEventType.COMPLETED,
            agent_id="agent-1",
            task_id="TSK-003",
            session_id="SES-1",
            message=None,
            status="COMPLETED",
            batch_id="",
        )
        await collector.process_agent_result(event)
        repo.update_node.assert_called_once()
        updates = repo.update_node.call_args[0][1]
        # No intelligence key if no message
        assert "intelligence" not in updates


# ---------------------------------------------------------------------------
# SubAgentPool — basic construction and dispatch plan registration
# ---------------------------------------------------------------------------


class TestSubAgentPool:
    """Smoke tests for SubAgentPool initialization and batch registration."""

    def test_initial_active_count_zero(self):
        from graphclaw.agent.sub_agent_pool import SubAgentPool

        broker = FakeBroker()
        llm = MagicMock()
        pool = SubAgentPool(max_size=4, broker=broker, llm_client=llm)
        assert pool.active_count == 0

    def test_register_dispatch_plan_registers_batches(self):
        from graphclaw.agent.sub_agent_pool import SubAgentPool
        from graphclaw.agent.sub_agent_runner import AgentJobEvent

        broker = FakeBroker()
        llm = MagicMock()
        pool = SubAgentPool(max_size=4, broker=broker, llm_client=llm)

        tier_0 = [
            AgentJobEvent(
                agent_id="a",
                task_id="T1",
                session_id="S",
                parent_task_id=None,
                batch_id="batch-S-t0",
                instructions="",
            ),
            AgentJobEvent(
                agent_id="b",
                task_id="T2",
                session_id="S",
                parent_task_id=None,
                batch_id="batch-S-t0",
                instructions="",
            ),
        ]
        tier_1 = [
            AgentJobEvent(
                agent_id="c",
                task_id="T3",
                session_id="S",
                parent_task_id=None,
                batch_id="batch-S-t1",
                instructions="",
            ),
        ]

        pool.register_dispatch_plan([tier_0, tier_1], session_id="S")

        # Batches should be registered in coordinator
        assert "batch-S-t0" in pool.batch_coordinator._batches
        assert "batch-S-t1" in pool.batch_coordinator._batches
        batch_0 = pool.batch_coordinator._batches["batch-S-t0"]
        assert batch_0.total_count == 2
        assert not batch_0.is_final_tier
        batch_1 = pool.batch_coordinator._batches["batch-S-t1"]
        assert batch_1.total_count == 1
        assert batch_1.is_final_tier


class TestSubAgentRunnerTimeouts:
    """Unit tests for runner-level and tool-level timeout behavior."""

    @pytest.mark.asyncio
    async def test_execute_times_out_when_runner_exceeds_execution_limit(self):
        from graphclaw.agent.sub_agent_runner import AgentJobEvent, RunnerState, SubAgentRunner

        broker = FakeBroker()
        llm = MagicMock()
        runner = SubAgentRunner(
            runner_id="runner-timeout",
            broker=broker,
            llm_client=llm,
            execution_timeout_seconds=1,
            tool_timeout_seconds=1,
        )

        async def _slow_loop(_job: AgentJobEvent) -> None:
            await asyncio.sleep(1.2)

        runner._run_llm_loop = _slow_loop  # type: ignore[method-assign]

        job = AgentJobEvent(
            agent_id="agent-1",
            task_id="TSK-timeout",
            session_id="SES-timeout",
            instructions="slow test",
        )

        status = await runner.execute(job)

        assert status == "TIMED_OUT"
        assert runner.state == RunnerState.TIMED_OUT

        events = [
            json.loads(payload) for queue, payload in broker.published if queue == "agent_updates"
        ]
        assert any(event.get("event_type") == "blocked" for event in events)
        assert any(
            event.get("event_type") == "completed" and event.get("status") == "TIMED_OUT"
            for event in events
        )

    @pytest.mark.asyncio
    async def test_dispatch_tool_returns_timeout_error_for_slow_tool(self):
        from graphclaw.agent.sub_agent_runner import AgentJobEvent, SubAgentRunner

        broker = FakeBroker()
        llm = MagicMock()
        runner = SubAgentRunner(
            runner_id="runner-tool-timeout",
            broker=broker,
            llm_client=llm,
            execution_timeout_seconds=5,
            tool_timeout_seconds=1,
        )

        async def _slow_skill(_args: dict, _job: AgentJobEvent) -> dict[str, str]:
            await asyncio.sleep(1.2)
            return {"status": "ok"}

        runner._tool_invoke_skill = _slow_skill  # type: ignore[method-assign]

        job = AgentJobEvent(
            agent_id="agent-2",
            task_id="TSK-slow-tool",
            session_id="SES-slow-tool",
            instructions="tool timeout test",
        )

        result = await runner._dispatch_tool("invoke_skill", {}, job)
        assert "error" in result
        assert "timeout" in str(result["error"]).lower()

    @pytest.mark.asyncio
    async def test_execute_emits_cancelled_status_when_task_is_cancelled(self):
        from graphclaw.agent.sub_agent_runner import AgentJobEvent, RunnerState, SubAgentRunner

        broker = FakeBroker()
        llm = MagicMock()
        runner = SubAgentRunner(
            runner_id="runner-cancel",
            broker=broker,
            llm_client=llm,
            execution_timeout_seconds=10,
            tool_timeout_seconds=1,
        )

        async def _very_slow_loop(_job: AgentJobEvent) -> None:
            await asyncio.sleep(10)

        runner._run_llm_loop = _very_slow_loop  # type: ignore[method-assign]

        job = AgentJobEvent(
            agent_id="agent-cancel",
            task_id="TSK-cancel",
            session_id="SES-cancel",
            instructions="cancel test",
        )

        run_task = asyncio.create_task(runner.execute(job))
        await asyncio.sleep(0)
        run_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await run_task

        assert runner.state == RunnerState.CANCELLED
        events = [
            json.loads(payload) for queue, payload in broker.published if queue == "agent_updates"
        ]
        assert any(event.get("event_type") == "blocked" for event in events)
        assert any(
            event.get("event_type") == "completed" and event.get("status") == "CANCELLED"
            for event in events
        )

    @pytest.mark.asyncio
    async def test_dispatch_tool_retries_retryable_skill(self):
        from graphclaw.agent.sub_agent_runner import AgentJobEvent, SubAgentRunner

        broker = FakeBroker()
        llm = MagicMock()
        runner = SubAgentRunner(
            runner_id="runner-retry",
            broker=broker,
            llm_client=llm,
            execution_timeout_seconds=5,
            tool_timeout_seconds=1,
            tool_max_retries=2,
            retry_backoff_base_ms=0,
            retry_backoff_max_ms=0,
            retryable_skills={"retryable-skill"},
        )

        attempts = 0

        async def _flaky_skill(_args: dict, _job: AgentJobEvent) -> dict[str, str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return {"error": "No idle skill workers available. Try again shortly."}
            return {"status": "COMPLETED", "output": "ok", "error": ""}

        runner._tool_invoke_skill = _flaky_skill  # type: ignore[method-assign]

        job = AgentJobEvent(
            agent_id="agent-retry",
            task_id="TSK-retry",
            session_id="SES-retry",
            instructions="retry test",
        )

        result = await runner._dispatch_tool(
            "invoke_skill",
            {"skill_name": "retryable-skill", "task_id": "TSK-retry"},
            job,
        )
        assert attempts == 2
        assert result.get("status") == "COMPLETED"
