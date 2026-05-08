# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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

import logging

from graphclaw.models.base import utcnow
from graphclaw.models.enums import ChangedBy, ConfidenceLevel, TaskState, TaskType
from graphclaw.models.nodes import StateHistoryEntry, TaskNode
from graphclaw.state.transitions import VALID_TRANSITIONS, InvalidTransitionError

logger = logging.getLogger(__name__)


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
        self._check_transition_table(task, from_state, new_state)

        # 2. Apply guards.
        self._guard_terminal_cancelled(task, from_state, new_state)
        self._guard_terminal_complete(task, from_state, new_state)
        self._guard_approval_auto_resolve(task, new_state, changed_by)
        self._guard_inactive_pending_activation(task, from_state, new_state, changed_by)
        self._guard_blocked_activation(task, from_state, new_state, changed_by)
        self._guard_autonomy(task, new_state, changed_by)

        # 3. Apply the transition.
        task.state = new_state

        # Stamp timeline on key transitions.
        now = utcnow()
        if new_state == TaskState.IN_PROGRESS and task.timeline.started_at is None:
            task.timeline.started_at = now
        if new_state == TaskState.COMPLETE:
            task.timeline.completed_at = now

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
    def _log_guard_rejection(
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
        guard_name: str,
        reason: str,
    ) -> None:
        """Emit structured logs for transition guard rejections."""
        logger.warning(
            "state_transition_guard_rejected guard=%s from=%s to=%s task_id=%s reason=%s",
            guard_name,
            from_state.value,
            new_state.value,
            task.id,
            reason,
            extra={
                "task_id": task.id,
                "task_type": task.task_type.value,
                "from_state": from_state.value,
                "to_state": new_state.value,
                "guard": guard_name,
                "reason": reason,
            },
        )

    @classmethod
    def _check_transition_table(
        cls, task: TaskNode, from_state: TaskState, new_state: TaskState
    ) -> None:
        """Raise if new_state is not in the allowed list for from_state."""
        # Wave 0 (FR-DEL-002): Explicitly forbid DELETED / PURGED target states.
        # These strings are not in TaskState enum, but a defensive string check
        # ensures that even future code additions cannot sneak them in.
        _forbidden_str = {"DELETED", "PURGED"}
        new_state_str = new_state.value if isinstance(new_state, TaskState) else str(new_state)
        if new_state_str in _forbidden_str:
            reason = (
                f"Transitioning to {new_state_str} is permanently forbidden "
                "(Wave 0 No-Delete principle FR-DEL-002). Use archive_task instead."
            )
            cls._log_guard_rejection(task, from_state, new_state, "wave0_no_delete", reason)
            raise InvalidTransitionError(from_state, new_state, reason)

        allowed = VALID_TRANSITIONS.get(from_state, [])
        # Special case: COMPLETE → NEEDS_REVIEW is allowed (low-confidence reopen).
        if from_state == TaskState.COMPLETE and new_state == TaskState.NEEDS_REVIEW:
            return
        if new_state not in allowed:
            if not allowed:
                reason = f"{from_state.value} is a terminal state"
            else:
                reason = f"Allowed from {from_state.value}: " + ", ".join(s.value for s in allowed)
            cls._log_guard_rejection(task, from_state, new_state, "transition_table", reason)
            raise InvalidTransitionError(from_state, new_state, reason)

    @classmethod
    def _guard_terminal_cancelled(
        cls,
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
    ) -> None:
        """CANCELLED is an absolute terminal state."""
        if from_state == TaskState.CANCELLED:
            reason = "CANCELLED is a terminal state"
            cls._log_guard_rejection(task, from_state, new_state, "terminal_cancelled", reason)
            raise InvalidTransitionError(from_state, new_state, reason)

    @classmethod
    def _guard_terminal_complete(
        cls, task: TaskNode, from_state: TaskState, new_state: TaskState
    ) -> None:
        """COMPLETE is terminal except for low-confidence reopen to NEEDS_REVIEW."""
        if from_state != TaskState.COMPLETE:
            return
        if new_state == TaskState.NEEDS_REVIEW:
            # Only allowed if the task's confidence is LOW.
            if task.progress.confidence == ConfidenceLevel.LOW:
                return
            reason = "COMPLETE → NEEDS_REVIEW only allowed when confidence is LOW"
            cls._log_guard_rejection(
                task, from_state, new_state, "terminal_complete_low_conf", reason
            )
            raise InvalidTransitionError(
                from_state,
                new_state,
                reason,
            )
        reason = "COMPLETE is a terminal state"
        cls._log_guard_rejection(task, from_state, new_state, "terminal_complete", reason)
        raise InvalidTransitionError(from_state, new_state, reason)

    @classmethod
    def _guard_approval_auto_resolve(
        cls,
        task: TaskNode,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """APPROVAL tasks cannot be auto-resolved — must be completed by a human."""
        if task.task_type != TaskType.APPROVAL:
            return
        if new_state == TaskState.COMPLETE and changed_by != ChangedBy.HUMAN:
            reason = "APPROVAL tasks must be completed by a human (HUMAN changed_by required)"
            cls._log_guard_rejection(task, task.state, new_state, "approval_auto_resolve", reason)
            raise InvalidTransitionError(
                task.state,
                new_state,
                reason,
            )

    @classmethod
    def _guard_inactive_pending_activation(
        cls,
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """INACTIVE_PENDING → ACTIVE only via CASCADE (predecessor completed)."""
        if from_state != TaskState.INACTIVE_PENDING or new_state != TaskState.ACTIVE:
            return
        if changed_by not in (ChangedBy.CASCADE, ChangedBy.HUMAN, ChangedBy.SYSTEM):
            reason = (
                "INACTIVE_PENDING → ACTIVE requires CASCADE, HUMAN, or SYSTEM as changed_by "
                "(predecessor task must have completed)"
            )
            cls._log_guard_rejection(
                task, from_state, new_state, "inactive_pending_activation", reason
            )
            raise InvalidTransitionError(
                from_state,
                new_state,
                reason,
            )

    @classmethod
    def _guard_blocked_activation(
        cls,
        task: TaskNode,
        from_state: TaskState,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """BLOCKED → ACTIVE only when blocker is resolved (CASCADE or explicit)."""
        if from_state != TaskState.BLOCKED or new_state != TaskState.ACTIVE:
            return
        if changed_by not in (ChangedBy.CASCADE, ChangedBy.HUMAN, ChangedBy.SYSTEM):
            reason = (
                "BLOCKED → ACTIVE requires CASCADE, HUMAN, or SYSTEM as changed_by "
                "(blocker must have been resolved)"
            )
            cls._log_guard_rejection(task, from_state, new_state, "blocked_activation", reason)
            raise InvalidTransitionError(
                from_state,
                new_state,
                reason,
            )

    @classmethod
    def _guard_autonomy(
        cls,
        task: TaskNode,
        new_state: TaskState,
        changed_by: ChangedBy,
    ) -> None:
        """Block AI-initiated state changes when task autonomy is not granted.

        PRD §14 / O-SM-04:
        If ``changed_by == AGENT`` and ``task.autonomy.auto_update_allowed`` is
        ``False``, the agent is not permitted to update the task state directly —
        it must surface the change for human review instead.

        CASCADE and SYSTEM updates bypass this guard (they are structural /
        internal and not AI-driven user-facing mutations).
        """
        if changed_by != ChangedBy.AGENT:
            return  # HUMAN, CASCADE, SYSTEM are always allowed
        if not task.autonomy.auto_update_allowed:
            reason = (
                f"Task {task.id} does not permit autonomous AI state updates "
                f"(autonomy.auto_update_allowed=False). "
                f"Request human approval or set auto_update_allowed=True."
            )
            cls._log_guard_rejection(task, task.state, new_state, "autonomy_guard", reason)
            raise InvalidTransitionError(
                task.state,
                new_state,
                reason,
            )


__all__ = ["StateMachine"]
