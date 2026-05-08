# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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


# ---------------------------------------------------------------------------
# FR-SCHED-002 — Owner-offline escalation queue
# ---------------------------------------------------------------------------


import uuid as _uuid  # noqa: E402 — placed here to avoid circular import noise
from typing import Any


class PendingDecision:
    """A pending decision in the owner-offline escalation queue (FR-SCHED-002).

    Parameters
    ----------
    id:
        UUID primary key.
    user_id:
        Owner of this decision.
    context_ref:
        Opaque reference to the triggering entity (task_id, checkin_id, …).
    prompt:
        Human-readable question presented to the owner.
    proposed_action:
        Structured fallback action the agent will take after expiry.
    created_at:
        When the decision was enqueued.
    expires_at:
        Deadline after which the proposed_action is taken automatically.
    resolved_at:
        Set when the owner responds or the fallback fires.
    resolution:
        ``"owner_decided"`` | ``"fallback_conservative"`` | ``"fallback_proposed"``.
    """

    __slots__ = (
        "id",
        "user_id",
        "context_ref",
        "prompt",
        "proposed_action",
        "created_at",
        "expires_at",
        "resolved_at",
        "resolution",
    )

    def __init__(
        self,
        user_id: str,
        context_ref: str,
        prompt: str,
        proposed_action: dict,
        expires_at: datetime | None = None,
        id: str | None = None,
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
        resolution: str | None = None,
    ) -> None:
        self.id = id or str(_uuid.uuid4())
        self.user_id = user_id
        self.context_ref = context_ref
        self.prompt = prompt
        self.proposed_action = proposed_action
        self.created_at = created_at or utcnow()
        self.expires_at = expires_at
        self.resolved_at = resolved_at
        self.resolution = resolution

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "context_ref": self.context_ref,
            "prompt": self.prompt,
            "proposed_action": self.proposed_action,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution": self.resolution,
        }


class OwnerOfflineEscalationQueue:
    """Pending-decision queue with timeout fallback (FR-SCHED-002).

    When the comms agent identifies an action that requires owner approval
    but the owner is unreachable, it enqueues a ``PendingDecision``.  The
    queue holds it until either:

    1. The owner next opens Cockpit and resolves it (``resolve``), or
    2. ``process_expired`` fires the conservative fallback.

    The queue is backed by a Postgres table (migration 0019).  An in-process
    list is used as a cache; a full DB implementation is wired at the service
    layer by the caller supplying a ``db_pool`` async callable.

    Parameters
    ----------
    db_pool:
        Optional async callable ``(sql, params) → list[dict]`` for Postgres
        persistence.  When ``None`` the queue operates in-memory only (useful
        for unit tests).
    on_owner_unreachable_after_hours:
        Default hours to wait before fallback fires (per-policy override
        possible per FR-POL-001).
    """

    def __init__(
        self,
        db_pool: Any | None = None,
        on_owner_unreachable_after_hours: int = 24,
    ) -> None:
        self._pool = db_pool
        self._default_wait_hours = on_owner_unreachable_after_hours
        # In-memory cache keyed by decision id
        self._cache: dict[str, PendingDecision] = {}

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        user_id: str,
        context_ref: str,
        prompt: str,
        proposed_action: dict,
        wait_hours: int | None = None,
    ) -> PendingDecision:
        """Create and persist a pending decision.

        Parameters
        ----------
        user_id:
            Owner who must decide.
        context_ref:
            Reference to the triggering entity.
        prompt:
            Human-readable question for the owner.
        proposed_action:
            Fallback action dict (will be logged and optionally executed).
        wait_hours:
            Override for how long to wait; uses ``default`` when None.

        Returns
        -------
        PendingDecision
            The created pending decision.
        """
        hours = wait_hours if wait_hours is not None else self._default_wait_hours
        expires_at = utcnow() + timedelta(hours=hours)
        decision = PendingDecision(
            user_id=user_id,
            context_ref=context_ref,
            prompt=prompt,
            proposed_action=proposed_action,
            expires_at=expires_at,
        )
        self._cache[decision.id] = decision
        if self._pool is not None:
            await self._persist_insert(decision)
        return decision

    # ------------------------------------------------------------------
    # Resolve (owner decided)
    # ------------------------------------------------------------------

    async def resolve(self, decision_id: str, resolution: str = "owner_decided") -> bool:
        """Mark a pending decision as resolved by the owner.

        Parameters
        ----------
        decision_id:
            UUID of the decision to resolve.
        resolution:
            Resolution label (default ``"owner_decided"``).

        Returns
        -------
        bool
            ``True`` if found and resolved; ``False`` if not found.
        """
        decision = self._cache.get(decision_id)
        if decision is None and self._pool is not None:
            decision = await self._load_from_db(decision_id)
        if decision is None:
            return False
        decision.resolved_at = utcnow()
        decision.resolution = resolution
        if self._pool is not None:
            await self._persist_update(decision)
        return True

    # ------------------------------------------------------------------
    # List pending for owner
    # ------------------------------------------------------------------

    async def list_pending(self, user_id: str) -> list[PendingDecision]:
        """Return all unresolved decisions for *user_id*.

        Combines in-memory cache with DB lookup when pool is available.
        """
        pending = [
            d for d in self._cache.values() if d.user_id == user_id and d.resolved_at is None
        ]
        if self._pool is not None:
            db_decisions = await self._load_pending_from_db(user_id)
            seen_ids = {d.id for d in pending}
            for d in db_decisions:
                if d.id not in seen_ids:
                    pending.append(d)
                    self._cache[d.id] = d
        return sorted(pending, key=lambda d: d.created_at)

    # ------------------------------------------------------------------
    # Process expired — conservative fallback
    # ------------------------------------------------------------------

    async def process_expired(self) -> list[PendingDecision]:
        """Fire conservative fallback for all expired, unresolved decisions.

        The fallback is conservative: the ``proposed_action`` is logged but
        NOT executed automatically (prevents duplicate or unexpected writes).
        Callers that want AUTO-execute should subclass and override
        ``_apply_fallback``.

        Returns
        -------
        list[PendingDecision]
            All decisions that were expired and had their fallback logged.
        """
        now = utcnow()
        expired: list[PendingDecision] = []

        candidates = list(self._cache.values())
        if self._pool is not None:
            db_candidates = await self._load_all_unresolved_from_db()
            seen_ids = {d.id for d in candidates}
            for d in db_candidates:
                if d.id not in seen_ids:
                    candidates.append(d)
                    self._cache[d.id] = d

        for decision in candidates:
            if decision.resolved_at is not None:
                continue
            if decision.expires_at is None or now < decision.expires_at:
                continue
            # Apply conservative fallback
            await self._apply_fallback(decision, now)
            expired.append(decision)

        return expired

    async def _apply_fallback(self, decision: PendingDecision, now: datetime) -> None:
        """Conservative fallback: mark resolved with conservative label (no auto-exec)."""
        decision.resolved_at = now
        decision.resolution = "fallback_conservative"
        if self._pool is not None:
            await self._persist_update(decision)

    # ------------------------------------------------------------------
    # DB persistence helpers (no-op when pool is None)
    # ------------------------------------------------------------------

    async def _persist_insert(self, decision: PendingDecision) -> None:
        """Insert a new decision row into escalation_queue."""
        import json  # noqa: PLC0415

        sql = """
            INSERT INTO escalation_queue
                (id, user_id, context_ref, prompt, proposed_action,
                 created_at, expires_at, resolved_at, resolution)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (id) DO NOTHING
        """
        params = (
            decision.id,
            decision.user_id,
            decision.context_ref,
            decision.prompt,
            json.dumps(decision.proposed_action),
            decision.created_at,
            decision.expires_at,
            decision.resolved_at,
            decision.resolution,
        )
        try:
            await self._pool(sql, params)
        except Exception:  # noqa: BLE001
            pass  # DB write failure is non-fatal; in-memory state preserved

    async def _persist_update(self, decision: PendingDecision) -> None:
        """Update resolution fields on an existing escalation_queue row."""
        sql = """
            UPDATE escalation_queue
               SET resolved_at = $1, resolution = $2
             WHERE id = $3
        """
        try:
            await self._pool(sql, (decision.resolved_at, decision.resolution, decision.id))
        except Exception:  # noqa: BLE001
            pass

    async def _load_from_db(self, decision_id: str) -> PendingDecision | None:
        """Load a single decision from DB."""

        sql = "SELECT * FROM escalation_queue WHERE id = $1"
        try:
            rows = await self._pool(sql, (decision_id,))
            if not rows:
                return None
            return self._row_to_decision(rows[0])
        except Exception:  # noqa: BLE001
            return None

    async def _load_pending_from_db(self, user_id: str) -> list[PendingDecision]:
        sql = "SELECT * FROM escalation_queue WHERE user_id = $1 AND resolved_at IS NULL"
        try:
            rows = await self._pool(sql, (user_id,))
            return [self._row_to_decision(r) for r in (rows or [])]
        except Exception:  # noqa: BLE001
            return []

    async def _load_all_unresolved_from_db(self) -> list[PendingDecision]:
        sql = "SELECT * FROM escalation_queue WHERE resolved_at IS NULL"
        try:
            rows = await self._pool(sql, ())
            return [self._row_to_decision(r) for r in (rows or [])]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _row_to_decision(row: dict) -> PendingDecision:
        import json as _json  # noqa: PLC0415

        proposed = row.get("proposed_action", {})
        if isinstance(proposed, str):
            proposed = _json.loads(proposed)
        return PendingDecision(
            id=str(row["id"]),
            user_id=row["user_id"],
            context_ref=row.get("context_ref", ""),
            prompt=row.get("prompt", ""),
            proposed_action=proposed,
            expires_at=row.get("expires_at"),
            created_at=row.get("created_at"),
            resolved_at=row.get("resolved_at"),
            resolution=row.get("resolution"),
        )


__all__ = [
    "EscalationEvent",
    "EscalationError",
    "EscalationService",
]
