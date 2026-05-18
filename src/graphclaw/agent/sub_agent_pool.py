# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.sub_agent_pool — SubAgentPool: bounded pool of SubAgentRunner instances.

Description
-----------
``SubAgentPool`` manages a bounded set of ``SubAgentRunner`` coroutines.  It
consumes from the ``AGENT_JOBS`` broker queue, dispatches jobs to available
runners via a semaphore, and tracks parallel dispatch tiers via
``BatchCoordinator``.

When all runners in a dispatch tier complete, ``BatchCoordinator`` fires the
next tier.  When the final tier completes, it publishes a ``DELEGATION_COMPLETE``
event to ``TRIGGER_EVENTS`` so ``AgentEventConsumer`` can re-engage the
orchestrator.

Design Patterns
---------------
- Semaphore Throttle: An ``asyncio.Semaphore(max_size)`` ensures at most
  ``max_size`` runners execute concurrently.  Overflow jobs remain in the
  broker queue and are picked up as slots free.
- Fan-in Coordination: ``BatchCoordinator`` tracks completion counts per batch.
- Background Task: Launched via ``start()``; cancelled cleanly via ``stop()``.
- Dependency Injection: All collaborators injected at construction time.

Public API
----------
- SubAgentPool: Pool manager.
- SubAgentPool.start: Launch the AGENT_JOBS consumer loop.
- SubAgentPool.stop: Cancel the consumer loop and wait for active runners.
- SubAgentPool.get_runner_statuses: Return status snapshots for all runners.
- SubAgentPool.register_dispatch_plan: Register ordered tier batches from
  AgentDispatchPlanner so BatchCoordinator knows what to dispatch next.
- BatchCoordinator: Tracks tier completion and dispatches next tiers.

Dependencies
------------
- asyncio: Semaphore, Queue, Task.
- graphclaw.infra.broker: MessageBroker, AGENT_JOBS, TRIGGER_EVENTS.
- graphclaw.agent.sub_agent_runner: SubAgentRunner, AgentJobEvent, RunnerStatus.
- graphclaw.infra.logger: AsyncLogger (TYPE_CHECKING).
- graphclaw.llm.base: LLMClient (TYPE_CHECKING).
- graphclaw.infra.storage: StorageClient (TYPE_CHECKING).
- graphclaw.skills.worker: WorkerPool (TYPE_CHECKING).
- graphclaw.mcp.registry: MCPRegistry (TYPE_CHECKING).
- graphclaw.skills.registry: SkillRegistryService (TYPE_CHECKING).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from graphclaw.agent.sub_agent_runner import AgentJobEvent, RunnerStatus, SubAgentRunner
from graphclaw.infra.broker import AGENT_JOBS, TRIGGER_EVENTS, MessageBroker
from graphclaw.models.base import utcnow

if TYPE_CHECKING:
    from graphclaw.infra.storage import StorageClient
    from graphclaw.llm.base import LLMClient
    from graphclaw.mcp.registry import MCPRegistry
    from graphclaw.skills.registry import SkillRegistryService
    from graphclaw.skills.worker import WorkerPool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BatchCoordinator — fan-in tracking for parallel dispatch tiers
# ---------------------------------------------------------------------------


@dataclass
class _BatchState:
    """Internal state for one parallel dispatch tier."""

    batch_id: str
    total_count: int
    current_jobs: list[AgentJobEvent] = field(default_factory=list)
    completed_count: int = 0
    next_tier_jobs: list[AgentJobEvent] = field(default_factory=list)
    session_id: str = ""
    is_final_tier: bool = False


class BatchCoordinator:
    """Tracks completion of parallel dispatch tiers and fires subsequent ones.

    When all runners in a tier complete, ``record_completion()`` returns True and
    the pool dispatches the next tier.  After the final tier, it publishes
    ``DELEGATION_COMPLETE`` to ``TRIGGER_EVENTS`` to re-engage the orchestrator.

    Parameters
    ----------
    broker:
        MessageBroker for publishing ``DELEGATION_COMPLETE`` events.
    """

    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker
        self._batches: dict[str, _BatchState] = {}

    def register_batch(
        self,
        batch_id: str,
        count: int,
        session_id: str,
        current_jobs: list[AgentJobEvent] | None = None,
        next_tier_jobs: list[AgentJobEvent] | None = None,
        is_final_tier: bool = False,
    ) -> None:
        """Register a new dispatch tier batch.

        Args:
            batch_id: Unique batch identifier for this tier.
            count: Number of jobs in this tier.
            session_id: Orchestration session ID for correlation.
            next_tier_jobs: Jobs to dispatch when this tier completes.
            is_final_tier: True if no more tiers follow this one.
        """
        self._batches[batch_id] = _BatchState(
            batch_id=batch_id,
            total_count=count,
            current_jobs=current_jobs or [],
            next_tier_jobs=next_tier_jobs or [],
            session_id=session_id,
            is_final_tier=is_final_tier,
        )

    async def record_completion(self, batch_id: str) -> tuple[bool, list[AgentJobEvent]]:
        """Record one job completion for a batch.

        Returns:
            ``(tier_done, next_tier_jobs)`` — ``tier_done`` is True when all
            jobs in the batch have completed.  ``next_tier_jobs`` contains jobs
            to dispatch for the next tier (empty list if none).
        """
        batch = self._batches.get(batch_id)
        if batch is None:
            return False, []

        batch.completed_count += 1
        if batch.completed_count < batch.total_count:
            return False, []

        # Tier complete
        logger.info(
            "BatchCoordinator: tier %s complete (%d/%d)",
            batch_id,
            batch.completed_count,
            batch.total_count,
        )

        if batch.is_final_tier:
            # All delegation tiers done — re-engage orchestrator
            await self._publish_delegation_complete(batch.session_id, batch_id)

        return True, batch.next_tier_jobs

    async def _publish_delegation_complete(self, session_id: str, final_batch_id: str) -> None:
        """Publish DELEGATION_COMPLETE event to TRIGGER_EVENTS."""
        event = {
            "type": "DELEGATION_COMPLETE",
            "session_id": session_id,
            "final_batch_id": final_batch_id,
            "completed_at": utcnow().isoformat(),
        }
        try:
            await self._broker.publish(TRIGGER_EVENTS, json.dumps(event))
            logger.info(
                "BatchCoordinator: published DELEGATION_COMPLETE for session %s", session_id
            )
        except Exception as exc:
            logger.warning("BatchCoordinator: failed to publish DELEGATION_COMPLETE: %s", exc)


# ---------------------------------------------------------------------------
# SubAgentPool
# ---------------------------------------------------------------------------


class SubAgentPool:
    """Bounded pool of SubAgentRunner instances consuming from AGENT_JOBS.

    Parameters
    ----------
    max_size:
        Maximum number of concurrently active runners.  Excess jobs remain
        queued in the broker until a slot becomes available.
    broker:
        MessageBroker for consuming AGENT_JOBS and emitting events.
    llm_client:
        LLMClient shared across all runners.
    storage:
        Optional StorageClient for agent profile/context reads.
    worker_pool:
        Dedicated WorkerPool for skill execution (sub-agent pool, NOT shared
        with the orchestrator pool).
    skill_registry:
        Optional SkillRegistryService for skill resolution.
    mcp_registry:
        Optional MCPRegistry for MCP tool calls.
    async_logger:
        Optional AsyncLogger for structured audit events.
    heartbeat_interval:
        Seconds between heartbeat emits (default 60).
    execution_timeout_seconds:
        Hard timeout applied to each delegated run.
    tool_timeout_seconds:
        Per-tool-call timeout applied inside each runner.
    tool_max_retries:
        Maximum retries for retry-eligible tool calls.
    retry_backoff_base_ms:
        Base backoff for retry-eligible tool calls.
    retry_backoff_max_ms:
        Maximum backoff for retry-eligible tool calls.
    retryable_skills:
        Skill-name allowlist for retry-safe ``invoke_skill`` calls.
    retryable_mcp_tools:
        MCP tool allowlist for retry-safe ``call_mcp_tool`` calls.
    """

    def __init__(
        self,
        max_size: int,
        broker: MessageBroker,
        llm_client: LLMClient,
        storage: StorageClient | None = None,
        worker_pool: WorkerPool | None = None,
        skill_registry: SkillRegistryService | None = None,
        mcp_registry: MCPRegistry | None = None,
        heartbeat_interval: int = 60,
        execution_timeout_seconds: int = 600,
        tool_timeout_seconds: int = 120,
        tool_max_retries: int = 0,
        retry_backoff_base_ms: int = 200,
        retry_backoff_max_ms: int = 1000,
        retryable_skills: set[str] | None = None,
        retryable_mcp_tools: set[str] | None = None,
    ) -> None:
        self._max_size = max_size
        self._broker = broker
        self._llm = llm_client
        self._storage = storage
        self._worker_pool = worker_pool
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._heartbeat_interval = heartbeat_interval
        self._execution_timeout_seconds = execution_timeout_seconds
        self._tool_timeout_seconds = tool_timeout_seconds
        self._tool_max_retries = max(0, tool_max_retries)
        self._retry_backoff_base_ms = max(0, retry_backoff_base_ms)
        self._retry_backoff_max_ms = max(self._retry_backoff_base_ms, retry_backoff_max_ms)
        self._retryable_skills = retryable_skills or set()
        self._retryable_mcp_tools = retryable_mcp_tools or set()

        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_size)
        self._active_runners: dict[str, SubAgentRunner] = {}  # runner_id → runner
        self._runner_counter: int = 0
        self._consumer_task: asyncio.Task | None = None
        self._running: bool = False
        self._queued_count: int = 0
        self._active_count: int = 0
        self._metrics_lock = asyncio.Lock()
        self.batch_coordinator: BatchCoordinator = BatchCoordinator(broker)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background AGENT_JOBS consumer loop."""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("SubAgentPool: started (max_size=%d)", self._max_size)

    async def stop(self) -> None:
        """Stop the consumer loop gracefully."""
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("SubAgentPool: stopped")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_runner_statuses(self) -> list[RunnerStatus]:
        """Return point-in-time status snapshots for all active runners."""
        return [r.status for r in self._active_runners.values()]

    def get_dispatch_plan(self, session_id: str) -> list[dict[str, object]]:
        """Return dispatch tiers and job states for a given orchestration session."""

        def _tier_index(batch_id: str) -> int:
            marker = "-t"
            if marker not in batch_id:
                return 0
            try:
                return int(batch_id.rsplit(marker, 1)[1])
            except ValueError:
                return 0

        def _job_state(raw_state: str, batch_done: bool) -> str:
            state = raw_state.upper()
            if state in {"FAILED", "TIMED_OUT", "CANCELLED", "BLOCKED"}:
                return "BLOCKED"
            if state in {"RUNNING", "WORKING", "BUSY"}:
                return "RUNNING"
            if state in {"COMPLETED"}:
                return "COMPLETED"
            return "COMPLETED" if batch_done else "PENDING"

        active_by_task: dict[str, str] = {}
        for status in self.get_runner_statuses():
            if status.session_id != session_id or not status.task_id:
                continue
            active_by_task[status.task_id] = getattr(status.state, "value", str(status.state))

        tiers: list[dict[str, object]] = []
        for batch in self.batch_coordinator._batches.values():
            if batch.session_id != session_id:
                continue

            batch_done = batch.completed_count >= batch.total_count
            jobs: list[dict[str, str]] = []
            for job in batch.current_jobs:
                raw_state = active_by_task.get(
                    job.task_id, "COMPLETED" if batch_done else "PENDING"
                )
                jobs.append(
                    {
                        "agent_id": job.agent_id,
                        "task_id": job.task_id,
                        "batch_id": job.batch_id,
                        "status": _job_state(raw_state, batch_done),
                    }
                )

            tier_status = "COMPLETED" if batch_done else "PENDING"
            if any(job["status"] == "RUNNING" for job in jobs):
                tier_status = "RUNNING"
            elif any(job["status"] == "BLOCKED" for job in jobs):
                tier_status = "BLOCKED"

            tiers.append(
                {
                    "tier": _tier_index(batch.batch_id),
                    "batch_id": batch.batch_id,
                    "total_count": batch.total_count,
                    "completed_count": batch.completed_count,
                    "status": tier_status,
                    "jobs": jobs,
                }
            )

        tiers.sort(key=lambda tier: int(tier.get("tier", 0)))
        return tiers

    @property
    def active_count(self) -> int:
        """Number of currently running runners."""
        return self._active_count

    @property
    def queue_depth(self) -> int:
        """Number of jobs currently waiting for an available runner slot."""
        return self._queued_count

    # ------------------------------------------------------------------
    # Dispatch plan registration
    # ------------------------------------------------------------------

    def register_dispatch_plan(
        self,
        tiers: list[list[AgentJobEvent]],
        session_id: str,
    ) -> None:
        """Register an ordered list of dispatch tiers from AgentDispatchPlanner.

        Each tier is a list of jobs that can run in parallel.  Tiers execute
        sequentially (tier N+1 starts after all jobs in tier N complete).

        Args:
            tiers: Ordered tier list from ``AgentDispatchPlanner.plan()``.
            session_id: Orchestration session ID.
        """
        for i, tier_jobs in enumerate(tiers):
            if not tier_jobs:
                continue
            is_final = i == len(tiers) - 1
            batch_id = tier_jobs[0].batch_id  # planner assigns consistent batch_id per tier
            next_tier = tiers[i + 1] if not is_final else []
            self.batch_coordinator.register_batch(
                batch_id=batch_id,
                count=len(tier_jobs),
                session_id=session_id,
                current_jobs=tier_jobs,
                next_tier_jobs=next_tier,
                is_final_tier=is_final,
            )

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Consume AGENT_JOBS queue and dispatch to runners with semaphore throttle."""
        logger.info("SubAgentPool: consumer loop started")
        try:
            async for raw_message in self._broker.consume(AGENT_JOBS):
                if not self._running:
                    break
                try:
                    job = AgentJobEvent.model_validate_json(raw_message)
                except Exception as exc:
                    logger.warning(
                        "SubAgentPool: malformed job message: %s — %s", raw_message[:100], exc
                    )
                    continue

                # Acquire semaphore slot — blocks until a runner slot is free
                async with self._metrics_lock:
                    self._queued_count += 1

                try:
                    await self._semaphore.acquire()
                finally:
                    async with self._metrics_lock:
                        self._queued_count = max(0, self._queued_count - 1)

                # Spawn runner as background task; semaphore released in _run_job
                asyncio.create_task(self._run_job(job))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("SubAgentPool: consumer loop error: %s", exc)

    async def _run_job(self, job: AgentJobEvent) -> None:
        """Execute one job and release the semaphore when done."""
        runner_id = f"runner-{self._runner_counter:03d}"
        self._runner_counter += 1

        runner = SubAgentRunner(
            runner_id=runner_id,
            broker=self._broker,
            llm_client=self._llm,
            storage=self._storage,
            worker_pool=self._worker_pool,
            skill_registry=self._skill_registry,
            mcp_registry=self._mcp_registry,
            heartbeat_interval=self._heartbeat_interval,
            execution_timeout_seconds=self._execution_timeout_seconds,
            tool_timeout_seconds=self._tool_timeout_seconds,
            tool_max_retries=self._tool_max_retries,
            retry_backoff_base_ms=self._retry_backoff_base_ms,
            retry_backoff_max_ms=self._retry_backoff_max_ms,
            retryable_skills=self._retryable_skills,
            retryable_mcp_tools=self._retryable_mcp_tools,
        )
        self._active_runners[runner_id] = runner
        async with self._metrics_lock:
            self._active_count += 1

        try:
            await runner.execute(job)
        except Exception as exc:
            logger.exception("SubAgentPool: runner %s raised: %s", runner_id, exc)
        finally:
            async with self._metrics_lock:
                self._active_count = max(0, self._active_count - 1)
            self._active_runners.pop(runner_id, None)
            self._semaphore.release()

            # Notify BatchCoordinator and dispatch next tier if this tier is done
            tier_done, next_tier_jobs = await self.batch_coordinator.record_completion(job.batch_id)
            if tier_done and next_tier_jobs:
                logger.info(
                    "SubAgentPool: tier %s complete, dispatching %d next-tier jobs",
                    job.batch_id,
                    len(next_tier_jobs),
                )
                for next_job in next_tier_jobs:
                    try:
                        await self._broker.publish(AGENT_JOBS, next_job.model_dump_json())
                    except Exception as exc:
                        logger.warning("SubAgentPool: failed to dispatch next-tier job: %s", exc)
