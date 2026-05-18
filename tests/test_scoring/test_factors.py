# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Known-answer tests for all 7 scoring factors.

Each factor function is pure — no DB, no side effects — so these are
straightforward unit tests with expected outputs from PRD Section 9 / SKILL.md.
"""

from __future__ import annotations

import pytest

from graphclaw.models.enums import EdgeStrength, GoalPriority, OverrideType
from graphclaw.scoring.factors import (
    blocker_score,
    constraint_pressure,
    critical_path_score,
    dependency_weight,
    human_override_score,
    resource_risk,
    timeline_urgency,
)

# ---------------------------------------------------------------------------
# Factor 1: Timeline Urgency
# ---------------------------------------------------------------------------


class TestTimelineUrgency:
    def test_overdue_returns_above_1(self):
        # Overdue by 5 days, no effort → base 1.2 + 0.30 (slack < 0) = 1.50
        assert timeline_urgency(-5.0, 0.0) == pytest.approx(1.50)

    def test_overdue_with_effort(self):
        # Overdue by 2 days, 3 days effort → base 1.2 + 0.30 = 1.50
        assert timeline_urgency(-2.0, 3.0) == pytest.approx(1.50)

    def test_due_today(self):
        # 0.5 days remaining, 0 effort → base 1.0, slack > 0 but < 1 → +0.15
        assert timeline_urgency(0.5, 0.0) == pytest.approx(1.15)

    def test_due_in_2_days(self):
        # 2 days remaining, 0 effort → base 0.85, slack ≥ 1 → no adjustment
        assert timeline_urgency(2.0, 0.0) == pytest.approx(0.85)

    def test_due_in_5_days(self):
        # 5 days remaining, 0 effort → base 0.6
        assert timeline_urgency(5.0, 0.0) == pytest.approx(0.6)

    def test_due_in_10_days(self):
        # 10 days remaining, 0 effort → base 0.4
        assert timeline_urgency(10.0, 0.0) == pytest.approx(0.4)

    def test_far_out(self):
        # 30 days out, 0 effort → base 0.2
        assert timeline_urgency(30.0, 0.0) == pytest.approx(0.2)

    def test_tight_slack_adds_adjustment(self):
        # 5 days remaining, 4.5 days effort → slack = 0.5 < 1 → +0.15
        assert timeline_urgency(5.0, 4.5) == pytest.approx(0.75)

    def test_negative_slack_adds_large_adjustment(self):
        # 5 days remaining, 6 days effort → slack = -1 < 0 → +0.30
        assert timeline_urgency(5.0, 6.0) == pytest.approx(0.90)

    def test_plenty_of_slack(self):
        # 10 days remaining, 2 days effort → slack = 8 → no adjustment
        assert timeline_urgency(10.0, 2.0) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Factor 2: Dependency Weight
# ---------------------------------------------------------------------------


class TestDependencyWeight:
    def test_no_dependents(self):
        assert dependency_weight(0, 0) == 0.0

    def test_only_direct(self):
        assert dependency_weight(3, 0) == 3.0

    def test_only_transitive(self):
        assert dependency_weight(0, 10) == 5.0

    def test_mixed(self):
        assert dependency_weight(2, 6) == pytest.approx(5.0)

    def test_large_fan_out(self):
        assert dependency_weight(10, 20) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Factor 3: Critical Path Score
# ---------------------------------------------------------------------------


class TestCriticalPathScore:
    def test_on_critical_path_p1(self):
        assert critical_path_score(True, GoalPriority.P1) == pytest.approx(1.5)

    def test_on_critical_path_p2(self):
        assert critical_path_score(True, GoalPriority.P2) == pytest.approx(1.3)

    def test_on_critical_path_p3(self):
        assert critical_path_score(True, GoalPriority.P3) == pytest.approx(1.1)

    def test_off_critical_path(self):
        assert critical_path_score(False, GoalPriority.P1) == 0.0

    def test_accepts_string_priority(self):
        assert critical_path_score(True, "P1") == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Factor 4: Blocker Score
# ---------------------------------------------------------------------------


class TestBlockerScore:
    def test_hard_blocker(self):
        assert blocker_score(EdgeStrength.HARD) == 1.0

    def test_soft_blocker(self):
        assert blocker_score(EdgeStrength.SOFT) == 0.6

    def test_no_blocker(self):
        assert blocker_score("NONE") == 0.0

    def test_string_hard(self):
        assert blocker_score("HARD") == 1.0

    def test_unknown_returns_zero(self):
        assert blocker_score("UNKNOWN") == 0.0


# ---------------------------------------------------------------------------
# Factor 5: Human Override Score
# ---------------------------------------------------------------------------


class TestHumanOverrideScore:
    def test_prioritize(self):
        assert human_override_score(OverrideType.PRIORITIZE) == 1.0

    def test_deprioritize(self):
        assert human_override_score(OverrideType.DEPRIORITIZE) == -0.3

    def test_snooze_returns_none(self):
        assert human_override_score(OverrideType.SNOOZE) is None

    def test_string_prioritize(self):
        assert human_override_score("PRIORITIZE") == 1.0

    def test_unknown_returns_zero(self):
        assert human_override_score("UNKNOWN") == 0.0


# ---------------------------------------------------------------------------
# Factor 6: Resource Risk
# ---------------------------------------------------------------------------


class TestResourceRisk:
    def test_perfect_resource(self):
        # reliability=1.0, load=0.0, signals=0.0 → 0.0
        assert resource_risk(1.0, 0.0, 0.0) == pytest.approx(0.0)

    def test_worst_case(self):
        # reliability=0.0, load=1.0, signals=1.0 → 0.5 + 0.3 + 0.2 = 1.0
        assert resource_risk(0.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_medium_risk(self):
        # reliability=0.5, load=0.5, signals=0.5 → 0.25 + 0.15 + 0.10 = 0.50
        assert resource_risk(0.5, 0.5, 0.5) == pytest.approx(0.50)

    def test_high_reliability_low_load(self):
        # reliability=0.9, load=0.1, signals=0.0 → 0.05 + 0.03 + 0.0 = 0.08
        assert resource_risk(0.9, 0.1, 0.0) == pytest.approx(0.08)


# ---------------------------------------------------------------------------
# Factor 7: Constraint Pressure
# ---------------------------------------------------------------------------


class TestConstraintPressure:
    def test_no_constraints(self):
        assert constraint_pressure([]) == 0.0

    def test_single_constraint_half_used(self):
        # threshold=100, current=50 → pressure = 50/100 = 0.5
        result = constraint_pressure([{"threshold": 100, "current_value": 50}])
        assert result == pytest.approx(0.5)

    def test_constraint_at_limit(self):
        # threshold=100, current=100 → pressure = 100/100 = 1.0
        result = constraint_pressure([{"threshold": 100, "current_value": 100}])
        assert result == pytest.approx(1.0)

    def test_constraint_exceeded(self):
        # threshold=100, current=150 → 150/100 = 1.5 → clamped to 1.0
        result = constraint_pressure([{"threshold": 100, "current_value": 150}])
        assert result == pytest.approx(1.0)

    def test_constraint_low_usage(self):
        # threshold=50, current=10 → pressure = 10/50 = 0.2
        result = constraint_pressure([{"threshold": 50, "current_value": 10}])
        assert result == pytest.approx(0.2)

    def test_multiple_constraints(self):
        # Two constraints: 0.5 + 0.2 = 0.7
        result = constraint_pressure(
            [
                {"threshold": 100, "current_value": 50},  # 0.5
                {"threshold": 50, "current_value": 10},  # 0.2
            ]
        )
        assert result == pytest.approx(0.7)

    def test_zero_threshold_skipped(self):
        result = constraint_pressure([{"threshold": 0, "current_value": 50}])
        assert result == 0.0

    def test_missing_keys_skipped(self):
        result = constraint_pressure([{"threshold": 100}])
        assert result == 0.0
