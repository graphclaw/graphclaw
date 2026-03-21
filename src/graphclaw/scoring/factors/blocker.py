"""graphclaw.scoring.factors.blocker — Factor 4: Blocker Score (W4=0.15).

Description
-----------
Computes the blocker contribution for a task that is currently blocking another
task via a BLOCKS edge.  When a downstream task is in BLOCKED state, the engine
suppresses that task's own score and instead elevates the blocker's score using
this factor, so the agent's action queue surfaces the root cause rather than the
symptom.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only a type string.

Public API
----------
- blocker_score: Compute the blocker factor score (0.0, 0.6, or 1.0).

Notes
-----
Both enum values (``EdgeStrength.HARD``) and plain strings (``"HARD"``) are
accepted because the scoring context may carry either form depending on how the
BLOCKS edge strength was retrieved from the AGE property graph.
"""

from __future__ import annotations

from graphclaw.models.enums import EdgeStrength

_BLOCKER_SCORES: dict[str, float] = {
    EdgeStrength.HARD: 1.0,
    EdgeStrength.SOFT: 0.6,
    "HARD": 1.0,
    "SOFT": 0.6,
    "NONE": 0.0,
}


def blocker_score(blocker_type: EdgeStrength | str) -> float:
    """Compute the blocker score for a task that is blocking another task.

    When a task is in BLOCKED state, its own score is suppressed (excluded
    from the action queue).  The *blocker* — the task causing the block —
    receives this elevated score contribution instead.

    Parameters
    ----------
    blocker_type:
        The strength of the blocking relationship.
        - ``EdgeStrength.HARD`` / ``"HARD"`` — hard dependency (1.0)
        - ``EdgeStrength.SOFT`` / ``"SOFT"`` — soft dependency (0.6)
        - ``"NONE"`` — task is not acting as a blocker (0.0)

    Returns
    -------
    float
        Blocker contribution score (0.0, 0.6, or 1.0).
    """
    key = blocker_type.value if hasattr(blocker_type, "value") else str(blocker_type)
    return _BLOCKER_SCORES.get(key, 0.0)


__all__ = ["blocker_score"]
