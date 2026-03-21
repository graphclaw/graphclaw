"""graphclaw.scoring.factors.override — Factor 5: Human Override Score (W5=0.10).

Description
-----------
Returns the score adjustment for a human override applied to a task.  Overrides
allow users to explicitly raise (PRIORITIZE: +1.0), lower (DEPRIORITIZE: -0.3),
or exclude (SNOOZE: None) a task from the action queue, superseding the computed
priority signal from the other six factors.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only the override type.

Public API
----------
- human_override_score: Return the score adjustment (float) or None if excluded.

Notes
-----
A return value of ``None`` signals to the engine that the task should be excluded
entirely from the action queue (SNOOZE case), not merely scored at 0.  The engine
checks for None explicitly in the SNOOZE branch of its override handling.

The ``_OVERRIDE_VALUES`` dict includes both canonical PRD names (PRIORITIZE,
DEPRIORITIZE, SNOOZE) and legacy aliases (PRIORITY, TOP, WATCH, WAIT, SNOOZED)
to ensure backward compatibility during the Phase 0 migration period.
"""

from __future__ import annotations

from graphclaw.models.enums import OverrideType

# Mapping of override type to score adjustment.
# ``None`` means the task is excluded from scoring entirely (SNOOZE).
_OVERRIDE_VALUES: dict[str, float | None] = {
    "PRIORITIZE": +1.0,  # "Make this a priority"
    "DEPRIORITIZE": -0.3,  # "This can wait"
    "SNOOZE": None,  # Excluded from scoring entirely
    # Internal canonical names from PRD skill (kept for reference):
    "PRIORITY": +1.0,
    "TOP": +1.0,
    "WATCH": +0.5,
    "WAIT": -0.3,
    "SNOOZED": None,
}


def human_override_score(override_type: OverrideType | str) -> float | None:
    """Return the score adjustment for a human override, or None if excluded.

    Parameters
    ----------
    override_type:
        The type of override applied to the task.

    Returns
    -------
    float | None
        Score adjustment to inject into the formula, or ``None`` if the
        task should be excluded from the action queue entirely (SNOOZE).
    """
    key = override_type.value if hasattr(override_type, "value") else str(override_type)
    return _OVERRIDE_VALUES.get(key, 0.0)


__all__ = ["human_override_score"]
