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

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graphclaw.api.deps import AdminGraphStoreDep, CallerContextDep
from graphclaw.models.base import utcnow

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
        user_node_raw = await store.get_node(body.user_id, caller_context=caller_context)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"User not found: {body.user_id}") from exc

    if user_node_raw is None:
        raise HTTPException(status_code=404, detail=f"User not found: {body.user_id}")

    updates: dict[str, Any] = {}
    raw_prefs = user_node_raw.get("preferences", {}) if isinstance(user_node_raw, dict) else {}
    if isinstance(raw_prefs, str):
        try:
            raw_prefs = json.loads(raw_prefs)
        except (ValueError, TypeError):
            raw_prefs = {}
    current_prefs: dict[str, Any] = dict(raw_prefs) if isinstance(raw_prefs, dict) else {}

    if body.default_follow_up_days is not None:
        current_prefs["default_follow_up_days"] = body.default_follow_up_days
        updates["preferences"] = current_prefs

    if body.interrupt_threshold_overrides is not None:
        current_prefs["interrupt_threshold_overrides"] = body.interrupt_threshold_overrides
        updates["preferences"] = current_prefs

    if updates:
        await store.update_node(body.user_id, updates, caller_context=caller_context)

    return FollowUpConfigResponse(
        user_id=body.user_id,
        default_follow_up_days=current_prefs.get("default_follow_up_days", 3),
        interrupt_threshold_overrides=current_prefs.get("interrupt_threshold_overrides", {}),
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
