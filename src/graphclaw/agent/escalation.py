"""graphclaw.agent.escalation — Approval task escalation for overdue delegation approvals.

Description
-----------
Provides ``EscalationService``, which periodically checks all APPROVAL tasks
assigned to a given user and escalates any that have exceeded their
``max_wait_days`` threshold.  Three escalation actions are supported:

- ``REASSIGN``    — Reassign the approval task to a different user (via
                    ``DelegationService.delegate_task``) and create a new
                    visibility grant for the new assignee.
- ``CANCEL``      — Transition the approval task to the CANCELLED state.
- ``AUTO_APPROVE`` — Transition the approval task to the COMPLETE state,
                    treating silence as implicit approval.

The service is intended to be called from a scheduled job or the agent loop's
maintenance cycle.

Design Patterns
---------------
- Service Object: ``EscalationService`` encapsulates all escalation logic,
  keeping the agent loop focused on high-level orchestration.
- Result Object: ``EscalationEvent`` is an immutable dataclass so callers
  receive a typed audit record for each escalation applied.
- Dependency Injection: Both ``GraphStore`` and ``DelegationService`` are
  injected at construction time for testability and backend independence.

Public API
----------
- EscalationEvent: Dataclass describing a single escalation applied.
- EscalationService: Service for checking and applying approval escalations.
- EscalationService.check_and_escalate: Find and escalate all overdue tasks.
- EscalationService.escalate_task: Apply escalation to a single task.

Dependencies
------------
- graphclaw.agent.delegation: DelegationService (for REASSIGN action).
- graphclaw.db.base: GraphStore (ABC for node/edge CRUD).
- graphclaw.models.base: utcnow.
- graphclaw.models.enums: TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.type_metadata: ApprovalMetadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from graphclaw.agent.delegation import DelegationService
from graphclaw.db.base import GraphStore
from graphclaw.models.base import utcnow
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import ApprovalMetadata

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EscalationEvent:
    """Describes a single escalation applied to an APPROVAL task.

    Attributes
    ----------
    task_id:
        The ID of the approval task that was escalated.
    action_taken:
        One of ``"REASSIGNED"``, ``"CANCELLED"``, or ``"AUTO_APPROVED"``.
    escalated_at:
        timezone.utc timestamp when the escalation was applied.
    escalated_to_user_id:
        The user the task was reassigned to, or ``None`` if the action was
        CANCELLED or AUTO_APPROVED.
    """

    task_id: str
    action_taken: str
    escalated_at: datetime
    escalated_to_user_id: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EscalationError(Exception):
    """Raised when an escalation precondition fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EscalationService:
    """Checks for overdue APPROVAL tasks and escalates them.

    Parameters
    ----------
    graph_store:
        A concrete ``GraphStore`` implementation used for all node and edge
        CRUD operations.
    delegation_service:
        ``DelegationService`` instance used to reassign tasks when the
        escalation action is ``REASSIGN``.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        delegation_service: DelegationService,
    ) -> None:
        self._store = graph_store
        self._delegation = delegation_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_and_escalate(self, user_id: str) -> list[EscalationEvent]:
        """Find all overdue APPROVAL tasks assigned to *user_id* and escalate them.

        A task is considered overdue when::

            task.created_at + timedelta(days=metadata.max_wait_days) < now

        Only tasks in the ``PENDING`` or ``IN_PROGRESS`` state are considered.

        Parameters
        ----------
        user_id:
            The user whose assigned APPROVAL tasks should be checked.

        Returns
        -------
        list[EscalationEvent]
            One event per task that was escalated.  Empty list when no tasks
            are overdue.
        """
        now = utcnow()
        events: list[EscalationEvent] = []

        # Query for APPROVAL tasks assigned to this user
        candidate_nodes = await self._store.list_nodes(
            label="TaskNode",
            filters={
                "task_type": TaskType.APPROVAL,
                "assigned_to": user_id,
            },
        )

        for raw_node in candidate_nodes:
            task = TaskNode.model_validate(raw_node)

            # Only check active states
            if task.state not in (TaskState.PENDING, TaskState.IN_PROGRESS):
                continue

            # Extract ApprovalMetadata; skip if missing or wrong type
            if task.type_metadata is None:
                continue
            if not isinstance(task.type_metadata, ApprovalMetadata):
                continue

            approval_meta = task.type_metadata

            # Check deadline
            deadline = task.created_at + timedelta(days=approval_meta.max_wait_days)
            if now < deadline:
                continue  # Not yet overdue

            event = await self.escalate_task(task.id, approval_meta.model_dump())
            events.append(event)

        return events

    async def escalate_task(
        self,
        task_id: str,
        approval_metadata: dict,
    ) -> EscalationEvent:
        """Apply the configured escalation action to a single APPROVAL task.

        The action is read from ``approval_metadata["escalation_action"]``
        (case-insensitive).

        Parameters
        ----------
        task_id:
            ID of the APPROVAL task to escalate.
        approval_metadata:
            A dict representation of the task's ``ApprovalMetadata`` block.
            Must contain at least ``escalation_action``.

        Returns
        -------
        EscalationEvent
            A record of the action taken.

        Raises
        ------
        EscalationError
            If the task cannot be found or the escalation action is unknown.
        """
        raw_task = await self._store.get_node(task_id)
        if raw_task is None:
            raise EscalationError(f"Task '{task_id}' not found for escalation.")

        task = TaskNode.model_validate(raw_task)
        now = utcnow()

        escalation_action = (approval_metadata.get("escalation_action") or "REASSIGN").upper()
        escalation_target = approval_metadata.get("escalation_target_user_id")
        owner_id = task.owned_by or task.created_by or "SYSTEM"

        if escalation_action == "REASSIGN":
            return await self._escalate_reassign(
                task=task,
                escalation_target=escalation_target,
                owner_id=owner_id,
                approval_metadata=approval_metadata,
                now=now,
            )
        elif escalation_action == "CANCEL":
            return await self._escalate_cancel(task=task, now=now)
        elif escalation_action == "AUTO_APPROVE":
            return await self._escalate_auto_approve(task=task, now=now)
        else:
            raise EscalationError(
                f"Unknown escalation_action '{escalation_action}' on task '{task_id}'. "
                "Expected REASSIGN, CANCEL, or AUTO_APPROVE."
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _escalate_reassign(
        self,
        task: TaskNode,
        escalation_target: str | None,
        owner_id: str,
        approval_metadata: dict,
        now: datetime,
    ) -> EscalationEvent:
        """Reassign *task* to *escalation_target*, creating a new visibility grant."""
        if not escalation_target:
            raise EscalationError(
                f"Task '{task.id}' has escalation_action=REASSIGN but no "
                "escalation_target_user_id is set."
            )

        await self._delegation.delegate_task(
            task_id=task.id,
            from_user_id=owner_id,
            to_user_id=escalation_target,
            create_visibility_grant=True,
            require_approval=False,
            max_wait_days=approval_metadata.get("max_wait_days", 7),
            escalation_target=None,
            escalation_action="REASSIGN",
        )

        # Record escalation timestamp on the task
        await self._store.update_node(
            task.id,
            {"updated_at": now.isoformat()},
        )

        return EscalationEvent(
            task_id=task.id,
            action_taken="REASSIGNED",
            escalated_at=now,
            escalated_to_user_id=escalation_target,
        )

    async def _escalate_cancel(
        self,
        task: TaskNode,
        now: datetime,
    ) -> EscalationEvent:
        """Transition *task* to CANCELLED state."""
        await self._store.update_node(
            task.id,
            {
                "state": TaskState.CANCELLED,
                "updated_at": now.isoformat(),
            },
        )
        return EscalationEvent(
            task_id=task.id,
            action_taken="CANCELLED",
            escalated_at=now,
            escalated_to_user_id=None,
        )

    async def _escalate_auto_approve(
        self,
        task: TaskNode,
        now: datetime,
    ) -> EscalationEvent:
        """Transition *task* to COMPLETE state (implicit approval by timeout)."""
        await self._store.update_node(
            task.id,
            {
                "state": TaskState.COMPLETE,
                "updated_at": now.isoformat(),
            },
        )
        return EscalationEvent(
            task_id=task.id,
            action_taken="AUTO_APPROVED",
            escalated_at=now,
            escalated_to_user_id=None,
        )


__all__ = [
    "EscalationEvent",
    "EscalationError",
    "EscalationService",
]
