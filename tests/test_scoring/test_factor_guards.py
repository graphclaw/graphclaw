# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Edge-case guard tests for scoring factors and briefing formatting."""

from __future__ import annotations

from graphclaw.agent.briefing import BriefingContext, format_briefing
from graphclaw.scoring.factors.blocker import blocker_score
from graphclaw.scoring.factors.constraint import constraint_pressure
from graphclaw.scoring.factors.resource_risk import resource_risk


def test_constraint_pressure_none_or_invalid_inputs_returns_safe_value() -> None:
    assert constraint_pressure(None) == 0.0
    assert constraint_pressure([]) == 0.0
    assert constraint_pressure([{"threshold": "x", "current_value": 5}]) == 0.0


def test_blocker_score_normalizes_unknown_and_case_inputs() -> None:
    assert blocker_score("hard") == 1.0
    assert blocker_score(" unknown ") == 0.0
    assert blocker_score("") == 0.0


def test_resource_risk_clamps_out_of_range_values() -> None:
    # reliability < 0 and load/risk > 1 should be clamped into [0,1]
    score = resource_risk(-5.0, 2.0, 9.0)
    assert 0.0 <= score <= 1.0


def test_briefing_handles_non_numeric_ahead_of_curve_score() -> None:
    text = format_briefing(
        queue=[],
        context=BriefingContext(ahead_of_curve=[{"id": "TSK-1", "title": "X", "score": "n/a"}]),
    )
    assert "score: 0.000" in text
