# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for the GraphClaw state machine.

Tests cover:
- Valid transitions succeed and are recorded in state_history
- Invalid transitions raise InvalidTransitionError
- Terminal state guards (CANCELLED, COMPLETE)
- APPROVAL task auto-resolve guard
- INACTIVE_PENDING → ACTIVE guard
- BLOCKED → ACTIVE guard
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import (
    ChangedBy,
    ConfidenceLevel,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import TaskNode
from graphclaw.state.machine import StateMachine
from graphclaw.state.transitions import InvalidTransitionError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_task(
    task_type: TaskType = TaskType.ATOMIC,
    state: TaskState = TaskState.PENDING,
) -> TaskNode:
    return TaskNode(
        id=generate_task_id("TS", task_type),
        task_type=task_type,
        title="Test Task",
        description="A test task",
        state=state,
        created_at=_now(),
        updated_at=_now(),
    )


# ---------------------------------------------------------------------------
# Basic valid transitions
# ---------------------------------------------------------------------------


class TestValidTransitions:
    def test_pending_to_active(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "Starting task")
        assert task.state == TaskState.ACTIVE

    def test_active_to_in_progress(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN, "Working on it")
        assert task.state == TaskState.IN_PROGRESS

    def test_in_progress_to_complete(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.IN_PROGRESS)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.HUMAN, "Done")
        assert task.state == TaskState.COMPLETE

    def test_pending_to_cancelled(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        sm.transition(task, TaskState.CANCELLED, ChangedBy.HUMAN, "Dropped")
        assert task.state == TaskState.CANCELLED

    def test_active_to_blocked(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        sm.transition(task, TaskState.BLOCKED, ChangedBy.SYSTEM, "Dependency not met")
        assert task.state == TaskState.BLOCKED

    def test_blocked_to_active_by_cascade(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.BLOCKED)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.CASCADE, "Blocker resolved")
        assert task.state == TaskState.ACTIVE

    def test_needs_review_to_complete(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.NEEDS_REVIEW)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.HUMAN, "Reviewed and approved")
        assert task.state == TaskState.COMPLETE

    def test_snoozed_to_active(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.SNOOZED)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.SYSTEM, "Snooze expired")
        assert task.state == TaskState.ACTIVE


# ---------------------------------------------------------------------------
# State history recording
# ---------------------------------------------------------------------------


class TestStateHistoryRecording:
    def test_history_entry_recorded(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        assert len(task.state_history) == 0
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "test")
        assert len(task.state_history) == 1

    def test_history_entry_fields(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        before = datetime.now(timezone.utc)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "starting work")
        entry = task.state_history[0]
        assert entry.from_state == TaskState.PENDING
        assert entry.to_state == TaskState.ACTIVE
        assert entry.changed_by == ChangedBy.HUMAN
        assert entry.reason == "starting work"
        assert entry.changed_at >= before

    def test_multiple_transitions_accumulate(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN)
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.HUMAN)
        assert len(task.state_history) == 3
        assert task.state_history[0].from_state == TaskState.PENDING
        assert task.state_history[2].to_state == TaskState.COMPLETE

    def test_history_reason_none_when_empty(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN)
        assert task.state_history[0].reason is None


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_pending_cannot_go_to_complete(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.PENDING)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.COMPLETE, ChangedBy.AGENT)

    def test_in_progress_cannot_go_to_pending(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.IN_PROGRESS)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.PENDING, ChangedBy.AGENT)

    def test_active_cannot_go_to_inactive_pending(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.INACTIVE_PENDING, ChangedBy.AGENT)

    def test_delayed_cannot_go_to_complete(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.DELAYED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.COMPLETE, ChangedBy.AGENT)


# ---------------------------------------------------------------------------
# Terminal state guards
# ---------------------------------------------------------------------------


class TestTerminalStateGuards:
    def test_cancelled_is_terminal(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.CANCELLED)
        with pytest.raises(InvalidTransitionError, match="terminal"):
            sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN)

    def test_complete_is_terminal(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.COMPLETE)
        with pytest.raises(InvalidTransitionError, match="terminal"):
            sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN)

    def test_complete_to_needs_review_allowed_for_low_confidence(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.COMPLETE)
        task.progress.confidence = ConfidenceLevel.LOW
        # Should not raise
        sm.transition(task, TaskState.NEEDS_REVIEW, ChangedBy.HUMAN, "Low confidence reopen")
        assert task.state == TaskState.NEEDS_REVIEW

    def test_complete_to_needs_review_blocked_for_high_confidence(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.COMPLETE)
        task.progress.confidence = ConfidenceLevel.HIGH
        with pytest.raises(InvalidTransitionError, match="confidence"):
            sm.transition(task, TaskState.NEEDS_REVIEW, ChangedBy.AGENT)


# ---------------------------------------------------------------------------
# Approval task guard
# ---------------------------------------------------------------------------


class TestApprovalGuard:
    def test_approval_task_requires_human_to_complete(self):
        sm = StateMachine()
        task = _make_task(task_type=TaskType.APPROVAL, state=TaskState.IN_PROGRESS)
        with pytest.raises(InvalidTransitionError, match="human"):
            sm.transition(task, TaskState.COMPLETE, ChangedBy.AGENT)

    def test_approval_task_completed_by_human_ok(self):
        sm = StateMachine()
        task = _make_task(task_type=TaskType.APPROVAL, state=TaskState.IN_PROGRESS)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.HUMAN, "Approved")
        assert task.state == TaskState.COMPLETE

    def test_approval_task_completed_by_cascade_rejected(self):
        sm = StateMachine()
        task = _make_task(task_type=TaskType.APPROVAL, state=TaskState.IN_PROGRESS)
        with pytest.raises(InvalidTransitionError, match="human"):
            sm.transition(task, TaskState.COMPLETE, ChangedBy.CASCADE)


# ---------------------------------------------------------------------------
# INACTIVE_PENDING guard
# ---------------------------------------------------------------------------


class TestInactivePendingGuard:
    def test_inactive_pending_to_active_by_cascade(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.INACTIVE_PENDING)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.CASCADE, "Predecessor done")
        assert task.state == TaskState.ACTIVE

    def test_inactive_pending_to_active_by_human(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.INACTIVE_PENDING)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "Manual override")
        assert task.state == TaskState.ACTIVE

    def test_inactive_pending_to_active_by_agent_rejected(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.INACTIVE_PENDING)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT, "Agent trying to activate")


# ---------------------------------------------------------------------------
# BLOCKED guard
# ---------------------------------------------------------------------------


class TestBlockedGuard:
    def test_blocked_to_active_by_cascade(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.BLOCKED)
        sm.transition(task, TaskState.ACTIVE, ChangedBy.CASCADE, "Blocker resolved")
        assert task.state == TaskState.ACTIVE

    def test_blocked_to_active_by_agent_rejected(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.BLOCKED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT, "Agent forcing")


# ---------------------------------------------------------------------------
# Timeline auto-stamping
# ---------------------------------------------------------------------------


class TestTimelineStamping:
    def test_started_at_set_on_first_in_progress(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        assert task.timeline.started_at is None
        before = datetime.now(timezone.utc)
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN)
        assert task.timeline.started_at is not None
        assert task.timeline.started_at >= before

    def test_started_at_not_overwritten_on_re_entry(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN)
        original_started = task.timeline.started_at
        # Simulate DELAYED → IN_PROGRESS re-entry.
        task.state = TaskState.DELAYED
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN)
        assert task.timeline.started_at == original_started

    def test_completed_at_set_on_complete(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.IN_PROGRESS)
        assert task.timeline.completed_at is None
        before = datetime.now(timezone.utc)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.HUMAN)
        assert task.timeline.completed_at is not None
        assert task.timeline.completed_at >= before

    def test_completed_at_not_set_for_other_transitions(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN)
        assert task.timeline.completed_at is None

    def test_active_cascade_complete_stamps_both(self):
        sm = StateMachine()
        task = _make_task(state=TaskState.ACTIVE)
        # ACTIVE → COMPLETE via CASCADE auto-complete skips IN_PROGRESS.
        before = datetime.now(timezone.utc)
        sm.transition(task, TaskState.COMPLETE, ChangedBy.CASCADE)
        assert task.timeline.completed_at is not None
        assert task.timeline.completed_at >= before
        # started_at not set because the task never entered IN_PROGRESS.
        assert task.timeline.started_at is None


class TestGuardRejectionLogging:
    def test_guard_rejection_logs_reason(self, caplog: pytest.LogCaptureFixture):
        sm = StateMachine()
        task = _make_task(state=TaskState.CANCELLED)

        with caplog.at_level("WARNING"):
            with pytest.raises(InvalidTransitionError):
                sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "Reopen")

        assert "state_transition_guard_rejected" in caplog.text
        assert "CANCELLED is a terminal state" in caplog.text
