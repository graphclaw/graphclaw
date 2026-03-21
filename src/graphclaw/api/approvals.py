"""graphclaw.api.approvals — MCP tool approval endpoints.

Description
-----------
Provides endpoints for managing pending APPROVAL tasks — tasks that require
explicit human confirmation before a gated MCP tool call is executed.

Endpoints
---------
- ``GET  /app/v1/approvals``                 — List pending APPROVAL tasks.
- ``POST /app/v1/approvals/{task_id}/approve`` — Approve, setting task COMPLETE.
- ``POST /app/v1/approvals/{task_id}/deny``    — Deny, setting task CANCELLED.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- Stub storage: A module-level dict simulates pending approvals until the
  graph store integration is implemented.
- 404 on missing task: Unknown task IDs always return HTTP 404 regardless of
  the action requested, to avoid leaking information about other users' tasks.

Public API
----------
- router: ``APIRouter`` for /approvals routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- fastapi: APIRouter, Depends, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["app-api"])

# ── Stub in-memory storage ─────────────────────────────────────────────────────

# Maps user_id -> list of approval task dicts
_pending_approvals: dict[str, list[dict[str, Any]]] = {}


# ── Response models ────────────────────────────────────────────────────────────


class ApprovalTask(BaseModel):
    """A pending approval task."""

    task_id: str
    description: str
    tool_name: str
    tool_args: dict[str, Any] = {}
    status: str = "APPROVAL"
    created_at: str | None = None


class ApprovalActionResponse(BaseModel):
    """Response body for approve/deny actions."""

    task_id: str
    status: str
    ok: bool = True


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[ApprovalTask],
    status_code=status.HTTP_200_OK,
    summary="List pending approval tasks",
    description=(
        "Return all tasks in APPROVAL status for the authenticated user.  "
        "These are gated MCP tool calls awaiting human confirmation."
    ),
)
async def list_approvals(
    user_id: str = Depends(require_auth),
) -> list[ApprovalTask]:
    """List pending APPROVAL tasks for the authenticated user.

    Parameters
    ----------
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    list[ApprovalTask]:
        All tasks currently in APPROVAL status for this user.
    """
    tasks = _pending_approvals.get(user_id, [])
    return [ApprovalTask(**t) for t in tasks if t.get("status") == "APPROVAL"]


@router.post(
    "/{task_id}/approve",
    response_model=ApprovalActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve a pending task",
    description=(
        "Approve an APPROVAL task, transitioning it to COMPLETE status.  "
        "This permits the gated MCP tool call to proceed."
    ),
)
async def approve_task(
    task_id: str,
    user_id: str = Depends(require_auth),
) -> ApprovalActionResponse:
    """Approve a pending APPROVAL task.

    Parameters
    ----------
    task_id:
        ID of the task to approve.
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    ApprovalActionResponse:
        ``{"task_id": ..., "status": "COMPLETE", "ok": true}``.

    Raises
    ------
    HTTPException(404):
        If the task does not exist or does not belong to the user.
    """
    tasks = _pending_approvals.get(user_id, [])
    for task in tasks:
        if task.get("task_id") == task_id:
            task["status"] = "COMPLETE"
            logger.info("approvals: task %s approved by user_id=%s", task_id, user_id)
            return ApprovalActionResponse(task_id=task_id, status="COMPLETE")
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Approval task '{task_id}' not found",
    )


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
    user_id: str = Depends(require_auth),
) -> ApprovalActionResponse:
    """Deny a pending APPROVAL task.

    Parameters
    ----------
    task_id:
        ID of the task to deny.
    user_id:
        Platform user ID extracted from the Bearer access token.

    Returns
    -------
    ApprovalActionResponse:
        ``{"task_id": ..., "status": "CANCELLED", "ok": true}``.

    Raises
    ------
    HTTPException(404):
        If the task does not exist or does not belong to the user.
    """
    tasks = _pending_approvals.get(user_id, [])
    for task in tasks:
        if task.get("task_id") == task_id:
            task["status"] = "CANCELLED"
            logger.info("approvals: task %s denied by user_id=%s", task_id, user_id)
            return ApprovalActionResponse(task_id=task_id, status="CANCELLED")
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Approval task '{task_id}' not found",
    )
