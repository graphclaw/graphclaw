# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.skills.worker — Async skill worker pool with priority dispatch.

Description
-----------
Provides two classes:

- ``SkillWorker``: Executes a single ``SkillJob`` by calling the injected
  ``LLMRouter``, updating its internal state machine, and returning a
  ``SkillResult``.
- ``WorkerPool``: Manages a fixed pool of ``SkillWorker`` instances, an
  ``asyncio.PriorityQueue`` for job dispatch, and exposes helpers for
  submitting jobs and querying worker status.

Design Patterns
---------------
- Worker Pool: A fixed set of ``SkillWorker`` coroutines handles concurrent
  LLM calls; new jobs are enqueued rather than spawning unlimited tasks.
- Priority Queue: Jobs are stored as ``(-priority, job)`` tuples so that
  higher-priority work is dispatched first.
- State Machine: Each ``SkillWorker`` transitions through
  ``SPAWNING → RUNNING → COMPLETED/FAILED/TIMED_OUT`` and resets to a
  waiting-like state (COMPLETED) between jobs.
- Dependency Injection: ``LLMRouter`` and ``AsyncLogger`` are injected at
  construction time, enabling unit tests with mock collaborators.

Public API
----------
- SkillWorker: Single async skill executor.
- SkillWorker.execute: Run one SkillJob and return a SkillResult.
- SkillWorker.status: Property returning a WorkerStatus snapshot.
- WorkerPool: Pool manager.
- WorkerPool.start: Initialise the worker pool.
- WorkerPool.stop: Tear down the pool.
- WorkerPool.submit: Enqueue a SkillJob for dispatch.
- WorkerPool.get_worker_statuses: Return status snapshots for all workers.
- WorkerPool.get_idle_worker: Return the first available worker, or None.

Dependencies
------------
- asyncio: PriorityQueue, TimeoutError.
- graphclaw.models.base: utcnow.
- graphclaw.skills.models: SkillDefinition, SkillJob, SkillResult,
  SkillStatus, ThreadState, WorkerStatus.
- graphclaw.skills.llm_router: LLMRouter (type hint only; injected).
- graphclaw.infra.logger: AsyncLogger (type hint only; injected).

Notes
-----
``WorkerPool.get_idle_worker`` considers a worker idle when its state is
``COMPLETED``, ``SPAWNING``, or ``WAITING`` — any state that is not actively
processing a job.  The ``FAILED`` and ``TIMED_OUT`` states are intentionally
excluded so that permanently broken workers are not re-used until
``HeartbeatMonitor`` respawns them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from graphclaw.models.base import utcnow
from graphclaw.skills.models import (
    SkillDefinition,
    SkillJob,
    SkillResult,
    SkillStatus,
    ThreadState,
    WorkerStatus,
)

if TYPE_CHECKING:
    from graphclaw.skills.llm_router import LLMRouter


logger = logging.getLogger(__name__)


class SkillWorker:
    """A single async worker that processes SkillJobs.

    Workers are created by ``WorkerPool.start()`` and should not be
    instantiated directly in application code.

    Args:
        worker_id: Unique string identifier for this worker (e.g. ``"worker-000"``).
        llm_router: ``LLMRouter`` instance used to dispatch LLM calls.
        logger: Optional ``AsyncLogger`` for structured event emission.
    """

    def __init__(
        self,
        worker_id: str,
        llm_router: LLMRouter,
    ) -> None:
        self._worker_id = worker_id
        self._llm_router = llm_router
        self._state: ThreadState = ThreadState.SPAWNING
        self._current_job: SkillJob | None = None
        self._last_heartbeat = utcnow()
        self._jobs_completed: int = 0
        self._jobs_failed: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def status(self) -> WorkerStatus:
        """Return a point-in-time status snapshot for this worker."""
        return WorkerStatus(
            worker_id=self._worker_id,
            state=self._state,
            current_job_id=self._current_job.job_id if self._current_job else None,
            last_heartbeat=self._last_heartbeat,
            jobs_completed=self._jobs_completed,
            jobs_failed=self._jobs_failed,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, job: SkillJob, skill: SkillDefinition) -> SkillResult:
        """Execute a single skill job.

        Transitions the worker state machine, calls the LLM router, and
        returns a ``SkillResult`` regardless of outcome.  The heartbeat
        timestamp is always updated in the ``finally`` block.

        Args:
            job: The ``SkillJob`` to execute.
            skill: The ``SkillDefinition`` describing the LLM call parameters.

        Returns:
            A ``SkillResult`` with ``status`` set to ``COMPLETED``, ``FAILED``,
            or ``TIMEOUT`` depending on the outcome.
        """
        self._state = ThreadState.RUNNING
        self._current_job = job
        started_at = utcnow()
        started_monotonic = time.monotonic()
        logger.debug(
            "skill.job.started",
            extra={
                "event_type": "skill.job.started",
                "worker_id": self._worker_id,
                "job_id": job.job_id,
                "skill_name": job.skill_name,
                "task_id": job.task_id,
                "session_id": job.session_id,
                "timeout_seconds": job.timeout_seconds,
            },
        )

        try:
            response = await asyncio.wait_for(
                self._llm_router.complete(
                    model=skill.model,
                    system_prompt=skill.system_prompt,
                    user_message=str(job.input_data),
                    max_tokens=skill.max_tokens,
                    temperature=skill.temperature,
                ),
                timeout=job.timeout_seconds,
            )

            self._jobs_completed += 1
            self._state = ThreadState.COMPLETED
            completed_at = utcnow()
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            logger.info(
                "skill.job.completed",
                extra={
                    "event_type": "skill.job.completed",
                    "worker_id": self._worker_id,
                    "job_id": job.job_id,
                    "skill_name": job.skill_name,
                    "task_id": job.task_id,
                    "session_id": job.session_id,
                    "duration_ms": duration_ms,
                    "tokens_used": response.get("tokens_used", 0),
                    "cost_usd": response.get("cost_usd", 0.0),
                },
            )
            return SkillResult(
                job_id=job.job_id,
                skill_name=job.skill_name,
                task_id=job.task_id,
                session_id=job.session_id,
                status=SkillStatus.COMPLETED,
                output=response.get("content", ""),
                started_at=started_at,
                completed_at=completed_at,
                tokens_used=response.get("tokens_used", 0),
                cost_usd=response.get("cost_usd", 0.0),
            )

        except (TimeoutError, asyncio.TimeoutError):
            self._jobs_failed += 1
            self._state = ThreadState.TIMED_OUT
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            logger.warning(
                "skill.job.timeout",
                extra={
                    "event_type": "skill.job.timeout",
                    "worker_id": self._worker_id,
                    "job_id": job.job_id,
                    "skill_name": job.skill_name,
                    "task_id": job.task_id,
                    "session_id": job.session_id,
                    "duration_ms": duration_ms,
                    "timeout_seconds": job.timeout_seconds,
                },
            )
            return SkillResult(
                job_id=job.job_id,
                skill_name=job.skill_name,
                task_id=job.task_id,
                session_id=job.session_id,
                status=SkillStatus.TIMEOUT,
                error="Execution timed out",
                started_at=started_at,
                completed_at=utcnow(),
            )

        except Exception as exc:
            self._jobs_failed += 1
            self._state = ThreadState.FAILED
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            logger.warning(
                "skill.job.failed",
                extra={
                    "event_type": "skill.job.failed",
                    "worker_id": self._worker_id,
                    "job_id": job.job_id,
                    "skill_name": job.skill_name,
                    "task_id": job.task_id,
                    "session_id": job.session_id,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                },
            )
            return SkillResult(
                job_id=job.job_id,
                skill_name=job.skill_name,
                task_id=job.task_id,
                session_id=job.session_id,
                status=SkillStatus.FAILED,
                error=str(exc),
                started_at=started_at,
                completed_at=utcnow(),
            )

        finally:
            self._current_job = None
            self._last_heartbeat = utcnow()


class WorkerPool:
    """Manages a pool of SkillWorkers with priority-based job dispatch.

    The pool pre-allocates a fixed number of workers at ``start()`` time.
    Jobs submitted via ``submit()`` are placed in a ``PriorityQueue`` and can
    be dispatched to idle workers using ``get_idle_worker()``.

    Args:
        pool_size: Number of concurrent workers to maintain (default 4).
        llm_router: ``LLMRouter`` instance shared by all workers.
        logger: Optional ``AsyncLogger`` for structured event emission.

    Usage::

        pool = WorkerPool(pool_size=4, llm_router=LLMRouter())
        await pool.start()
        await pool.submit(job)
        worker = pool.get_idle_worker()
        if worker:
            result = await worker.execute(job, skill)
        await pool.stop()
    """

    def __init__(
        self,
        pool_size: int = 4,
        llm_router: LLMRouter | None = None,
    ) -> None:
        self._pool_size = pool_size
        self._llm_router = llm_router
        self._workers: dict[str, SkillWorker] = {}
        self._job_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise the worker pool.

        Creates ``pool_size`` ``SkillWorker`` instances, each identified by
        ``worker-NNN``.  Safe to call once; subsequent calls before ``stop()``
        replace the existing workers.
        """
        self._running = True
        self._workers = {}
        for i in range(self._pool_size):
            wid = f"worker-{i:03d}"
            self._workers[wid] = SkillWorker(
                worker_id=wid,
                llm_router=self._llm_router,
            )
        logger.info(
            "worker.pool.started",
            extra={
                "event_type": "worker.pool.started",
                "pool_size": self._pool_size,
            },
        )

    async def stop(self) -> None:
        """Tear down the worker pool.

        Sets the running flag to ``False`` and removes all worker references.
        In-flight jobs are not cancelled; callers should drain the queue
        before calling ``stop()`` if graceful shutdown is required.
        """
        self._running = False
        self._workers = {}
        logger.info(
            "worker.pool.stopped",
            extra={
                "event_type": "worker.pool.stopped",
                "pool_size": self._pool_size,
            },
        )

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    async def submit(self, job: SkillJob) -> None:
        """Enqueue a job in the priority queue.

        Higher ``job.priority`` values are dispatched first.  The queue uses
        ``(-priority, job)`` tuples so that Python's min-heap behaviour yields
        the highest-priority item first.

        Args:
            job: The ``SkillJob`` to enqueue.
        """
        await self._job_queue.put((-job.priority, job))
        logger.debug(
            "worker.pool.job_enqueued",
            extra={
                "event_type": "worker.pool.job_enqueued",
                "job_id": job.job_id,
                "task_id": job.task_id,
                "skill_name": job.skill_name,
                "session_id": job.session_id,
                "priority": job.priority,
            },
        )

    # ------------------------------------------------------------------
    # Worker queries
    # ------------------------------------------------------------------

    def get_worker_statuses(self) -> list[WorkerStatus]:
        """Return a list of status snapshots for all workers in the pool.

        Returns:
            One ``WorkerStatus`` per worker, in insertion order.
        """
        return [w.status for w in self._workers.values()]

    def get_idle_worker(self) -> SkillWorker | None:
        """Return the first worker that is not actively processing a job.

        A worker is considered idle when its state is ``COMPLETED``,
        ``SPAWNING``, or ``WAITING``.

        Returns:
            The first idle ``SkillWorker``, or ``None`` if all workers are busy.
        """
        _IDLE_STATES = {ThreadState.COMPLETED, ThreadState.SPAWNING, ThreadState.WAITING}
        for worker in self._workers.values():
            if worker._state in _IDLE_STATES:
                return worker
        return None
