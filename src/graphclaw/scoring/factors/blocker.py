"""Factor 4: Blocker Score (W4=0.15).

Pure function — no I/O, no imports from db layer.
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
