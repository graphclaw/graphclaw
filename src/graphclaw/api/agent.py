# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.agent — Agent monitor and trigger management endpoints.

Description
-----------
Provides the six cockpit endpoints that expose the agent runtime state and
allow the UI to interact with the trigger engine.

Routes
------
GET  /app/v1/agent/status            — agent running state + last cycle time
GET  /app/v1/agent/action-queue      — ranked action queue from last scoring cycle
GET  /app/v1/agent/briefing          — 5-section daily briefing from graph state
GET  /app/v1/agent/triggers/schedule — list all configured triggers
GET  /app/v1/agent/triggers/{id}     — single trigger detail
POST /app/v1/agent/triggers/{id}/fire — fire an on-demand trigger
POST /app/v1/agent/triggers/{id}/snooze — temporarily disable a trigger
POST /app/v1/agent/triggers/{id}/resume — re-enable a snoozed trigger

Design Patterns
---------------
- Graceful degradation: ``agent_loop`` and ``trigger_engine`` may not be present
  in all deployment configurations.  Status and queue endpoints return empty/
  stopped responses rather than 503 when the runtime objects are absent.
- State access: Runtime objects are read from ``request.app.state``; there are
  no dep providers for optional runtime objects to avoid hard-failing at startup.

Public API
----------
- router: ``APIRouter`` for /agent routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, GraphStoreDep, ScoringEngineDep.
- graphclaw.triggers.briefing: BriefingGenerator.
- graphclaw.triggers.models: TriggerEvent.
- fastapi: APIRouter, HTTPException, Request, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, ScoringEngineDep
from graphclaw.notifications.emit import emit_notification
from graphclaw.notifications.models import NotificationEventType
from graphclaw.triggers.models import TriggerConfig, TriggerType
from graphclaw.triggers.persistence import (
    TriggerPersistenceError,
    UserNodeNotFoundError,
    load_follow_up_settings,
    load_trigger_schedule,
    save_follow_up_settings,
    save_trigger_schedule,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["app-api"])

# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class AgentStatusResponse(BaseModel):
    """Response body for GET /app/v1/agent/status."""

    running: bool
    last_cycle_at: datetime | None = None
    queue_depth: int = 0
    agent_version: str = "1.0.0"


class ActionQueueItem(BaseModel):
    """A single ranked item in the agent action queue."""

    node_id: str
    final_score: float
    rank: int
    recommended_action: str
    autonomy_level: str = "SUGGEST"
    explanation: dict[str, Any] = {}
    batched_with: list[str] = []


class BriefingSectionOut(BaseModel):
    """A single section of the daily briefing."""

    title: str
    items: list[str] = []
    max_items: int = 10


class BriefingResponse(BaseModel):
    """Response body for GET /app/v1/agent/briefing."""

    generated_at: datetime
    session_id: str = ""
    critical: BriefingSectionOut
    inferences: BriefingSectionOut
    completed: BriefingSectionOut
    ahead_of_curve: BriefingSectionOut
    deferred: BriefingSectionOut


class TriggerConfigResponse(BaseModel):
    """Response body for trigger schedule endpoints."""

    trigger_id: str
    trigger_type: str
    user_id: str
    enabled: bool
    cron_expression: str | None = None
    next_fire_at: datetime | None = None
    last_fired_at: datetime | None = None
    payload_template: dict[str, Any] = {}


class TriggerFireResponse(BaseModel):
    """Response body for POST /app/v1/agent/triggers/{id}/fire."""

    trigger_id: str
    trigger_type: str
    user_id: str
    fired_at: datetime


class TriggerSettingsResponse(BaseModel):
    """Response body for trigger follow-up settings endpoints."""

    default_follow_up_days: int
    interrupt_threshold_overrides: dict[str, float] = Field(default_factory=dict)


class TriggerSettingsPatchRequest(BaseModel):
    """Patch model for trigger follow-up settings."""

    default_follow_up_days: int | None = Field(None, ge=1, le=365)
    interrupt_threshold_overrides: dict[str, float] | None = None


class TriggerCreateRequest(BaseModel):
    """Create model for trigger schedule CRUD."""

    trigger_type: TriggerType = TriggerType.TIME_BASED
    cron_expression: str | None = None
    enabled: bool = True
    payload_template: dict[str, Any] = Field(default_factory=dict)


class TriggerUpdateRequest(BaseModel):
    """Partial update model for trigger schedule CRUD."""

    cron_expression: str | None = None
    enabled: bool | None = None
    payload_template: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cfg_to_response(cfg: Any) -> TriggerConfigResponse:
    """Convert a TriggerConfig to its API response form."""
    return TriggerConfigResponse(
        trigger_id=cfg.trigger_id,
        trigger_type=(
            cfg.trigger_type.value if hasattr(cfg.trigger_type, "value") else str(cfg.trigger_type)
        ),
        user_id=cfg.user_id,
        enabled=cfg.enabled,
        cron_expression=cfg.cron_expression,
        next_fire_at=cfg.next_fire_at,
        last_fired_at=cfg.last_fired_at,
        payload_template=dict(cfg.payload_template),
    )


def _get_trigger_registry(request: Request) -> dict[str, Any]:
    """Return the scheduler's _triggers dict, or empty dict when absent."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        return {}
    return getattr(scheduler, "_triggers", {})


def _get_trigger_scheduler(request: Request) -> Any | None:
    """Return the trigger scheduler object when trigger engine is initialised."""
    engine = getattr(request.app.state, "trigger_engine", None)
    if engine is None:
        return None
    scheduler = getattr(engine, "_scheduler", None)
    return scheduler


async def _persist_user_triggers(
    graph_store: Any,
    user_id: str,
    triggers: dict[str, Any],
) -> None:
    """Persist all triggers owned by a user into DB-backed preferences."""
    schedule = [cfg for cfg in triggers.values() if getattr(cfg, "user_id", "") == user_id]
    await save_trigger_schedule(graph_store, user_id, schedule)


# ---------------------------------------------------------------------------
# Routes — agent runtime
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get agent status",
    description=(
        "Return the current running state, last scoring cycle timestamp, and "
        "approximate action queue depth.  Returns ``running: false`` when the "
        "agent loop is not initialised rather than raising 503."
    ),
)
async def get_agent_status(
    user_id: CurrentUserDep,
    request: Request,
) -> AgentStatusResponse:
    """Report agent running state; gracefully handles absent agent_loop."""
    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is None:
        return AgentStatusResponse(running=False)

    last_cycle_at: datetime | None = getattr(agent_loop, "_last_cycle_at", None)
    queue_depth: int = getattr(agent_loop, "_last_queue_depth", 0)
    return AgentStatusResponse(
        running=True,
        last_cycle_at=last_cycle_at,
        queue_depth=queue_depth,
    )


@router.get(
    "/action-queue",
    response_model=list[ActionQueueItem],
    status_code=status.HTTP_200_OK,
    summary="Get action queue",
    description=(
        "Execute one scoring cycle and return the ranked task action queue. "
        "Delegates to ``AgentLoop.run_cycle()`` when available; returns an empty "
        "list when the agent loop is not initialised."
    ),
)
async def get_action_queue(
    user_id: CurrentUserDep,
    scoring_engine: ScoringEngineDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> list[ActionQueueItem]:
    """Return the scored action queue for the authenticated user."""
    agent_loop = getattr(request.app.state, "agent_loop", None)

    if agent_loop is not None and hasattr(agent_loop, "run_cycle"):
        try:
            queue = await agent_loop.run_cycle(user_id=user_id, trigger_source="on_demand")
        except Exception as exc:
            logger.warning("agent: run_cycle failed: %s", exc)
            queue = []
    else:
        queue = []

    return [
        ActionQueueItem(
            node_id=entry.node_id,
            final_score=entry.final_score,
            rank=entry.rank,
            recommended_action=entry.recommended_action,
            autonomy_level=(
                entry.autonomy_level.value
                if hasattr(entry.autonomy_level, "value")
                else str(entry.autonomy_level)
            ),
            explanation=(
                entry.explanation.model_dump() if hasattr(entry.explanation, "model_dump") else {}
            ),
            batched_with=list(entry.batched_with),
        )
        for entry in queue
    ]


@router.get(
    "/briefing",
    response_model=BriefingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get daily briefing",
    description=(
        "Generate and return the 5-section daily briefing (critical, inferences, "
        "completed, ahead-of-curve, deferred) from the current graph state."
    ),
)
async def get_briefing(
    request: Request,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> BriefingResponse:
    """Generate the daily briefing using BriefingGenerator."""
    from graphclaw.triggers.briefing import BriefingGenerator

    try:
        raw_tasks = await graph_store.list_nodes("TaskNode")
    except Exception as exc:
        logger.warning("agent: briefing task fetch failed: %s", exc)
        raw_tasks = []

    try:
        raw_goals = await graph_store.list_nodes("GoalNode")
    except Exception as exc:
        logger.warning("agent: briefing goal fetch failed: %s", exc)
        raw_goals = []

    generator = BriefingGenerator()
    briefing = await generator.generate(tasks=raw_tasks, goals=raw_goals)

    await emit_notification(
        pool=getattr(request.app.state, "pool", None),
        redis=getattr(request.app.state, "redis", None),
        user_id=user_id,
        event_type=NotificationEventType.BRIEFING_READY,
        title="Your daily briefing is ready",
        body=f"{len(briefing.critical.items)} critical items",
        metadata={"briefing_date": briefing.generated_at.date().isoformat()},
    )

    def _section(sec: Any) -> BriefingSectionOut:
        return BriefingSectionOut(
            title=sec.title,
            items=list(sec.items),
            max_items=sec.max_items,
        )

    return BriefingResponse(
        generated_at=briefing.generated_at,
        session_id=briefing.session_id,
        critical=_section(briefing.critical),
        inferences=_section(briefing.inferences),
        completed=_section(briefing.completed),
        ahead_of_curve=_section(briefing.ahead_of_curve),
        deferred=_section(briefing.deferred),
    )


# ---------------------------------------------------------------------------
# Routes — trigger management
# ---------------------------------------------------------------------------


@router.get(
    "/triggers/schedule",
    response_model=list[TriggerConfigResponse],
    status_code=status.HTTP_200_OK,
    summary="List trigger schedule",
    description=(
        "Return all triggers registered with the trigger engine, including their "
        "cron expressions and next scheduled fire times.  Returns an empty list "
        "when the trigger engine is not initialised."
    ),
)
async def list_trigger_schedule(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> list[TriggerConfigResponse]:
    """List all registered TriggerConfigs from the trigger engine scheduler."""
    scheduler = _get_trigger_scheduler(request)
    triggers = _get_trigger_registry(request)
    if not triggers:
        persisted = await load_trigger_schedule(graph_store, user_id)
        if scheduler is not None:
            for cfg in persisted:
                scheduler.register(cfg)
            triggers = _get_trigger_registry(request)
        else:
            triggers = {cfg.trigger_id: cfg for cfg in persisted}

    filtered = [cfg for cfg in triggers.values() if getattr(cfg, "user_id", "") == user_id]
    return [_cfg_to_response(cfg) for cfg in filtered]


@router.get(
    "/triggers/settings",
    response_model=TriggerSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get trigger follow-up settings",
    description=("Return DB-backed follow-up policy settings for the authenticated user."),
)
async def get_trigger_settings(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> TriggerSettingsResponse:
    """Return persisted follow-up trigger settings for the authenticated user."""
    try:
        settings = await load_follow_up_settings(graph_store, user_id)
    except UserNodeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc

    return TriggerSettingsResponse(
        default_follow_up_days=settings["default_follow_up_days"],
        interrupt_threshold_overrides=settings["interrupt_threshold_overrides"],
    )


@router.patch(
    "/triggers/settings",
    response_model=TriggerSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update trigger follow-up settings",
    description=("Persist follow-up policy settings for the authenticated user in graph DB."),
)
async def patch_trigger_settings(
    body: TriggerSettingsPatchRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> TriggerSettingsResponse:
    """Persist and return follow-up trigger settings."""
    try:
        settings = await save_follow_up_settings(
            graph_store,
            user_id,
            default_follow_up_days=body.default_follow_up_days,
            interrupt_threshold_overrides=body.interrupt_threshold_overrides,
        )
    except UserNodeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc

    return TriggerSettingsResponse(
        default_follow_up_days=settings["default_follow_up_days"],
        interrupt_threshold_overrides=settings["interrupt_threshold_overrides"],
    )


@router.post(
    "/triggers/schedule",
    response_model=TriggerConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create trigger",
    description="Create a trigger and persist it as DB-backed schedule state.",
)
async def create_trigger(
    body: TriggerCreateRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> TriggerConfigResponse:
    """Create a trigger, register it in scheduler, and persist schedule in DB."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    if body.trigger_type == TriggerType.TIME_BASED and not body.cron_expression:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cron_expression is required for TIME_BASED triggers",
        )

    next_fire_at: datetime | None = None
    if body.trigger_type == TriggerType.TIME_BASED and body.cron_expression:
        compute_next = getattr(scheduler, "_compute_next_cron", None)
        if callable(compute_next):
            next_fire_at = compute_next(body.cron_expression, datetime.now(timezone.utc))

    cfg = TriggerConfig(
        trigger_id=f"TRIG-{uuid.uuid4().hex[:12]}",
        trigger_type=body.trigger_type,
        user_id=user_id,
        enabled=body.enabled,
        cron_expression=body.cron_expression,
        next_fire_at=next_fire_at,
        payload_template=dict(body.payload_template),
    )
    scheduler.register(cfg)

    try:
        await _persist_user_triggers(graph_store, user_id, _get_trigger_registry(request))
    except UserNodeNotFoundError as exc:
        scheduler.unregister(cfg.trigger_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc
    except TriggerPersistenceError as exc:
        scheduler.unregister(cfg.trigger_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist trigger schedule",
        ) from exc

    return _cfg_to_response(cfg)


@router.get(
    "/triggers/{trigger_id}",
    response_model=TriggerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get trigger detail",
    description="Return the configuration and schedule for a specific trigger by ID.",
)
async def get_trigger(
    trigger_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> TriggerConfigResponse:
    """Return a single TriggerConfig by ID."""
    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None:
        persisted = await load_trigger_schedule(graph_store, user_id)
        for candidate in persisted:
            if candidate.trigger_id == trigger_id:
                cfg = candidate
                break
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )
    if cfg.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )
    return _cfg_to_response(cfg)


@router.patch(
    "/triggers/{trigger_id}",
    response_model=TriggerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Update trigger",
    description="Update a trigger and persist schedule changes in graph DB.",
)
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdateRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> TriggerConfigResponse:
    """Update trigger schedule state and persist DB-backed configuration."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None or cfg.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )

    updates: dict[str, Any] = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.cron_expression is not None:
        updates["cron_expression"] = body.cron_expression
        if cfg.trigger_type == TriggerType.TIME_BASED:
            compute_next = getattr(scheduler, "_compute_next_cron", None)
            if callable(compute_next):
                updates["next_fire_at"] = compute_next(
                    body.cron_expression,
                    datetime.now(timezone.utc),
                )
    if body.payload_template is not None:
        updates["payload_template"] = dict(body.payload_template)

    updated = cfg.model_copy(update=updates)
    triggers[trigger_id] = updated

    try:
        await _persist_user_triggers(graph_store, user_id, triggers)
    except UserNodeNotFoundError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc
    except TriggerPersistenceError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist trigger schedule",
        ) from exc

    return _cfg_to_response(updated)


@router.delete(
    "/triggers/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete trigger",
    description="Delete a trigger and persist schedule changes in graph DB.",
)
async def delete_trigger(
    trigger_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> None:
    """Delete a trigger from scheduler registry and persisted DB schedule."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None or cfg.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )

    del triggers[trigger_id]
    try:
        await _persist_user_triggers(graph_store, user_id, triggers)
    except UserNodeNotFoundError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc
    except TriggerPersistenceError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist trigger schedule",
        ) from exc


@router.post(
    "/triggers/{trigger_id}/fire",
    response_model=TriggerFireResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fire trigger on-demand",
    description=(
        "Immediately dispatch an ON_DEMAND trigger event for the authenticated "
        "user.  The trigger_id is echoed in the payload so downstream consumers "
        "can correlate the event."
    ),
)
async def fire_trigger(
    trigger_id: str,
    user_id: CurrentUserDep,
    request: Request,
) -> TriggerFireResponse:
    """Dispatch an ON_DEMAND trigger via the trigger engine."""
    trigger_engine = getattr(request.app.state, "trigger_engine", None)
    if trigger_engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    try:
        event = await trigger_engine.fire_on_demand(
            user_id=user_id,
            payload={"source": "cockpit", "trigger_id": trigger_id},
        )
    except Exception as exc:
        logger.error("agent: fire_on_demand failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fire trigger",
        ) from exc

    return TriggerFireResponse(
        trigger_id=event.trigger_id,
        trigger_type=event.trigger_type.value,
        user_id=event.user_id,
        fired_at=event.created_at,
    )


@router.post(
    "/triggers/{trigger_id}/snooze",
    response_model=TriggerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Snooze trigger",
    description="Disable an existing trigger until it is resumed.",
)
async def snooze_trigger(
    trigger_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> TriggerConfigResponse:
    """Disable a trigger in the in-memory scheduler registry."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None or cfg.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )

    updated = cfg.model_copy(update={"enabled": False})
    triggers[trigger_id] = updated

    try:
        await _persist_user_triggers(graph_store, user_id, triggers)
    except UserNodeNotFoundError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc
    except TriggerPersistenceError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist trigger schedule",
        ) from exc

    return _cfg_to_response(updated)


@router.post(
    "/triggers/{trigger_id}/resume",
    response_model=TriggerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume trigger",
    description="Re-enable a snoozed trigger and recompute its next fire time.",
)
async def resume_trigger(
    trigger_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    request: Request,
) -> TriggerConfigResponse:
    """Re-enable a trigger and recompute schedule when required."""
    scheduler = _get_trigger_scheduler(request)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trigger engine is not initialised",
        )

    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None or cfg.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )

    next_fire_at = cfg.next_fire_at
    if cfg.trigger_type == TriggerType.TIME_BASED and cfg.cron_expression:
        compute_next = getattr(scheduler, "_compute_next_cron", None)
        if callable(compute_next):
            next_fire_at = compute_next(cfg.cron_expression, datetime.now(timezone.utc))

    updated = cfg.model_copy(
        update={
            "enabled": True,
            "next_fire_at": next_fire_at,
        }
    )
    triggers[trigger_id] = updated

    try:
        await _persist_user_triggers(graph_store, user_id, triggers)
    except UserNodeNotFoundError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        ) from exc
    except TriggerPersistenceError as exc:
        triggers[trigger_id] = cfg
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist trigger schedule",
        ) from exc

    return _cfg_to_response(updated)
