"""Tests for graphclaw.triggers.models — Pydantic model creation, defaults, and serialisation.

Description
-----------
Verifies that TriggerEvent, TriggerConfig, FollowupConfig, TriggerDefinition,
FollowUpTiming, BriefingSection, and DailyBriefing can be instantiated with
required fields, that optional fields carry expected defaults, that TriggerType
values match the expected strings, and that round-trip JSON serialisation is
lossless.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

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


def _utc(year: int = 2026, month: int = 3, day: int = 18) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TriggerEvent
# ---------------------------------------------------------------------------


def test_trigger_event_creation() -> None:
    event = TriggerEvent(
        trigger_id="TRIG-abc123",
        trigger_type=TriggerType.ON_DEMAND,
        user_id="USER-x",
        payload={"key": "value"},
        created_at=_utc(),
    )
    assert event.trigger_id == "TRIG-abc123"
    assert event.trigger_type == TriggerType.ON_DEMAND
    assert event.user_id == "USER-x"
    assert event.payload == {"key": "value"}
    assert event.idempotency_key == ""
    assert event.session_id == ""


def test_trigger_event_default_payload() -> None:
    event = TriggerEvent(
        trigger_id="TRIG-1",
        trigger_type=TriggerType.TIME_BASED,
        user_id="USER-1",
        created_at=_utc(),
    )
    assert event.payload == {}


def test_trigger_event_default_created_at_is_set() -> None:
    """created_at should be auto-populated via utcnow() when omitted."""
    event = TriggerEvent(
        trigger_id="TRIG-2",
        trigger_type=TriggerType.INBOUND,
        user_id="USER-2",
    )
    assert event.created_at is not None
    assert event.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# TriggerConfig
# ---------------------------------------------------------------------------


def test_trigger_config_defaults() -> None:
    config = TriggerConfig(
        trigger_id="TC-1",
        trigger_type=TriggerType.TIME_BASED,
        user_id="USER-1",
    )
    assert config.enabled is True
    assert config.cron_expression is None
    assert config.next_fire_at is None
    assert config.last_fired_at is None
    assert config.payload_template == {}


def test_trigger_config_with_cron() -> None:
    config = TriggerConfig(
        trigger_id="TC-2",
        trigger_type=TriggerType.TIME_BASED,
        user_id="USER-1",
        cron_expression="0 8 * * *",
        next_fire_at=_utc(),
    )
    assert config.cron_expression == "0 8 * * *"
    assert config.next_fire_at == _utc()


# ---------------------------------------------------------------------------
# FollowupConfig
# ---------------------------------------------------------------------------


def test_followup_config_defaults() -> None:
    config = FollowupConfig(task_id="TSK-AB-0001-ATM")
    assert config.base_cadence_days == 3.0
    assert config.complexity_factor == 1.0
    assert config.reliability_score == 0.8
    assert config.recency_bonus == 0.0
    assert config.next_followup_at is None


# ---------------------------------------------------------------------------
# TriggerType values
# ---------------------------------------------------------------------------


def test_trigger_type_values() -> None:
    assert TriggerType.TIME_BASED == "TIME_BASED"
    assert TriggerType.EVENT_BASED == "EVENT_BASED"
    assert TriggerType.INBOUND == "INBOUND"
    assert TriggerType.ON_DEMAND == "ON_DEMAND"


def test_trigger_type_is_str_enum() -> None:
    assert isinstance(TriggerType.INBOUND, str)


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


def test_serialization_roundtrip() -> None:
    event = TriggerEvent(
        trigger_id="TRIG-rt",
        trigger_type=TriggerType.EVENT_BASED,
        user_id="USER-rt",
        payload={"a": 1, "b": [2, 3]},
        created_at=_utc(),
        idempotency_key="key:2026-03-18",
        session_id="SES-xyz",
    )
    json_str = event.model_dump_json()
    data = json.loads(json_str)

    # Re-hydrate
    restored = TriggerEvent.model_validate(data)
    assert restored.trigger_id == event.trigger_id
    assert restored.trigger_type == event.trigger_type
    assert restored.user_id == event.user_id
    assert restored.payload == event.payload
    assert restored.idempotency_key == event.idempotency_key
    assert restored.session_id == event.session_id


# ---------------------------------------------------------------------------
# TriggerDefinition
# ---------------------------------------------------------------------------


def test_trigger_definition_creation() -> None:
    """TriggerDefinition should accept required fields and apply defaults."""
    td = TriggerDefinition(
        trigger_id="TD-1",
        trigger_type=TriggerType.TIME_BASED,
        name="Daily briefing",
    )
    assert td.trigger_id == "TD-1"
    assert td.trigger_type == TriggerType.TIME_BASED
    assert td.name == "Daily briefing"
    assert td.description == ""
    assert td.enabled is True
    assert td.cron_expression is None
    assert td.interval_seconds is None
    assert td.event_pattern is None
    assert td.event_filter == {}
    assert td.channel_filter is None
    assert td.action == ""
    assert td.action_params == {}


def test_trigger_definition_event_based() -> None:
    """TriggerDefinition for EVENT_BASED type with event_pattern and filter."""
    td = TriggerDefinition(
        trigger_id="TD-2",
        trigger_type=TriggerType.EVENT_BASED,
        name="State change trigger",
        event_pattern="task.state_changed",
        event_filter={"new_state": "COMPLETE"},
        action="run_scoring",
    )
    assert td.event_pattern == "task.state_changed"
    assert td.event_filter == {"new_state": "COMPLETE"}
    assert td.action == "run_scoring"


def test_trigger_definition_inbound() -> None:
    """TriggerDefinition for INBOUND type with channel_filter."""
    td = TriggerDefinition(
        trigger_id="TD-3",
        trigger_type=TriggerType.INBOUND,
        name="Email trigger",
        channel_filter="email",
    )
    assert td.channel_filter == "email"


def test_trigger_definition_on_demand() -> None:
    """TriggerDefinition for ON_DEMAND type."""
    td = TriggerDefinition(
        trigger_id="TD-4",
        trigger_type=TriggerType.ON_DEMAND,
        name="Manual trigger",
        action="spawn_followup",
        action_params={"task_id": "TSK-AB-0001-ATM"},
    )
    assert td.action == "spawn_followup"
    assert td.action_params == {"task_id": "TSK-AB-0001-ATM"}


# ---------------------------------------------------------------------------
# FollowUpTiming
# ---------------------------------------------------------------------------


def test_followup_timing_creation() -> None:
    """FollowUpTiming should apply default multipliers."""
    ft = FollowUpTiming(task_id="TSK-AB-0001-ATM")
    assert ft.base_interval_hours == 24.0
    assert ft.urgency_multiplier == 1.0
    assert ft.confidence_adjustment == 1.0
    assert ft.next_followup_at is None


def test_followup_timing_effective_interval() -> None:
    """effective_interval_hours should multiply the three component fields."""
    ft = FollowUpTiming(
        task_id="TSK-AB-0002-ATM",
        base_interval_hours=24.0,
        urgency_multiplier=0.5,
        confidence_adjustment=0.5,
    )
    # 24 * 0.5 * 0.5 = 6.0
    assert ft.effective_interval_hours == pytest.approx(6.0)


def test_followup_timing_effective_interval_default() -> None:
    """Default multipliers produce effective_interval_hours == base_interval_hours."""
    ft = FollowUpTiming(task_id="TSK-AB-0003-ATM", base_interval_hours=48.0)
    assert ft.effective_interval_hours == pytest.approx(48.0)


# ---------------------------------------------------------------------------
# BriefingSection
# ---------------------------------------------------------------------------


def test_briefing_section_creation() -> None:
    """BriefingSection should accept title and items."""
    section = BriefingSection(
        title="CRITICAL",
        items=["Task A — BLOCKED", "Task B — OVERDUE"],
        max_items=3,
    )
    assert section.title == "CRITICAL"
    assert len(section.items) == 2
    assert section.max_items == 3


def test_briefing_section_defaults() -> None:
    """BriefingSection default items is an empty list, max_items is 10."""
    section = BriefingSection(title="COMPLETED")
    assert section.items == []
    assert section.max_items == 10


# ---------------------------------------------------------------------------
# DailyBriefing
# ---------------------------------------------------------------------------


def _empty_section(title: str) -> BriefingSection:
    return BriefingSection(title=title, items=[])


def test_daily_briefing_creation() -> None:
    """DailyBriefing should accept all 5 sections and auto-generate generated_at."""
    briefing = DailyBriefing(
        session_id="SES-abc123",
        critical=_empty_section("CRITICAL"),
        inferences=_empty_section("INFERENCES"),
        completed=_empty_section("COMPLETED"),
        ahead_of_curve=_empty_section("AHEAD OF CURVE"),
        deferred=_empty_section("DEFERRED"),
    )
    assert briefing.session_id == "SES-abc123"
    assert briefing.generated_at is not None
    assert briefing.generated_at.tzinfo is not None
    assert briefing.critical.title == "CRITICAL"
    assert briefing.inferences.title == "INFERENCES"
    assert briefing.completed.title == "COMPLETED"
    assert briefing.ahead_of_curve.title == "AHEAD OF CURVE"
    assert briefing.deferred.title == "DEFERRED"
