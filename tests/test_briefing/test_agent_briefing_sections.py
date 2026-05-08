# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Tests for graphclaw.agent.briefing — 5-section briefing formatter.

Covers:
  - O-BRF-01: All 5 sections (Critical, Inferences, Completed, Ahead of Curve, Deferred)
  - O-BRF-02: Critical section capped at MAX_CRITICAL_ITEMS (3) with autonomous note
  - O-BRF-03: interrupt_threshold filtering — items above threshold tagged [INTERRUPT]
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.agent.briefing import (
    MAX_CRITICAL_ITEMS,
    BriefingContext,
    format_briefing,
    has_interrupt_items,
)
from graphclaw.models.enums import AutonomyLevel
from graphclaw.models.scoring import ActionQueueEntry, ScoreExplanation, ScoreFactor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entry(rank: int, score: float = 0.9) -> ActionQueueEntry:
    """Return a minimal ActionQueueEntry."""
    return ActionQueueEntry(
        node_id=f"TSK-TT-{rank:04d}-ATM",
        final_score=score,
        rank=rank,
        recommended_action="Review this task",
        autonomy_level=AutonomyLevel.SUGGEST,
        explanation=ScoreExplanation(
            node_id=f"TSK-TT-{rank:04d}-ATM",
            scored_at=_now(),
            final_score=score,
            rank=rank,
            factors=[
                ScoreFactor(
                    factor_name="deadline",
                    raw_score=score,
                    weight=1.0,
                    weighted_score=score,
                    plain_english="Deadline approaching",
                )
            ],
            summary=f"Task #{rank} needs attention",
        ),
    )


# ---------------------------------------------------------------------------
# Empty queue
# ---------------------------------------------------------------------------


async def test_empty_queue_no_context_returns_fallback():
    output = format_briefing([], context=None)
    assert "No actionable" in output


async def test_empty_queue_with_context_still_generates_sections():
    ctx = BriefingContext(
        inferences_to_confirm=[
            {"id": "T1", "title": "Check this", "state": "NEEDS_REVIEW", "reason": "uncertain"}
        ]
    )
    output = format_briefing([], context=ctx)
    assert "## 2. Inferences to Confirm" in output
    assert "T1" in output


# ---------------------------------------------------------------------------
# Section 1: Critical + O-BRF-02 cap
# ---------------------------------------------------------------------------


async def test_section1_shows_top_n_entries():
    entries = [_entry(i, score=1.0 - i * 0.1) for i in range(1, 4)]
    output = format_briefing(entries, top_n=3)
    assert "## 1. Critical" in output
    for i in range(1, 4):
        assert f"TSK-TT-{i:04d}-ATM" in output


async def test_section1_cap_at_max_critical_items():
    """Only MAX_CRITICAL_ITEMS (3) shown; remainder noted as autonomous (O-BRF-02)."""
    entries = [_entry(i) for i in range(1, 7)]  # 6 entries
    output = format_briefing(entries)
    # First 3 appear
    for i in range(1, 4):
        assert f"TSK-TT-{i:04d}-ATM" in output
    # Items 4-6 do NOT appear as explicit entries
    for i in range(4, 7):
        assert f"TSK-TT-{i:04d}-ATM" not in output
    # But the autonomous note must be present
    assert "autonomously" in output.lower()
    assert "3" in output  # mentions the 3 remaining items


async def test_section1_no_items_prints_none_message():
    output = format_briefing([])
    assert "No urgent items" in output or "No actionable" in output


async def test_section1_default_top_n_is_three():
    """Default top_n equals MAX_CRITICAL_ITEMS = 3."""
    assert MAX_CRITICAL_ITEMS == 3
    entries = [_entry(i) for i in range(1, 6)]
    output = format_briefing(entries)
    # 4th and 5th should not appear as critical items
    assert "TSK-TT-0004-ATM" not in output
    assert "TSK-TT-0005-ATM" not in output


# ---------------------------------------------------------------------------
# Section 2: Inferences to Confirm
# ---------------------------------------------------------------------------


async def test_section2_inferences_appears():
    ctx = BriefingContext(
        inferences_to_confirm=[
            {
                "id": "T-INF-001",
                "title": "Deploy pipeline inference",
                "state": "NEEDS_REVIEW",
                "reason": "3 tasks blocked",
            },
        ]
    )
    output = format_briefing([], context=ctx)
    assert "## 2. Inferences to Confirm" in output
    assert "T-INF-001" in output
    assert "3 tasks blocked" in output


async def test_section2_empty_shows_none_pending():
    output = format_briefing([], context=BriefingContext())
    assert "## 2. Inferences to Confirm — None pending." in output


# ---------------------------------------------------------------------------
# Section 3: Completed Since Last Briefing
# ---------------------------------------------------------------------------


async def test_section3_completed_appears():
    ctx = BriefingContext(
        completed_since_last=[
            {
                "id": "T-DONE-001",
                "title": "Finish auth service",
                "completed_at": "2025-01-10T08:00:00Z",
            },
        ]
    )
    output = format_briefing([], context=ctx)
    assert "## 3. Completed Since Last Briefing" in output
    assert "T-DONE-001" in output
    assert "Finish auth service" in output


async def test_section3_empty_shows_none():
    output = format_briefing([], context=BriefingContext())
    assert "## 3. Completed Since Last Briefing — None." in output


# ---------------------------------------------------------------------------
# Section 4: Ahead of the Curve
# ---------------------------------------------------------------------------


async def test_section4_ahead_of_curve_appears():
    ctx = BriefingContext(
        ahead_of_curve=[
            {
                "id": "T-AHEAD-001",
                "title": "Prepare Q3 report",
                "deadline": "2025-03-15",
                "score": 0.45,
            },
        ]
    )
    output = format_briefing([], context=ctx)
    assert "## 4. Ahead of the Curve" in output
    assert "T-AHEAD-001" in output
    assert "0.450" in output


async def test_section4_empty_shows_no_proactive():
    output = format_briefing([], context=BriefingContext())
    assert "## 4. Ahead of the Curve — No proactive items identified." in output


# ---------------------------------------------------------------------------
# Section 5: Deferred Items Check
# ---------------------------------------------------------------------------


async def test_section5_deferred_appears():
    ctx = BriefingContext(
        deferred_items=[
            {"id": "T-SNZ-001", "title": "Review vendor contract", "snooze_until": "2025-02-01"},
        ]
    )
    output = format_briefing([], context=ctx)
    assert "## 5. Deferred Items Check" in output
    assert "T-SNZ-001" in output
    assert "2025-02-01" in output


async def test_section5_empty_shows_no_snoozed():
    output = format_briefing([], context=BriefingContext())
    assert "## 5. Deferred Items Check — No snoozed items." in output


# ---------------------------------------------------------------------------
# Full 5-section briefing
# ---------------------------------------------------------------------------


async def test_full_briefing_all_five_sections_present():
    """All 5 section headers appear when data is provided for each."""
    entries = [_entry(1)]
    ctx = BriefingContext(
        inferences_to_confirm=[
            {"id": "I1", "title": "Inference 1", "state": "NEEDS_REVIEW", "reason": ""}
        ],
        completed_since_last=[{"id": "C1", "title": "Completed task", "completed_at": "now"}],
        ahead_of_curve=[
            {"id": "A1", "title": "Proactive task", "deadline": "2025-06-01", "score": 0.3}
        ],
        deferred_items=[{"id": "D1", "title": "Snoozed task", "snooze_until": "2025-03-01"}],
    )
    output = format_briefing(entries, context=ctx)
    assert "## 1. Critical" in output
    assert "## 2. Inferences to Confirm" in output
    assert "## 3. Completed Since Last Briefing" in output
    assert "## 4. Ahead of the Curve" in output
    assert "## 5. Deferred Items Check" in output
    # Footer
    assert "Total tasks in queue" in output


async def test_footer_shows_total_queue_length():
    entries = [_entry(i) for i in range(1, 6)]
    output = format_briefing(entries)
    assert "Total tasks in queue: 5" in output


# ---------------------------------------------------------------------------
# O-BRF-03: interrupt_threshold
# ---------------------------------------------------------------------------


async def test_interrupt_threshold_tags_high_score_items():
    """Items above interrupt_threshold are tagged [INTERRUPT] in section 1."""
    low = _entry(1, score=0.6)
    high = _entry(2, score=0.95)
    output = format_briefing([high, low], top_n=3, interrupt_threshold=0.8)
    assert "[INTERRUPT]" in output
    # Low-score item should not be tagged
    lines = output.splitlines()
    for line in lines:
        if "TSK-TT-0001-ATM" in line:
            assert "[INTERRUPT]" not in line


async def test_interrupt_threshold_none_no_interrupt_tags():
    """When interrupt_threshold is None, no [INTERRUPT] tags should appear."""
    entries = [_entry(1, score=0.99)]
    output = format_briefing(entries, interrupt_threshold=None)
    assert "[INTERRUPT]" not in output


async def test_interrupt_threshold_boundary_not_above():
    """Score exactly equal to threshold does NOT trigger [INTERRUPT] (strictly >)."""
    entry = _entry(1, score=0.8)
    output = format_briefing([entry], interrupt_threshold=0.8)
    assert "[INTERRUPT]" not in output


async def test_has_interrupt_items_true():
    entries = [_entry(1, score=0.95), _entry(2, score=0.5)]
    assert has_interrupt_items(entries, interrupt_threshold=0.8) is True


async def test_has_interrupt_items_false():
    entries = [_entry(1, score=0.7), _entry(2, score=0.5)]
    assert has_interrupt_items(entries, interrupt_threshold=0.8) is False


async def test_has_interrupt_items_empty_queue():
    assert has_interrupt_items([], interrupt_threshold=0.8) is False
