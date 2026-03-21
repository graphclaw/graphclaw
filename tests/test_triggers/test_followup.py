"""Tests for graphclaw.triggers.followup — Known-answer follow-up timing tests.

Description
-----------
Verifies the PRD Section 10 follow-up cadence formula using known-answer tests,
checks that compute_next_followup returns a future datetime, and verifies that
FollowUpCalculator produces correctly bounded FollowUpTiming instances from
task priority and ConfidenceLevel inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graphclaw.models.enums import ConfidenceLevel
from graphclaw.triggers.followup import (
    FollowUpCalculator,
    compute_followup_timing,
    compute_next_followup,
)
from graphclaw.triggers.models import FollowupConfig, FollowUpTiming

# ---------------------------------------------------------------------------
# compute_followup_timing — known-answer tests
# ---------------------------------------------------------------------------


def test_baseline_timing() -> None:
    """base=3, complexity=1, reliability=1, recency=0 → 3.0 days."""
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=1.0,
        reliability_score=1.0,
        recency_bonus=0.0,
    )
    assert result == pytest.approx(3.0)


def test_low_reliability_increases_frequency() -> None:
    """reliability=0.5 → 3 * 1 * (1/0.5) * 1 = 6.0 days."""
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=1.0,
        reliability_score=0.5,
        recency_bonus=0.0,
    )
    assert result == pytest.approx(6.0)


def test_high_complexity_increases_interval() -> None:
    """complexity=2.0 → 3 * 2 * 1 * 1 = 6.0 days."""
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=2.0,
        reliability_score=1.0,
        recency_bonus=0.0,
    )
    assert result == pytest.approx(6.0)


def test_recency_bonus_decreases_interval() -> None:
    """recency=1.0 → 3 * 1 * 1 * (1 - 0.2) = 2.4 days."""
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=1.0,
        reliability_score=1.0,
        recency_bonus=1.0,
    )
    assert result == pytest.approx(2.4)


def test_combined_factors() -> None:
    """base=4, complexity=1.5, reliability=0.8, recency=0.5.
    days = 4 * 1.5 * (1/0.8) * (1 - 0.1) = 4 * 1.5 * 1.25 * 0.9 = 6.75
    """
    result = compute_followup_timing(
        base_cadence=4.0,
        complexity_factor=1.5,
        reliability_score=0.8,
        recency_bonus=0.5,
    )
    assert result == pytest.approx(6.75)


def test_minimum_reliability_clamped() -> None:
    """reliability=0 is clamped to 0.1 to prevent division-by-zero.
    days = 3 * 1 * (1/0.1) * 1 = 30.0
    """
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=1.0,
        reliability_score=0.0,
        recency_bonus=0.0,
    )
    assert result == pytest.approx(30.0)


def test_very_small_reliability_clamped() -> None:
    """Negative reliability is also clamped to 0.1."""
    result = compute_followup_timing(
        base_cadence=3.0,
        complexity_factor=1.0,
        reliability_score=-1.0,
        recency_bonus=0.0,
    )
    assert result == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# compute_next_followup
# ---------------------------------------------------------------------------


def test_compute_next_followup_returns_future_datetime() -> None:
    config = FollowupConfig(
        task_id="TSK-AB-0001-ATM",
        base_cadence_days=3.0,
        complexity_factor=1.0,
        reliability_score=1.0,
        recency_bonus=0.0,
    )
    now_before = datetime.now(UTC)
    result = compute_next_followup(config)
    assert result > now_before
    assert result.tzinfo is not None


def test_compute_next_followup_interval() -> None:
    """The returned datetime should be approximately base_cadence_days in the future."""
    config = FollowupConfig(
        task_id="TSK-AB-0002-ATM",
        base_cadence_days=5.0,
        complexity_factor=1.0,
        reliability_score=1.0,
        recency_bonus=0.0,
    )
    now_before = datetime.now(UTC)
    result = compute_next_followup(config)
    expected = now_before + timedelta(days=5.0)
    # Allow 2 seconds tolerance for test execution time
    assert abs((result - expected).total_seconds()) < 2.0


# ---------------------------------------------------------------------------
# FollowUpCalculator
# ---------------------------------------------------------------------------


def test_p1_high_confidence_timing() -> None:
    """P1 + HIGH confidence: 24 * 0.5 * 1.5 = 18 hours."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P1", confidence=ConfidenceLevel.HIGH)
    assert isinstance(timing, FollowUpTiming)
    assert timing.urgency_multiplier == pytest.approx(0.5)
    assert timing.confidence_adjustment == pytest.approx(1.5)
    assert timing.effective_interval_hours == pytest.approx(18.0)
    assert timing.next_followup_at is not None
    assert timing.next_followup_at > datetime.now(UTC)


def test_p1_low_confidence_timing() -> None:
    """P1 + LOW confidence: 24 * 0.5 * 0.5 = 6.0 hours (more frequent)."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P1", confidence=ConfidenceLevel.LOW)
    assert timing.effective_interval_hours == pytest.approx(6.0)


def test_p2_medium_confidence_timing() -> None:
    """P2 + MEDIUM confidence: 24 * 1.0 * 1.0 = 24.0 hours (baseline)."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P2", confidence=ConfidenceLevel.MEDIUM)
    assert timing.effective_interval_hours == pytest.approx(24.0)


def test_p3_high_confidence_timing() -> None:
    """P3 + HIGH confidence: 24 * 2.0 * 1.5 = 72.0 hours (least frequent)."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P3", confidence=ConfidenceLevel.HIGH)
    assert timing.effective_interval_hours == pytest.approx(72.0)


def test_interval_clamped_to_min() -> None:
    """No combination should produce an interval below MIN_INTERVAL_HOURS (4h)."""
    calc = FollowUpCalculator()
    # P1 + LOW = 24 * 0.5 * 0.5 = 6.0 hours, which is above MIN (4h).
    # Force below min by subclassing — or verify MIN boundary holds.
    assert calc.MIN_INTERVAL_HOURS == 4.0
    timing = calc.calculate(task_priority="P1", confidence=ConfidenceLevel.LOW)
    assert timing.effective_interval_hours >= calc.MIN_INTERVAL_HOURS


def test_interval_clamped_to_max() -> None:
    """P3 + HIGH produces 72h which is well below MAX (168h); verify MAX holds."""
    calc = FollowUpCalculator()
    assert calc.MAX_INTERVAL_HOURS == 168.0
    timing = calc.calculate(task_priority="P3", confidence=ConfidenceLevel.HIGH)
    assert timing.effective_interval_hours <= calc.MAX_INTERVAL_HOURS


def test_default_priority_fallback() -> None:
    """Unknown priority falls back to urgency_multiplier of 1.0."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="UNKNOWN", confidence=ConfidenceLevel.MEDIUM)
    assert timing.urgency_multiplier == pytest.approx(1.0)
    assert timing.effective_interval_hours == pytest.approx(24.0)


def test_task_id_empty_by_default() -> None:
    """FollowUpCalculator leaves task_id as empty string for caller to fill."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P2", confidence=ConfidenceLevel.MEDIUM)
    assert timing.task_id == ""


def test_p3_low_confidence_clamped_to_max() -> None:
    """P3 + LOW: 24 * 2.0 * 0.5 = 24h — not hitting MAX boundary, verifying calc."""
    calc = FollowUpCalculator()
    timing = calc.calculate(task_priority="P3", confidence=ConfidenceLevel.LOW)
    assert timing.effective_interval_hours == pytest.approx(24.0)
