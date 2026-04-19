"""graphclaw.infra.logger — AsyncLogger: non-blocking structured JSON logger.

Description
-----------
Provides ``AsyncLogger``, a high-throughput structured logger that buffers
log entries in an in-process ``asyncio.Queue`` and flushes them to stdout
as JSON lines on a background task.  The design prioritises non-blocking
writes: if the buffer is full, entries are silently dropped rather than
blocking the caller.  A ``generate_session_id`` helper creates distributed
tracing session IDs in the ``SES-{uuid4}`` format.

Optionally writes filtered logs (min_level and above) to a durable S3/MinIO
sink organized by user_id, service, and datetime for long-term audit trails.

Design Patterns
---------------
- Producer/Consumer: Application code calls ``log()`` (producer) without
  waiting; a background ``_flush_loop`` task drains the queue (consumer).
- Template Method: ``_write_batch`` can be overridden in tests to capture
  output without touching stdout.
- PII-Safe Events: Structured log event classes with explicit field allowlists
  prevent accidental logging of sensitive data (message bodies, user content).

Public API
----------
- AsyncLogger: Non-blocking buffered structured logger.
- AsyncLogger.log: Enqueue a structured log entry (non-blocking).
- AsyncLogger.create: Class factory with storage/user_id support.
- AsyncLogger.start: Start the background flush loop.
- AsyncLogger.stop: Gracefully drain the queue and stop the flush loop.
- generate_session_id: Generate a ``SES-{uuid4}`` session identifier.
- PII-safe event classes: AgentToolCallEvent, AgentMessageEvent, etc.

Dependencies
------------
- asyncio: Queue, Task, sleep.
- datetime: datetime for hourly log file timestamps.
- json: Serialise log entries to JSON lines.
- sys: Write to stdout.
- uuid: Session ID generation.
- pydantic: BaseModel for PII-safe event validation.
- graphclaw.models.base: utcnow for ISO-8601 timestamps.
- graphclaw.infra.storage: StorageClient for durable log sink (optional).

Notes
-----
All timestamps use timezone.utc.  The ``session_id`` field enables distributed
tracing across services: generate one session ID per inbound request or
agent cycle and propagate it through all log calls.

The durable log sink (when enabled) uses read-modify-write since MinIO/S3
don't support append operations. Sink failures never crash the logger.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel

from graphclaw.infra.sinks import LogSink, ObjectStorageSink, StdoutSink
from graphclaw.models.base import utcnow

if TYPE_CHECKING:
    from graphclaw.infra.storage import StorageClient


def generate_session_id() -> str:
    """Generate a session ID in the format ``SES-{uuid4}``.

    Returns:
        A unique session identifier string, e.g. ``"SES-3f2504e0-..."``.
    """
    return f"SES-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# PII-Safe Log Event Models
# ---------------------------------------------------------------------------
# These models enforce explicit field allowlists to prevent accidental logging
# of sensitive data (message bodies, user content, tool arguments, etc.).
# ---------------------------------------------------------------------------


class AgentToolCallEvent(BaseModel):
    """Log event for an agent tool invocation (no args/body allowed)."""

    tool_name: str
    user_id: str
    latency_ms: int


class AgentMessageEvent(BaseModel):
    """Log event for an agent LLM message (no content allowed)."""

    user_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class AgentScoringCycleEvent(BaseModel):
    """Log event for a scoring cycle completion."""

    user_id: str
    tasks_scored: int
    top_task_id: str | None
    queue_depth: int


class InboundProcessedEvent(BaseModel):
    """Log event for an inbound message processing (no body/subject allowed)."""

    message_id: str
    channel: str
    task_id: str | None
    signal: str | None
    matched_by: str | None


class IntelligenceUpdateEvent(BaseModel):
    """Log event for intelligence layer update action (no text allowed)."""

    task_id: str | None
    channel: str
    direction: str  # "inbound" | "outbound"
    action_taken: str


class OutboundSentEvent(BaseModel):
    """Log event for an outbound message sent (no body allowed)."""

    task_id: str | None
    channel: str
    recipient_hashed: str
    subject_length: int


class MCPActionEvent(BaseModel):
    """Audit log event for an MCP tool call.

    No tool arguments or response content are logged — only the metadata
    needed to reconstruct who called what, when, and whether it succeeded.
    """

    user_id: str
    server_id: str
    server_name: str
    tool_name: str
    success: bool
    latency_ms: int
    task_id: str | None = None  # associated task context if known


# ---------------------------------------------------------------------------
# Phase 5 — Sub-agent orchestration audit events
# All events carry agent_id + task_id for bi-directional audit queries.
# ---------------------------------------------------------------------------


class AgentTaskStartedEvent(BaseModel):
    """Audit event: sub-agent picked up a delegated task and started execution."""

    agent_id: str
    task_id: str
    session_id: str
    parent_task_id: str | None = None
    batch_id: str = ""


class AgentTaskProgressEvent(BaseModel):
    """Audit event: sub-agent reported an intermediate progress update."""

    agent_id: str
    task_id: str
    session_id: str
    message: str
    iteration: int = 0


class AgentTaskCompletedEvent(BaseModel):
    """Audit event: sub-agent finished executing a delegated task."""

    agent_id: str
    task_id: str
    session_id: str
    status: str  # COMPLETED | FAILED | TIMED_OUT
    duration_ms: int
    parent_task_id: str | None = None
    batch_id: str = ""


class AgentTaskBlockedEvent(BaseModel):
    """Audit event: sub-agent encountered a blocker or heartbeat timeout."""

    agent_id: str
    task_id: str
    session_id: str
    reason: str


class AgentHeartbeatEvent(BaseModel):
    """Audit event: sub-agent liveness pulse (emitted every heartbeat interval)."""

    agent_id: str
    task_id: str
    session_id: str


class AsyncLogger:
    """Non-blocking structured logger with async queueing and sink fan-out.

    Log entries are buffered in an in-memory queue and flushed in batches to
    one or more configured sinks (stdout, object storage, CloudWatch, etc.).
    If the queue is full, new entries are dropped to avoid blocking callers.

    Args:
        service_name: Identifying name embedded in every log entry.
        buffer_size: Maximum number of queued entries before drops occur.
        sinks: Optional explicit sink list. If omitted, stdout is always used.
        storage: Optional storage used by default sink wiring.
        user_id: Optional user_id used by default storage sink wiring.
        min_level: Minimum level applied to durable default sinks.
        log_format: Output format (``"jsonl"`` or ``"pipe"``).
    """

    def __init__(
        self,
        service_name: str,
        buffer_size: int = 10_000,
        sinks: list[LogSink] | None = None,
        storage: StorageClient | None = None,
        user_id: str | None = None,
        min_level: str = "INFO",
        log_format: str = "jsonl",
    ) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=buffer_size)
        self._service_name = service_name
        self._flush_interval: float = 1.0
        self._flush_batch_size: int = 100
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._sinks: list[LogSink] = sinks or self._build_default_sinks(
            storage=storage,
            user_id=user_id,
            min_level=min_level,
            log_format=log_format,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        service: str,
        sinks: list[LogSink] | None = None,
        storage: StorageClient | None = None,
        user_id: str | None = None,
        min_level: str = "INFO",
        log_format: str = "jsonl",
    ) -> AsyncLogger:
        """Class factory for creating AsyncLogger instances.

        Args:
            service: Service name embedded in every log entry.
            sinks: Optional explicit sink list.
            storage: Optional StorageClient for durable log sink.
            user_id: Optional user_id for per-user log paths.
            min_level: Minimum level for durable sink (DEBUG goes stdout only).
            log_format: Output format (``"jsonl"`` or ``"pipe"``).

        Returns:
            Configured AsyncLogger instance.
        """
        return cls(
            service_name=service,
            sinks=sinks,
            storage=storage,
            user_id=user_id,
            min_level=min_level,
            log_format=log_format,
        )

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
            "timestamp": self._format_timestamp(utcnow()),
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
        for sink in self._sinks:
            try:
                await sink.start()
            except Exception:
                continue
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
        for sink in self._sinks:
            try:
                await sink.stop()
            except Exception:
                continue

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
        """Fan out the batch to all configured sinks, best effort."""
        if not self._sinks:
            return

        results = await asyncio.gather(
            *(sink.write_batch(batch) for sink in self._sinks),
            return_exceptions=True,
        )

        # Swallow sink failures; logger is best-effort and never blocks callers.
        _ = results

    def _build_default_sinks(
        self,
        storage: StorageClient | None,
        user_id: str | None,
        min_level: str,
        log_format: str,
    ) -> list[LogSink]:
        sinks: list[LogSink] = [StdoutSink(log_format=log_format)]
        if storage is not None:
            sinks.append(
                ObjectStorageSink(
                    storage=storage,
                    min_level=min_level,
                    log_format=log_format,
                )
            )
        # user_id is reserved for explicit sink wiring at composition points.
        _ = user_id
        return sinks

    @staticmethod
    def _format_timestamp(ts: datetime) -> str:
        utc = ts.astimezone(timezone.utc).replace(microsecond=0)
        return utc.isoformat().replace("+00:00", "Z")
