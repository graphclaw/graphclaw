# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.scoring.topology — Chain topology analysis and sequential suppression.

Description
-----------
Implements the PRD Section 9 chain topology modifiers used by the scoring engine.
For sequential chains (CompositeMetadata.breakdown_strategy == SEQUENTIAL), only
the first actionable node is surfaced in the action queue — all later nodes are
suppressed until their predecessor completes.  The urgency of downstream tasks
rolls up to the chain head so the agent sees the full urgency context.  Parallel
chains score all nodes independently with no suppression.

Design Patterns
---------------
- Graph Traversal: Topology analysis is performed by navigating PART_OF and
  DEPENDS_ON edges via GraphRepository, keeping the logic DB-query-driven
  rather than requiring an in-memory graph structure.

Public API
----------
- ChainTopology: Topology metadata for a single task.
- analyze_chain_topology: Determine topology (sequential/parallel) for one task.
- apply_sequential_suppression: Return suppression map for a list of tasks.
- urgency_rollup: Compute the maximum downstream urgency for each chain head.

Dependencies
------------
- graphclaw.db.base: GraphStore ABC (TYPE_CHECKING only).
- graphclaw.models.enums: BreakdownStrategy, TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.type_metadata: CompositeMetadata.

Notes
-----
``analyze_chain_topology`` makes multiple DB round-trips per task (PART_OF edge
look-up, parent node fetch, siblings look-up, sibling DEPENDS_ON look-up).  For
Phase 0 with small graphs this is acceptable.  Future phases should batch these
queries or cache the results across the scoring cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphclaw.models.enums import BreakdownStrategy, TaskState
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import CompositeMetadata

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topology data class
# ---------------------------------------------------------------------------


class ChainTopology:
    """Topology metadata for a single task."""

    def __init__(
        self,
        task_id: str,
        is_sequential: bool,
        is_first_actionable: bool,
        chain_head_id: str | None,
        chain_length: int,
    ) -> None:
        self.task_id = task_id
        self.is_sequential = is_sequential
        self.is_first_actionable = is_first_actionable
        self.chain_head_id = chain_head_id
        self.chain_length = chain_length


# ---------------------------------------------------------------------------
# analyze_chain_topology
# ---------------------------------------------------------------------------


async def analyze_chain_topology(
    task: TaskNode,
    graph_repo: GraphStore,
) -> ChainTopology:
    """Determine whether *task* lives in a sequential or parallel chain.

    A task is in a sequential chain when its composite parent uses
    ``BreakdownStrategy.SEQUENTIAL``.  If the parent cannot be found, we
    default to parallel (independent).

    Parameters
    ----------
    task:
        The task to analyse.
    graph_repo:
        Repository for graph look-ups.

    Returns
    -------
    ChainTopology
        Topology metadata for this task.
    """
    # Find the parent composite task via PART_OF edge.
    part_of_edges = await graph_repo.get_edges(task.id, direction="out", edge_type="PART_OF")
    if not part_of_edges:
        # No parent — treat as standalone (parallel).
        return ChainTopology(
            task_id=task.id,
            is_sequential=False,
            is_first_actionable=True,
            chain_head_id=None,
            chain_length=1,
        )

    parent_id: str = part_of_edges[0].get("_end_id", "")
    if not parent_id:
        return ChainTopology(
            task_id=task.id,
            is_sequential=False,
            is_first_actionable=True,
            chain_head_id=None,
            chain_length=1,
        )

    parent_props = await graph_repo.get_node(parent_id)
    if parent_props is None:
        return ChainTopology(
            task_id=task.id,
            is_sequential=False,
            is_first_actionable=True,
            chain_head_id=None,
            chain_length=1,
        )

    try:
        parent_task = TaskNode.model_validate(parent_props)
    except Exception:
        return ChainTopology(
            task_id=task.id,
            is_sequential=False,
            is_first_actionable=True,
            chain_head_id=None,
            chain_length=1,
        )

    is_sequential = False
    if isinstance(parent_task.type_metadata, CompositeMetadata):
        is_sequential = parent_task.type_metadata.breakdown_strategy == BreakdownStrategy.SEQUENTIAL

    if not is_sequential:
        return ChainTopology(
            task_id=task.id,
            is_sequential=False,
            is_first_actionable=True,
            chain_head_id=None,
            chain_length=1,
        )

    # Find all siblings in the chain.
    sibling_edges = await graph_repo.get_edges(parent_id, direction="in", edge_type="PART_OF")
    sibling_ids = [e.get("_start_id") for e in sibling_edges if e.get("_start_id")]
    chain_length = len(sibling_ids)

    # Determine first actionable: the sibling in an actionable state with the
    # lowest position in the dependency chain.  In practice, we look for the
    # first sibling that has no incomplete DEPENDS_ON predecessors.
    # For Phase 0 simplicity: first sibling that is ACTIVE or PENDING and
    # not waiting on incomplete predecessors.
    first_actionable_id: str | None = None
    for sid in sibling_ids:
        sib_props = await graph_repo.get_node(sid)
        if sib_props is None:
            continue
        try:
            sib = TaskNode.model_validate(sib_props)
        except Exception:
            continue
        state = sib.state
        if state in (TaskState.ACTIVE, TaskState.PENDING, TaskState.IN_PROGRESS):
            # Check if this sibling has any incomplete DEPENDS_ON predecessors
            # within the same chain.
            dep_edges = await graph_repo.get_edges(sid, direction="out", edge_type="DEPENDS_ON")
            incomplete_preds = False
            for dep in dep_edges:
                pred_id = dep.get("_end_id")
                if pred_id in sibling_ids:
                    pred_props = await graph_repo.get_node(pred_id)
                    if pred_props and pred_props.get("state") != TaskState.COMPLETE.value:
                        incomplete_preds = True
                        break
            if not incomplete_preds:
                first_actionable_id = sid
                break

    is_first = first_actionable_id == task.id

    return ChainTopology(
        task_id=task.id,
        is_sequential=True,
        is_first_actionable=is_first,
        chain_head_id=first_actionable_id,
        chain_length=chain_length,
    )


# ---------------------------------------------------------------------------
# apply_sequential_suppression
# ---------------------------------------------------------------------------


async def apply_sequential_suppression(
    tasks: list[TaskNode],
    graph_repo: GraphStore,
) -> dict[str, bool]:
    """Determine which tasks should be suppressed due to sequential chain topology.

    Parameters
    ----------
    tasks:
        All candidate tasks for the action queue.
    graph_repo:
        Repository for topology look-ups.

    Returns
    -------
    dict[str, bool]
        Maps task_id → True if the task should be suppressed (not surfaced
        in the action queue), False if it should be included.
    """
    suppressed: dict[str, bool] = {}
    for task in tasks:
        topology = await analyze_chain_topology(task, graph_repo)
        if topology.is_sequential and not topology.is_first_actionable:
            suppressed[task.id] = True
            logger.debug(
                "topology: suppressing %s (sequential chain, not first actionable)",
                task.id,
            )
        else:
            suppressed[task.id] = False
    return suppressed


# ---------------------------------------------------------------------------
# urgency_rollup
# ---------------------------------------------------------------------------


async def urgency_rollup(
    tasks: list[TaskNode],
    graph_repo: GraphStore,
) -> dict[str, float]:
    """Compute the chain urgency rollup for sequential chain heads.

    For each task that is the first actionable node in a sequential chain,
    the urgency rollup is the maximum timeline_urgency score among all
    downstream tasks in the same chain.

    Parameters
    ----------
    tasks:
        All candidate tasks (already scored with ``scoring.timeline_urgency``).
    graph_repo:
        Repository for topology look-ups.

    Returns
    -------
    dict[str, float]
        Maps task_id → rollup urgency score.  Non-chain-head tasks get 0.0.
    """
    rollup: dict[str, float] = {t.id: 0.0 for t in tasks}

    # Build a quick lookup by id.
    task_by_id = {t.id: t for t in tasks}

    for task in tasks:
        topology = await analyze_chain_topology(task, graph_repo)
        if not (topology.is_sequential and topology.is_first_actionable):
            continue

        # Find all siblings via the parent.
        part_of_edges = await graph_repo.get_edges(task.id, direction="out", edge_type="PART_OF")
        if not part_of_edges:
            continue
        parent_id = part_of_edges[0].get("_end_id", "")
        if not parent_id:
            continue

        sibling_edges = await graph_repo.get_edges(parent_id, direction="in", edge_type="PART_OF")
        sibling_ids = {e.get("_start_id") for e in sibling_edges if e.get("_start_id")}
        sibling_ids.discard(task.id)  # exclude the chain head itself

        max_urgency = 0.0
        for sid in sibling_ids:
            sib = task_by_id.get(sid)
            if sib is not None:
                urgency = sib.scoring.timeline_urgency
                if urgency > max_urgency:
                    max_urgency = urgency

        rollup[task.id] = max_urgency
        logger.debug(
            "topology: urgency rollup for chain head %s = %.3f",
            task.id,
            max_urgency,
        )

    return rollup


__all__ = [
    "ChainTopology",
    "analyze_chain_topology",
    "apply_sequential_suppression",
    "urgency_rollup",
]
