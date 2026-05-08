# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.scoring.action_queue — Action queue builder from scored tasks.

Description
-----------
Converts a pre-sorted list of ``(TaskNode, ScoreExplanation)`` pairs into
``ActionQueueEntry`` objects by deriving the recommended action verb and
autonomy level from the task's type, state, and autonomy block.  This is the
final assembly step before the action queue is returned to the agent loop or CLI.

Design Patterns
---------------
- Factory Function: ``build_action_queue`` is a pure function with no I/O;
  it maps typed data to typed data using the ``_ACTION_MAP`` vocabulary and the
  autonomy block on each task.

Public API
----------
- build_action_queue: Build a ranked ActionQueueEntry list from scored tasks.

Dependencies
------------
- graphclaw.models.enums: AutonomyLevel, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.scoring: ActionQueueEntry, ScoreExplanation.
"""

from __future__ import annotations

from graphclaw.models.enums import AutonomyLevel, TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import ActionQueueEntry, ScoreExplanation

# ---------------------------------------------------------------------------
# Recommended action vocabulary
# ---------------------------------------------------------------------------

_ACTION_MAP: dict[TaskType, str] = {
    TaskType.DELEGATED: "SEND_FOLLOWUP",
    TaskType.FOLLOWUP: "SEND_FOLLOWUP",
    TaskType.APPROVAL: "REQUEST_APPROVAL",
    TaskType.REVIEW: "REQUEST_REVIEW",
    TaskType.CHECKIN: "SEND_CHECKIN",
    TaskType.DECISION: "PRESENT_DECISION",
    TaskType.RESEARCH: "REVIEW_RESEARCH",
    TaskType.MILESTONE: "NOTIFY_MILESTONE",
    TaskType.COMPOSITE: "BRIEF_HUMAN",
    TaskType.RECURRING: "SPAWN_INSTANCE",
    TaskType.ATOMIC: "EXECUTE_TASK",
}


def _recommended_action(task: TaskNode, explanation: ScoreExplanation) -> str:
    """Derive a recommended action string from task type and state."""
    if task.state == TaskState.BLOCKED:
        return "RESOLVE_BLOCKER"
    if task.state == TaskState.NEEDS_REVIEW:
        return "REQUEST_REVIEW"
    if task.state == TaskState.DELAYED:
        return "ESCALATE"
    return _ACTION_MAP.get(task.task_type, "BRIEF_HUMAN")


def _autonomy_level(task: TaskNode) -> AutonomyLevel:
    """Derive the autonomy level from the task's autonomy block."""
    return task.autonomy.level


# ---------------------------------------------------------------------------
# build_action_queue
# ---------------------------------------------------------------------------


def build_action_queue(
    scored_tasks: list[tuple[TaskNode, ScoreExplanation]],
) -> list[ActionQueueEntry]:
    """Build a ranked ActionQueueEntry list from scored (task, explanation) pairs.

    Parameters
    ----------
    scored_tasks:
        List of (TaskNode, ScoreExplanation) tuples, already sorted by
        final_score descending with rank assigned on the explanation.

    Returns
    -------
    list[ActionQueueEntry]
        One ActionQueueEntry per task, preserving the supplied order.
    """
    entries: list[ActionQueueEntry] = []
    for task, explanation in scored_tasks:
        entry = ActionQueueEntry(
            node_id=task.id,
            final_score=explanation.final_score,
            rank=explanation.rank,
            recommended_action=_recommended_action(task, explanation),
            autonomy_level=_autonomy_level(task),
            explanation=explanation,
            batched_with=[],
        )
        entries.append(entry)
    return entries


__all__ = ["build_action_queue"]
