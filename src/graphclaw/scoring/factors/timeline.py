# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.scoring.factors.timeline — Factor 1: Timeline Urgency (W1=0.25).

Description
-----------
Computes a stepped urgency score from calendar days remaining until the task
deadline, with an additive effort-slack adjustment.  The design uses discrete
brackets rather than a continuous function so that the scoring is predictable
and explainable: operators can reason about which bracket a task falls into
without needing to evaluate a formula.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only scalars.

Public API
----------
- timeline_urgency: Compute the timeline urgency score (may exceed 1.0 if overdue
  with negative slack).

Notes
-----
Score brackets (days_remaining → base):
  > 14 days → 0.2   (comfortable buffer)
  > 7 days  → 0.4
  > 3 days  → 0.6
  > 1 day   → 0.85
  > 0 days  → 1.0   (today)
  <= 0 days → 1.2   (overdue)

Effort-slack adjustment adds up to +0.30 when effort exceeds remaining time
(negative slack), ensuring that a near-deadline task with high effort is scored
more urgently than one with trivial remaining work.
"""

from __future__ import annotations


def timeline_urgency(days_remaining: float, estimated_effort_days: float) -> float:
    """Compute the timeline urgency score for a task.

    Parameters
    ----------
    days_remaining:
        Calendar days until the task deadline (negative = overdue).
    estimated_effort_days:
        Estimated remaining effort in days (from TaskNode.timeline).

    Returns
    -------
    float
        Urgency score.  Values > 1.0 are possible when overdue AND
        effort exceeds remaining time (e.g. 1.2 + 0.30 = 1.50 maximum).
    """
    # Base urgency from days remaining.
    if days_remaining > 14:
        base = 0.2
    elif days_remaining > 7:
        base = 0.4
    elif days_remaining > 3:
        base = 0.6
    elif days_remaining > 1:
        base = 0.85
    elif days_remaining > 0:
        base = 1.0
    else:
        base = 1.2  # overdue

    # Effort-slack adjustment.
    slack = days_remaining - estimated_effort_days
    if slack < 0:
        base += 0.30
    elif slack < 1:
        base += 0.15

    return base


__all__ = ["timeline_urgency"]
