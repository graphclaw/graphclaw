"""Factor 5: Human Override Score (W5=0.10).

Pure function — no I/O, no imports from db layer.
"""
from __future__ import annotations

from graphclaw.models.enums import OverrideType

# Mapping of override type to score adjustment.
# ``None`` means the task is excluded from scoring entirely (SNOOZE).
_OVERRIDE_VALUES: dict[str, float | None] = {
    "PRIORITIZE": +1.0,   # "Make this a priority"
    "DEPRIORITIZE": -0.3, # "This can wait"
    "SNOOZE": None,       # Excluded from scoring entirely
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
