"""Valid state transitions and related exceptions for the GraphClaw state machine.

Defines the directed-graph of allowed TaskState moves (PRD Section 7.1).
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
        msg = (
            f"Cannot transition from {from_state.value} to {to_state.value}"
        )
        if reason:
            msg = f"{msg}: {reason}"
        super().__init__(msg)


__all__ = ["VALID_TRANSITIONS", "InvalidTransitionError"]
