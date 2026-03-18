"""Factor 2: Dependency Weight (W2=0.20).

Pure function — no I/O, no imports from db layer.
"""
from __future__ import annotations


def dependency_weight(direct_dependents: int, transitive_dependents: int) -> float:
    """Compute the dependency weight score for a task.

    Tasks that block many other tasks should score higher because
    unblocking them has disproportionate leverage on overall progress.

    Parameters
    ----------
    direct_dependents:
        Number of tasks that directly depend on this task
        (one hop via DEPENDS_ON).
    transitive_dependents:
        Number of tasks that transitively depend on this task
        (all downstream DEPENDS_ON hops).

    Returns
    -------
    float
        Raw dependency weight.  Not bounded to [0, 1] — the engine
        normalises or caps as needed.
    """
    return direct_dependents + (transitive_dependents * 0.5)


__all__ = ["dependency_weight"]
