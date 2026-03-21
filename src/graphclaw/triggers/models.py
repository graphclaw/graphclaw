"""graphclaw.triggers.models — Pydantic models for the trigger engine.

Description
-----------
Defines the data contracts used throughout the trigger subsystem: the
``TriggerType`` enumeration, the ``TriggerEvent`` model dispatched to the agent
runtime, the ``TriggerConfig`` model persisted in the database, the
``FollowupConfig`` model used to parameterise follow-up timing calculations,
``TriggerDefinition`` for rich trigger rule definitions, ``FollowUpTiming``
for urgency/confidence-adjusted follow-up scheduling, and ``BriefingSection``
/ ``DailyBriefing`` for the 5-section daily briefing structure.

Design Patterns
---------------
- Value Objects: All models are immutable Pydantic BaseModels that carry data
  without behaviour, keeping domain logic separate (see followup.py, engine.py,
  briefing.py).
- Enum Strategy: ``TriggerType`` drives dispatch logic in TriggerEngine without
  requiring isinstance checks on the event payload.
- Computed Properties: ``FollowUpTiming.effective_interval_hours`` derives
  the final interval from its component multipliers without additional state.

Public API
----------
- TriggerType: Enum of supported trigger categories (TIME_BASED, EVENT_BASED,
  INBOUND, ON_DEMAND).
- TriggerEvent: Event dispatched by the trigger engine to the agent runtime via
  the TRIGGER_EVENTS message queue.
- TriggerConfig: Persistent trigger configuration stored in the database.
- FollowupConfig: Follow-up timing parameters for a single task assignment.
- TriggerDefinition: Rich trigger rule definition with action and filter params.
- FollowUpTiming: Calculated follow-up timing for a task.
- BriefingSection: One labelled section of the daily briefing.
- DailyBriefing: The 5-section daily briefing structure.

Dependencies
------------
- pydantic: BaseModel, Field.
- datetime: datetime.
- graphclaw.models.base: utcnow (timezone-aware UTC timestamp factory).

Notes
-----
``TriggerEvent.idempotency_key`` is empty-string by default (not None) so that
callers can check its truthiness without an isinstance guard.  An empty key means
"no deduplication" for this event.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from graphclaw.models.base import utcnow


class TriggerType(str, Enum):
    """Categories of trigger that the engine can dispatch."""

    TIME_BASED = "TIME_BASED"  # Daily briefing, recurring tasks
    EVENT_BASED = "EVENT_BASED"  # status.md write, completion signal
    INBOUND = "INBOUND"  # Email / message received
    ON_DEMAND = "ON_DEMAND"  # CLI / API invocation


class TriggerEvent(BaseModel):
    """Event dispatched by the trigger engine to the agent runtime.

    Serialised to JSON and published onto the TRIGGER_EVENTS queue so that the
    agent loop can consume it asynchronously.
    """

    trigger_id: str
    trigger_type: TriggerType
    user_id: str
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    idempotency_key: str = ""
    session_id: str = ""


class TriggerConfig(BaseModel):
    """Persistent trigger configuration stored in the database.

    Represents a single logical trigger that the engine evaluates on each
    scheduling tick.  TIME_BASED triggers carry a ``cron_expression`` and a
    pre-computed ``next_fire_at`` timestamp; other trigger types may omit those.
    """

    trigger_id: str
    trigger_type: TriggerType
    user_id: str
    enabled: bool = True
    cron_expression: str | None = None
    next_fire_at: datetime | None = None
    last_fired_at: datetime | None = None
    payload_template: dict = Field(default_factory=dict)


class FollowupConfig(BaseModel):
    """Follow-up timing parameters for a single task assignment.

    Used by ``compute_next_followup`` to derive the datetime at which the engine
    should next check in on a delegated task.
    """

    task_id: str
    base_cadence_days: float = 3.0
    complexity_factor: float = 1.0
    reliability_score: float = 0.8
    recency_bonus: float = 0.0
    next_followup_at: datetime | None = None


# ---------------------------------------------------------------------------
# Rich trigger rule definition (TriggerDefinition)
# ---------------------------------------------------------------------------


class TriggerDefinition(BaseModel):
    """Definition of a trigger rule combining type, scheduling, and action metadata.

    Provides a richer structure than ``TriggerConfig`` by carrying action names,
    action parameters, event patterns, event filters, and channel filters in one
    place.  Intended for in-process registration with ``TriggerEngine``; not
    persisted directly to the database.
    """

    trigger_id: str
    trigger_type: TriggerType
    name: str
    description: str = ""
    enabled: bool = True
    # TIME_BASED scheduling
    cron_expression: str | None = None
    interval_seconds: float | None = None
    # EVENT_BASED matching
    event_pattern: str | None = None
    event_filter: dict[str, str] = Field(default_factory=dict)
    # INBOUND channel matching
    channel_filter: str | None = None
    # Action to dispatch
    action: str = ""
    action_params: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Follow-up timing value object
# ---------------------------------------------------------------------------


class FollowUpTiming(BaseModel):
    """Calculated follow-up timing for a task based on urgency and confidence.

    ``effective_interval_hours`` is derived from the three component fields
    without additional storage.
    """

    task_id: str
    base_interval_hours: float = 24.0
    urgency_multiplier: float = 1.0
    confidence_adjustment: float = 1.0
    next_followup_at: datetime | None = None

    @property
    def effective_interval_hours(self) -> float:
        """Return the clamped effective interval in hours."""
        return self.base_interval_hours * self.urgency_multiplier * self.confidence_adjustment


# ---------------------------------------------------------------------------
# Daily briefing structures
# ---------------------------------------------------------------------------


class BriefingSection(BaseModel):
    """One labelled section of the daily briefing.

    ``max_items`` is advisory metadata; callers are responsible for limiting
    item counts before construction.
    """

    title: str
    items: list[str] = Field(default_factory=list)
    max_items: int = 10


class DailyBriefing(BaseModel):
    """The 5-section daily briefing structure produced by ``BriefingGenerator``.

    Sections:
    - critical: Tasks needing immediate attention (max 3).
    - inferences: Agent-observed patterns about task progress.
    - completed: Recently completed tasks.
    - ahead_of_curve: Tasks progressing better than expected.
    - deferred: Snoozed or low-priority pending tasks.
    """

    generated_at: datetime = Field(default_factory=utcnow)
    session_id: str = ""
    critical: BriefingSection
    inferences: BriefingSection
    completed: BriefingSection
    ahead_of_curve: BriefingSection
    deferred: BriefingSection
