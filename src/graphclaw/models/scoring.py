"""Scoring record models for GraphClaw.

These records are written to the database at every scoring pass and power the
explainability interface without requiring the agent to re-reason from scratch
at query time (PRD Section 4.7).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from graphclaw.models.enums import AutonomyLevel


# ---------------------------------------------------------------------------
# ScoreFactor
# ---------------------------------------------------------------------------


class ScoreFactor(BaseModel):
    """A single weighted factor contributing to a task's final priority score."""

    factor_name: str
    raw_score: float
    weight: float
    weighted_score: float
    plain_english: str
    # Example: "Deadline is in 3 days and estimated effort is 2 days —
    #           very little slack remaining."


# ---------------------------------------------------------------------------
# ScoreModifier
# ---------------------------------------------------------------------------


class ScoreModifier(BaseModel):
    """A multiplier applied on top of the base weighted score."""

    modifier_type: str
    multiplier: float
    plain_english: str
    # Example: "This task is on the critical path for the Q3 Launch goal (P1)"


# ---------------------------------------------------------------------------
# ScoreExplanation
# ---------------------------------------------------------------------------


class ScoreExplanation(BaseModel):
    """Full scoring explanation record persisted after each scoring pass."""

    node_id: str
    scored_at: datetime
    final_score: float
    rank: int  # position in the current action queue

    factors: list[ScoreFactor]
    modifiers: list[ScoreModifier] = []

    summary: str
    # Example: "Ranked #1 because it is on the critical path for your highest
    #           priority goal, the deadline is in 3 days, and the assigned
    #           resource has signaled low bandwidth this week."

    topology_note: Optional[str] = None
    # Example: "This is the first actionable node in a sequential chain of
    #           4 tasks. Moving this forward unblocks the entire chain."


# ---------------------------------------------------------------------------
# ActionQueueEntry
# ---------------------------------------------------------------------------


class ActionQueueEntry(BaseModel):
    """A single entry in the agent's prioritised action queue.

    Combines the scored task reference with a recommended action and the full
    scoring explanation so the agent can present a coherent briefing to the user.
    """

    node_id: str
    final_score: float
    rank: int
    recommended_action: str
    autonomy_level: AutonomyLevel = AutonomyLevel.SUGGEST
    explanation: ScoreExplanation
    batched_with: list[str] = []  # node_ids of tasks batched in the same check-in


__all__ = [
    "ScoreFactor",
    "ScoreModifier",
    "ScoreExplanation",
    "ActionQueueEntry",
]
