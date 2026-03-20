"""graphclaw.agent.delegation — Cross-user task delegation with visibility grant creation.

Description
-----------
Provides ``DelegationService``, which manages the full lifecycle of delegating a
GraphClaw task from one user to another.  Delegation involves reassigning the
task's ``assigned_to`` field, optionally creating a ``VisibilityGrantNode`` so the
delegate can see the task, and optionally creating an APPROVAL ``TaskNode`` that
tracks the delegation and can be escalated if the delegate does not act within
``max_wait_days``.

Design Patterns
---------------
- Service Object: ``DelegationService`` encapsulates all delegation logic behind a
  single ``delegate_task`` entry-point, keeping orchestration concerns out of the
  agent loop.
- Result Object: ``DelegationResult`` is a plain dataclass so callers get a typed
  summary of every side-effect without inspecting the graph directly.
- Strategy injection: ``GraphStore`` is injected at construction time, making the
  service backend-agnostic and easy to unit-test with a fake store.

Public API
----------
- DelegationResult: Dataclass summarising the outcome of a delegate_task call.
- DelegationService: Service for cross-user task delegation.
- DelegationService.delegate_task: Delegate a task to another user.
- DelegationService.revoke_delegation: Revoke a previous delegation.

Dependencies
------------
- graphclaw.db.base: GraphStore (ABC for node/edge CRUD).
- graphclaw.models.base: generate_grant_id, generate_task_id, utcnow.
- graphclaw.models.enums: EdgeType, TaskState, TaskType, VisibilityScope.
- graphclaw.models.nodes: TaskNode, VisibilityGrantNode.
- graphclaw.models.type_metadata: ApprovalMetadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from graphclaw.db.base import GraphStore
from graphclaw.models.base import (
    generate_grant_id,
    generate_task_id,
    utcnow,
)
from graphclaw.models.enums import (
    EdgeType,
    TaskState,
    TaskType,
    VisibilityScope,
)
from graphclaw.models.nodes import TaskNode, VisibilityGrantNode
from graphclaw.models.type_metadata import ApprovalMetadata


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DelegationResult:
    """Summarises the outcome of a :meth:`DelegationService.delegate_task` call.

    Attributes
    ----------
    task_id:
        The ID of the task that was delegated.
    delegated_to_user_id:
        The user who now has the task assigned to them.
    visibility_grant_id:
        The GRANT-{uuid} ID of the VisibilityGrantNode created for the
        delegate, or ``None`` if ``create_visibility_grant=False``.
    approval_task_id:
        The TSK-…-APR ID of the APPROVAL task created to track this
        delegation, or ``None`` if ``require_approval=False``.
    """

    task_id: str
    delegated_to_user_id: str
    visibility_grant_id: str | None = field(default=None)
    approval_task_id: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DelegationError(Exception):
    """Raised when a delegation precondition fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DelegationService:
    """Handles cross-user task delegation with visibility grant creation.

    Parameters
    ----------
    graph_store:
        A concrete ``GraphStore`` implementation used for all node and edge
        CRUD operations.
    """

    def __init__(self, graph_store: GraphStore) -> None:
        self._store = graph_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def delegate_task(
        self,
        task_id: str,
        from_user_id: str,
        to_user_id: str,
        *,
        create_visibility_grant: bool = True,
        require_approval: bool = False,
        max_wait_days: int = 7,
        escalation_target: str | None = None,
        escalation_action: str = "REASSIGN",
    ) -> DelegationResult:
        """Delegate *task_id* from *from_user_id* to *to_user_id*.

        Steps
        -----
        1. Load the task and verify *from_user_id* is the owner.
        2. Optionally create a ``VisibilityGrantNode`` giving *to_user_id*
           EDITOR access to the task, and a ``GRANTS_ACCESS_TO`` edge.
        3. Update the task's ``assigned_to`` field to *to_user_id*.
        4. Optionally create an APPROVAL ``TaskNode`` tracking this
           delegation, with escalation configuration in its
           ``ApprovalMetadata``.

        Parameters
        ----------
        task_id:
            ID of the task to delegate (must match TASK_ID_PATTERN).
        from_user_id:
            The user delegating the task; must be the task owner.
        to_user_id:
            The user who will receive the task.
        create_visibility_grant:
            When ``True`` (default), create a ``VisibilityGrantNode`` so
            *to_user_id* can see and edit the task.
        require_approval:
            When ``True``, create an APPROVAL task to track whether the
            delegation is acted upon within *max_wait_days*.
        max_wait_days:
            Days before the approval task triggers escalation (1–90).
        escalation_target:
            User ID to escalate to when *escalation_action* is ``REASSIGN``.
        escalation_action:
            One of ``REASSIGN``, ``CANCEL``, or ``AUTO_APPROVE``.

        Returns
        -------
        DelegationResult
            A summary of every side-effect applied.

        Raises
        ------
        DelegationError
            If the task does not exist, or *from_user_id* is not the owner.
        """
        # 1. Load and verify ownership
        raw_task = await self._store.get_node(task_id)
        if raw_task is None:
            raise DelegationError(f"Task '{task_id}' not found.")

        task = TaskNode.model_validate(raw_task)
        if task.owned_by != from_user_id and task.created_by != from_user_id:
            raise DelegationError(
                f"User '{from_user_id}' is not the owner of task '{task_id}'."
            )

        now = utcnow()
        grant_id: str | None = None
        approval_task_id: str | None = None

        # 2. Create visibility grant for the delegate
        if create_visibility_grant:
            grant_id = generate_grant_id()
            grant_node = VisibilityGrantNode(
                id=grant_id,
                grantor_user_id=from_user_id,
                granted_to_user_id=to_user_id,
                target_node_id=task_id,
                target_node_type="TaskNode",
                scope=VisibilityScope.EDITOR,
                granted_at=now,
                reason=f"Delegated by {from_user_id}",
                created_at=now,
                updated_at=now,
                version=0,
            )
            await self._store.create_node(grant_node)
            await self._store.create_edge(
                source_id=grant_id,
                target_id=task_id,
                edge_type=EdgeType.GRANTS_ACCESS_TO,
                properties={"granted_at": now.isoformat()},
            )

        # 3. Reassign the task to the delegate
        await self._store.update_node(
            task_id,
            {
                "assigned_to": to_user_id,
                "updated_at": now.isoformat(),
            },
        )

        # 4. Optionally create an APPROVAL task for the delegation
        if require_approval:
            # Derive initials from the delegating user ID for task ID generation.
            # USER-<identifier> — use the first two alpha chars after "USER-".
            initials = _extract_initials(from_user_id)
            approval_task_id = generate_task_id(initials, TaskType.APPROVAL)

            approval_metadata = ApprovalMetadata(
                approver_id=to_user_id,
                approval_criteria=(
                    f"Complete delegated task '{task_id}' within {max_wait_days} days."
                ),
                max_wait_days=max_wait_days,
                escalation_target_user_id=escalation_target,
                escalation_action=escalation_action,
                delegated_by_user_id=from_user_id,
            )

            approval_task = TaskNode(
                id=approval_task_id,
                task_type=TaskType.APPROVAL,
                title=f"Delegation approval: {task.title}",
                description=(
                    f"Tracks the delegation of task '{task_id}' from user "
                    f"'{from_user_id}' to '{to_user_id}'. "
                    f"Escalates after {max_wait_days} days."
                ),
                created_by=from_user_id,
                owned_by=from_user_id,
                assigned_to=to_user_id,
                state=TaskState.PENDING,
                type_metadata=approval_metadata,
                created_at=now,
                updated_at=now,
                version=0,
            )
            await self._store.create_node(approval_task)

            # Link the approval task to the original task via SPAWNED_FROM
            await self._store.create_edge(
                source_id=approval_task_id,
                target_id=task_id,
                edge_type=EdgeType.SPAWNED_FROM,
                properties={"reason": "delegation_approval"},
            )

        return DelegationResult(
            task_id=task_id,
            delegated_to_user_id=to_user_id,
            visibility_grant_id=grant_id,
            approval_task_id=approval_task_id,
        )

    async def revoke_delegation(
        self,
        task_id: str,
        from_user_id: str,
    ) -> None:
        """Revoke a previous delegation by clearing ``assigned_to`` on the task.

        Also marks any active VisibilityGrantNode for (from_user_id →
        task_id) as revoked by setting ``revoked_at`` and ``revoked_by``.

        Parameters
        ----------
        task_id:
            ID of the delegated task.
        from_user_id:
            The user who originally delegated the task; must be the owner.

        Raises
        ------
        DelegationError
            If the task does not exist or *from_user_id* is not the owner.
        """
        raw_task = await self._store.get_node(task_id)
        if raw_task is None:
            raise DelegationError(f"Task '{task_id}' not found.")

        task = TaskNode.model_validate(raw_task)
        if task.owned_by != from_user_id and task.created_by != from_user_id:
            raise DelegationError(
                f"User '{from_user_id}' is not the owner of task '{task_id}'."
            )

        now = utcnow()

        # Clear the assignment
        await self._store.update_node(
            task_id,
            {
                "assigned_to": None,
                "updated_at": now.isoformat(),
            },
        )

        # Revoke any GRANTS_ACCESS_TO edges pointing from a VisibilityGrantNode
        # to this task where the grantor is from_user_id.
        grant_nodes = await self._store.list_nodes(
            label="VisibilityGrantNode",
            filters={
                "grantor_user_id": from_user_id,
                "target_node_id": task_id,
                "revoked_at": None,
            },
        )
        for raw_grant in grant_nodes:
            await self._store.update_node(
                raw_grant["id"],
                {
                    "revoked_at": now.isoformat(),
                    "revoked_by": from_user_id,
                    "updated_at": now.isoformat(),
                },
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_initials(user_id: str) -> str:
    """Derive a 2-character uppercase initials string from a user ID.

    Strips the leading ``USER-`` prefix (if present), then takes the first
    two alphabetic characters of the remainder, defaulting to ``"XX"`` if
    fewer than two alpha chars are found.

    Parameters
    ----------
    user_id:
        A USER-{identifier} string.

    Returns
    -------
    str
        A 2-character uppercase string suitable for use in task ID generation.
    """
    suffix = user_id.removeprefix("USER-") if user_id.startswith("USER-") else user_id
    alpha_chars = [c for c in suffix if c.isalpha()]
    if len(alpha_chars) >= 2:
        return (alpha_chars[0] + alpha_chars[1]).upper()
    if len(alpha_chars) == 1:
        return (alpha_chars[0] + "X").upper()
    return "XX"


__all__ = [
    "DelegationResult",
    "DelegationError",
    "DelegationService",
]
