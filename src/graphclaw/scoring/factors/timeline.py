"""Factor 1: Timeline Urgency (W1=0.25).

Pure function — no I/O, no imports from db layer.
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
