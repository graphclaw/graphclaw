# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.triggers — Trigger engine for time-based, event-based, inbound, and on-demand triggers.

Description
-----------
Provides the public API for the GraphClaw trigger engine subsystem.  Consumers
import from this package to access trigger models, the follow-up timing helpers,
the cron-like scheduler, the daily briefing generator, and the main
``TriggerEngine`` orchestrator.

Design Patterns
---------------
- Facade: Exposes a curated public API from the trigger sub-modules so callers
  need only import from ``graphclaw.triggers``.

Public API
----------
- TriggerType: Enum of supported trigger categories.
- TriggerEvent: Pydantic model for a dispatched trigger event.
- TriggerConfig: Pydantic model for a persisted trigger configuration.
- FollowupConfig: Pydantic model for follow-up timing parameters (PRD Section 10).
- TriggerDefinition: Rich trigger rule definition with action and filter params.
- FollowUpTiming: Calculated follow-up timing value object.
- BriefingSection: One labelled section of the daily briefing.
- DailyBriefing: The 5-section daily briefing structure.
- compute_followup_timing: Pure function returning days until next follow-up.
- compute_next_followup: Return next follow-up datetime from a FollowupConfig.
- FollowUpCalculator: Class-based calculator using priority/confidence maps.
- BriefingGenerator: Async generator producing DailyBriefing from graph state.
- TriggerScheduler: In-memory cron-like scheduler for TIME_BASED triggers.
- TriggerEngine: Async engine orchestrating scheduled and event-driven triggers.

Dependencies
------------
- graphclaw.triggers.models: TriggerType, TriggerEvent, TriggerConfig,
  FollowupConfig, TriggerDefinition, FollowUpTiming, BriefingSection,
  DailyBriefing.
- graphclaw.triggers.followup: compute_followup_timing, compute_next_followup,
  FollowUpCalculator.
- graphclaw.triggers.briefing: BriefingGenerator.
- graphclaw.triggers.scheduler: TriggerScheduler.
- graphclaw.triggers.engine: TriggerEngine.

Notes
-----
The MessageBroker dependency (graphclaw.infra.broker) is injected at construction
time so that the trigger engine can be tested with a mock broker.
"""

from __future__ import annotations

from graphclaw.triggers.briefing import BriefingGenerator
from graphclaw.triggers.engine import TriggerEngine
from graphclaw.triggers.followup import (
    FollowUpCalculator,
    compute_followup_timing,
    compute_next_followup,
)
from graphclaw.triggers.models import (
    BriefingSection,
    DailyBriefing,
    FollowupConfig,
    FollowUpTiming,
    TriggerConfig,
    TriggerDefinition,
    TriggerEvent,
    TriggerType,
)
from graphclaw.triggers.scheduler import TriggerScheduler

__all__ = [
    "TriggerType",
    "TriggerEvent",
    "TriggerConfig",
    "FollowupConfig",
    "TriggerDefinition",
    "FollowUpTiming",
    "BriefingSection",
    "DailyBriefing",
    "compute_followup_timing",
    "compute_next_followup",
    "FollowUpCalculator",
    "BriefingGenerator",
    "TriggerScheduler",
    "TriggerEngine",
]
