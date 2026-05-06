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
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, ScoringEngineDep
from graphclaw.triggers.models import TriggerType

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
            queue = await agent_loop.run_cycle()
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
    request: Request,
) -> list[TriggerConfigResponse]:
    """List all registered TriggerConfigs from the trigger engine scheduler."""
    triggers = _get_trigger_registry(request)
    return [_cfg_to_response(cfg) for cfg in triggers.values()]


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
    request: Request,
) -> TriggerConfigResponse:
    """Return a single TriggerConfig by ID."""
    triggers = _get_trigger_registry(request)
    cfg = triggers.get(trigger_id)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )
    return _cfg_to_response(cfg)


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
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger '{trigger_id}' not found",
        )

    updated = cfg.model_copy(update={"enabled": False})
    triggers[trigger_id] = updated
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
    if cfg is None:
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
    return _cfg_to_response(updated)
