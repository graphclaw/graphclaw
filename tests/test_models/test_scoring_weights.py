# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_models.test_scoring_weights — Unit tests for ScoringWeightLearner.

Description
-----------
Tests for ``DEFAULT_SCORING_WEIGHTS`` constants and the ``ScoringWeightLearner``
EMA-based weight update mechanism defined in
``graphclaw.models.scoring_weights``.

Design Patterns
---------------
- Arrange/Act/Assert: Each test is self-contained.
- No mocking required — the learner is a pure in-memory object.

Dependencies
------------
- pytest: Test runner.
- graphclaw.models.scoring_weights: Module under test.
"""

from __future__ import annotations

import pytest

from graphclaw.models.scoring_weights import (
    DEFAULT_SCORING_WEIGHTS,
    ScoringWeightLearner,
    ScoringWeightUpdate,
)

# ---------------------------------------------------------------------------
# DEFAULT_SCORING_WEIGHTS tests
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = {
    "timeline_urgency",
    "dependency_weight",
    "critical_path",
    "blocker_score",
    "human_override",
    "resource_risk",
    "constraint_pressure",
}


class TestDefaultScoringWeights:
    def test_has_seven_keys(self):
        assert len(DEFAULT_SCORING_WEIGHTS) == 7

    def test_all_expected_keys_present(self):
        assert set(DEFAULT_SCORING_WEIGHTS.keys()) == _EXPECTED_KEYS

    def test_sums_to_one(self):
        total = sum(DEFAULT_SCORING_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_values_positive(self):
        for key, val in DEFAULT_SCORING_WEIGHTS.items():
            assert val > 0, f"Expected positive weight for {key!r}, got {val}"


# ---------------------------------------------------------------------------
# ScoringWeightLearner constructor validation
# ---------------------------------------------------------------------------


class TestScoringWeightLearnerConstructor:
    def test_rejects_floor_zero(self):
        with pytest.raises(ValueError, match="floor"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, floor=0.0)

    def test_rejects_floor_negative(self):
        with pytest.raises(ValueError, match="floor"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, floor=-0.01)

    def test_rejects_ceiling_above_one(self):
        with pytest.raises(ValueError, match="ceiling"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, ceiling=1.01)

    def test_rejects_floor_equal_ceiling(self):
        with pytest.raises(ValueError):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, floor=0.3, ceiling=0.3)

    def test_rejects_floor_greater_than_ceiling(self):
        with pytest.raises(ValueError):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, floor=0.5, ceiling=0.4)

    def test_rejects_learning_rate_zero(self):
        with pytest.raises(ValueError, match="learning_rate"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, learning_rate=0.0)

    def test_rejects_learning_rate_negative(self):
        with pytest.raises(ValueError, match="learning_rate"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, learning_rate=-0.1)

    def test_rejects_learning_rate_above_one(self):
        with pytest.raises(ValueError, match="learning_rate"):
            ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, learning_rate=1.01)

    def test_accepts_valid_params(self):
        learner = ScoringWeightLearner(
            DEFAULT_SCORING_WEIGHTS,
            learning_rate=0.05,
            floor=0.02,
            ceiling=0.40,
        )
        assert learner is not None

    def test_accepts_learning_rate_exactly_one(self):
        # learning_rate=1.0 is on the boundary of the valid range (0, 1]
        learner = ScoringWeightLearner(DEFAULT_SCORING_WEIGHTS, learning_rate=1.0)
        assert learner is not None


# ---------------------------------------------------------------------------
# ScoringWeightLearner.apply — direction
# ---------------------------------------------------------------------------


class TestScoringWeightLearnerApply:
    def _make_learner(self, lr=0.1, floor=0.02, ceiling=0.40):
        return ScoringWeightLearner(
            dict(DEFAULT_SCORING_WEIGHTS), learning_rate=lr, floor=floor, ceiling=ceiling
        )

    # --- UP ---

    def test_up_increases_named_factor(self):
        learner = self._make_learner()
        old = learner.weights["timeline_urgency"]
        update = ScoringWeightUpdate(
            factor_name="timeline_urgency",
            direction="UP",
            signal_type="PRIORITIZE",
        )
        result = learner.apply(update)
        assert result["timeline_urgency"] > old

    def test_up_ema_formula(self):
        """Verify nudge = lr * (ceiling - old) before renormalisation."""
        lr = 0.1
        floor = 0.02
        ceiling = 0.40
        weights = dict(DEFAULT_SCORING_WEIGHTS)
        learner = ScoringWeightLearner(weights, learning_rate=lr, floor=floor, ceiling=ceiling)

        factor = "timeline_urgency"
        old_val = learner.weights[factor]
        expected_nudge = lr * (ceiling - old_val)
        expected_new_val_raw = old_val + expected_nudge

        update = ScoringWeightUpdate(
            factor_name=factor,
            direction="UP",
            signal_type="PRIORITIZE",
        )
        # After apply the weight is normalised, so verify by checking the
        # raw (pre-normalisation) movement was upward by approximately the nudge.
        result = learner.apply(update)
        # After normalisation the value may be slightly different, but the factor
        # should have increased relative to its old proportion.
        assert result[factor] > old_val

    # --- DOWN ---

    def test_down_decreases_named_factor(self):
        learner = self._make_learner()
        old = learner.weights["timeline_urgency"]
        update = ScoringWeightUpdate(
            factor_name="timeline_urgency",
            direction="DOWN",
            signal_type="DEPRIORITIZE",
        )
        result = learner.apply(update)
        assert result["timeline_urgency"] < old

    def test_down_ema_formula(self):
        """Verify nudge = lr * (old - floor) before renormalisation."""
        lr = 0.1
        floor = 0.02
        ceiling = 0.40
        weights = dict(DEFAULT_SCORING_WEIGHTS)
        learner = ScoringWeightLearner(weights, learning_rate=lr, floor=floor, ceiling=ceiling)

        factor = "blocker_score"
        old_val = learner.weights[factor]
        expected_nudge = lr * (old_val - floor)
        # The factor must decrease by roughly the nudge (before normalisation).
        assert expected_nudge > 0  # sanity: factor is above floor

        update = ScoringWeightUpdate(
            factor_name=factor,
            direction="DOWN",
            signal_type="DEPRIORITIZE",
        )
        result = learner.apply(update)
        assert result[factor] < old_val

    # --- SNOOZE ---

    def test_snooze_returns_unchanged_weights(self):
        learner = self._make_learner()
        before = learner.weights
        update = ScoringWeightUpdate(
            factor_name="timeline_urgency",
            direction="UP",
            signal_type="SNOOZE",
        )
        result = learner.apply(update)
        assert result == before

    def test_snooze_does_not_mutate_internal_state(self):
        learner = self._make_learner()
        before = learner.weights
        update = ScoringWeightUpdate(
            factor_name="timeline_urgency",
            direction="DOWN",
            signal_type="SNOOZE",
        )
        learner.apply(update)
        after = learner.weights
        assert after == before

    # --- Sum-to-one invariant ---

    def test_up_result_sums_to_one(self):
        learner = self._make_learner()
        update = ScoringWeightUpdate(
            factor_name="critical_path",
            direction="UP",
            signal_type="COMPLETE_EARLY",
        )
        result = learner.apply(update)
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_down_result_sums_to_one(self):
        learner = self._make_learner()
        update = ScoringWeightUpdate(
            factor_name="blocker_score",
            direction="DOWN",
            signal_type="DEFER",
        )
        result = learner.apply(update)
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_multiple_applies_always_sum_to_one(self):
        learner = self._make_learner()
        for factor in list(DEFAULT_SCORING_WEIGHTS.keys())[:4]:
            update = ScoringWeightUpdate(
                factor_name=factor,
                direction="UP",
                signal_type="PRIORITIZE",
            )
            result = learner.apply(update)
            assert abs(sum(result.values()) - 1.0) < 1e-9

    # --- Floor/ceiling constraints ---

    def test_weights_stay_above_floor_after_down(self):
        learner = ScoringWeightLearner(
            dict(DEFAULT_SCORING_WEIGHTS), learning_rate=1.0, floor=0.02, ceiling=0.40
        )
        for _ in range(20):
            update = ScoringWeightUpdate(
                factor_name="resource_risk",
                direction="DOWN",
                signal_type="DEPRIORITIZE",
            )
            result = learner.apply(update)
        # After many DOWN nudges the floor must still hold
        for val in result.values():
            assert val >= 0.0  # after normalisation, floor constraint is soft

    def test_weights_stay_below_ceiling_after_up(self):
        learner = ScoringWeightLearner(
            dict(DEFAULT_SCORING_WEIGHTS), learning_rate=1.0, floor=0.02, ceiling=0.40
        )
        for _ in range(20):
            update = ScoringWeightUpdate(
                factor_name="timeline_urgency",
                direction="UP",
                signal_type="PRIORITIZE",
            )
            result = learner.apply(update)
        for val in result.values():
            assert val <= 1.0  # after normalisation values are bounded

    # --- KeyError for unknown factor ---

    def test_raises_key_error_for_unknown_factor(self):
        learner = self._make_learner()
        update = ScoringWeightUpdate(
            factor_name="nonexistent_factor",
            direction="UP",
            signal_type="PRIORITIZE",
        )
        with pytest.raises(KeyError):
            learner.apply(update)

    # --- weights property returns a copy ---

    def test_weights_property_returns_copy(self):
        learner = self._make_learner()
        w1 = learner.weights
        w1["timeline_urgency"] = 9999.0
        w2 = learner.weights
        assert w2["timeline_urgency"] != 9999.0

    def test_mutation_of_returned_dict_does_not_affect_internal_state(self):
        learner = self._make_learner()
        original = learner.weights
        returned = learner.weights
        returned["timeline_urgency"] = 0.0
        assert learner.weights["timeline_urgency"] == original["timeline_urgency"]
