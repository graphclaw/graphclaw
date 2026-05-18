"""
GC-U-SCO-W50-001 - validates scoring explanation weight rendering.

Scenario: Score explanation should return applied runtime weights when persisted,
and default PRD weights when persisted keys are absent.

PRD: docs/cockpit-backend-api-prd.md
Build wave: W50
Layer: L1 Unit
Owner: backend-team
Last reviewed: 2026-05-17

Cases covered:
- uses persisted W1..W7 weights from the task scoring block
- falls back to default PRD weights when no persisted weights are present

Notes:
- Tests the route helper that transforms a scoring block into API explanation DTOs.

"""

# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from graphclaw.api.scoring import _scoring_block_to_explanation


def test_scoring_block_to_explanation_uses_persisted_weights() -> None:
    """Helper should render factor weights from persisted scoring block values."""
    scoring = {
        "timeline_urgency": 0.8,
        "dependency_weight": 0.5,
        "critical_path": 1.0,
        "blocker": 0.6,
        "human_override": 0.0,
        "resource_risk": 0.2,
        "constraint_pressure": 0.3,
        "W1_timeline_weight": 0.40,
        "W2_dependencies_weight": 0.10,
        "W3_critical_path_weight": 0.10,
        "W4_blocker_weight": 0.10,
        "W5_override_weight": 0.10,
        "W6_resource_risk_weight": 0.10,
        "W7_constraint_weight": 0.10,
        "computed_priority": 0.59,
    }

    explanation = _scoring_block_to_explanation("TSK-test-001", scoring)

    weight_by_name = {factor.factor_name: factor.weight for factor in explanation.factors}
    assert weight_by_name["W1 Timeline Urgency"] == pytest.approx(0.40)
    assert weight_by_name["W2 Dependency Weight"] == pytest.approx(0.10)
    assert weight_by_name["W7 Constraint Pressure"] == pytest.approx(0.10)


def test_scoring_block_to_explanation_falls_back_to_default_weights() -> None:
    """Helper should use PRD default weights when persisted weight keys are absent."""
    scoring = {
        "timeline_urgency": 0.8,
        "dependency_weight": 0.5,
        "critical_path": 1.0,
        "blocker": 0.6,
        "human_override": 0.0,
        "resource_risk": 0.2,
        "constraint_pressure": 0.3,
        "computed_priority": 0.59,
    }

    explanation = _scoring_block_to_explanation("TSK-test-002", scoring)

    weight_by_name = {factor.factor_name: factor.weight for factor in explanation.factors}
    assert weight_by_name["W1 Timeline Urgency"] == pytest.approx(0.25)
    assert weight_by_name["W2 Dependency Weight"] == pytest.approx(0.20)
    assert weight_by_name["W7 Constraint Pressure"] == pytest.approx(0.05)
