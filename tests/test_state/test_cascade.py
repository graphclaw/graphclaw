# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for composite completion cascade logic.

Tests cover:
- AND gate: parent completes only when all children complete
- OR gate: parent completes when any child completes
- Low-confidence child halts cascade → NEEDS_REVIEW
- Approval/review children block auto-complete
- Auto-complete disabled flag is respected
- Already-resolved parent is skipped
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.models.base import generate_task_id
from graphclaw.models.enums import (
    ChangedBy,
    ConfidenceLevel,
    GateType,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import CompositeMetadata
from graphclaw.state.cascade import check_composite_completion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_task(
    task_type: TaskType = TaskType.ATOMIC,
    state: TaskState = TaskState.ACTIVE,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
) -> TaskNode:
    task = TaskNode(
        id=generate_task_id("TS", task_type),
        task_type=task_type,
        title="Test Task",
        description="A test task",
        state=state,
        created_at=_now(),
        updated_at=_now(),
    )
    task.progress.confidence = confidence
    return task


def _make_composite(
    gate: GateType = GateType.AND,
    auto_complete: bool = True,
    state: TaskState = TaskState.ACTIVE,
) -> TaskNode:
    parent = TaskNode(
        id=generate_task_id("TS", TaskType.COMPOSITE),
        task_type=TaskType.COMPOSITE,
        title="Composite Parent",
        description="A composite task",
        state=state,
        created_at=_now(),
        updated_at=_now(),
        type_metadata=CompositeMetadata(
            completion_gate=gate,
            auto_complete_on_children=auto_complete,
        ),
    )
    return parent


# ---------------------------------------------------------------------------
# AND gate tests
# ---------------------------------------------------------------------------


class TestANDGate:
    def test_all_children_complete_triggers_parent_complete(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(state=TaskState.COMPLETE),
            _make_task(state=TaskState.COMPLETE),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.COMPLETE

    def test_incomplete_children_blocks_parent(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(state=TaskState.IN_PROGRESS),
            _make_task(state=TaskState.COMPLETE),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE  # unchanged


# ---------------------------------------------------------------------------
# OR gate tests
# ---------------------------------------------------------------------------


class TestORGate:
    def test_one_child_complete_triggers_parent_complete(self):
        parent = _make_composite(gate=GateType.OR)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(state=TaskState.IN_PROGRESS),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.COMPLETE

    def test_no_children_complete_blocks_parent(self):
        """OR gate still needs at least one complete child. But since
        check_composite_completion is called when a child completes, if
        we have no complete children and an incomplete REVIEW, it blocks."""
        parent = _make_composite(gate=GateType.OR)
        children = [
            _make_task(task_type=TaskType.REVIEW, state=TaskState.IN_PROGRESS),
        ]
        # REVIEW child is incomplete → blocks auto-complete
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE


# ---------------------------------------------------------------------------
# Low-confidence halt
# ---------------------------------------------------------------------------


class TestLowConfidenceHalt:
    def test_low_confidence_research_child_halts_cascade(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(
                task_type=TaskType.RESEARCH,
                state=TaskState.COMPLETE,
                confidence=ConfidenceLevel.LOW,
            ),
            _make_task(state=TaskState.COMPLETE),
        ]
        check_composite_completion(parent, children)
        # Parent should be NEEDS_REVIEW, not COMPLETE
        assert parent.state == TaskState.NEEDS_REVIEW

    def test_high_confidence_does_not_halt(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(
                task_type=TaskType.RESEARCH,
                state=TaskState.COMPLETE,
                confidence=ConfidenceLevel.HIGH,
            ),
            _make_task(state=TaskState.COMPLETE),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.COMPLETE


# ---------------------------------------------------------------------------
# Approval / Review child blocks
# ---------------------------------------------------------------------------


class TestApprovalReviewBlocks:
    def test_pending_approval_blocks_auto_complete(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(task_type=TaskType.APPROVAL, state=TaskState.IN_PROGRESS),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE  # unchanged

    def test_pending_review_blocks_auto_complete(self):
        parent = _make_composite(gate=GateType.AND)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(task_type=TaskType.REVIEW, state=TaskState.IN_PROGRESS),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE  # unchanged


# ---------------------------------------------------------------------------
# Auto-complete disabled
# ---------------------------------------------------------------------------


class TestAutoCompleteDisabled:
    def test_auto_complete_off_skips_cascade(self):
        parent = _make_composite(gate=GateType.AND, auto_complete=False)
        children = [
            _make_task(state=TaskState.COMPLETE),
            _make_task(state=TaskState.COMPLETE),
        ]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE  # unchanged


# ---------------------------------------------------------------------------
# Already-resolved parent
# ---------------------------------------------------------------------------


class TestAlreadyResolved:
    def test_complete_parent_is_skipped(self):
        parent = _make_composite(state=TaskState.COMPLETE)
        children = [_make_task(state=TaskState.COMPLETE)]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.COMPLETE

    def test_cancelled_parent_is_skipped(self):
        parent = _make_composite(state=TaskState.CANCELLED)
        children = [_make_task(state=TaskState.COMPLETE)]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.CANCELLED


# ---------------------------------------------------------------------------
# Non-composite parent
# ---------------------------------------------------------------------------


class TestNonCompositeParent:
    def test_atomic_parent_does_not_cascade(self):
        parent = _make_task(task_type=TaskType.ATOMIC, state=TaskState.ACTIVE)
        children = [_make_task(state=TaskState.COMPLETE)]
        check_composite_completion(parent, children)
        assert parent.state == TaskState.ACTIVE  # unchanged


class TestInjectedStateMachine:
    def test_check_composite_completion_uses_injected_state_machine(self):
        class _StubStateMachine:
            def __init__(self) -> None:
                self.calls: list[tuple[str, TaskState, ChangedBy, str]] = []

            def transition(
                self,
                task: TaskNode,
                new_state: TaskState,
                changed_by: ChangedBy,
                reason: str = "",
            ) -> None:
                self.calls.append((task.id, new_state, changed_by, reason))
                task.state = new_state

        parent = _make_composite(gate=GateType.AND)
        children = [_make_task(state=TaskState.COMPLETE), _make_task(state=TaskState.COMPLETE)]
        stub = _StubStateMachine()

        check_composite_completion(parent, children, state_machine=stub)  # type: ignore[arg-type]

        assert parent.state == TaskState.COMPLETE
        assert len(stub.calls) == 1
        assert stub.calls[0][1] == TaskState.COMPLETE
