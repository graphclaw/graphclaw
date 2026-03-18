"""Factor 3: Critical Path Score (W3=0.20).

Pure function — no I/O, no imports from db layer.
"""
from __future__ import annotations

from graphclaw.models.enums import GoalPriority

_PRIORITY_MULTIPLIER: dict[str, float] = {
    GoalPriority.P1: 1.5,
    GoalPriority.P2: 1.3,
    GoalPriority.P3: 1.1,
}


def critical_path_score(on_critical_path: bool, goal_priority: GoalPriority | str) -> float:
    """Compute the critical-path contribution for a task.

    Parameters
    ----------
    on_critical_path:
        Whether this task lies on the critical path of its parent goal.
    goal_priority:
        The GoalPriority of the goal whose critical path this task is on.
        Ignored when ``on_critical_path`` is False.

    Returns
    -------
    float
        0.0 if not on the critical path, otherwise
        ``1.0 * priority_multiplier`` (1.1 – 1.5).
    """
    if not on_critical_path:
        return 0.0

    # Accept both enum and plain string values.
    priority_key = goal_priority.value if hasattr(goal_priority, "value") else str(goal_priority)
    multiplier = _PRIORITY_MULTIPLIER.get(priority_key, 1.0)
    return 1.0 * multiplier


__all__ = ["critical_path_score"]
