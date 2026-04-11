"""graphclaw.skills.heartbeat — Worker heartbeat monitor with auto-respawn.

Description
-----------
Provides ``HeartbeatMonitor``, which runs a background async loop that
periodically inspects every worker in a ``WorkerPool``.  If a worker has been
in ``RUNNING`` state for longer than ``HeartbeatConfig.timeout_seconds``
without updating its ``last_heartbeat`` timestamp, the monitor increments a
respawn counter for that worker and logs a warning.  Once a worker's respawn
counter exceeds ``HeartbeatConfig.max_respawn_attempts``, an error is logged
indicating the worker is permanently unresponsive.

Design Patterns
---------------
- Observer: The monitor polls worker state on a fixed interval rather than
  receiving push notifications, keeping the worker implementation simple.
- Strategy: The respawn action is currently limited to logging; a concrete
  respawn implementation (e.g. replacing the worker object in the pool) can
  be added without changing the public interface.
- Dependency Injection: ``WorkerPool`` and ``AsyncLogger`` are injected at
  construction time for testability.

Public API
----------
- HeartbeatMonitor: Monitors worker heartbeats and logs timeout events.
- HeartbeatMonitor.start: Begin the heartbeat monitoring loop.
- HeartbeatMonitor.stop: Stop the monitoring loop.
- HeartbeatMonitor.get_respawn_counts: Return per-worker respawn attempt counts.

Dependencies
------------
- asyncio: sleep for the monitoring interval.
- datetime: timedelta for timeout comparison.
- graphclaw.models.base: utcnow.
- graphclaw.skills.models: HeartbeatConfig, ThreadState, WorkerStatus.
- graphclaw.skills.worker: WorkerPool (type hint only; injected).
- graphclaw.infra.logger: AsyncLogger (type hint only; injected).

Notes
-----
The loop runs until ``_running`` is set to ``False`` by ``stop()``.  Unlike
``TriggerEngine``, the monitor does not create an ``asyncio.Task`` internally:
callers are expected to wrap ``start()`` in ``asyncio.create_task()`` if they
want it to run concurrently.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from typing import TYPE_CHECKING

from graphclaw.models.base import utcnow
from graphclaw.skills.models import HeartbeatConfig, ThreadState

if TYPE_CHECKING:
    from graphclaw.infra.logger import AsyncLogger
    from graphclaw.skills.worker import WorkerPool


class HeartbeatMonitor:
    """Monitors worker heartbeats and respawns dead workers.

    Args:
        pool: ``WorkerPool`` whose workers will be monitored.
        config: ``HeartbeatConfig`` tuning parameters.  Defaults to the
            standard 5-minute interval / 15-minute timeout / 3-attempt config.
        logger: Optional ``AsyncLogger`` for structured warning/error events.

    Usage::

        monitor = HeartbeatMonitor(pool=pool, logger=logger)
        task = asyncio.create_task(monitor.start())
        # … runtime …
        await monitor.stop()
        task.cancel()
    """

    def __init__(
        self,
        pool: WorkerPool,
        config: HeartbeatConfig | None = None,
        logger: AsyncLogger | None = None,
    ) -> None:
        self._pool = pool
        self._config = config or HeartbeatConfig()
        self._logger = logger
        self._running: bool = False
        self._respawn_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the heartbeat monitoring loop.

        Runs until ``stop()`` is called.  Checks all workers every
        ``config.interval_seconds`` seconds.
        """
        self._running = True
        while self._running:
            await self._check_heartbeats()
            await asyncio.sleep(self._config.interval_seconds)

    async def stop(self) -> None:
        """Signal the heartbeat loop to exit on its next iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    def get_respawn_counts(self) -> dict[str, int]:
        """Return a copy of the per-worker respawn attempt counter.

        Returns:
            A dict mapping ``worker_id`` to the number of respawn attempts
            that have been recorded for that worker.
        """
        return dict(self._respawn_counts)

    # ------------------------------------------------------------------
    # Internal logic
    # ------------------------------------------------------------------

    async def _check_heartbeats(self) -> None:
        """Inspect all workers for missed heartbeats.

        For each worker in ``RUNNING`` state whose ``last_heartbeat`` is older
        than ``config.timeout_seconds``:

        - If respawn attempts are below the maximum, increment the counter and
          log a ``WARN`` event.
        - If the maximum has been reached, log an ``ERROR`` event.
        """
        now = utcnow()
        timeout_delta = timedelta(seconds=self._config.timeout_seconds)

        for worker_status in self._pool.get_worker_statuses():
            if worker_status.last_heartbeat is None:
                continue

            if worker_status.state != ThreadState.RUNNING:
                continue

            elapsed = now - worker_status.last_heartbeat
            if elapsed <= timeout_delta:
                continue

            # Worker has timed out — record and log.
            worker_id = worker_status.worker_id
            attempts = self._respawn_counts.get(worker_id, 0)

            if attempts < self._config.max_respawn_attempts:
                self._respawn_counts[worker_id] = attempts + 1
                if self._logger:
                    self._logger.log(
                        "WARN",
                        "heartbeat.timeout",
                        "",
                        worker_id=worker_id,
                        attempt=attempts + 1,
                        elapsed_seconds=elapsed.total_seconds(),
                    )
            else:
                if self._logger:
                    self._logger.log(
                        "ERROR",
                        "heartbeat.failed",
                        "",
                        worker_id=worker_id,
                        message="Max respawn attempts exceeded",
                        attempts=attempts,
                    )
