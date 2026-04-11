"""Tests for graphclaw.triggers.scheduler — TriggerScheduler registration and cron logic.

Description
-----------
Verifies trigger registration, due-trigger detection, disabled trigger filtering,
unregistration, advance/next_fire_at updates, and the basic cron parser for
daily patterns.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.triggers.models import TriggerConfig, TriggerType
from graphclaw.triggers.scheduler import TriggerScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _make_config(
    trigger_id: str = "TC-1",
    trigger_type: TriggerType = TriggerType.TIME_BASED,
    user_id: str = "USER-1",
    enabled: bool = True,
    cron_expression: str | None = "0 8 * * *",
    next_fire_at: datetime | None = None,
) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=trigger_id,
        trigger_type=trigger_type,
        user_id=user_id,
        enabled=enabled,
        cron_expression=cron_expression,
        next_fire_at=next_fire_at,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_and_get_due() -> None:
    """A trigger with a past next_fire_at should appear in get_due_triggers."""
    scheduler = TriggerScheduler()
    past = _utc(2026, 3, 17, 8, 0)  # yesterday 8am
    config = _make_config(next_fire_at=past)
    scheduler.register(config)

    now = _utc(2026, 3, 18, 9, 0)
    due = scheduler.get_due_triggers(now)
    assert len(due) == 1
    assert due[0].trigger_id == "TC-1"


def test_not_due_yet() -> None:
    """A trigger with a future next_fire_at should not appear."""
    scheduler = TriggerScheduler()
    future = _utc(2026, 3, 19, 8, 0)
    config = _make_config(next_fire_at=future)
    scheduler.register(config)

    now = _utc(2026, 3, 18, 9, 0)
    due = scheduler.get_due_triggers(now)
    assert due == []


def test_due_at_exact_time() -> None:
    """A trigger with next_fire_at == now should be due (<=)."""
    scheduler = TriggerScheduler()
    now = _utc(2026, 3, 18, 8, 0)
    config = _make_config(next_fire_at=now)
    scheduler.register(config)

    due = scheduler.get_due_triggers(now)
    assert len(due) == 1


# ---------------------------------------------------------------------------
# Disabled triggers
# ---------------------------------------------------------------------------


def test_disabled_trigger_not_returned() -> None:
    """Disabled triggers must never appear in get_due_triggers."""
    scheduler = TriggerScheduler()
    past = _utc(2026, 3, 17, 8, 0)
    config = _make_config(enabled=False, next_fire_at=past)
    scheduler.register(config)

    now = _utc(2026, 3, 18, 9, 0)
    due = scheduler.get_due_triggers(now)
    assert due == []


# ---------------------------------------------------------------------------
# Unregister
# ---------------------------------------------------------------------------


def test_unregister_removes_trigger() -> None:
    """After unregistering, the trigger should not appear in due triggers."""
    scheduler = TriggerScheduler()
    past = _utc(2026, 3, 17, 8, 0)
    config = _make_config(next_fire_at=past)
    scheduler.register(config)
    scheduler.unregister("TC-1")

    now = _utc(2026, 3, 18, 9, 0)
    due = scheduler.get_due_triggers(now)
    assert due == []


def test_unregister_unknown_id_is_silent() -> None:
    """Unregistering an unknown ID should not raise."""
    scheduler = TriggerScheduler()
    scheduler.unregister("NONEXISTENT")  # must not raise


# ---------------------------------------------------------------------------
# Advance
# ---------------------------------------------------------------------------


def test_advance_updates_next_fire_at() -> None:
    """advance() should update next_fire_at for a TIME_BASED cron trigger."""
    scheduler = TriggerScheduler()
    # Fire at 8am on 2026-03-18
    past = _utc(2026, 3, 18, 8, 0)
    config = _make_config(next_fire_at=past, cron_expression="0 8 * * *")
    scheduler.register(config)

    now = _utc(2026, 3, 18, 8, 1)  # just after 8am
    scheduler.advance("TC-1", now)

    # Retrieve the updated config
    due_next_day = scheduler.get_due_triggers(_utc(2026, 3, 19, 8, 1))
    assert len(due_next_day) == 1
    assert due_next_day[0].next_fire_at == _utc(2026, 3, 19, 8, 0)


def test_advance_sets_last_fired_at() -> None:
    """advance() must record last_fired_at."""
    scheduler = TriggerScheduler()
    past = _utc(2026, 3, 18, 8, 0)
    config = _make_config(next_fire_at=past, cron_expression="0 8 * * *")
    scheduler.register(config)

    now = _utc(2026, 3, 18, 8, 1)
    scheduler.advance("TC-1", now)

    # The trigger should not be due before tomorrow
    due = scheduler.get_due_triggers(now)
    assert due == []


def test_advance_non_cron_trigger_sets_none() -> None:
    """advance() on a non-cron trigger sets next_fire_at to None."""
    scheduler = TriggerScheduler()
    past = _utc(2026, 3, 18, 8, 0)
    config = _make_config(
        trigger_type=TriggerType.ON_DEMAND,
        cron_expression=None,
        next_fire_at=past,
    )
    scheduler.register(config)

    now = _utc(2026, 3, 18, 8, 1)
    scheduler.advance("TC-1", now)

    due = scheduler.get_due_triggers(_utc(2026, 3, 19, 9, 0))
    assert due == []


# ---------------------------------------------------------------------------
# Cron computation
# ---------------------------------------------------------------------------


def test_compute_next_cron_daily() -> None:
    """'0 8 * * *' after 8am should produce next day at 8am timezone.utc."""
    scheduler = TriggerScheduler()
    after = _utc(2026, 3, 18, 9, 0)  # 9am → already past 8am
    next_fire = scheduler._compute_next_cron("0 8 * * *", after)
    assert next_fire == _utc(2026, 3, 19, 8, 0)


def test_compute_next_cron_before_fire_time() -> None:
    """'0 8 * * *' before 8am on the same day → today at 8am."""
    scheduler = TriggerScheduler()
    after = _utc(2026, 3, 18, 7, 0)  # 7am
    next_fire = scheduler._compute_next_cron("0 8 * * *", after)
    assert next_fire == _utc(2026, 3, 18, 8, 0)


def test_compute_next_cron_invalid_fields() -> None:
    """Non-wildcard day-of-month should raise ValueError."""
    scheduler = TriggerScheduler()
    with pytest.raises(ValueError, match="Unsupported cron expression"):
        scheduler._compute_next_cron("0 8 1 * *", datetime.now(timezone.utc))


def test_compute_next_cron_wrong_field_count() -> None:
    """Wrong number of fields should raise ValueError."""
    scheduler = TriggerScheduler()
    with pytest.raises(ValueError, match="expected 5 fields"):
        scheduler._compute_next_cron("0 8 * *", datetime.now(timezone.utc))
