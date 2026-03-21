"""graphclaw.state.machine — Task state machine with guard-based transition validation.

Description
-----------
Provides the ``StateMachine`` class, which validates and applies task state
transitions according to the allowed transition table in ``transitions.py`` and
a set of domain-specific guards.  All mutations are applied in-place on the
``TaskNode`` object; callers are responsible for persisting the updated node.
A ``StateHistoryEntry`` is appended on every successful transition for full
audit trail support (PRD Section 7.1).

Design Patterns
---------------
- State Machine: Centralises all transition logic behind a single ``transition()``
  method, preventing scattered ad-hoc state mutations throughout the codebase.
- Guard Clauses: Each guard is an independent static method that raises
  ``InvalidTransitionError`` on violation, keeping the rules testable in isolation.

Public API
----------
- StateMachine.transition: Validate and apply a state transition to a TaskNode.

Dependencies
------------
- graphclaw.models.base: utcnow for timestamp generation.
- graphclaw.models.enums: ChangedBy, ConfidenceLevel, TaskState, TaskType.
- graphclaw.models.nodes: StateHistoryEntry, TaskNode.
- graphclaw.state.transitions: VALID_TRANSITIONS, InvalidTransitionError.
"""

from __future__ import annotations

from graphclaw.models.base import utcnow
from graphclaw.models.enums import ChangedBy, ConfidenceLevel, TaskState, TaskType
from graphclaw.models.nodes import StateHistoryEntry, TaskNode
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError


class StateMachine:
    """Validates and applies task state transitions.

    All mutation is done in-place on the ``TaskNode`` object.  Callers are
    responsible for persisting the updated node to the database.

    Usage::

        sm = StateMachine()
        sm.transition(task, TaskState.IN_PROGRESS, ChangedBy.HUMAN, "Started work")
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transition(
        self,
        task: TaskNode,
        new_state: TaskState,
        changed_by: ChangedBy,
        reason: str = "",
    ) -> None:
        """Validate and apply a state transition to *task*.

        Parameters
        ----------
        task:
            The TaskNode to update (mutated in place).
        new_state:
            The desired target state.
        changed_by:
            Who/what is requesting the transition.
        reason:
            Human-readable explanation stored in StateHistoryEntry.

        Raises
        ------
        InvalidTransitionError
            If the transition is not allowed by the transition table or
            any applicable guard.
        """
        from_state = task.state

        # 1. Check the transition table first.
        self._check_transition_table(from_state, new_state)

        # 2. Apply guards.
        self._guard_terminal_cancelled(from_state, new_state)
        self._guard_terminal_complete(task, from_state, new_state)
        self._guard_approval_auto_resolve(task, new_state, changed_by)
        self._guard_inactive_pending_activation(task, from_state, new_state, changed_by)
        self._guard_blocked_activation(task, from_state, new_state, changed_by)

        # 3. Apply the transition.
        task.state = new_state

        # 4. Record history.
        entry = StateHistoryEntry(
            from_state=from_state,
            to_state=new_state,
            changed_at=utcnow(),
            changed_by=changed_by,
            reason=reason or None,
        )
        task.state_history.append(entry)

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_transition_table(from_state: TaskState, new_state: TaskState) -> None:
        """Raise if new_state is not in the allowed list for from_state."""
        allowed = VALID_TRANSITIONS.get(from_state, [])
        # Special case: COMPLETE → NEEDS_REVIEW is allowed (low-confidence reopen).
        if from_state == TaskState.COMPLETE and new_state == TaskState.NEEDS_REVIEW:
            return
        if new_state not in allowed:
            if not allowed:
                reason = f"{from_state.value} is a terminal state"
            else:
                reason = f"Allowed from {from_state.value}: " + ", ".join(s.value for s in allowed)
            raise InvalidTransitionError(from_state, new_state, reason)

    @staticmethod
    def _guard_terminal_cancelled(from_state: TaskState, new_state: TaskState) -> None:
        """CANCELLED is an absolute terminal state."""
        if from_state == TaskState.CANCELLED:
            raise InvalidTransitionError(from_state, new_state, "CANCELLED is a terminal state")

    @staticmethod
    def _guard_terminal_complete(
        task: TaskNode, from_state: TaskState, new_state: TaskState
    ) -> None:
        """COMPLETE is terminal except for low-confidence reopen to NEEDS_REVIEW."""
        if from_state != TaskState.COMPLETE:
            return
        if new_state == TaskState.NEEDS_REVIEW:
            # Only allowed if the task's confidence is LOW.
            if task.progress.confidence == ConfidenceLevel.LOW:
                return
            raise InvalidTransitionError(
                from_state,
                new_state,
                "COMPLETE → NEEDS_REVIEW only allowed when confidence is LOW",
            )
        raise InvalidTransitionError(from_state, new_state, "COMPLETE is a terminal state")

    @staticmethod
    def _guard_approval_auto_resolve(
        task: TaskNode,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """APPROVAL tasks cannot be auto-resolved — must be completed by a human."""
        if task.task_type != TaskType.APPROVAL:
            return
        if new_state == TaskState.COMPLETE and changed_by != ChangedBy.HUMAN:
            raise InvalidTransitionError(
                task.state,
                new_state,
                "APPROVAL tasks must be completed by a human (HUMAN changed_by required)",
            )

    @staticmethod
    def _guard_inactive_pending_activation(
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """INACTIVE_PENDING → ACTIVE only via CASCADE (predecessor completed)."""
        if from_state != TaskState.INACTIVE_PENDING or new_state != TaskState.ACTIVE:
            return
        if changed_by not in (ChangedBy.CASCADE, ChangedBy.HUMAN, ChangedBy.SYSTEM):
            raise InvalidTransitionError(
                from_state,
                new_state,
                "INACTIVE_PENDING → ACTIVE requires CASCADE, HUMAN, or SYSTEM as changed_by "
                "(predecessor task must have completed)",
            )

    @staticmethod
    def _guard_blocked_activation(
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """BLOCKED → ACTIVE only when blocker is resolved (CASCADE or explicit)."""
        if from_state != TaskState.BLOCKED or new_state != TaskState.ACTIVE:
            return
        if changed_by not in (ChangedBy.CASCADE, ChangedBy.HUMAN, ChangedBy.SYSTEM):
            raise InvalidTransitionError(
                from_state,
                new_state,
                "BLOCKED → ACTIVE requires CASCADE, HUMAN, or SYSTEM as changed_by "
                "(blocker must have been resolved)",
            )


__all__ = ["StateMachine"]
