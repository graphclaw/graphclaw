# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.scoring.factors.dependencies — Factor 2: Dependency Weight (W2=0.20).

Description
-----------
Computes the dependency weight contribution for a task based on how many other
tasks (directly or transitively) depend on it.  Tasks that block many downstream
tasks provide disproportionate leverage when unblocked, so they should be surfaced
higher in the action queue.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only scalars.

Public API
----------
- dependency_weight: Compute the dependency weight score for a task.

Notes
-----
The formula ``direct + transitive * 0.5`` is intentionally unbounded above 1.0.
The ScoringEngine multiplies this by W2=0.20, which caps the practical contribution
for typical graphs (e.g. 5 direct + 10 transitive = 10.0 raw * 0.20 = 2.0 weighted).
Future phases may normalise this against the maximum observed value in the current
scoring batch to keep scores comparable across graph sizes.
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
