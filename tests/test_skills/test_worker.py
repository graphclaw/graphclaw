# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_skills.test_worker — Unit tests for SkillWorker and WorkerPool.

Description
-----------
Verifies the SkillWorker state machine transitions (RUNNING → COMPLETED /
FAILED / TIMED_OUT), the WorkerPool lifecycle (start/stop, worker creation),
job submission via the priority queue, and the get_idle_worker helper.

Design Patterns
---------------
- Mock Collaborators: LLMRouter and AsyncLogger are replaced with MagicMock /
  AsyncMock objects; no real LLM calls are made.
- Arrange/Act/Assert: Each test sets up a worker or pool, executes the
  operation under test, and asserts state and return values.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: AsyncMock, MagicMock.
- datetime: Timestamps for SkillJob creation.
- graphclaw.skills.worker: SkillWorker, WorkerPool.
- graphclaw.skills.models: SkillDefinition, SkillJob, SkillStatus, ThreadState, WorkerStatus.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.skills.models import (
    SkillDefinition,
    SkillJob,
    SkillStatus,
    ThreadState,
)
from graphclaw.skills.worker import SkillWorker, WorkerPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _make_router(content: str = "LLM output", tokens: int = 50) -> MagicMock:
    """Return an AsyncMock llm_router whose complete() returns a preset dict."""
    router = MagicMock()
    router.complete = AsyncMock(
        return_value={
            "content": content,
            "tokens_used": tokens,
            "cost_usd": 0.001,
            "model": "test-model",
        }
    )
    return router


def _make_skill(timeout_seconds: int = 300) -> SkillDefinition:
    return SkillDefinition(
        name="test-skill",
        system_prompt="You are helpful.",
        timeout_seconds=timeout_seconds,
    )


def _make_job(priority: int = 0, timeout_seconds: int = 300) -> SkillJob:
    return SkillJob(
        job_id="job-001",
        skill_name="test-skill",
        task_id="TSK-AB-0001-ATM",
        session_id="SES-abc",
        created_at=_utc(2026, 3, 18, 10, 0),
        priority=priority,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# SkillWorker.execute — success
# ---------------------------------------------------------------------------


async def test_worker_execute_success(caplog: pytest.LogCaptureFixture) -> None:
    """execute() should return a COMPLETED SkillResult on LLM success."""
    router = _make_router(content="Summary result", tokens=42)
    worker = SkillWorker(worker_id="worker-000", llm_router=router)
    job = _make_job()
    skill = _make_skill()

    with caplog.at_level(logging.DEBUG, logger="graphclaw.skills.worker"):
        result = await worker.execute(job, skill)

    assert result.status == SkillStatus.COMPLETED
    assert result.output == "Summary result"
    assert result.error is None
    assert result.tokens_used == 42
    assert result.job_id == "job-001"
    assert result.skill_name == "test-skill"
    assert any(
        r.__dict__.get("event_type") == "skill.job.started"
        and r.__dict__.get("job_id") == "job-001"
        for r in caplog.records
    )
    assert any(
        r.__dict__.get("event_type") == "skill.job.completed"
        and r.__dict__.get("job_id") == "job-001"
        for r in caplog.records
    )


async def test_worker_execute_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """execute() should return a TIMEOUT SkillResult when the LLM call times out."""
    router = MagicMock()

    async def slow_complete(*args, **kwargs):
        await asyncio.sleep(999)

    router.complete = slow_complete
    worker = SkillWorker(worker_id="worker-001", llm_router=router)
    job = _make_job(timeout_seconds=1)  # 1-second timeout
    skill = _make_skill(timeout_seconds=1)

    with caplog.at_level(logging.WARNING, logger="graphclaw.skills.worker"):
        result = await worker.execute(job, skill)

    assert result.status == SkillStatus.TIMEOUT
    assert result.error == "Execution timed out"
    assert result.output == ""
    assert any(
        r.__dict__.get("event_type") == "skill.job.timeout"
        and r.__dict__.get("job_id") == "job-001"
        for r in caplog.records
    )


async def test_worker_execute_failure(caplog: pytest.LogCaptureFixture) -> None:
    """execute() should return a FAILED SkillResult when the LLM call raises."""
    router = MagicMock()
    router.complete = AsyncMock(side_effect=RuntimeError("API failure"))
    worker = SkillWorker(worker_id="worker-002", llm_router=router)
    job = _make_job()
    skill = _make_skill()

    with caplog.at_level(logging.WARNING, logger="graphclaw.skills.worker"):
        result = await worker.execute(job, skill)

    assert result.status == SkillStatus.FAILED
    assert "API failure" in result.error
    assert result.output == ""
    assert any(
        r.__dict__.get("event_type") == "skill.job.failed" and r.__dict__.get("job_id") == "job-001"
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# SkillWorker.status — after execution
# ---------------------------------------------------------------------------


async def test_worker_status_after_success() -> None:
    """After a successful execute(), the worker state should be COMPLETED."""
    router = _make_router()
    worker = SkillWorker(worker_id="worker-000", llm_router=router)

    await worker.execute(_make_job(), _make_skill())

    status = worker.status
    assert status.state == ThreadState.COMPLETED
    assert status.jobs_completed == 1
    assert status.jobs_failed == 0
    assert status.current_job_id is None
    assert status.last_heartbeat is not None


async def test_worker_status_after_failure() -> None:
    """After a failed execute(), the worker state should be FAILED."""
    router = MagicMock()
    router.complete = AsyncMock(side_effect=Exception("boom"))
    worker = SkillWorker(worker_id="worker-003", llm_router=router)

    await worker.execute(_make_job(), _make_skill())

    status = worker.status
    assert status.state == ThreadState.FAILED
    assert status.jobs_completed == 0
    assert status.jobs_failed == 1


async def test_worker_status_initial_state() -> None:
    """A freshly created SkillWorker should be in SPAWNING state."""
    worker = SkillWorker(worker_id="worker-init", llm_router=MagicMock())

    status = worker.status
    assert status.state == ThreadState.SPAWNING
    assert status.jobs_completed == 0
    assert status.jobs_failed == 0
    assert status.current_job_id is None


# ---------------------------------------------------------------------------
# WorkerPool lifecycle
# ---------------------------------------------------------------------------


async def test_pool_start_creates_workers(caplog: pytest.LogCaptureFixture) -> None:
    """start() should create pool_size workers accessible by worker ID."""
    pool = WorkerPool(pool_size=3, llm_router=_make_router())
    with caplog.at_level(logging.INFO, logger="graphclaw.skills.worker"):
        await pool.start()

    assert len(pool._workers) == 3
    assert "worker-000" in pool._workers
    assert "worker-001" in pool._workers
    assert "worker-002" in pool._workers
    assert any(r.__dict__.get("event_type") == "worker.pool.started" for r in caplog.records)

    await pool.stop()


async def test_pool_stop_clears_workers(caplog: pytest.LogCaptureFixture) -> None:
    """stop() should clear the workers dict and set _running to False."""
    pool = WorkerPool(pool_size=2, llm_router=_make_router())
    await pool.start()
    with caplog.at_level(logging.INFO, logger="graphclaw.skills.worker"):
        await pool.stop()

    assert len(pool._workers) == 0
    assert pool._running is False
    assert any(r.__dict__.get("event_type") == "worker.pool.stopped" for r in caplog.records)


async def test_pool_get_worker_statuses() -> None:
    """get_worker_statuses() should return one WorkerStatus per worker."""
    pool = WorkerPool(pool_size=4, llm_router=_make_router())
    await pool.start()

    statuses = pool.get_worker_statuses()
    assert len(statuses) == 4
    worker_ids = {s.worker_id for s in statuses}
    assert worker_ids == {"worker-000", "worker-001", "worker-002", "worker-003"}

    await pool.stop()


# ---------------------------------------------------------------------------
# WorkerPool.submit and get_idle_worker
# ---------------------------------------------------------------------------


async def test_pool_submit_enqueues_job(caplog: pytest.LogCaptureFixture) -> None:
    """submit() should place the job in the priority queue."""
    pool = WorkerPool(pool_size=2, llm_router=_make_router())
    await pool.start()

    job = _make_job(priority=3)
    with caplog.at_level(logging.DEBUG, logger="graphclaw.skills.worker"):
        await pool.submit(job)

    assert pool._job_queue.qsize() == 1

    # The queue stores (-priority, job) tuples
    priority_val, dequeued_job = await pool._job_queue.get()
    assert priority_val == -3
    assert dequeued_job.job_id == "job-001"
    assert any(
        r.__dict__.get("event_type") == "worker.pool.job_enqueued"
        and r.__dict__.get("job_id") == "job-001"
        for r in caplog.records
    )

    await pool.stop()


async def test_pool_submit_and_get_idle() -> None:
    """After start, all workers should be idle (SPAWNING state)."""
    pool = WorkerPool(pool_size=2, llm_router=_make_router())
    await pool.start()

    worker = pool.get_idle_worker()
    assert worker is not None
    assert worker._state == ThreadState.SPAWNING

    await pool.stop()


async def test_pool_get_idle_returns_none_when_all_busy() -> None:
    """get_idle_worker() should return None when all workers are in RUNNING state."""
    pool = WorkerPool(pool_size=2, llm_router=_make_router())
    await pool.start()

    # Manually force all workers into RUNNING state
    for w in pool._workers.values():
        w._state = ThreadState.RUNNING

    assert pool.get_idle_worker() is None

    await pool.stop()


async def test_pool_priority_ordering() -> None:
    """Higher-priority jobs should be dequeued before lower-priority ones."""
    pool = WorkerPool(pool_size=1, llm_router=_make_router())
    await pool.start()

    low_job = SkillJob(
        job_id="low",
        skill_name="s",
        task_id="TSK-AB-0001-ATM",
        session_id="SES-1",
        created_at=_utc(2026, 3, 18, 9, 0),
        priority=1,
    )
    high_job = SkillJob(
        job_id="high",
        skill_name="s",
        task_id="TSK-AB-0002-ATM",
        session_id="SES-2",
        created_at=_utc(2026, 3, 18, 9, 0),
        priority=10,
    )

    await pool.submit(low_job)
    await pool.submit(high_job)

    _, first = await pool._job_queue.get()
    assert first.job_id == "high"

    await pool.stop()
