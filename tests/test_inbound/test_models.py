# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.inbound.models — Domain model instantiation and defaults.

Description
-----------
Verifies that StatusSignal enum values are correct strings, that TaskResolution,
StatusExtraction, and InboundResult can be instantiated with their required
fields and that optional fields carry the expected defaults.
"""

from __future__ import annotations

import pytest

from graphclaw.inbound.models import (
    InboundResult,
    StatusExtraction,
    StatusSignal,
    TaskResolution,
)
from graphclaw.models.enums import ConfidenceLevel, MatchedBy, TaskState

# ---------------------------------------------------------------------------
# StatusSignal
# ---------------------------------------------------------------------------


def test_status_signal_values() -> None:
    """All StatusSignal members must have the expected string values."""
    assert StatusSignal.DONE == "DONE"
    assert StatusSignal.IN_PROGRESS == "IN_PROGRESS"
    assert StatusSignal.BLOCKED == "BLOCKED"
    assert StatusSignal.DELAYED == "DELAYED"
    assert StatusSignal.NEEDS_HELP == "NEEDS_HELP"
    assert StatusSignal.INFO_ONLY == "INFO_ONLY"
    assert StatusSignal.UNKNOWN == "UNKNOWN"


def test_status_signal_is_str() -> None:
    """StatusSignal members are str instances (str Enum mixin)."""
    for signal in StatusSignal:
        assert isinstance(signal, str)


# ---------------------------------------------------------------------------
# TaskResolution
# ---------------------------------------------------------------------------


def test_task_resolution_defaults() -> None:
    """TaskResolution with no args should have all-default/None fields."""
    res = TaskResolution()
    assert res.task_id is None
    assert res.matched_by is None
    assert res.confidence == ConfidenceLevel.LOW
    assert res.score == 0.0
    assert res.matched_text == ""


def test_task_resolution_task_id_match() -> None:
    """TaskResolution should store provided task_id and matched_by."""
    res = TaskResolution(
        task_id="TSK-AB-1234-ATM",
        matched_by=MatchedBy.TASK_ID,
        confidence=ConfidenceLevel.HIGH,
        score=1.0,
        matched_text="TSK-AB-1234-ATM",
    )
    assert res.task_id == "TSK-AB-1234-ATM"
    assert res.matched_by == MatchedBy.TASK_ID
    assert res.confidence == ConfidenceLevel.HIGH
    assert res.score == 1.0
    assert res.matched_text == "TSK-AB-1234-ATM"


def test_task_resolution_vector_match() -> None:
    """TaskResolution should support VECTOR_SEARCH matched_by."""
    res = TaskResolution(
        task_id="TSK-XY-9999-DEL",
        matched_by=MatchedBy.VECTOR_SEARCH,
        confidence=ConfidenceLevel.MEDIUM,
        score=0.55,
        matched_text="Deploy new service",
    )
    assert res.matched_by == MatchedBy.VECTOR_SEARCH
    assert res.confidence == ConfidenceLevel.MEDIUM
    assert res.score == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# StatusExtraction
# ---------------------------------------------------------------------------


def test_status_extraction_defaults() -> None:
    """StatusExtraction with no args should default to UNKNOWN/LOW/empty."""
    ext = StatusExtraction()
    assert ext.signal == StatusSignal.UNKNOWN
    assert ext.confidence == ConfidenceLevel.LOW
    assert ext.summary == ""
    assert ext.suggested_state is None


def test_status_extraction_with_signal() -> None:
    """StatusExtraction should store signal, confidence, summary, and state."""
    ext = StatusExtraction(
        signal=StatusSignal.DONE,
        confidence=ConfidenceLevel.HIGH,
        summary="All done.",
        suggested_state=TaskState.COMPLETE,
    )
    assert ext.signal == StatusSignal.DONE
    assert ext.confidence == ConfidenceLevel.HIGH
    assert ext.summary == "All done."
    assert ext.suggested_state == TaskState.COMPLETE


def test_status_extraction_info_only_no_state() -> None:
    """INFO_ONLY signal may have no suggested_state."""
    ext = StatusExtraction(
        signal=StatusSignal.INFO_ONLY,
        confidence=ConfidenceLevel.MEDIUM,
        summary="FYI update",
        suggested_state=None,
    )
    assert ext.signal == StatusSignal.INFO_ONLY
    assert ext.suggested_state is None


# ---------------------------------------------------------------------------
# InboundResult
# ---------------------------------------------------------------------------


def test_inbound_result_creation() -> None:
    """InboundResult should store all provided fields."""
    resolution = TaskResolution(
        task_id="TSK-AB-0001-ATM",
        matched_by=MatchedBy.TASK_ID,
        confidence=ConfidenceLevel.HIGH,
        score=1.0,
        matched_text="TSK-AB-0001-ATM",
    )
    status = StatusExtraction(
        signal=StatusSignal.DONE,
        confidence=ConfidenceLevel.HIGH,
        summary="Task complete",
        suggested_state=TaskState.COMPLETE,
    )
    result = InboundResult(
        message_id="MSG-001",
        session_id="SES-abc",
        resolution=resolution,
        status=status,
        action_taken="state_update_published",
        followup_needed=False,
    )
    assert result.message_id == "MSG-001"
    assert result.session_id == "SES-abc"
    assert result.resolution.task_id == "TSK-AB-0001-ATM"
    assert result.status.signal == StatusSignal.DONE
    assert result.action_taken == "state_update_published"
    assert result.followup_needed is False


def test_inbound_result_defaults() -> None:
    """InboundResult action_taken and followup_needed should have zero defaults."""
    result = InboundResult(
        message_id="MSG-002",
        session_id="SES-xyz",
        resolution=TaskResolution(),
        status=StatusExtraction(),
    )
    assert result.action_taken == ""
    assert result.followup_needed is False
