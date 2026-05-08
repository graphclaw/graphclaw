# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.models.scoring_weights — EMA-based scoring weight learner.

Description
-----------
Implements the Exponential Moving Average (EMA) weight-learning mechanism
described in PRD Section 4.1 and review issue #2.  As users take override
actions (PRIORITIZE, DEPRIORITIZE, COMPLETE_EARLY, DEFER) the learner nudges
the seven scoring-factor weights so that future automatic prioritisation better
reflects the user's revealed preferences.

Weights are constrained between ``floor`` and ``ceiling`` at all times, and are
renormalised after every update so they always sum to 1.0.  SNOOZE signals do
not alter weights because snoozing is a contextual user action (e.g. "not now")
rather than a statement about a factor's relative importance.

Design Patterns
---------------
- Dataclass: ``ScoringWeightUpdate`` is a lightweight, immutable value object
  carrying a single feedback signal.
- Strategy / Learner: ``ScoringWeightLearner`` encapsulates the EMA update rule
  and all constraint enforcement, keeping the scoring engine free of learning
  logic.

Public API
----------
- ScoringWeightUpdate: Value object describing one user-feedback signal.
- ScoringWeightLearner: Stateful learner that applies EMA nudges to a weight
  dict and returns a new, normalised copy.
- DEFAULT_SCORING_WEIGHTS: Baseline weights matching PRD Section 4.1 and the
  seven factors in ``graphclaw.scoring.engine``.

Dependencies
------------
- dataclasses: Standard library ``@dataclass`` decorator.
- typing: ``Literal`` for exhaustive signal-type narrowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Baseline scoring weights matching PRD Section 4.1 and the seven factors
#: used by ``graphclaw.scoring.engine``.  Keys correspond to field names in
#: ``graphclaw.models.nodes.ScoringBlock``.
DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "timeline_urgency": 0.25,
    "dependency_weight": 0.20,
    "critical_path": 0.20,
    "blocker_score": 0.15,
    "human_override": 0.10,
    "resource_risk": 0.05,
    "constraint_pressure": 0.05,
}


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringWeightUpdate:
    """A single user-feedback signal used to update one scoring factor's weight.

    Attributes:
        factor_name: Key in the weights dict to update (e.g. ``"timeline_urgency"``).
        direction: ``"UP"`` to increase the factor's weight, ``"DOWN"`` to decrease it.
        signal_type: The observable user action that produced this signal.
            ``SNOOZE`` is included for completeness but does **not** modify weights.
    """

    factor_name: str
    direction: Literal["UP", "DOWN"]
    signal_type: Literal["PRIORITIZE", "DEPRIORITIZE", "SNOOZE", "COMPLETE_EARLY", "DEFER"]


# ---------------------------------------------------------------------------
# Learner
# ---------------------------------------------------------------------------


class ScoringWeightLearner:
    """Applies EMA-based nudges to a scoring weight dict in response to user
    override signals.

    The update rule for direction ``"UP"`` is::

        new_weight = old_weight + lr * (ceiling - old_weight)

    And for direction ``"DOWN"``::

        new_weight = old_weight - lr * (old_weight - floor)

    This ensures the weight approaches (but never reaches) the ceiling or floor
    asymptotically, giving a soft EMA rather than a hard clamp.  After every
    non-snooze update all weights are renormalised so they sum to 1.0.

    Args:
        weights: Initial weight dict.  A shallow copy is taken immediately so
            the caller's dict is never mutated.
        learning_rate: Step-size multiplier (default 0.05).  Smaller values
            produce slower, smoother adaptation.
        floor: Minimum permissible value for any single weight (default 0.02).
        ceiling: Maximum permissible value for any single weight (default 0.40).
    """

    def __init__(
        self,
        weights: dict[str, float],
        learning_rate: float = 0.05,
        floor: float = 0.02,
        ceiling: float = 0.40,
    ) -> None:
        if floor <= 0:
            raise ValueError(f"floor must be positive, got {floor!r}")
        if ceiling > 1.0:
            raise ValueError(f"ceiling must be <= 1.0, got {ceiling!r}")
        if floor >= ceiling:
            raise ValueError(f"floor ({floor!r}) must be strictly less than ceiling ({ceiling!r})")
        if not (0.0 < learning_rate <= 1.0):
            raise ValueError(f"learning_rate must be in (0, 1], got {learning_rate!r}")

        self._weights: dict[str, float] = dict(weights)
        self._lr = learning_rate
        self._floor = floor
        self._ceiling = ceiling

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def weights(self) -> dict[str, float]:
        """Return a copy of the current weight dict."""
        return dict(self._weights)

    def apply(self, update: ScoringWeightUpdate) -> dict[str, float]:
        """Apply a single feedback signal and return the updated weight dict.

        ``SNOOZE`` signals leave all weights unchanged; any other signal nudges
        the named factor according to the EMA rule, clamps all weights to
        ``[floor, ceiling]``, then renormalises so they sum to 1.0.

        Args:
            update: The feedback signal to apply.

        Returns:
            A new ``dict[str, float]`` with the updated, normalised weights.
            The internal state of the learner is also updated in place.

        Raises:
            KeyError: If ``update.factor_name`` is not present in the current
                weight dict.
        """
        if update.signal_type == "SNOOZE":
            # Snooze is a user-context action, not a priority signal.
            return self.weights

        if update.factor_name not in self._weights:
            raise KeyError(
                f"Unknown factor {update.factor_name!r}. Known factors: {sorted(self._weights)}"
            )

        old = self._weights[update.factor_name]

        if update.direction == "UP":
            nudge = self._lr * (self._ceiling - old)
            new_val = old + nudge
        else:  # "DOWN"
            nudge = self._lr * (old - self._floor)
            new_val = old - nudge

        # Hard-clamp (guards against floating-point drift)
        new_val = max(self._floor, min(self._ceiling, new_val))
        self._weights[update.factor_name] = new_val

        # Clamp all weights then renormalise
        self._clamp_all()
        self._normalise()

        return self.weights

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _clamp_all(self) -> None:
        """Clamp every weight to [floor, ceiling] in place."""
        for key in self._weights:
            self._weights[key] = max(self._floor, min(self._ceiling, self._weights[key]))

    def _normalise(self) -> None:
        """Divide all weights by their sum so they sum to 1.0."""
        total = sum(self._weights.values())
        if total == 0.0:
            # Degenerate case: reset to uniform distribution
            n = len(self._weights)
            uniform = 1.0 / n if n > 0 else 0.0
            for key in self._weights:
                self._weights[key] = uniform
        else:
            for key in self._weights:
                self._weights[key] /= total
