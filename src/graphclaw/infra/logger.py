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
import json
import sys
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

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


class AsyncLogger:
    """Non-blocking structured JSON logger with an async flush loop.

    Log entries are placed in an in-memory queue and written to stdout
    as newline-delimited JSON by a background task.  If the queue is
    full, new entries are dropped to avoid blocking callers.

    Optionally writes filtered logs (min_level and above) to a durable
    S3/MinIO sink for long-term audit trail.

    Args:
        service_name: Identifying name embedded in every log entry.
        buffer_size: Maximum number of queued entries before drops occur.
        storage: Optional StorageClient for durable log sink.
        user_id: Optional user_id for per-user log paths; if None, logs go to _system.
        min_level: Minimum level for durable sink (DEBUG goes stdout only).
    """

    def __init__(
        self,
        service_name: str,
        buffer_size: int = 10_000,
        storage: StorageClient | None = None,
        user_id: str | None = None,
        min_level: str = "INFO",
    ) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=buffer_size)
        self._service_name = service_name
        self._flush_interval: float = 1.0
        self._flush_batch_size: int = 100
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._storage: StorageClient | None = storage
        self._user_id = user_id
        self._min_level = min_level.upper()
        # Log level priority mapping for filtering
        self._level_priority = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        service: str,
        storage: StorageClient | None = None,
        user_id: str | None = None,
        min_level: str = "INFO",
    ) -> AsyncLogger:
        """Class factory for creating AsyncLogger instances.

        Args:
            service: Service name embedded in every log entry.
            storage: Optional StorageClient for durable log sink.
            user_id: Optional user_id for per-user log paths; if None, logs go to _system.
            min_level: Minimum level for durable sink (DEBUG goes stdout only).

        Returns:
            Configured AsyncLogger instance.
        """
        return cls(
            service_name=service,
            storage=storage,
            user_id=user_id,
            min_level=min_level,
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

        If storage sink is configured, also writes filtered entries (min_level
        and above) to S3/MinIO using read-modify-write pattern.

        Args:
            batch: List of structured log entry dicts to write.
        """
        # Always write to stdout
        lines = "\n".join(json.dumps(entry, default=str) for entry in batch) + "\n"
        sys.stdout.write(lines)
        sys.stdout.flush()

        # Optionally write to durable storage sink
        if self._storage is not None:
            await self._write_to_storage_sink(batch)

    async def _write_to_storage_sink(self, batch: list[dict]) -> None:
        """Write filtered log entries to S3/MinIO sink (read-modify-write).

        Args:
            batch: List of log entries to filter and write.
        """
        try:
            # Filter batch by min_level
            min_priority = self._level_priority.get(self._min_level, 1)
            filtered_batch = [
                entry
                for entry in batch
                if self._level_priority.get(entry.get("level", "INFO"), 1) >= min_priority
            ]

            if not filtered_batch:
                return

            # Group entries by hour for efficient file organization
            hourly_batches: dict[str, list[dict]] = {}
            for entry in filtered_batch:
                ts_str = entry.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    hour_key = ts.strftime("%Y-%m-%d/%H00Z")
                    if hour_key not in hourly_batches:
                        hourly_batches[hour_key] = []
                    hourly_batches[hour_key].append(entry)
                except Exception:
                    # Skip entries with invalid timestamps
                    continue

            # Write each hourly batch to its own file
            for hour_key, entries in hourly_batches.items():
                await self._append_to_log_file(hour_key, entries)

        except Exception:
            # Sink failures must never crash the logger — silent drop
            pass

    async def _append_to_log_file(self, hour_key: str, entries: list[dict]) -> None:
        """Append log entries to an hourly log file using read-modify-write.

        Args:
            hour_key: Datetime key in format YYYY-MM-DD/HH00Z.
            entries: Log entries to append to this hour's file.
        """
        try:
            # Construct storage path based on user_id
            if self._user_id:
                log_path = f"{self._user_id}/logs/{self._service_name}/{hour_key}.jsonl"
            else:
                log_path = f"_system/logs/{self._service_name}/{hour_key}.jsonl"

            # Read existing file content if it exists
            try:
                existing_bytes = await self._storage.read(log_path)  # type: ignore[union-attr]
                existing_content = existing_bytes.decode("utf-8")
            except FileNotFoundError:
                existing_content = ""

            # Append new entries
            new_lines = "\n".join(json.dumps(entry, default=str) for entry in entries)
            if existing_content and not existing_content.endswith("\n"):
                existing_content += "\n"
            updated_content = existing_content + new_lines + "\n"

            # Write back to storage
            await self._storage.write(  # type: ignore[union-attr]
                log_path, updated_content.encode("utf-8"), content_type="application/x-ndjson"
            )

        except Exception:
            # Sink failures must never crash the logger
            pass
