# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.state.cascade — Composite completion cascade and shared transition helpers.

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
- persist_transition: Apply a state-machine transition and persist it.
- persist_transition_and_cascade: Persist a transition and run COMPLETE-triggered cascade.

Dependencies
------------
- graphclaw.db.base: GraphStore ABC (TYPE_CHECKING only).
- graphclaw.models.enums: ChangedBy, ConfidenceLevel, GateType, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.type_metadata: CompositeMetadata.
- graphclaw.state.machine: StateMachine (module-level singleton ``_sm``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphclaw.models.deserialization import deserialize_task_node_props
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
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)


def _deserialize_node_props(raw: dict) -> dict:
    """Parse JSON-string fields in a raw AGE node property dict.

    AGE stores nested Pydantic objects as JSON strings (via ``_to_cypher_value``).
    This helper converts them back to Python dicts/lists so that
    ``TaskNode.model_validate`` succeeds.
    """
    return deserialize_task_node_props(raw)


def _resolve_state_machine(state_machine: StateMachine | None) -> StateMachine:
    """Return an injected state machine or a local default instance."""
    return state_machine or StateMachine()


# ---------------------------------------------------------------------------
# Composite completion cascade
# ---------------------------------------------------------------------------


def check_composite_completion(
    parent_task: TaskNode,
    children: list[TaskNode],
    grandparent: TaskNode | None = None,
    siblings: list[TaskNode] | None = None,
    state_machine: StateMachine | None = None,
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
    sm = _resolve_state_machine(state_machine)

    if low_confidence_children:
        logger.info(
            "cascade: low-confidence children for %s — transitioning to NEEDS_REVIEW",
            parent_task.id,
        )
        sm.transition(
            parent_task,
            TaskState.NEEDS_REVIEW,
            ChangedBy.CASCADE,
            "Low-confidence child tasks require human review before parent can complete",
        )
        return

    # Step 5: auto-complete the parent.
    logger.info("cascade: auto-completing %s via CASCADE", parent_task.id)
    sm.transition(
        parent_task,
        TaskState.COMPLETE,
        ChangedBy.CASCADE,
        "All required children completed — cascade auto-complete",
    )

    # Step 6: recurse upward.
    if grandparent is not None and siblings is not None:
        check_composite_completion(grandparent, siblings, state_machine=sm)


# ---------------------------------------------------------------------------
# Sequential chain activation
# ---------------------------------------------------------------------------


async def activate_next_in_chain(
    completed_task: TaskNode,
    graph_repo: GraphStore,
    state_machine: StateMachine | None = None,
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
    sm = _resolve_state_machine(state_machine)

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
                sm.transition(
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


async def _load_children_for_parent(parent_id: str, graph_repo: GraphStore) -> list[TaskNode]:
    """Return parsed direct PART_OF children for *parent_id*."""
    child_edges = await graph_repo.get_edges(parent_id, direction="in", edge_type="PART_OF")

    children: list[TaskNode] = []
    for edge in child_edges:
        child_id = edge.get("_start_id")
        if not child_id:
            continue
        raw_child = await graph_repo.get_node(child_id)
        if raw_child is None:
            continue
        try:
            children.append(TaskNode.model_validate(_deserialize_node_props(raw_child)))
        except Exception:
            logger.warning("cascade: could not parse child node %s", child_id)
    return children


async def run_post_transition_cascade(
    task: TaskNode,
    graph_repo: GraphStore,
    state_machine: StateMachine | None = None,
) -> list[TaskNode]:
    """Run COMPLETE-triggered cascade side effects for *task* and persist them."""
    if task.state != TaskState.COMPLETE:
        return []

    persisted_updates: list[TaskNode] = []

    sm = _resolve_state_machine(state_machine)

    activated = await activate_next_in_chain(task, graph_repo, state_machine=sm)
    for activated_task in activated:
        await graph_repo.update_node(activated_task.id, activated_task.model_dump(mode="json"))
        persisted_updates.append(activated_task)

    parent_edges = await graph_repo.get_edges(task.id, direction="out", edge_type="PART_OF")
    for edge in parent_edges:
        parent_id = edge.get("_end_id")
        if not parent_id:
            continue

        raw_parent = await graph_repo.get_node(parent_id)
        if raw_parent is None:
            continue

        try:
            parent_task = TaskNode.model_validate(_deserialize_node_props(raw_parent))
        except Exception:
            logger.warning("cascade: could not parse parent node %s", parent_id)
            continue

        children = await _load_children_for_parent(parent_id, graph_repo)
        prior_state = parent_task.state
        check_composite_completion(parent_task, children, state_machine=sm)

        if parent_task.state == prior_state:
            continue

        await graph_repo.update_node(parent_task.id, parent_task.model_dump(mode="json"))
        persisted_updates.append(parent_task)

        if parent_task.state == TaskState.COMPLETE:
            persisted_updates.extend(
                await run_post_transition_cascade(parent_task, graph_repo, state_machine=sm)
            )

    return persisted_updates


async def persist_transition(
    task: TaskNode,
    target_state: TaskState,
    changed_by: ChangedBy,
    reason: str,
    graph_repo: GraphStore,
    state_machine: StateMachine,
) -> TaskNode:
    """Apply a state transition and persist the mutated task node."""
    state_machine.transition(task, target_state, changed_by, reason)
    await graph_repo.update_node(task.id, task.model_dump(mode="json"))
    return task


async def persist_transition_and_cascade(
    task: TaskNode,
    target_state: TaskState,
    changed_by: ChangedBy,
    reason: str,
    graph_repo: GraphStore,
    state_machine: StateMachine,
) -> TaskNode:
    """Persist a state transition and run any cascade side effects it triggers."""
    await persist_transition(task, target_state, changed_by, reason, graph_repo, state_machine)
    await run_post_transition_cascade(task, graph_repo, state_machine=state_machine)
    return task


__all__ = [
    "check_composite_completion",
    "activate_next_in_chain",
    "persist_transition",
    "persist_transition_and_cascade",
    "run_post_transition_cascade",
]
