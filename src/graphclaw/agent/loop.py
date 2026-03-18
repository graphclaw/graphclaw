"""Agent reasoning loop for GraphClaw.

The AgentLoop orchestrates one complete scoring cycle:
  1. Fetch active tasks from the graph.
  2. Build a ScoringContext from graph relationships.
  3. Score all tasks and build the ranked ActionQueueEntry list.
  4. Optionally generate a human-readable briefing.

This is the primary entry point for the agent-side of the CLI
(``graphclaw agent run`` / ``graphclaw agent score``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphclaw.models.nodes import GoalNode, ResourceNode, TaskNode
from graphclaw.models.scoring import ActionQueueEntry
from graphclaw.scoring.engine import ScoringContext, ScoringEngine

if TYPE_CHECKING:
    from graphclaw.db.graph_repository import GraphRepository
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)


class AgentLoop:
    """Orchestrates one scoring cycle of the GraphClaw agent.

    Parameters
    ----------
    graph_repo:
        GraphRepository instance for reading nodes and edges.
    scoring_engine:
        ScoringEngine instance used to score tasks.
    state_machine:
        StateMachine instance (available for transition operations if needed
        by future extensions).
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        scoring_engine: ScoringEngine,
        state_machine: StateMachine,
    ) -> None:
        self._repo = graph_repo
        self._engine = scoring_engine
        self._sm = state_machine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_cycle(self) -> list[ActionQueueEntry]:
        """Execute one full agent reasoning cycle.

        Steps
        -----
        1. Fetch all active (non-terminal) TaskNode records from the graph.
        2. Build a ScoringContext by querying relationships for each task.
        3. Score all tasks via ScoringEngine.score_all().
        4. Return the ranked ActionQueueEntry list.

        Returns
        -------
        list[ActionQueueEntry]
            Sorted descending by final_score with rank assigned.
        """
        logger.info("AgentLoop: starting scoring cycle")

        # 1. Fetch active tasks.
        tasks = await self._fetch_active_tasks()
        logger.info("AgentLoop: fetched %d active tasks", len(tasks))

        if not tasks:
            return []

        # 2. Build scoring context.
        context = await self.build_scoring_context(tasks)

        # 3. Score all tasks and return.
        queue = await self._engine.score_all(tasks, context)
        logger.info("AgentLoop: scoring cycle complete — %d items in queue", len(queue))
        return queue

    async def build_scoring_context(self, tasks: list[TaskNode]) -> ScoringContext:
        """Build a ScoringContext for the given task list.

        Queries the graph to populate:
        - task_goal_priority — parent GoalNode priority per task
        - task_direct_dependents — count of direct dependents per task
        - task_transitive_dependents — count of transitive dependents per task
        - task_blocker_type — blocker edge strength per task
        - task_resource_reliability — assigned resource reliability per task
        - task_resource_load_factor — assigned resource load per task
        - task_resource_risk_signals — resource risk signal per task
        - task_constraints — list of constraint dicts per task

        Falls back to safe defaults if any individual lookup fails.

        Parameters
        ----------
        tasks:
            The task list to build context for.

        Returns
        -------
        ScoringContext
            Populated context ready for ScoringEngine.score_all().
        """
        from graphclaw.models.enums import GoalPriority

        task_goal_priority: dict[str, GoalPriority] = {}
        task_direct_dependents: dict[str, int] = {}
        task_transitive_dependents: dict[str, int] = {}
        task_blocker_type: dict[str, str] = {}
        task_resource_reliability: dict[str, float] = {}
        task_resource_load_factor: dict[str, float] = {}
        task_resource_risk_signals: dict[str, float] = {}
        task_constraints: dict[str, list[dict]] = {}

        for task in tasks:
            tid = task.id

            # --- Goal priority ---
            try:
                goal_edges = await self._repo.get_edges(
                    tid, direction="out", edge_type="APPLIES_TO"
                )
                if not goal_edges:
                    # Also check PART_OF
                    goal_edges = await self._repo.get_edges(
                        tid, direction="out", edge_type="PART_OF"
                    )
                priority = GoalPriority.P3
                for edge in goal_edges:
                    goal_id = edge.get("_end_id")
                    if goal_id:
                        goal_props = await self._repo.get_node(goal_id)
                        if goal_props and goal_props.get("priority"):
                            try:
                                priority = GoalPriority(goal_props["priority"])
                            except ValueError:
                                pass
                            break
                task_goal_priority[tid] = priority
            except Exception as exc:
                logger.debug("build_scoring_context: goal lookup failed for %s: %s", tid, exc)
                task_goal_priority[tid] = GoalPriority.P3

            # --- Dependency counts ---
            try:
                # Direct dependents: tasks that depend directly on this task
                # (T)-[:DEPENDS_ON]->(task) — inbound DEPENDS_ON edges
                direct_edges = await self._repo.get_edges(
                    tid, direction="in", edge_type="DEPENDS_ON"
                )
                direct_count = len(direct_edges)
                task_direct_dependents[tid] = direct_count
                # Use direct count as a proxy for transitive when graph
                # traversal queries are not yet wired; a dedicated query
                # module (db/queries/dependencies.py) handles the full graph.
                task_transitive_dependents[tid] = direct_count
            except Exception as exc:
                logger.debug("build_scoring_context: dep lookup failed for %s: %s", tid, exc)
                task_direct_dependents[tid] = 0
                task_transitive_dependents[tid] = 0

            # --- Blocker type ---
            try:
                blocker_edges = await self._repo.get_edges(
                    tid, direction="out", edge_type="BLOCKS"
                )
                if blocker_edges:
                    strength = blocker_edges[0].get("strength", "HARD")
                    task_blocker_type[tid] = str(strength).upper()
                else:
                    task_blocker_type[tid] = "NONE"
            except Exception as exc:
                logger.debug("build_scoring_context: blocker lookup failed for %s: %s", tid, exc)
                task_blocker_type[tid] = "NONE"

            # --- Resource data ---
            try:
                res_edges = await self._repo.get_edges(
                    tid, direction="out", edge_type="ASSIGNED_TO"
                )
                if res_edges:
                    res_id = res_edges[0].get("_end_id")
                    if res_id:
                        res_props = await self._repo.get_node(res_id)
                        if res_props:
                            reliability_block = res_props.get("reliability", {})
                            capacity_block = res_props.get("capacity", {})
                            task_resource_reliability[tid] = float(
                                reliability_block.get("overall_score", 0.8)
                                if isinstance(reliability_block, dict)
                                else 0.8
                            )
                            task_resource_load_factor[tid] = float(
                                capacity_block.get("load_factor", 0.0)
                                if isinstance(capacity_block, dict)
                                else 0.0
                            )
                            # Risk signals: average of normalised risk levels
                            risk_block = res_props.get("current_risk", {})
                            if isinstance(risk_block, dict):
                                level_map = {"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.0}
                                levels = [
                                    level_map.get(
                                        str(risk_block.get(k, "LOW")).upper(), 0.0
                                    )
                                    for k in (
                                        "capacity_risk",
                                        "delivery_risk",
                                        "responsiveness_risk",
                                    )
                                ]
                                task_resource_risk_signals[tid] = sum(levels) / len(levels)
                            else:
                                task_resource_risk_signals[tid] = 0.0
            except Exception as exc:
                logger.debug(
                    "build_scoring_context: resource lookup failed for %s: %s", tid, exc
                )

            # --- Constraints ---
            try:
                con_edges = await self._repo.get_edges(
                    tid, direction="in", edge_type="APPLIES_TO"
                )
                constraints: list[dict] = []
                for edge in con_edges:
                    con_id = edge.get("_start_id")
                    if con_id:
                        con_props = await self._repo.get_node(con_id)
                        if con_props:
                            rule = con_props.get("rule", {})
                            constraints.append(
                                {
                                    "threshold": rule.get("threshold") if isinstance(rule, dict) else None,
                                    "current_value": rule.get("current_value") if isinstance(rule, dict) else None,
                                    "pressure_score": rule.get("pressure_score", 0.0) if isinstance(rule, dict) else 0.0,
                                    "hard_limit": rule.get("hard_limit", False) if isinstance(rule, dict) else False,
                                }
                            )
                task_constraints[tid] = constraints
            except Exception as exc:
                logger.debug(
                    "build_scoring_context: constraint lookup failed for %s: %s", tid, exc
                )
                task_constraints[tid] = []

        return ScoringContext(
            task_goal_priority=task_goal_priority,
            task_direct_dependents=task_direct_dependents,
            task_transitive_dependents=task_transitive_dependents,
            task_blocker_type=task_blocker_type,
            task_resource_reliability=task_resource_reliability,
            task_resource_load_factor=task_resource_load_factor,
            task_resource_risk_signals=task_resource_risk_signals,
            task_constraints=task_constraints,
            graph_repo=self._repo,
        )

    async def generate_briefing(
        self, queue: list[ActionQueueEntry], top_n: int = 5
    ) -> str:
        """Generate a human-readable briefing from the action queue.

        Delegates to ``graphclaw.agent.briefing.format_briefing``.

        Parameters
        ----------
        queue:
            Ranked ActionQueueEntry list.
        top_n:
            Number of top entries to include.

        Returns
        -------
        str
            Formatted briefing text.
        """
        from graphclaw.agent.briefing import format_briefing

        return format_briefing(queue, top_n=top_n)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_active_tasks(self) -> list[TaskNode]:
        """Retrieve all non-terminal TaskNode records from the graph."""
        from graphclaw.models.enums import TaskState

        _TERMINAL = {
            TaskState.COMPLETE.value,
            TaskState.CANCELLED.value,
            TaskState.SNOOZED.value,
        }

        try:
            raw_nodes = await self._repo.list_nodes("TaskNode")
        except Exception as exc:
            logger.warning("AgentLoop: failed to list TaskNode vertices: %s", exc)
            return []

        tasks: list[TaskNode] = []
        for props in raw_nodes:
            if props.get("state") in _TERMINAL:
                continue
            try:
                task = TaskNode.model_validate(props)
                tasks.append(task)
            except Exception as exc:
                logger.warning(
                    "AgentLoop: could not parse TaskNode %s: %s",
                    props.get("id", "?"),
                    exc,
                )
        return tasks


__all__ = ["AgentLoop"]
