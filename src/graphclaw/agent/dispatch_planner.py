# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.dispatch_planner — AgentDispatchPlanner: topological sort for parallel dispatch.

Description
-----------
``AgentDispatchPlanner`` takes a list of task IDs proposed for delegation and
queries the task graph for ``DEPENDS_ON`` edges among them.  It performs a
topological sort (Kahn's algorithm) and returns an ordered list of dispatch
tiers, where each tier contains tasks that are safe to execute in parallel.

Example
-------
Tasks: A depends_on C, B is independent of both.
Result: [[B, C], [A]]
  → Tier 1: B and C run in parallel.
  → Tier 2: A starts only after both B and C complete.

If no dependency edges exist among the proposed tasks, all tasks are returned
in a single tier for fully parallel dispatch.

Design Patterns
---------------
- Query Object: Encapsulates the dependency subgraph query.
- Algorithm Object: Kahn's topological sort isolated in ``_topological_sort()``.
- Dependency Injection: ``GraphQueryEngine`` injected at construction time.

Public API
----------
- AgentDispatchPlanner: Planner class.
- AgentDispatchPlanner.plan: Return ordered tiers for a list of task IDs.

Dependencies
------------
- graphclaw.db.base: GraphQueryEngine (TYPE_CHECKING).
- graphclaw.models.enums: EdgeType.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from graphclaw.agent.sub_agent_runner import AgentJobEvent

if TYPE_CHECKING:
    from graphclaw.db.base import GraphQueryEngine

logger = logging.getLogger(__name__)


class AgentDispatchPlanner:
    """Plans parallel dispatch tiers based on task dependency edges.

    Parameters
    ----------
    query_engine:
        GraphQueryEngine for querying DEPENDS_ON edges.
    """

    def __init__(self, query_engine: GraphQueryEngine) -> None:
        self._qe = query_engine

    async def plan(
        self,
        jobs: list[AgentJobEvent],
        session_id: str,
    ) -> list[list[AgentJobEvent]]:
        """Compute ordered dispatch tiers for a list of delegation jobs.

        Queries task graph for DEPENDS_ON edges among the given task IDs,
        performs topological sort, and assigns batch IDs per tier.

        Args:
            jobs: List of ``AgentJobEvent`` objects to plan dispatch for.
            session_id: Orchestration session ID for batch ID generation.

        Returns:
            Ordered list of tiers.  Each tier is a list of ``AgentJobEvent``
            objects that can be dispatched in parallel.  Tier N+1 jobs start
            only after all tier N jobs complete.
            Returns ``[jobs]`` (single tier) if no dependencies found.
        """
        if len(jobs) <= 1:
            # Single job — trivial, no planning needed
            tier_batch_id = f"batch-{session_id[:8]}-t0"
            for job in jobs:
                object.__setattr__(job, "batch_id", tier_batch_id) if hasattr(
                    job, "__dataclass_fields__"
                ) else None
            return [self._assign_batch_id(jobs, tier_batch_id)]

        task_id_to_job: dict[str, AgentJobEvent] = {j.task_id: j for j in jobs}
        task_ids = set(task_id_to_job)

        # Query dependency edges among this subgraph
        edges = await self._fetch_dependency_edges(task_ids)

        if not edges:
            # No dependencies — all parallel
            batch_id = f"batch-{session_id[:8]}-t0"
            return [self._assign_batch_id(jobs, batch_id)]

        # Topological sort
        tiers_ids = self._topological_sort(task_ids, edges)

        # Map task IDs back to jobs and assign batch IDs
        result: list[list[AgentJobEvent]] = []
        for tier_idx, tier_task_ids in enumerate(tiers_ids):
            batch_id = f"batch-{session_id[:8]}-t{tier_idx}"
            tier_jobs = [task_id_to_job[tid] for tid in tier_task_ids if tid in task_id_to_job]
            if tier_jobs:
                result.append(self._assign_batch_id(tier_jobs, batch_id))

        return result if result else [self._assign_batch_id(jobs, f"batch-{session_id[:8]}-t0")]

    # ------------------------------------------------------------------
    # Graph query
    # ------------------------------------------------------------------

    async def _fetch_dependency_edges(self, task_ids: set[str]) -> list[tuple[str, str]]:
        """Return (from_id, to_id) tuples for DEPENDS_ON edges within the set.

        Edge ``(A, B)`` means A depends on B (B must complete before A).
        Only edges where both endpoints are in ``task_ids`` are returned.
        """
        edges: list[tuple[str, str]] = []
        try:
            for task_id in task_ids:
                # Outbound DEPENDS_ON edges: task_id → dependency
                deps = await self._qe.get_edges(
                    node_id=task_id,
                    direction="out",
                    edge_type="DEPENDS_ON",
                )
                for edge in deps:
                    target = edge.get("target_id") or edge.get("to_id") or edge.get("end_id")
                    if target and target in task_ids:
                        edges.append((task_id, target))
        except Exception as exc:
            logger.warning(
                "AgentDispatchPlanner: edge query failed: %s — treating as no dependencies", exc
            )
        return edges

    # ------------------------------------------------------------------
    # Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    @staticmethod
    def _topological_sort(task_ids: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
        """Return ordered tiers via Kahn's BFS topological sort.

        Each tier contains nodes with no remaining unsatisfied dependencies.

        Args:
            task_ids: All task IDs to sort.
            edges: ``(from_id, to_id)`` dependency edges (from depends on to).

        Returns:
            List of tiers, where tier[0] has no dependencies and later tiers
            depend on earlier ones.  Cycles are broken by treating remaining
            nodes as a final tier.
        """
        # Build adjacency: node → set of nodes it depends on
        # and in-degree: how many unsatisfied deps does each node have
        in_degree: dict[str, int] = {t: 0 for t in task_ids}
        dependents: dict[str, list[str]] = defaultdict(list)  # dep → [nodes that need it]

        for from_id, to_id in edges:
            in_degree[from_id] += 1
            dependents[to_id].append(from_id)

        # Kahn's BFS
        queue: deque[str] = deque(tid for tid in task_ids if in_degree[tid] == 0)
        tiers: list[list[str]] = []

        while queue:
            # All nodes currently in queue form one parallel tier
            tier = list(queue)
            tiers.append(tier)
            queue.clear()

            for node in tier:
                for dependent in dependents[node]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        # Handle cycle remainder (should not occur in well-formed task graphs)
        remaining = [tid for tid in task_ids if in_degree[tid] > 0]
        if remaining:
            logger.warning(
                "AgentDispatchPlanner: %d tasks form a cycle — appending as final tier: %s",
                len(remaining),
                remaining,
            )
            tiers.append(remaining)

        return tiers

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_batch_id(jobs: list[AgentJobEvent], batch_id: str) -> list[AgentJobEvent]:
        """Return new AgentJobEvent instances with the given batch_id assigned."""
        return [job.model_copy(update={"batch_id": batch_id}) for job in jobs]
