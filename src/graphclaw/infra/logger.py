"""graphclaw.infra.logger — AsyncLogger: non-blocking structured JSON logger.

Description
-----------
Provides ``AsyncLogger``, a high-throughput structured logger that buffers
log entries in an in-process ``asyncio.Queue`` and flushes them to stdout
as JSON lines on a background task.  The design prioritises non-blocking
writes: if the buffer is full, entries are silently dropped rather than
blocking the caller.  A ``generate_session_id`` helper creates distributed
tracing session IDs in the ``SES-{uuid4}`` format.

Design Patterns
---------------
- Producer/Consumer: Application code calls ``log()`` (producer) without
  waiting; a background ``_flush_loop`` task drains the queue (consumer).
- Template Method: ``_write_batch`` can be overridden in tests to capture
  output without touching stdout.

Public API
----------
- AsyncLogger: Non-blocking buffered structured logger.
- AsyncLogger.log: Enqueue a structured log entry (non-blocking).
- AsyncLogger.start: Start the background flush loop.
- AsyncLogger.stop: Gracefully drain the queue and stop the flush loop.
- generate_session_id: Generate a ``SES-{uuid4}`` session identifier.

Dependencies
------------
- asyncio: Queue, Task, sleep.
- json: Serialise log entries to JSON lines.
- sys: Write to stdout.
- uuid: Session ID generation.
- graphclaw.models.base: utcnow for ISO-8601 timestamps.

Notes
-----
All timestamps use timezone.utc.  The ``session_id`` field enables distributed
tracing across services: generate one session ID per inbound request or
agent cycle and propagate it through all log calls.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from graphclaw.models.base import utcnow


def generate_session_id() -> str:
    """Generate a session ID in the format ``SES-{uuid4}``.

    Returns:
        A unique session identifier string, e.g. ``"SES-3f2504e0-..."``.
    """
    return f"SES-{uuid.uuid4()}"


class AsyncLogger:
    """Non-blocking structured JSON logger with an async flush loop.

    Log entries are placed in an in-memory queue and written to stdout
    as newline-delimited JSON by a background task.  If the queue is
    full, new entries are dropped to avoid blocking callers.

    Args:
        service_name: Identifying name embedded in every log entry.
        buffer_size: Maximum number of queued entries before drops occur.
    """

    def __init__(
        self,
        service_name: str,
        buffer_size: int = 10_000,
    ) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=buffer_size)
        self._service_name = service_name
        self._flush_interval: float = 1.0
        self._flush_batch_size: int = 100
        self._running: bool = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        level: str,
        event_type: str,
        session_id: str,
        **fields: object,
    ) -> None:
        """Enqueue a structured log entry (non-blocking).

        If the internal queue is full, the entry is silently dropped —
        this method never blocks the calling coroutine.

        Args:
            level: Log severity (e.g. ``"INFO"``, ``"ERROR"``).
            event_type: Short event classifier (e.g. ``"task.scored"``).
            session_id: Distributed tracing session identifier.
            **fields: Additional key/value pairs to include in the entry.
        """
        entry: dict = {
            "timestamp": utcnow().isoformat(),
            "level": level.upper(),
            "service": self._service_name,
            "event_type": event_type,
            "session_id": session_id,
            **fields,
        }
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass  # Never block — drop the entry silently.

    async def start(self) -> None:
        """Start the background flush loop task.

        Safe to call multiple times; subsequent calls are no-ops if
        already running.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._flush_loop(), name="async-logger-flush")

    async def stop(self) -> None:
        """Gracefully stop the flush loop and flush any remaining entries.

        Signals the loop to stop, waits for the task to finish, then
        performs a final drain of the queue.
        """
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Final drain.
        await self._drain_remaining()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Background task: flush the queue every second or per batch."""
        while self._running:
            batch: list[dict] = []
            deadline = asyncio.get_event_loop().time() + self._flush_interval
            while len(batch) < self._flush_batch_size:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    entry = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(entry)
                except TimeoutError:
                    break
            if batch:
                await self._write_batch(batch)

    async def _drain_remaining(self) -> None:
        """Flush all entries still in the queue (called during shutdown)."""
        batch: list[dict] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._write_batch(batch)

    async def _write_batch(self, batch: list[dict]) -> None:
        """Serialise *batch* to newline-delimited JSON and write to stdout.

        Args:
            batch: List of structured log entry dicts to write.
        """
        lines = "\n".join(json.dumps(entry, default=str) for entry in batch) + "\n"
        sys.stdout.write(lines)
        sys.stdout.flush()
