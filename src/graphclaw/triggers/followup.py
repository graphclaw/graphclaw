# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.triggers.followup — Follow-up timing computation for delegated tasks.

Description
-----------
Implements the PRD Section 10 follow-up cadence formula that determines how
frequently the agent should check on a delegated task.  Less reliable resources
receive more frequent follow-ups; recent on-time delivery slightly reduces the
check-in frequency.

Also provides ``FollowUpCalculator``, a stateless class that derives follow-up
timing from task priority and ``ConfidenceLevel`` using urgency/confidence maps.
The calculator applies clamping so intervals always stay within
``[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS]``.

Design Patterns
---------------
- Pure Functions: Both ``compute_followup_timing`` and ``compute_next_followup``
  are stateless, side-effect-free functions that take explicit arguments and
  return computed values.  This makes them trivially testable and composable.
- Guard Clause: ``reliability_score`` is clamped to a minimum of 0.1 to prevent
  division-by-zero when a resource has no reliability history.
- Class-based Strategy: ``FollowUpCalculator`` wraps the urgency/confidence maps
  so callers deal with domain enums rather than raw floats.

Public API
----------
- compute_followup_timing: Return the number of days until the next follow-up
  given the four timing parameters.
- compute_next_followup: Return the next follow-up datetime from a
  ``FollowupConfig`` model.
- FollowUpCalculator: Stateless calculator producing ``FollowUpTiming`` from
  task priority and confidence level.

Dependencies
------------
- datetime: timedelta.
- graphclaw.models.enums: ConfidenceLevel.
- graphclaw.triggers.models: FollowupConfig, FollowUpTiming.
- graphclaw.models.base: utcnow (timezone-aware timezone.utc timestamp factory).

Notes
-----
Formula (PRD Section 10):
    days = base_cadence * complexity * (1 / reliability) * (1 - recency * 0.2)

The recency_bonus multiplier of 0.2 means a perfect recency score of 1.0 reduces
the interval by 20 %.  A recency_bonus > 1.0 is permitted but unusual.

``FollowUpCalculator`` intervals are in hours (not days) and clamped to
[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS].
"""

from __future__ import annotations

from datetime import datetime, timedelta

from graphclaw.models.base import utcnow
from graphclaw.models.enums import ConfidenceLevel
from graphclaw.triggers.models import FollowupConfig, FollowUpTiming


def compute_followup_timing(
    base_cadence: float,
    complexity_factor: float,
    reliability_score: float,
    recency_bonus: float,
) -> float:
    """Return the number of days until the next follow-up.

    Implements the PRD Section 10 formula::

        days = base_cadence * complexity * (1 / reliability) * (1 - recency * 0.2)

    Less reliable resources (lower ``reliability_score``) produce a shorter
    interval (more frequent follow-ups).  A positive ``recency_bonus`` from
    recent on-time delivery produces a slightly longer interval.

    Args:
        base_cadence: Base check-in cadence in days, from UserNode preferences.
        complexity_factor: Task-type and effort multiplier (>= 1.0 = more complex).
        reliability_score: ResourceNode reliability in [0, 1].  Clamped to 0.1 to
            prevent division-by-zero.
        recency_bonus: On-time delivery bonus in [0, 1].  Each unit reduces the
            interval by 20 %.

    Returns:
        Number of days (float) until the next follow-up.
    """
    return (
        base_cadence
        * complexity_factor
        * (1.0 / max(reliability_score, 0.1))
        * (1.0 - recency_bonus * 0.2)
    )


def compute_next_followup(config: FollowupConfig) -> datetime:
    """Return the next follow-up datetime derived from a FollowupConfig.

    Calls ``compute_followup_timing`` with the config's parameters and adds the
    resulting interval to the current timezone.utc time.

    Args:
        config: FollowupConfig carrying the four timing parameters.

    Returns:
        A timezone-aware timezone.utc datetime in the future.
    """
    from datetime import datetime  # noqa: F401 — imported for type hint below, timezone

    days = compute_followup_timing(
        config.base_cadence_days,
        config.complexity_factor,
        config.reliability_score,
        config.recency_bonus,
    )
    return utcnow() + timedelta(days=days)


# ---------------------------------------------------------------------------
# FollowUpCalculator — priority + confidence → FollowUpTiming
# ---------------------------------------------------------------------------


class FollowUpCalculator:
    """Calculates optimal follow-up timing based on task urgency and confidence.

    Applies urgency multipliers (from task priority P1/P2/P3) and confidence
    adjustments (from ``ConfidenceLevel``) to a base interval of 24 hours, then
    clamps the result to ``[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS]``.

    Usage::

        calc = FollowUpCalculator()
        timing = calc.calculate(task_priority="P1", confidence=ConfidenceLevel.LOW)
        timing.task_id = "TSK-AB-0001-ATM"
    """

    BASE_INTERVAL_HOURS: float = 24.0
    MIN_INTERVAL_HOURS: float = 4.0
    MAX_INTERVAL_HOURS: float = 168.0  # 7 days

    # P1 → more frequent, P3 → less frequent
    URGENCY_MAP: dict[str, float] = {
        "P1": 0.5,
        "P2": 1.0,
        "P3": 2.0,
    }

    # LOW confidence → more frequent checks
    CONFIDENCE_MAP: dict[ConfidenceLevel, float] = {
        ConfidenceLevel.HIGH: 1.5,
        ConfidenceLevel.MEDIUM: 1.0,
        ConfidenceLevel.LOW: 0.5,
    }

    def calculate(
        self,
        task_priority: str,
        confidence: ConfidenceLevel,
        last_update_hours_ago: float = 0.0,  # reserved for future use
    ) -> FollowUpTiming:
        """Calculate the next follow-up timing for a task.

        Args:
            task_priority: Priority string — "P1", "P2", or "P3".  Unknown
                values fall back to a multiplier of 1.0.
            confidence: Agent confidence level for the task's current estimate.
            last_update_hours_ago: Hours since the last task update (reserved
                for future recency-adjustment logic; currently unused).

        Returns:
            A ``FollowUpTiming`` with ``task_id`` left as an empty string so
            the caller can set it after construction.
        """
        urgency = self.URGENCY_MAP.get(task_priority, 1.0)
        conf_adj = self.CONFIDENCE_MAP.get(confidence, 1.0)

        raw = self.BASE_INTERVAL_HOURS * urgency * conf_adj
        effective = max(self.MIN_INTERVAL_HOURS, min(self.MAX_INTERVAL_HOURS, raw))

        next_at = utcnow() + timedelta(hours=effective)

        return FollowUpTiming(
            task_id="",
            base_interval_hours=self.BASE_INTERVAL_HOURS,
            urgency_multiplier=urgency,
            confidence_adjustment=conf_adj,
            next_followup_at=next_at,
        )
