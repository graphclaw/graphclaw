# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for O-SM-04: autonomy guard in StateMachine.

Verifies that AGENT-initiated state transitions are blocked when
task.autonomy.auto_update_allowed is False, and allowed when True.
HUMAN, CASCADE, and SYSTEM transitions always bypass the guard.

These are unit-level tests (no DB) since the guard is pure logic
inside StateMachine.transition().
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.models.enums import ChangedBy, TaskState, TaskType
from graphclaw.models.nodes import AutonomyBlock, TaskNode
from graphclaw.state.machine import StateMachine
from graphclaw.state.transitions import InvalidTransitionError


def _utc():
    return datetime.now(timezone.utc)


def _task(auto_update: bool = False, state: TaskState = TaskState.PENDING) -> TaskNode:
    return TaskNode(
        id="TSK-AU-0001-ATM",
        title="Autonomy test task",
        description="Test for O-SM-04",
        task_type=TaskType.ATOMIC,
        state=state,
        autonomy=AutonomyBlock(auto_update_allowed=auto_update),
        created_at=_utc(),
        updated_at=_utc(),
    )


sm = StateMachine()


# ---------------------------------------------------------------------------
# AGENT blocked when auto_update_allowed=False
# ---------------------------------------------------------------------------


async def test_agent_blocked_when_auto_update_false():
    """AGENT cannot transition state when auto_update_allowed=False."""
    task = _task(auto_update=False)
    with pytest.raises(InvalidTransitionError, match="auto_update_allowed=False"):
        sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT, "agent triggered")


async def test_agent_blocked_preserves_original_state():
    """Task state remains unchanged after a blocked AGENT transition."""
    task = _task(auto_update=False)
    original_state = task.state
    with pytest.raises(InvalidTransitionError):
        sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT)
    assert task.state == original_state


async def test_agent_blocked_does_not_append_history():
    """No StateHistoryEntry added when AGENT transition is blocked."""
    task = _task(auto_update=False)
    with pytest.raises(InvalidTransitionError):
        sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT)
    assert len(task.state_history) == 0


# ---------------------------------------------------------------------------
# AGENT allowed when auto_update_allowed=True
# ---------------------------------------------------------------------------


async def test_agent_allowed_when_auto_update_true():
    """AGENT can transition state when auto_update_allowed=True."""
    task = _task(auto_update=True)
    sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT, "agent triggered")
    assert task.state == TaskState.ACTIVE


async def test_agent_allowed_appends_history():
    """StateHistoryEntry is recorded when AGENT transition is allowed."""
    task = _task(auto_update=True)
    sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT, "agent triggered")
    assert len(task.state_history) == 1
    assert task.state_history[0].changed_by == ChangedBy.AGENT


# ---------------------------------------------------------------------------
# HUMAN always allowed regardless of auto_update_allowed
# ---------------------------------------------------------------------------


async def test_human_always_allowed_when_auto_update_false():
    """HUMAN can always transition state, even when auto_update_allowed=False."""
    task = _task(auto_update=False)
    sm.transition(task, TaskState.ACTIVE, ChangedBy.HUMAN, "human triggered")
    assert task.state == TaskState.ACTIVE


# ---------------------------------------------------------------------------
# CASCADE and SYSTEM bypass the autonomy guard
# ---------------------------------------------------------------------------


async def test_cascade_bypasses_autonomy_guard():
    """CASCADE transition always allowed (structural, not AI-driven)."""
    task = _task(auto_update=False, state=TaskState.INACTIVE_PENDING)
    sm.transition(task, TaskState.ACTIVE, ChangedBy.CASCADE, "predecessor complete")
    assert task.state == TaskState.ACTIVE


async def test_system_bypasses_autonomy_guard():
    """SYSTEM transition always allowed (internal infrastructure)."""
    task = _task(auto_update=False)
    sm.transition(task, TaskState.ACTIVE, ChangedBy.SYSTEM, "system activation")
    assert task.state == TaskState.ACTIVE


# ---------------------------------------------------------------------------
# Default AutonomyBlock has auto_update_allowed=False
# ---------------------------------------------------------------------------


async def test_default_autonomy_block_blocks_agent():
    """Default AutonomyBlock (auto_update_allowed=False by default) blocks AGENT."""
    task = TaskNode(
        id="TSK-AU-0002-ATM",
        title="Default autonomy",
        description="Test",
        task_type=TaskType.ATOMIC,
        created_at=_utc(),
        updated_at=_utc(),
    )
    assert task.autonomy.auto_update_allowed is False
    with pytest.raises(InvalidTransitionError):
        sm.transition(task, TaskState.ACTIVE, ChangedBy.AGENT)
