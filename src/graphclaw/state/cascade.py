"""graphclaw.state.cascade — Composite completion cascade and sequential chain activation.

Description
-----------
Implements the PRD Section 7.2 cascade logic that fires when a child task reaches
COMPLETE.  ``check_composite_completion`` evaluates the AND/OR completion gate of
the parent composite/milestone task, applies confidence halting and approval
blocking guards, then either auto-completes the parent (via CASCADE) or routes it
to NEEDS_REVIEW.  ``activate_next_in_chain`` transitions INACTIVE_PENDING siblings
to ACTIVE when their predecessor finishes, enabling sequential workflow progression.

Design Patterns
---------------
- Chain of Responsibility: ``check_composite_completion`` checks each blocking
  condition in order (REVIEW/APPROVAL pending → gate check → confidence check)
  and short-circuits on the first match, keeping the logic readable.
- Recursive: The function accepts an optional grandparent so it can recurse up
  the PART_OF hierarchy after completing a parent.

Public API
----------
- check_composite_completion: Evaluate and potentially auto-complete a composite parent.
- activate_next_in_chain: Activate INACTIVE_PENDING tasks waiting on a completed task.

Dependencies
------------
- graphclaw.db.base: GraphStore ABC (TYPE_CHECKING only).
- graphclaw.models.enums: ChangedBy, ConfidenceLevel, GateType, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.type_metadata: CompositeMetadata.
- graphclaw.state.machine: StateMachine (module-level singleton ``_sm``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from graphclaw.models.enums import (
    ChangedBy,
    ConfidenceLevel,
    GateType,
    TaskState,
    TaskType,
)
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import CompositeMetadata
from graphclaw.state.machine import StateMachine

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)

_sm = StateMachine()

# Fields that AGE stores as JSON strings inside a node property dict.
# They must be decoded back to dicts before TaskNode.model_validate().
_NODE_JSON_STR_FIELDS = (
    "scoring",
    "timeline",
    "progress",
    "override",
    "autonomy",
    "type_metadata",
)
_NODE_JSON_LIST_FIELDS = ("state_history", "update_log", "tags")


def _deserialize_node_props(raw: dict) -> dict:
    """Parse JSON-string fields in a raw AGE node property dict.

    AGE stores nested Pydantic objects as JSON strings (via ``_to_cypher_value``).
    This helper converts them back to Python dicts/lists so that
    ``TaskNode.model_validate`` succeeds.
    """
    result = dict(raw)
    for field in _NODE_JSON_STR_FIELDS:
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, ValueError):
                result[field] = None
    for field in _NODE_JSON_LIST_FIELDS:
        val = result.get(field)
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                result[field] = []
        elif isinstance(val, list):
            parsed: list = []
            for item in val:
                if isinstance(item, str):
                    try:
                        parsed.append(json.loads(item))
                    except (json.JSONDecodeError, ValueError):
                        parsed.append(item)
                else:
                    parsed.append(item)
            result[field] = parsed
    return result


# ---------------------------------------------------------------------------
# Composite completion cascade
# ---------------------------------------------------------------------------


def check_composite_completion(
    parent_task: TaskNode,
    children: list[TaskNode],
    grandparent: TaskNode | None = None,
    siblings: list[TaskNode] | None = None,
) -> None:
    """Evaluate whether a composite parent should auto-complete.

    This is called when any child task reaches COMPLETE.  It implements
    the full PRD Section 7.2 cascade:

    1. Find incomplete children.
    2. If any incomplete child is REVIEW or APPROVAL → block auto-complete.
    3. Evaluate the completion gate (AND requires all complete; OR requires one).
    4. If any RESEARCH or REVIEW child has LOW confidence → transition to
       NEEDS_REVIEW and halt the cascade.
    5. Otherwise auto-complete the parent via CASCADE.
    6. Recurse upward if a grandparent was supplied.

    Parameters
    ----------
    parent_task:
        The composite (or milestone) parent whose children just changed.
    children:
        All direct children of *parent_task*.
    grandparent:
        Optional grandparent node for upward recursion.
    siblings:
        Children of *grandparent* (needed if recursing).
    """
    if parent_task.state in (TaskState.COMPLETE, TaskState.CANCELLED):
        # Already resolved — nothing to do.
        return

    # Only composite / milestone tasks participate in the cascade.
    if parent_task.task_type not in (TaskType.COMPOSITE, TaskType.MILESTONE):
        return

    # Retrieve gate type from type_metadata (defaults to AND).
    gate: GateType = GateType.AND
    if isinstance(parent_task.type_metadata, CompositeMetadata):
        gate = parent_task.type_metadata.completion_gate
        if not parent_task.type_metadata.auto_complete_on_children:
            logger.debug(
                "cascade: auto_complete_on_children=False for %s, skipping",
                parent_task.id,
            )
            return

    # Step 1: find incomplete children.
    incomplete = [c for c in children if c.state != TaskState.COMPLETE]

    # Step 2: block if any incomplete child requires human action.
    pending_review_or_approval = [
        c for c in incomplete if c.task_type in (TaskType.REVIEW, TaskType.APPROVAL)
    ]
    if pending_review_or_approval:
        logger.debug(
            "cascade: %s has pending REVIEW/APPROVAL children, cannot auto-complete",
            parent_task.id,
        )
        return

    # Step 3: evaluate gate.
    if gate == GateType.AND:
        if incomplete:
            logger.debug(
                "cascade: AND gate — %d incomplete children remain for %s",
                len(incomplete),
                parent_task.id,
            )
            return
    # GateType.OR: at least one child is complete (we were called after a
    # child completed, so the condition is inherently satisfied).

    # Step 4: confidence check — halt at NEEDS_REVIEW.
    low_confidence_children = [
        c
        for c in children
        if c.task_type in (TaskType.RESEARCH, TaskType.REVIEW)
        and c.progress.confidence == ConfidenceLevel.LOW
    ]
    if low_confidence_children:
        logger.info(
            "cascade: low-confidence children for %s — transitioning to NEEDS_REVIEW",
            parent_task.id,
        )
        _sm.transition(
            parent_task,
            TaskState.NEEDS_REVIEW,
            ChangedBy.CASCADE,
            "Low-confidence child tasks require human review before parent can complete",
        )
        return

    # Step 5: auto-complete the parent.
    logger.info("cascade: auto-completing %s via CASCADE", parent_task.id)
    _sm.transition(
        parent_task,
        TaskState.COMPLETE,
        ChangedBy.CASCADE,
        "All required children completed — cascade auto-complete",
    )

    # Step 6: recurse upward.
    if grandparent is not None and siblings is not None:
        check_composite_completion(grandparent, siblings)


# ---------------------------------------------------------------------------
# Sequential chain activation
# ---------------------------------------------------------------------------


async def activate_next_in_chain(
    completed_task: TaskNode,
    graph_repo: GraphStore,
) -> list[TaskNode]:
    """Activate INACTIVE_PENDING tasks that were waiting on *completed_task*.

    Traverses outgoing DEPENDS_ON edges from *completed_task* and transitions
    any task in INACTIVE_PENDING state to ACTIVE (changed_by=CASCADE).

    Parameters
    ----------
    completed_task:
        The task that just reached COMPLETE.
    graph_repo:
        Repository used to look up dependent task nodes.

    Returns
    -------
    list[TaskNode]
        All tasks that were activated.  Callers are responsible for
        persisting these nodes.
    """
    from graphclaw.models.nodes import TaskNode as TN  # local import to avoid cycles

    # Find tasks that depend on the completed task
    # (i.e. tasks T where T-[:DEPENDS_ON]->completed_task).
    dependent_edges = await graph_repo.get_edges(
        completed_task.id, direction="in", edge_type="DEPENDS_ON"
    )

    activated: list[TaskNode] = []
    for edge in dependent_edges:
        dep_id = edge.get("_start_id")
        if not dep_id:
            continue
        node_props = await graph_repo.get_node(dep_id)
        if node_props is None:
            continue
        try:
            dep_task = TN.model_validate(_deserialize_node_props(node_props))
        except Exception:
            logger.warning("activate_next_in_chain: could not parse node %s", dep_id)
            continue

        if dep_task.state == TaskState.INACTIVE_PENDING:
            try:
                _sm.transition(
                    dep_task,
                    TaskState.ACTIVE,
                    ChangedBy.CASCADE,
                    f"Predecessor {completed_task.id} completed — activating next in chain",
                )
                activated.append(dep_task)
                logger.info(
                    "activate_next_in_chain: activated %s after %s completed",
                    dep_task.id,
                    completed_task.id,
                )
            except Exception as exc:
                logger.warning(
                    "activate_next_in_chain: could not activate %s: %s",
                    dep_task.id,
                    exc,
                )

    return activated


__all__ = ["check_composite_completion", "activate_next_in_chain"]
