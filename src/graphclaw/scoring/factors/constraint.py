"""graphclaw.scoring.factors.constraint — Factor 7: Constraint Pressure (W7=0.05).

Description
-----------
Computes the aggregate constraint pressure for a task by summing the normalised
proximity-to-threshold for each linked ConstraintNode.  As current_value rises
toward the threshold (e.g. budget spend approaching the cap), the pressure score
approaches 1.0.  The aggregate is unbounded for many active constraints but
weighted with W7=0.05, so its practical impact on the final score remains modest.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only a list of dicts.

Public API
----------
- constraint_pressure: Compute the aggregate constraint pressure score.

Notes
-----
Formula per constraint: ``min(current_value / threshold, 1.0)``, clamped to [0, 1].

Pressure increases as current_value approaches the threshold.  Constraints with a
zero threshold or missing keys are silently skipped to avoid division by zero.
The aggregate sum across multiple constraints is not capped; tasks with many active
near-threshold constraints can accumulate pressure scores above 1.0, but the W7=0.05
weight keeps the contribution to the final score small.
"""
from __future__ import annotations


def constraint_pressure(constraints: list[dict]) -> float:
    """Compute the aggregate constraint pressure for a task.

    Each constraint contributes a normalised pressure value:
    ``current_value / threshold``, clamped to [0, 1].  Pressure increases as
    ``current_value`` approaches or reaches the threshold.

    The total is the sum of all constraint pressures — unbounded above 1.0
    if there are many active constraints (the engine weights this with W7=0.05
    so the practical impact on the final score remains modest).

    Parameters
    ----------
    constraints:
        List of constraint dicts, each with at minimum:
        - ``"threshold"`` (float): the constraint limit (e.g. budget cap)
        - ``"current_value"`` (float): current value approaching the threshold

        Constraints with ``"threshold"`` == 0 or missing keys are skipped
        to avoid division by zero.

    Returns
    -------
    float
        Total constraint pressure score (>= 0.0).
    """
    total = 0.0
    for c in constraints:
        threshold = c.get("threshold")
        current_value = c.get("current_value")
        if threshold is None or current_value is None:
            continue
        threshold = float(threshold)
        current_value = float(current_value)
        if threshold == 0.0:
            continue
        pressure = min(current_value / threshold, 1.0)
        total += max(0.0, pressure)
    return total


__all__ = ["constraint_pressure"]
