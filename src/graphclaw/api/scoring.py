"""graphclaw.api.scoring — Scoring explanation and simulation endpoints.

Description
-----------
Exposes the 7-factor scoring engine results to the cockpit's Explainability
Dashboard (PRD §08).  Endpoints read pre-computed scoring data stored on the
TaskNode's ``scoring`` block, and can optionally re-run the engine for
hypothetical "what-if" simulations.

Endpoints
---------
  GET  /app/v1/scoring/tasks/{task_id}          — Current ScoreExplanation for a task.
  GET  /app/v1/scoring/tasks/{task_id}/history  — Score history from the task's audit log.
  POST /app/v1/scoring/simulate                 — Hypothetical score with modified weights.

Design Patterns
---------------
- Read from stored data: The current score is read from the TaskNode's embedded
  ``scoring`` block (written by the last ``ScoringEngine.score_all()`` pass).
  No DB queries beyond the single node fetch are needed for the read path.
- Simulation: The simulate endpoint instantiates a fresh ``ScoringEngine`` with
  the caller-supplied weights and runs a single ``score_task()`` call using the
  stored raw factor values from the ``ScoringBlock``.
- Dependency injection: ``GraphStore`` and ``ScoringEngine`` are injected via
  ``graphclaw.api.deps``.

Public API
----------
- router: ``APIRouter`` for /scoring routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, GraphStoreDep, ScoringEngineDep.
- graphclaw.models.scoring: ScoreExplanation, ScoreFactor, ScoreModifier.
- graphclaw.models.nodes: TaskNode.
- fastapi: APIRouter, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, ScoringEngineDep
from graphclaw.models.scoring import ScoreExplanation, ScoreFactor, ScoreModifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scoring", tags=["scoring"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScoreHistoryResponse(BaseModel):
    """Paginated list of historical ScoreExplanations for a task."""

    scores: list[ScoreExplanation]
    next_cursor: str | None = None


class SimulateRequest(BaseModel):
    """Request body for the score simulation endpoint."""

    task_id: str
    modified_weights: dict[str, float] | None = Field(
        default=None,
        description="Override any of w1..w7.  Keys: 'w1' through 'w7'.  Must sum to 1.0.",
    )
    modified_factors: dict[str, float] | None = Field(
        default=None,
        description="Override specific raw factor values on the task.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scoring_block_to_explanation(task_id: str, scoring: dict[str, Any]) -> ScoreExplanation:
    """Build a ``ScoreExplanation`` from the raw scoring block stored on a task.

    The scoring block is written by ``ScoringEngine.score_all()`` after each
    scoring pass and contains the pre-computed factor values and final score.
    """
    now = datetime.now(timezone.utc)
    scored_at_raw = scoring.get("last_scored_at") or scoring.get("scored_at")
    if isinstance(scored_at_raw, str):
        try:
            scored_at = datetime.fromisoformat(scored_at_raw)
        except ValueError:
            scored_at = now
    elif isinstance(scored_at_raw, datetime):
        scored_at = scored_at_raw
    else:
        scored_at = now

    # Default weights (PRD §4.1)
    factor_meta: list[tuple[str, str, float]] = [
        ("W1 Timeline Urgency", "timeline_urgency", 0.25),
        ("W2 Dependency Weight", "dependency_weight", 0.20),
        ("W3 Critical Path", "critical_path", 0.20),
        ("W4 Blocker Score", "blocker", 0.15),
        ("W5 Human Override", "human_override", 0.10),
        ("W6 Resource Risk", "resource_risk", 0.05),
        ("W7 Constraint Pressure", "constraint_pressure", 0.05),
    ]

    factors: list[ScoreFactor] = []
    for name, key, weight in factor_meta:
        raw = float(scoring.get(key, 0.0))
        factors.append(
            ScoreFactor(
                factor_name=name,
                raw_score=raw,
                weight=weight,
                weighted_score=round(raw * weight, 4),
                plain_english=f"{name}: raw={raw:.3f}",
            )
        )

    final_score = float(scoring.get("computed_priority", 0.0))
    summary = scoring.get("score_reasoning") or f"Computed priority score: {final_score:.3f}"

    return ScoreExplanation(
        node_id=task_id,
        scored_at=scored_at,
        final_score=final_score,
        rank=0,  # rank is queue-position; not stored per-node; 0 = unknown
        factors=factors,
        modifiers=[],
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/tasks/{task_id}",
    response_model=ScoreExplanation,
    status_code=status.HTTP_200_OK,
    summary="Get task score explanation",
    description=(
        "Return the current 7-factor ScoreExplanation for the given task. "
        "Data is read from the scoring block stored on the TaskNode during "
        "the last scoring pass."
    ),
)
async def get_task_score(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> ScoreExplanation:
    """Return the current ScoreExplanation for *task_id*."""
    task = await graph_store.get_node(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found")

    scoring = task.get("scoring") or task.get("score_block") or {}
    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scoring data found for task '{task_id}'. Run a scoring pass first.",
        )

    return _scoring_block_to_explanation(task_id, scoring)


@router.get(
    "/tasks/{task_id}/history",
    response_model=ScoreHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task score history",
    description=(
        "Return paginated historical ScoreExplanations for a task. "
        "Currently returns the single latest scoring block; future passes "
        "will be appended as the scoring history log grows."
    ),
)
async def get_task_score_history(
    task_id: str,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ScoreHistoryResponse:
    """Return scoring history for *task_id*."""
    task = await graph_store.get_node(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found")

    scoring = task.get("scoring") or task.get("score_block") or {}
    scores: list[ScoreExplanation] = []
    if scoring:
        scores.append(_scoring_block_to_explanation(task_id, scoring))

    # TODO: When a dedicated score_history table/log is implemented, query it here
    # and apply cursor pagination.  For now, return the single current record.
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = scores[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(scores) else None

    return ScoreHistoryResponse(scores=page, next_cursor=next_cursor)


@router.post(
    "/simulate",
    response_model=ScoreExplanation,
    status_code=status.HTTP_200_OK,
    summary="Simulate score with modified parameters",
    description=(
        "Re-compute the priority score for a task using hypothetical weights "
        "or factor overrides.  Useful for the cockpit's 'what-if' slider UI."
    ),
)
async def simulate_score(
    body: SimulateRequest,
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
    scoring_engine: ScoringEngineDep,
) -> ScoreExplanation:
    """Simulate a score with modified weights or factor values."""
    task = await graph_store.get_node(body.task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{body.task_id}' not found",
        )

    scoring = dict(task.get("scoring") or task.get("score_block") or {})
    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scoring data found for task '{body.task_id}'. Run a scoring pass first.",
        )

    # Apply any factor overrides from the request.
    if body.modified_factors:
        for key, val in body.modified_factors.items():
            scoring[key] = float(val)

    # Apply any weight overrides — we rebuild the factor list with new weights.
    weights: dict[str, float] = {
        "w1": 0.25, "w2": 0.20, "w3": 0.20,
        "w4": 0.15, "w5": 0.10, "w6": 0.05, "w7": 0.05,
    }
    if body.modified_weights:
        for k, v in body.modified_weights.items():
            key = k.lower()
            if key in weights:
                weights[key] = float(v)

    factor_keys = [
        ("W1 Timeline Urgency", "timeline_urgency", "w1"),
        ("W2 Dependency Weight", "dependency_weight", "w2"),
        ("W3 Critical Path", "critical_path", "w3"),
        ("W4 Blocker Score", "blocker", "w4"),
        ("W5 Human Override", "human_override", "w5"),
        ("W6 Resource Risk", "resource_risk", "w6"),
        ("W7 Constraint Pressure", "constraint_pressure", "w7"),
    ]

    factors: list[ScoreFactor] = []
    simulated_score = 0.0
    for name, raw_key, w_key in factor_keys:
        raw = float(scoring.get(raw_key, 0.0))
        w = weights[w_key]
        weighted = round(raw * w, 4)
        simulated_score += weighted
        factors.append(
            ScoreFactor(
                factor_name=name,
                raw_score=raw,
                weight=w,
                weighted_score=weighted,
                plain_english=f"{name}: raw={raw:.3f} × weight={w:.2f}",
            )
        )

    return ScoreExplanation(
        node_id=body.task_id,
        scored_at=datetime.now(timezone.utc),
        final_score=round(simulated_score, 4),
        rank=0,
        factors=factors,
        modifiers=[
            ScoreModifier(
                modifier_type="simulation",
                multiplier=1.0,
                plain_english="Simulated score — not persisted to the graph",
            )
        ],
        summary=(
            f"Simulated priority score: {simulated_score:.3f} "
            f"(weights modified: {bool(body.modified_weights)}, "
            f"factors modified: {bool(body.modified_factors)})"
        ),
    )
