"""Tests for graphclaw.triggers.briefing — BriefingGenerator section building.

Description
-----------
Verifies that each of the 5 briefing sections is built correctly from task
dicts, that the CRITICAL section caps at 3 items, that the full
``generate()`` method returns a well-formed ``DailyBriefing``, and that an
empty task list produces sections with no items.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graphclaw.triggers.briefing import BriefingGenerator
from graphclaw.triggers.models import DailyBriefing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(**kwargs) -> datetime:
    """Return a timezone.utc datetime relative to now."""
    return datetime.now(timezone.utc) + timedelta(**kwargs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# CRITICAL section
# ---------------------------------------------------------------------------


async def test_critical_section_blocked_critical_path() -> None:
    """BLOCKED task on critical path appears in CRITICAL section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0001-ATM",
            "title": "Deploy auth service",
            "state": "BLOCKED",
            "is_critical_path": True,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 1
    assert "TSK-AB-0001-ATM" in briefing.critical.items[0]
    assert "BLOCKED" in briefing.critical.items[0]


async def test_critical_section_delayed_critical_path() -> None:
    """DELAYED task on critical path appears in CRITICAL section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0002-ATM",
            "title": "Run integration tests",
            "state": "DELAYED",
            "is_critical_path": True,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 1
    assert "DELAYED" in briefing.critical.items[0]


async def test_critical_section_overdue() -> None:
    """Task with deadline in the past appears in CRITICAL section as OVERDUE."""
    generator = BriefingGenerator()
    past_deadline = _utc(days=-1)  # yesterday
    tasks = [
        {
            "id": "TSK-AB-0003-ATM",
            "title": "Submit report",
            "state": "ACTIVE",
            "deadline": past_deadline,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 1
    assert "OVERDUE" in briefing.critical.items[0]


async def test_critical_max_3_items() -> None:
    """CRITICAL section caps at 3 items even when more qualify."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": f"TSK-AB-{i:04d}-ATM",
            "title": f"Task {i}",
            "state": "BLOCKED",
            "is_critical_path": True,
        }
        for i in range(1, 7)  # 6 qualifying tasks
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 3
    assert briefing.critical.max_items == 3


async def test_critical_non_critical_path_blocked_excluded() -> None:
    """BLOCKED task NOT on critical path does not appear in CRITICAL section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0007-ATM",
            "title": "Nice-to-have feature",
            "state": "BLOCKED",
            "is_critical_path": False,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 0


async def test_critical_future_deadline_excluded() -> None:
    """Task with a future deadline is not overdue and not in CRITICAL."""
    generator = BriefingGenerator()
    future = _utc(days=5)
    tasks = [
        {
            "id": "TSK-AB-0008-ATM",
            "title": "Future task",
            "state": "ACTIVE",
            "deadline": future,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.critical.items) == 0


# ---------------------------------------------------------------------------
# COMPLETED section
# ---------------------------------------------------------------------------


async def test_completed_section() -> None:
    """COMPLETE tasks appear in the COMPLETED section."""
    generator = BriefingGenerator()
    tasks = [
        {"id": "TSK-AB-0010-ATM", "title": "Done task", "state": "COMPLETE"},
        {"id": "TSK-AB-0011-ATM", "title": "Active task", "state": "ACTIVE"},
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.completed.items) == 1
    assert "TSK-AB-0010-ATM" in briefing.completed.items[0]


# ---------------------------------------------------------------------------
# AHEAD OF CURVE section
# ---------------------------------------------------------------------------


async def test_ahead_of_curve_high_score() -> None:
    """ACTIVE task with score > 0.8 appears in AHEAD OF CURVE section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0020-ATM",
            "title": "High scorer",
            "state": "ACTIVE",
            "score": 0.92,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.ahead_of_curve.items) == 1
    assert "0.92" in briefing.ahead_of_curve.items[0]


async def test_ahead_of_curve_in_progress_high_score() -> None:
    """IN_PROGRESS task with score > 0.8 also appears in AHEAD OF CURVE."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0021-ATM",
            "title": "In progress scorer",
            "state": "IN_PROGRESS",
            "score": 0.85,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.ahead_of_curve.items) == 1


async def test_ahead_of_curve_low_score_excluded() -> None:
    """ACTIVE task with score <= 0.8 does not appear in AHEAD OF CURVE."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0022-ATM",
            "title": "Average task",
            "state": "ACTIVE",
            "score": 0.75,
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.ahead_of_curve.items) == 0


# ---------------------------------------------------------------------------
# DEFERRED section
# ---------------------------------------------------------------------------


async def test_deferred_snoozed_tasks() -> None:
    """SNOOZED tasks appear in the DEFERRED section."""
    generator = BriefingGenerator()
    tasks = [{"id": "TSK-AB-0030-ATM", "title": "Snoozed task", "state": "SNOOZED"}]
    briefing = await generator.generate(tasks)
    assert len(briefing.deferred.items) == 1
    assert "snoozed" in briefing.deferred.items[0]


async def test_deferred_p3_pending() -> None:
    """P3 PENDING tasks appear in the DEFERRED section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0031-ATM",
            "title": "Low priority",
            "state": "PENDING",
            "priority": "P3",
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.deferred.items) == 1
    assert "P3 pending" in briefing.deferred.items[0]


async def test_deferred_p1_pending_excluded() -> None:
    """P1 PENDING task does NOT appear in DEFERRED section."""
    generator = BriefingGenerator()
    tasks = [
        {
            "id": "TSK-AB-0032-ATM",
            "title": "High priority pending",
            "state": "PENDING",
            "priority": "P1",
        }
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.deferred.items) == 0


# ---------------------------------------------------------------------------
# INFERENCES section
# ---------------------------------------------------------------------------


async def test_inferences_blocked_pattern() -> None:
    """More than 2 blocked tasks triggers a systemic inference."""
    generator = BriefingGenerator()
    tasks = [
        {"id": f"TSK-AB-{i:04d}-ATM", "title": f"Blocked {i}", "state": "BLOCKED"} for i in range(3)
    ]
    briefing = await generator.generate(tasks)
    assert len(briefing.inferences.items) >= 1
    assert any("blocked" in item.lower() for item in briefing.inferences.items)


async def test_inferences_delayed_pattern() -> None:
    """At least one DELAYED task triggers a delayed inference."""
    generator = BriefingGenerator()
    tasks = [{"id": "TSK-AB-0040-ATM", "title": "Delayed task", "state": "DELAYED"}]
    briefing = await generator.generate(tasks)
    assert any("delayed" in item.lower() for item in briefing.inferences.items)


async def test_inferences_two_blocked_no_systemic() -> None:
    """Exactly 2 blocked tasks does NOT trigger systemic inference."""
    generator = BriefingGenerator()
    tasks = [
        {"id": f"TSK-AB-{i:04d}-ATM", "title": f"Blocked {i}", "state": "BLOCKED"} for i in range(2)
    ]
    briefing = await generator.generate(tasks)
    # No systemic message — 2 is not > 2
    systemic = [item for item in briefing.inferences.items if "systemic" in item.lower()]
    assert len(systemic) == 0


# ---------------------------------------------------------------------------
# Empty task list
# ---------------------------------------------------------------------------


async def test_empty_tasks_produces_empty_briefing() -> None:
    """An empty task list should produce sections with no items."""
    generator = BriefingGenerator()
    briefing = await generator.generate(tasks=[])
    assert briefing.critical.items == []
    assert briefing.inferences.items == []
    assert briefing.completed.items == []
    assert briefing.ahead_of_curve.items == []
    assert briefing.deferred.items == []


# ---------------------------------------------------------------------------
# Full generate() method
# ---------------------------------------------------------------------------


async def test_generate_returns_daily_briefing() -> None:
    """generate() should return a DailyBriefing with all 5 sections populated."""
    generator = BriefingGenerator()
    tasks = [
        {"id": "TSK-AB-0050-ATM", "title": "Done", "state": "COMPLETE"},
        {
            "id": "TSK-AB-0051-ATM",
            "title": "Blocked critical",
            "state": "BLOCKED",
            "is_critical_path": True,
        },
        {
            "id": "TSK-AB-0052-ATM",
            "title": "High score",
            "state": "ACTIVE",
            "score": 0.95,
        },
        {"id": "TSK-AB-0053-ATM", "title": "Snoozed", "state": "SNOOZED"},
    ]
    briefing = await generator.generate(tasks)

    assert isinstance(briefing, DailyBriefing)
    assert briefing.generated_at is not None
    assert briefing.session_id.startswith("SES-")
    assert briefing.critical.title == "CRITICAL"
    assert briefing.inferences.title == "INFERENCES"
    assert briefing.completed.title == "COMPLETED"
    assert briefing.ahead_of_curve.title == "AHEAD OF CURVE"
    assert briefing.deferred.title == "DEFERRED"
    assert len(briefing.critical.items) == 1
    assert len(briefing.completed.items) == 1
    assert len(briefing.ahead_of_curve.items) == 1
    assert len(briefing.deferred.items) == 1


async def test_generate_with_goals_argument() -> None:
    """generate() should accept an optional goals list without error."""
    generator = BriefingGenerator()
    goals = [{"id": "GOAL-1", "title": "Ship product"}]
    briefing = await generator.generate(tasks=[], goals=goals)
    assert isinstance(briefing, DailyBriefing)
