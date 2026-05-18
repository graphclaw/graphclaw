# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.state.transitions — Allowed task state transitions and InvalidTransitionError.

Description
-----------
Defines the directed-graph of allowed ``TaskState`` moves as ``VALID_TRANSITIONS``,
a dict mapping each source state to its permitted target states.  This table is the
single source of truth for what transitions the state machine enforces; guards in
``machine.py`` add additional domain constraints on top of the table.

Design Patterns
---------------
- Lookup Table: ``VALID_TRANSITIONS`` externalises the allowed-move set so that
  tests can assert the table directly without instantiating the StateMachine.

Public API
----------
- VALID_TRANSITIONS: Dict mapping each TaskState to its list of permitted targets.
- InvalidTransitionError: Exception raised when a transition is not allowed.

Dependencies
------------
- graphclaw.models.enums: TaskState.

Notes
-----
Terminal states (COMPLETE, CANCELLED) have empty target lists in the table.
The COMPLETE → NEEDS_REVIEW low-confidence reopen is handled as a special-cased
guard in StateMachine, not here, because it requires inspecting the task's
progress.confidence field rather than just the state pair.
"""

from __future__ import annotations

from graphclaw.models.enums import TaskState

# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.PENDING: [
        TaskState.ACTIVE,
        TaskState.CANCELLED,
        TaskState.SNOOZED,
        TaskState.INACTIVE_PENDING,
    ],
    TaskState.ACTIVE: [
        TaskState.IN_PROGRESS,
        TaskState.COMPLETE,  # via CASCADE (composite auto-complete)
        TaskState.BLOCKED,
        TaskState.DELAYED,
        TaskState.NEEDS_REVIEW,
        TaskState.CANCELLED,
        TaskState.SNOOZED,
    ],
    TaskState.IN_PROGRESS: [
        TaskState.COMPLETE,
        TaskState.BLOCKED,
        TaskState.DELAYED,
        TaskState.NEEDS_REVIEW,
        TaskState.CANCELLED,
    ],
    TaskState.BLOCKED: [
        TaskState.ACTIVE,
    ],
    TaskState.DELAYED: [
        TaskState.IN_PROGRESS,
    ],
    TaskState.NEEDS_REVIEW: [
        TaskState.IN_PROGRESS,
        TaskState.COMPLETE,
    ],
    TaskState.SNOOZED: [
        TaskState.ACTIVE,
    ],
    TaskState.INACTIVE_PENDING: [
        TaskState.ACTIVE,
    ],
    # Terminal states — no transitions out by default.
    # COMPLETE can be reopened to NEEDS_REVIEW (low-confidence reopen),
    # handled as a special-cased guard in the StateMachine, not here.
    TaskState.COMPLETE: [],
    TaskState.CANCELLED: [],
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not allowed."""

    def __init__(
        self,
        from_state: TaskState,
        to_state: TaskState,
        reason: str = "",
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        msg = f"Cannot transition from {from_state.value} to {to_state.value}"
        if reason:
            msg = f"{msg}: {reason}"
        super().__init__(msg)


__all__ = ["VALID_TRANSITIONS", "InvalidTransitionError"]
