"""graphclaw.scoring.factors.critical_path — Factor 3: Critical Path Score (W3=0.20).

Description
-----------
Computes the critical path contribution for a task.  If the task is on the
critical path of its parent goal, the score is 1.0 multiplied by the goal's
priority multiplier (P1=1.5, P2=1.3, P3=1.1).  If off the critical path, the
score is 0.0.  The multiplier is embedded here rather than in the engine so that
the factor's raw score is self-explanatory: 1.5 immediately communicates "on a P1
critical path."

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only primitives.

Public API
----------
- critical_path_score: Compute the critical-path factor score (0.0 or 1.1–1.5).

Notes
-----
The ``_PRIORITY_MULTIPLIER`` lookup accepts both ``GoalPriority`` enum values and
plain strings (e.g. ``"P1"``) because the scoring context may carry either form
depending on how the goal priority was retrieved from the graph.
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
