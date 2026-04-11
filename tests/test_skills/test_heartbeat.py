"""tests.test_skills.test_heartbeat — Unit tests for graphclaw.skills.heartbeat.HeartbeatMonitor.

Description
-----------
Verifies that HeartbeatMonitor._check_heartbeats correctly identifies timed-out
workers, increments respawn counters, logs appropriate warning and error events,
and stops after max_respawn_attempts is reached.

Design Patterns
---------------
- Mock Pool: A simple stub object replaces WorkerPool; it returns a list of
  WorkerStatus instances that simulates different worker states and heartbeat
  ages.
- Capturing Logger: AsyncLogger is replaced with a MagicMock that records
  calls to log() for assertion.

Dependencies
------------
- pytest, pytest-asyncio: Async test runner.
- unittest.mock: MagicMock, patch, AsyncMock.
- datetime: timedelta and utcnow for constructing expired heartbeats.
- graphclaw.skills.heartbeat: HeartbeatMonitor under test.
- graphclaw.skills.models: HeartbeatConfig, ThreadState, WorkerStatus.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from graphclaw.skills.heartbeat import HeartbeatMonitor
from graphclaw.skills.models import HeartbeatConfig, ThreadState, WorkerStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired_heartbeat(timeout_seconds: float = 900.0) -> datetime:
    """Return a datetime that is timeout_seconds + 1 second in the past."""
    return _now() - timedelta(seconds=timeout_seconds + 1)


def _fresh_heartbeat() -> datetime:
    """Return a recent heartbeat timestamp (5 seconds ago)."""
    return _now() - timedelta(seconds=5)


def _make_mock_pool(statuses: list[WorkerStatus]) -> MagicMock:
    """Return a mock WorkerPool that returns the given list of statuses."""
    pool = MagicMock()
    pool.get_worker_statuses = MagicMock(return_value=statuses)
    return pool


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.log = MagicMock()
    return logger


def _make_config(
    interval: float = 300.0,
    timeout: float = 900.0,
    max_respawn: int = 3,
) -> HeartbeatConfig:
    return HeartbeatConfig(
        interval_seconds=interval,
        timeout_seconds=timeout,
        max_respawn_attempts=max_respawn,
    )


# ---------------------------------------------------------------------------
# _check_heartbeats — no timeout
# ---------------------------------------------------------------------------


async def test_check_heartbeats_no_timeout() -> None:
    """Workers with fresh heartbeats should not trigger any warnings."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.RUNNING,
            last_heartbeat=_fresh_heartbeat(),
        )
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    monitor = HeartbeatMonitor(pool=pool, config=_make_config(), logger=logger)

    await monitor._check_heartbeats()

    logger.log.assert_not_called()
    assert monitor.get_respawn_counts() == {}


async def test_check_heartbeats_idle_worker_not_checked() -> None:
    """Workers not in RUNNING state should not be checked for heartbeat timeout."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.COMPLETED,
            last_heartbeat=_expired_heartbeat(),
        ),
        WorkerStatus(
            worker_id="worker-001",
            state=ThreadState.SPAWNING,
            last_heartbeat=_expired_heartbeat(),
        ),
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    monitor = HeartbeatMonitor(pool=pool, config=_make_config(), logger=logger)

    await monitor._check_heartbeats()

    logger.log.assert_not_called()


async def test_check_heartbeats_none_heartbeat_skipped() -> None:
    """Workers with no last_heartbeat recorded should be skipped without error."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.RUNNING,
            last_heartbeat=None,
        )
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    monitor = HeartbeatMonitor(pool=pool, config=_make_config(), logger=logger)

    await monitor._check_heartbeats()

    logger.log.assert_not_called()


# ---------------------------------------------------------------------------
# _check_heartbeats — timeout detected
# ---------------------------------------------------------------------------


async def test_check_heartbeats_detects_timeout() -> None:
    """A RUNNING worker with an expired heartbeat should trigger a WARN log."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.RUNNING,
            last_heartbeat=_expired_heartbeat(900.0),
        )
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    monitor = HeartbeatMonitor(pool=pool, config=_make_config(), logger=logger)

    await monitor._check_heartbeats()

    logger.log.assert_called_once()
    call_args = logger.log.call_args
    level, event_type = call_args[0][0], call_args[0][1]
    assert level == "WARN"
    assert event_type == "heartbeat.timeout"


async def test_respawn_count_incremented() -> None:
    """Calling _check_heartbeats twice with a timed-out worker should increment count to 2."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.RUNNING,
            last_heartbeat=_expired_heartbeat(900.0),
        )
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    config = _make_config(max_respawn=5)
    monitor = HeartbeatMonitor(pool=pool, config=config, logger=logger)

    await monitor._check_heartbeats()
    await monitor._check_heartbeats()

    counts = monitor.get_respawn_counts()
    assert counts["worker-000"] == 2
    assert logger.log.call_count == 2


async def test_max_respawn_exceeded_logs_error() -> None:
    """Once max_respawn_attempts is reached, subsequent checks should log ERROR."""
    statuses = [
        WorkerStatus(
            worker_id="worker-bad",
            state=ThreadState.RUNNING,
            last_heartbeat=_expired_heartbeat(900.0),
        )
    ]
    pool = _make_mock_pool(statuses)
    logger = _make_logger()
    config = _make_config(max_respawn=2)
    monitor = HeartbeatMonitor(pool=pool, config=config, logger=logger)

    # First two calls increment count (1 then 2) — WARN
    await monitor._check_heartbeats()
    await monitor._check_heartbeats()

    # Third call: count is now at max, should log ERROR
    await monitor._check_heartbeats()

    calls = logger.log.call_args_list
    assert len(calls) == 3

    # First two should be WARN
    assert calls[0][0][0] == "WARN"
    assert calls[1][0][0] == "WARN"

    # Third should be ERROR
    assert calls[2][0][0] == "ERROR"
    assert calls[2][0][1] == "heartbeat.failed"


# ---------------------------------------------------------------------------
# get_respawn_counts
# ---------------------------------------------------------------------------


async def test_get_respawn_counts() -> None:
    """get_respawn_counts() should return an independent copy of the counter dict."""
    statuses = [
        WorkerStatus(
            worker_id="worker-000",
            state=ThreadState.RUNNING,
            last_heartbeat=_expired_heartbeat(900.0),
        )
    ]
    pool = _make_mock_pool(statuses)
    monitor = HeartbeatMonitor(pool=pool, config=_make_config())

    await monitor._check_heartbeats()

    counts = monitor.get_respawn_counts()
    assert isinstance(counts, dict)
    assert counts["worker-000"] == 1

    # Verify it is a copy, not the internal dict
    counts["worker-000"] = 999
    assert monitor._respawn_counts["worker-000"] == 1


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


async def test_stop_sets_running_false() -> None:
    """stop() should set _running to False so the loop exits."""
    pool = _make_mock_pool([])
    monitor = HeartbeatMonitor(pool=pool, config=_make_config(interval=0.01))
    monitor._running = True

    await monitor.stop()

    assert monitor._running is False


async def test_start_runs_and_stops() -> None:
    """start() should loop and exit cleanly when _running becomes False."""
    pool = _make_mock_pool([])
    config = _make_config(interval=0.01)
    monitor = HeartbeatMonitor(pool=pool, config=config)

    call_count = 0
    original_check = monitor._check_heartbeats

    async def counting_check():
        nonlocal call_count
        call_count += 1
        await original_check()
        if call_count >= 2:
            monitor._running = False

    monitor._check_heartbeats = counting_check

    await monitor.start()

    assert call_count >= 2
    assert monitor._running is False
