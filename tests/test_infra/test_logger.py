"""tests.test_infra.test_logger — Unit tests for AsyncLogger and generate_session_id.

Description
-----------
Tests for the non-blocking ``AsyncLogger`` and the ``generate_session_id``
helper.  Output capturing is achieved by subclassing ``AsyncLogger`` and
overriding ``_write_batch`` so no real stdout writes are required.

Design Patterns
---------------
- Subclass Override: ``CapturingLogger`` extends ``AsyncLogger`` with an
  in-process list accumulator for ``_write_batch``, enabling assertion
  without stdout redirection.
- Arrange/Act/Assert: Each test starts the logger, logs entries, stops it,
  and checks the captured output.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- asyncio: Queue, sleep for timing control.
- graphclaw.infra.logger: AsyncLogger, generate_session_id under test.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from graphclaw.infra.logger import AsyncLogger, generate_session_id


# ---------------------------------------------------------------------------
# Capturing logger helper
# ---------------------------------------------------------------------------


class CapturingLogger(AsyncLogger):
    """AsyncLogger that captures batches instead of writing to stdout."""

    def __init__(self, service_name: str, buffer_size: int = 10_000) -> None:
        super().__init__(service_name, buffer_size)
        self.captured: list[dict] = []

    async def _write_batch(self, batch: list[dict]) -> None:
        self.captured.extend(batch)


# ---------------------------------------------------------------------------
# test_log_adds_to_queue
# ---------------------------------------------------------------------------


async def test_log_adds_to_queue() -> None:
    logger = CapturingLogger(service_name="test-service")

    logger.log("INFO", "task.scored", "SES-abc", task_id="TSK-AB-0001-ATM")

    assert logger._queue.qsize() == 1


async def test_log_entry_has_required_fields() -> None:
    logger = CapturingLogger(service_name="test-service")

    logger.log("ERROR", "task.failed", "SES-xyz", reason="timeout")

    entry = logger._queue.get_nowait()
    assert entry["level"] == "ERROR"
    assert entry["event_type"] == "task.failed"
    assert entry["session_id"] == "SES-xyz"
    assert entry["service"] == "test-service"
    assert entry["reason"] == "timeout"
    assert "timestamp" in entry


# ---------------------------------------------------------------------------
# test_log_drops_on_full
# ---------------------------------------------------------------------------


async def test_log_drops_on_full() -> None:
    logger = CapturingLogger(service_name="test-service", buffer_size=1)

    logger.log("INFO", "event.a", "SES-1")  # fills the queue
    logger.log("INFO", "event.b", "SES-2")  # should be dropped

    assert logger._queue.qsize() == 1
    entry = logger._queue.get_nowait()
    assert entry["event_type"] == "event.a"


# ---------------------------------------------------------------------------
# test_flush_loop_writes_batch
# ---------------------------------------------------------------------------


async def test_flush_loop_writes_batch() -> None:
    logger = CapturingLogger(service_name="test-service")
    await logger.start()

    logger.log("INFO", "task.started", "SES-111", task_id="T1")
    logger.log("INFO", "task.started", "SES-222", task_id="T2")

    await logger.stop()

    assert len(logger.captured) == 2
    event_types = {e["event_type"] for e in logger.captured}
    assert event_types == {"task.started"}
    task_ids = {e["task_id"] for e in logger.captured}
    assert task_ids == {"T1", "T2"}


async def test_start_is_idempotent() -> None:
    logger = CapturingLogger(service_name="test-service")
    await logger.start()
    task_ref = logger._task
    await logger.start()  # second call should be no-op
    assert logger._task is task_ref
    await logger.stop()


async def test_stop_flushes_remaining() -> None:
    logger = CapturingLogger(service_name="test-service")
    # Do NOT start the flush loop — entries should be drained by stop()
    logger.log("INFO", "orphan.event", "SES-orphan")
    await logger.stop()
    assert any(e["event_type"] == "orphan.event" for e in logger.captured)


# ---------------------------------------------------------------------------
# test_generate_session_id_format
# ---------------------------------------------------------------------------


def test_generate_session_id_format() -> None:
    session_id = generate_session_id()
    pattern = re.compile(
        r"^SES-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert pattern.match(session_id), f"Unexpected session_id format: {session_id}"


def test_generate_session_id_is_unique() -> None:
    ids = {generate_session_id() for _ in range(100)}
    assert len(ids) == 100
