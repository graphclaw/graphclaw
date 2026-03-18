"""Factor 7: Constraint Pressure (W7=0.05).

Pure function — no I/O, no imports from db layer.
"""
from __future__ import annotations


def constraint_pressure(constraints: list[dict]) -> float:
    """Compute the aggregate constraint pressure for a task.

    Each constraint contributes a normalised pressure value:
    ``(threshold - current_value) / threshold``, clamped to [0, 1].

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
        pressure = (threshold - current_value) / threshold
        total += max(0.0, min(1.0, pressure))
    return total


__all__ = ["constraint_pressure"]
