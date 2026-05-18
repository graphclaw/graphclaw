# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.triggers — Admin endpoints for trigger tuning (FR-SCHED-001).

Description
-----------
Exposes admin-only REST endpoints for viewing and configuring the follow-up
trigger cadence and the owner-offline escalation queue per user.

Routes
------
GET  /admin/triggers                          — list pending escalations
POST /admin/triggers/follow_up/configure      — update per-user follow-up config
GET  /admin/triggers/escalation/{user_id}     — list pending decisions for user
POST /admin/triggers/escalation/{decision_id}/resolve — resolve a pending decision
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graphclaw.api.deps import AdminGraphStoreDep, CallerContextDep
from graphclaw.models.base import utcnow
from graphclaw.triggers.persistence import (
    UserNodeNotFoundError,
    load_follow_up_settings,
    save_follow_up_settings,
)

router = APIRouter(prefix="/admin/triggers", tags=["admin-triggers"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class FollowUpConfigRequest(BaseModel):
    user_id: str
    default_follow_up_days: int | None = Field(None, ge=1, le=365)
    interrupt_threshold_overrides: dict[str, float] | None = None


class FollowUpConfigResponse(BaseModel):
    user_id: str
    default_follow_up_days: int
    interrupt_threshold_overrides: dict[str, float]
    updated_at: str


class EscalationQueueItem(BaseModel):
    id: str
    user_id: str
    context_ref: str
    prompt: str
    proposed_action: dict
    created_at: str
    expires_at: str | None
    resolved_at: str | None
    resolution: str | None


class ResolveDecisionRequest(BaseModel):
    resolution: str = "owner_decided"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_trigger_config(
    store: AdminGraphStoreDep,
) -> dict[str, Any]:
    """List current trigger configuration summary (admin only)."""
    return {
        "status": "active",
        "description": "Follow-up triggers run on cron cadence per user preferences.",
        "retrieved_at": utcnow().isoformat(),
    }


@router.post("/follow_up/configure")
async def configure_follow_up(
    body: FollowUpConfigRequest,
    store: AdminGraphStoreDep,
    caller_context: CallerContextDep,
) -> FollowUpConfigResponse:
    """Update follow-up trigger configuration for a user (FR-SCHED-001).

    Persists ``default_follow_up_days`` and ``interrupt_threshold_overrides``
    onto the user's ``UserNode.preferences``.
    """
    try:
        persisted = await save_follow_up_settings(
            store,
            body.user_id,
            default_follow_up_days=body.default_follow_up_days,
            interrupt_threshold_overrides=body.interrupt_threshold_overrides,
            caller_context=caller_context,
        )
    except UserNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"User not found: {body.user_id}") from exc

    return FollowUpConfigResponse(
        user_id=body.user_id,
        default_follow_up_days=persisted["default_follow_up_days"],
        interrupt_threshold_overrides=persisted["interrupt_threshold_overrides"],
        updated_at=utcnow().isoformat(),
    )


@router.get("/follow_up/{user_id}")
async def get_follow_up_config(
    user_id: str,
    store: AdminGraphStoreDep,
    caller_context: CallerContextDep,
) -> FollowUpConfigResponse:
    """Read follow-up trigger configuration for a specific user."""
    try:
        settings = await load_follow_up_settings(store, user_id, caller_context=caller_context)
    except UserNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"User not found: {user_id}") from exc

    return FollowUpConfigResponse(
        user_id=user_id,
        default_follow_up_days=settings["default_follow_up_days"],
        interrupt_threshold_overrides=settings["interrupt_threshold_overrides"],
        updated_at=utcnow().isoformat(),
    )


@router.get("/escalation/{user_id}")
async def list_escalation_queue(
    user_id: str,
    store: AdminGraphStoreDep,
) -> dict[str, Any]:
    """List pending decisions in the escalation queue for *user_id* (FR-SCHED-002)."""
    # Import here to avoid heavy import at module load
    from graphclaw.agent.escalation import OwnerOfflineEscalationQueue  # noqa: PLC0415

    queue = OwnerOfflineEscalationQueue()
    pending = await queue.list_pending(user_id)
    return {
        "user_id": user_id,
        "pending_count": len(pending),
        "items": [p.to_dict() for p in pending],
    }


@router.post("/escalation/{decision_id}/resolve")
async def resolve_escalation(
    decision_id: str,
    body: ResolveDecisionRequest,
    store: AdminGraphStoreDep,
) -> dict[str, Any]:
    """Resolve a pending decision in the escalation queue (FR-SCHED-002)."""
    from graphclaw.agent.escalation import OwnerOfflineEscalationQueue  # noqa: PLC0415

    queue = OwnerOfflineEscalationQueue()
    resolved = await queue.resolve(decision_id, body.resolution)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")
    return {
        "decision_id": decision_id,
        "resolution": body.resolution,
        "resolved_at": utcnow().isoformat(),
    }
