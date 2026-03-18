"""Factor 6: Resource Risk (W6=0.05).

Pure function — no I/O, no imports from db layer.
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
