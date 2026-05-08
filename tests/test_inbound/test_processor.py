# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.inbound.processor — InboundProcessor pipeline.

Description
-----------
Verifies the full processing pipeline using mocked resolver, extractor,
broker, and logger collaborators. Covers state update publishing, follow-up
flagging, unmatched routing, INFO_ONLY no-op paths, logging calls, and
graceful operation when optional dependencies are absent.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.inbound.extractor import StatusExtractor
from graphclaw.inbound.models import (
    InboundResult,
    StatusExtraction,
    StatusSignal,
    TaskResolution,
)
from graphclaw.inbound.processor import InboundProcessor
from graphclaw.inbound.resolver import TaskResolver
from graphclaw.models.enums import ConfidenceLevel, MatchedBy, TaskState

# ---------------------------------------------------------------------------
# Test helpers / factories
# ---------------------------------------------------------------------------


def _make_resolution(
    task_id: str | None = "TSK-AB-0001-ATM",
    matched_by: MatchedBy | None = MatchedBy.TASK_ID,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    score: float = 1.0,
) -> TaskResolution:
    return TaskResolution(
        task_id=task_id,
        matched_by=matched_by,
        confidence=confidence,
        score=score,
        matched_text=task_id or "",
    )


def _make_extraction(
    signal: StatusSignal = StatusSignal.DONE,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    suggested_state: TaskState | None = TaskState.COMPLETE,
) -> StatusExtraction:
    return StatusExtraction(
        signal=signal,
        confidence=confidence,
        summary="Test summary",
        suggested_state=suggested_state,
    )


def _make_processor(
    resolution: TaskResolution | None = None,
    extraction: StatusExtraction | None = None,
    broker: object | None = None,
) -> InboundProcessor:
    mock_resolver = AsyncMock(spec=TaskResolver)
    mock_resolver.resolve = AsyncMock(return_value=resolution or _make_resolution())

    mock_extractor = MagicMock(spec=StatusExtractor)
    mock_extractor.extract = MagicMock(return_value=extraction or _make_extraction())

    return InboundProcessor(
        resolver=mock_resolver,
        extractor=mock_extractor,
        broker=broker,
    )


# ---------------------------------------------------------------------------
# test_process_matched_done_publishes_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_matched_done_publishes_update() -> None:
    """Matched task + DONE signal → state_update_published + broker.publish called."""
    mock_broker = AsyncMock()
    mock_broker.publish = AsyncMock()

    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(signal=StatusSignal.DONE, suggested_state=TaskState.COMPLETE),
        broker=mock_broker,
    )

    result = await processor.process(
        message_id="MSG-001",
        session_id="SES-test",
        subject="Update",
        body="Task is done.",
        channel="email",
    )

    assert result.action_taken == "state_update_published"
    assert result.followup_needed is False
    mock_broker.publish.assert_called_once()

    # Verify the published payload contains expected keys.
    import json

    call_args = mock_broker.publish.call_args
    queue_name = call_args[0][0]
    payload = json.loads(call_args[0][1])
    assert queue_name == "status_updates"
    assert payload["task_id"] == "TSK-AB-0001-ATM"
    assert payload["new_state"] == "COMPLETE"
    assert payload["signal"] == "DONE"
    assert payload["session_id"] == "SES-test"


# ---------------------------------------------------------------------------
# test_process_matched_blocked_needs_followup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_matched_blocked_needs_followup() -> None:
    """Matched task + BLOCKED signal → state_update_published + followup_needed=True."""
    mock_broker = AsyncMock()
    mock_broker.publish = AsyncMock()

    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(signal=StatusSignal.BLOCKED, suggested_state=TaskState.BLOCKED),
        broker=mock_broker,
    )

    result = await processor.process(
        message_id="MSG-002",
        session_id="SES-blocked",
        subject="Blocked",
        body="I am blocked on the deployment.",
        channel="email",
    )

    assert result.action_taken == "state_update_published"
    assert result.followup_needed is True
    mock_broker.publish.assert_called_once()


@pytest.mark.asyncio
async def test_process_matched_needs_help_flags_followup() -> None:
    """NEEDS_HELP signal → followup_needed=True."""
    mock_broker = AsyncMock()

    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(
            signal=StatusSignal.NEEDS_HELP, suggested_state=TaskState.BLOCKED
        ),
        broker=mock_broker,
    )

    result = await processor.process(
        message_id="MSG-003",
        session_id="SES-help",
        subject="Help",
        body="I need help with the task.",
        channel="api",
    )

    assert result.followup_needed is True


# ---------------------------------------------------------------------------
# test_process_unmatched_needs_followup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_unmatched_needs_followup() -> None:
    """Unmatched message → action='unmatched' + followup_needed=True."""
    processor = _make_processor(
        resolution=_make_resolution(task_id=None, matched_by=None, score=0.0),
        extraction=_make_extraction(signal=StatusSignal.INFO_ONLY, suggested_state=None),
    )

    result = await processor.process(
        message_id="MSG-004",
        session_id="SES-unmatched",
        subject="Unknown",
        body="Random message with no task reference.",
        channel="email",
    )

    assert result.action_taken == "unmatched"
    assert result.followup_needed is True
    assert result.resolution.task_id is None


@pytest.mark.asyncio
async def test_process_manual_match_required_when_embedding_unavailable() -> None:
    """Unmatched + embedding unavailable should request manual match."""
    resolution = _make_resolution(task_id=None, matched_by=None, score=0.0)
    resolution.match_unavailable_reason = "embedding_service_unavailable"

    processor = _make_processor(
        resolution=resolution,
        extraction=_make_extraction(signal=StatusSignal.INFO_ONLY, suggested_state=None),
    )

    result = await processor.process(
        message_id="MSG-004A",
        session_id="SES-manual-match",
        subject="Unknown",
        body="Random message with no task reference.",
        channel="email",
        user_id="USER-1",
    )

    assert result.action_taken == "manual_match_required"
    assert result.followup_needed is True


# ---------------------------------------------------------------------------
# test_process_info_only_no_state_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_info_only_no_state_update() -> None:
    """Matched task + INFO_ONLY (no suggested_state) → no_action, no broker call."""
    mock_broker = AsyncMock()
    mock_broker.publish = AsyncMock()

    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(
            signal=StatusSignal.INFO_ONLY,
            suggested_state=None,
        ),
        broker=mock_broker,
    )

    result = await processor.process(
        message_id="MSG-005",
        session_id="SES-info",
        subject="FYI",
        body="Just an informational update.",
        channel="email",
    )

    assert result.action_taken == "no_action"
    assert result.followup_needed is False
    mock_broker.publish.assert_not_called()


# ---------------------------------------------------------------------------
# test_process_logs_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_logs_result(caplog) -> None:
    """stdlib logger emits inbound.processed with correct fields."""
    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(),
    )

    with caplog.at_level(logging.INFO, logger="graphclaw.inbound.processor"):
        result = await processor.process(
            message_id="MSG-006",
            session_id="SES-log",
            subject="Done",
            body="Task is done.",
            channel="email",
        )

    records = [r for r in caplog.records if getattr(r, "event_type", "") == "inbound.processed"]
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "message_id", None) == "MSG-006"
    assert getattr(record, "task_id", None) == "TSK-AB-0001-ATM"


# ---------------------------------------------------------------------------
# test_process_no_broker_still_works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_no_broker_still_works() -> None:
    """Processor without a broker should not raise and still return a result."""
    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(signal=StatusSignal.DONE, suggested_state=TaskState.COMPLETE),
        broker=None,
    )

    result = await processor.process(
        message_id="MSG-007",
        session_id="SES-nobroker",
        subject="Done",
        body="Finished the task.",
        channel="api",
    )

    # Without broker, action is still "state_update_published" (it just skips the publish).
    assert result.action_taken == "state_update_published"
    assert result.message_id == "MSG-007"
    assert result.followup_needed is False


@pytest.mark.asyncio
async def test_process_no_broker_no_logger_still_works() -> None:
    """Processor without broker should not raise and returns a valid result."""
    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=_make_extraction(),
    )

    result = await processor.process(
        message_id="MSG-008",
        session_id="SES-nolog",
        subject="",
        body="Task done.",
        channel="cli",
    )

    assert result.message_id == "MSG-008"


# ---------------------------------------------------------------------------
# test_process_unknown_signal_no_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_matched_unknown_signal_no_action() -> None:
    """Matched task + UNKNOWN signal → no_action (signal excluded from broker path)."""
    mock_broker = AsyncMock()

    processor = _make_processor(
        resolution=_make_resolution(),
        extraction=StatusExtraction(
            signal=StatusSignal.UNKNOWN,
            confidence=ConfidenceLevel.LOW,
            summary="",
            suggested_state=None,
        ),
        broker=mock_broker,
    )

    result = await processor.process(
        message_id="MSG-009",
        session_id="SES-unknown",
        subject="",
        body="",
        channel="email",
    )

    assert result.action_taken == "no_action"
    mock_broker.publish.assert_not_called()


# ---------------------------------------------------------------------------
# InboundResult fields propagated correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_result_fields_propagated() -> None:
    """All InboundResult fields should match the inputs and computed outputs."""
    resolution = _make_resolution()
    extraction = _make_extraction()
    processor = _make_processor(resolution=resolution, extraction=extraction)

    result = await processor.process(
        message_id="MSG-010",
        session_id="SES-fields",
        subject="Subject",
        body="Task is done.",
        channel="email",
    )

    assert isinstance(result, InboundResult)
    assert result.message_id == "MSG-010"
    assert result.session_id == "SES-fields"
    assert result.resolution is resolution
    assert result.status is extraction
