# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.approvals — MCP tool approval endpoints.

Description
-----------
Provides endpoints for managing pending APPROVAL tasks — tasks that require
explicit human confirmation before a gated MCP tool call is executed.

Endpoints
---------
- ``GET  /app/v1/approvals``                  — List pending APPROVAL tasks.
- ``POST /app/v1/approvals/{task_id}/approve`` — Approve, driving task to COMPLETE.
- ``POST /app/v1/approvals/{task_id}/deny``    — Deny, transitioning task to CANCELLED.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- Graph-backed: APPROVAL tasks are ``TaskNode`` vertices stored in the graph
  database with ``task_type=APPROVAL``.  The list endpoint queries both PENDING
  and IN_PROGRESS states since the agent may advance a task to IN_PROGRESS while
  waiting for approval.
- StateMachine chaining: Approving a task drives it through required intermediate
  states (PENDING → ACTIVE → IN_PROGRESS → COMPLETE) since the state table does
  not allow a direct PENDING → COMPLETE jump.
- Ownership guard: Both approve and deny validate that the task is an APPROVAL type
  assigned to the authenticated user before applying any transition.

Public API
----------
- router: ``APIRouter`` for /approvals routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, GraphStoreDep, StateMachineDep.
- graphclaw.models.enums: ChangedBy, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.state.transitions: InvalidTransitionError.
- fastapi: APIRouter, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import CallerContextDep, CurrentUserDep, GraphStoreDep, StateMachineDep
from graphclaw.models.enums import ChangedBy, TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.state.cascade import persist_transition, run_post_transition_cascade
from graphclaw.state.transitions import InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["app-api"])

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ApprovalTask(BaseModel):
    """A pending approval task."""

    task_id: str
    title: str
    description: str
    approval_criteria: str | None = None
    status: str = "PENDING"
    created_at: str | None = None
    assigned_to: str | None = None


class ApprovalActionResponse(BaseModel):
    """Response body for approve/deny actions."""

    task_id: str
    status: str
    ok: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ApprovalTask],
    status_code=status.HTTP_200_OK,
    summary="List pending approval tasks",
    description=(
        "Return all tasks in APPROVAL state (PENDING or IN_PROGRESS) for the "
        "authenticated user.  These are gated MCP tool calls awaiting human "
        "confirmation before execution."
    ),
)
async def list_approvals(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    caller_context: CallerContextDep,
) -> list[ApprovalTask]:
    """List APPROVAL tasks assigned to the authenticated user."""
    pending = await graph_store.list_nodes(
        "TaskNode",
        {
            "task_type": TaskType.APPROVAL.value,
            "assigned_to": user_id,
            "state": TaskState.PENDING.value,
        },
        caller_context=caller_context,
    )
    in_progress = await graph_store.list_nodes(
        "TaskNode",
        {
            "task_type": TaskType.APPROVAL.value,
            "assigned_to": user_id,
            "state": TaskState.IN_PROGRESS.value,
        },
        caller_context=caller_context,
    )
    return [_task_dict_to_approval(t) for t in list(pending) + list(in_progress)]


@router.post(
    "/{task_id}/approve",
    response_model=ApprovalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve a pending task",
    description=(
        "Approve an APPROVAL task, driving it to COMPLETE status via the state "
        "machine.  This permits the gated MCP tool call to proceed."
    ),
)
async def approve_task(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    state_machine: StateMachineDep,
    caller_context: CallerContextDep,
) -> ApprovalActionResponse:
    """Drive an APPROVAL task to COMPLETE."""
    raw_task = await graph_store.get_node(task_id, caller_context=caller_context)
    if raw_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    _assert_approval_ownership(raw_task, user_id, task_id)

    try:
        task_node = TaskNode.model_validate(raw_task)
    except Exception as exc:
        logger.error("approvals: failed to deserialise task %s: %s", task_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load task '{task_id}': {exc}",
        )

    try:
        _drive_to_complete(state_machine, task_node)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    await graph_store.update_node(
        task_id, task_node.model_dump(mode="json"), caller_context=caller_context
    )
    await run_post_transition_cascade(task_node, graph_store, caller_context=caller_context)
    logger.info("approvals: task %s approved by user_id=%s", task_id, user_id)
    return ApprovalActionResponse(task_id=task_id, status="COMPLETE")


@router.post(
    "/{task_id}/deny",
    response_model=ApprovalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Deny a pending task",
    description=(
        "Deny an APPROVAL task, transitioning it to CANCELLED status.  "
        "The gated MCP tool call will not proceed."
    ),
)
async def deny_task(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    state_machine: StateMachineDep,
    caller_context: CallerContextDep,
) -> ApprovalActionResponse:
    """Transition an APPROVAL task to CANCELLED."""
    raw_task = await graph_store.get_node(task_id, caller_context=caller_context)
    if raw_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found"
        )

    _assert_approval_ownership(raw_task, user_id, task_id)

    try:
        task_node = TaskNode.model_validate(raw_task)
    except Exception as exc:
        logger.error("approvals: failed to deserialise task %s: %s", task_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load task '{task_id}': {exc}",
        )

    try:
        await persist_transition(
            task_node,
            TaskState.CANCELLED,
            ChangedBy.HUMAN,
            "Denied by user",
            graph_store,
            state_machine,
            caller_context=caller_context,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    logger.info("approvals: task %s denied by user_id=%s", task_id, user_id)
    return ApprovalActionResponse(task_id=task_id, status="CANCELLED")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_approval_ownership(raw_task: dict[str, Any], user_id: str, task_id: str) -> None:
    """Raise HTTP 404 if task is not an APPROVAL type belonging to *user_id*.

    Using 404 (not 403) intentionally so callers cannot distinguish missing
    tasks from ownership mismatches.
    """
    is_approval = raw_task.get("task_type") == TaskType.APPROVAL.value
    owner = raw_task.get("assigned_to") or raw_task.get("owned_by") or raw_task.get("created_by")
    if not is_approval or owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval task '{task_id}' not found",
        )


def _drive_to_complete(state_machine, task_node: TaskNode) -> None:
    """Chain state transitions to advance *task_node* to COMPLETE.

    The state table does not allow PENDING → COMPLETE directly.
    Required path: PENDING → ACTIVE → IN_PROGRESS → COMPLETE.
    """
    current = task_node.state
    reason = "Approved by user"

    if current == TaskState.PENDING:
        state_machine.transition(task_node, TaskState.ACTIVE, ChangedBy.HUMAN, reason)
        current = task_node.state

    if current == TaskState.ACTIVE:
        state_machine.transition(task_node, TaskState.IN_PROGRESS, ChangedBy.HUMAN, reason)
        current = task_node.state

    if current == TaskState.IN_PROGRESS:
        state_machine.transition(task_node, TaskState.COMPLETE, ChangedBy.HUMAN, reason)
    elif current != TaskState.COMPLETE:
        # Fallback: attempt direct transition from whatever current state is
        state_machine.transition(task_node, TaskState.COMPLETE, ChangedBy.HUMAN, reason)


def _task_dict_to_approval(task: dict[str, Any]) -> ApprovalTask:
    """Map a raw TaskNode dict to an ``ApprovalTask`` response."""
    type_meta = task.get("type_metadata") or {}
    criteria: str | None = None
    if isinstance(type_meta, dict):
        criteria = type_meta.get("approval_criteria")

    timeline = task.get("timeline") or {}
    created_at = task.get("created_at") or (
        timeline.get("created_at") if isinstance(timeline, dict) else None
    )

    return ApprovalTask(
        task_id=task["id"],
        title=task.get("title", "Approval Required"),
        description=task.get("description", ""),
        approval_criteria=criteria,
        status=task.get("state", TaskState.PENDING.value),
        created_at=str(created_at) if created_at else None,
        assigned_to=task.get("assigned_to"),
    )
