# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.models.scoring — Scoring result models for the explainability interface.

Description
-----------
Defines the Pydantic models that capture the output of each scoring pass:
``ScoreFactor`` (one weighted factor), ``ScoreModifier`` (a post-scoring
multiplier), ``ScoreExplanation`` (the complete breakdown persisted to the DB),
and ``ActionQueueEntry`` (the ranked, actionable representation of a scored task).
These records power the PRD Section 4.7 explainability interface without requiring
the agent to re-reason from raw graph data at query time.

Design Patterns
---------------
- Data Transfer Objects: These models carry scoring results from the engine to
  the CLI, API, and DB persistence layer without coupling those consumers to the
  engine internals.

Public API
----------
- ScoreFactor: A single weighted factor contributing to a task's final score.
- ScoreModifier: A multiplier applied on top of the base weighted score.
- ScoreExplanation: Full scoring breakdown for one task, persisted after each pass.
- ActionQueueEntry: A ranked, actionable queue entry combining task and explanation.

Dependencies
------------
- graphclaw.models.enums: AutonomyLevel.
- pydantic: BaseModel.
"""

from datetime import datetime

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

    topology_note: str | None = None
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
