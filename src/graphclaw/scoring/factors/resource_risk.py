"""graphclaw.scoring.factors.resource_risk — Factor 6: Resource Risk (W6=0.05).

Description
-----------
Computes a resource risk score for a task by combining three complementary
signals about the assigned resource: reliability history, current capacity load,
and active risk signals.  Tasks assigned to unreliable, overloaded, or at-risk
resources should score higher so the agent can proactively escalate or re-assign.

Design Patterns
---------------
- Pure Function: No I/O or imports from the DB layer; accepts only scalar floats.

Public API
----------
- resource_risk: Compute the resource risk score (0.0–1.0).

Notes
-----
Formula: ``(1 - reliability) * 0.5 + load_factor * 0.3 + risk_signals * 0.2``

The reliability component is inverted (1 - reliability) so that low reliability
increases the score.  The weights (0.5, 0.3, 0.2) reflect the relative predictive
value of each signal: historical delivery rate is the strongest predictor, current
load is secondary, and point-in-time risk signals are the weakest (most volatile).
"""

from __future__ import annotations


def resource_risk(
    reliability: float,
    load_factor: float,
    risk_signals: float,
) -> float:
    """Compute the resource risk score for a task.

    Parameters
    ----------
    reliability:
        The assigned resource's overall reliability score (0.0 – 1.0).
        Higher reliability → lower risk.  Use ``ResourceNode.reliability.overall_score``.
    load_factor:
        The resource's current load (0.0 = free, 1.0 = fully loaded).
        Use ``ResourceNode.capacity.load_factor``.
    risk_signals:
        An aggregated risk signal value (0.0 – 1.0) derived from the
        number and severity of active risk signals on the resource.
        Callers should normalise this (e.g. ``min(len(signals) / 5, 1.0)``).

    Returns
    -------
    float
        Risk contribution score (0.0 – 1.0).
    """
    return (1.0 - reliability) * 0.5 + load_factor * 0.3 + risk_signals * 0.2


__all__ = ["resource_risk"]
