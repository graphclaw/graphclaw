"""graphclaw.scoring.engine — 7-factor weighted priority scoring engine.

Description
-----------
The ``ScoringEngine`` is the computational heart of the GraphClaw agent.  It takes
a list of active ``TaskNode`` objects and a pre-populated ``ScoringContext``, computes
a weighted priority score for each task across 7 factors, applies chain topology
modifiers (critical path multiplier, urgency rollup), and returns a sorted
``ActionQueueEntry`` list ready for the agent briefing.

Design Patterns
---------------
- Strategy: Each of the 7 factors is a pure function imported from
  ``graphclaw.scoring.factors``; the engine orchestrates them without embedding
  factor logic itself.
- Context Object: ``ScoringContext`` is a dataclass populated by the agent loop
  before the scoring pass so that factor functions remain pure and DB-free.
- Cache-Aside: ``ScoreCache`` is checked before computing; results are stored
  after computation and updated with final rank.

Public API
----------
- ScoringEngine: Computes 7-factor priority scores and builds the action queue.
- ScoringEngine.score_task: Score a single task, returning a ScoreExplanation.
- ScoringEngine.score_all: Score all tasks, apply topology, return sorted queue.
- ScoringContext: Pre-computed graph data needed to score a set of tasks.

Dependencies
------------
- graphclaw.scoring.factors: The 7 pure factor functions.
- graphclaw.scoring.cache: ScoreCache for invalidation-based caching.
- graphclaw.scoring.topology: apply_sequential_suppression, urgency_rollup.
- graphclaw.scoring.action_queue: build_action_queue for final assembly.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.scoring: ActionQueueEntry, ScoreExplanation, ScoreFactor, ScoreModifier.
- graphclaw.models.enums: AutonomyLevel, GoalPriority, OverrideType, TaskState.

Notes
-----
Weight defaults (W1=0.25, W2=0.20, W3=0.20, W4=0.15, W5=0.10, W6=0.05, W7=0.05)
match PRD Section 4.1.  These are configurable at ScoringEngine construction time
and will eventually be learned per-user via the UserNode.scoring_weights model.
Critical path importance is expressed through factor F3 (critical_path_score,
W3=0.20) whose raw value is already amplified by goal priority (1.0–1.5x) inside
the factor function itself.  The final_score is the plain weighted sum of all 7
factors; the urgency rollup modifier is recorded for explainability but does not
alter the numeric score directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from graphclaw.models.enums import GoalPriority, OverrideType, TaskState
from graphclaw.models.nodes import TaskNode
from graphclaw.models.scoring import ActionQueueEntry, ScoreExplanation, ScoreFactor, ScoreModifier
from graphclaw.scoring.cache import ScoreCache
from graphclaw.scoring.factors import (
    blocker_score,
    constraint_pressure,
    critical_path_score,
    dependency_weight,
    human_override_score,
    resource_risk,
    timeline_urgency,
)

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring context
# ---------------------------------------------------------------------------


@dataclass
class ScoringContext:
    """Pre-computed graph data needed to score a set of tasks.

    Callers (typically the orchestrating agent loop) populate this from
    the DB layer before calling ``ScoringEngine.score_all()``.  The factor
    functions themselves are pure and do not touch the DB.

    Attributes
    ----------
    task_goal_priority:
        Maps task_id → GoalPriority of the task's parent goal (if any).
    task_direct_dependents:
        Maps task_id → count of direct downstream dependents.
    task_transitive_dependents:
        Maps task_id → count of transitive downstream dependents.
    task_blocker_type:
        Maps task_id → blocker edge strength ("HARD"|"SOFT"|"NONE").
    task_resource_reliability:
        Maps task_id → assigned resource reliability score (0–1).
    task_resource_load_factor:
        Maps task_id → assigned resource load factor (0–1).
    task_resource_risk_signals:
        Maps task_id → normalised risk signal value (0–1).
    task_constraints:
        Maps task_id → list of constraint dicts (threshold, current_value).
    graph_repo:
        Optional graph repository — used only for topology modifiers.
        May be None when running without DB (e.g. unit tests).
    """

    task_goal_priority: dict[str, GoalPriority] = field(default_factory=dict)
    task_direct_dependents: dict[str, int] = field(default_factory=dict)
    task_transitive_dependents: dict[str, int] = field(default_factory=dict)
    task_blocker_type: dict[str, str] = field(default_factory=dict)
    task_resource_reliability: dict[str, float] = field(default_factory=dict)
    task_resource_load_factor: dict[str, float] = field(default_factory=dict)
    task_resource_risk_signals: dict[str, float] = field(default_factory=dict)
    task_constraints: dict[str, list[dict]] = field(default_factory=dict)
    graph_repo: GraphStore | None = field(default=None, compare=False)


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------


class ScoringEngine:
    """Computes 7-factor priority scores and builds the action queue.

    Parameters
    ----------
    w1 through w7:
        Factor weights.  Defaults match PRD Section 4.1.
    cache:
        Optional ScoreCache instance.  If None, caching is disabled.
    """

    def __init__(
        self,
        w1: float = 0.25,
        w2: float = 0.20,
        w3: float = 0.20,
        w4: float = 0.15,
        w5: float = 0.10,
        w6: float = 0.05,
        w7: float = 0.05,
        cache: ScoreCache | None = None,
    ) -> None:
        self.w1 = w1
        self.w2 = w2
        self.w3 = w3
        self.w4 = w4
        self.w5 = w5
        self.w6 = w6
        self.w7 = w7
        self.cache = cache or ScoreCache()

    # ------------------------------------------------------------------
    # Single-task scoring
    # ------------------------------------------------------------------

    def score_task(
        self,
        task: TaskNode,
        context: ScoringContext,
    ) -> ScoreExplanation:
        """Compute the full 7-factor score for a single task.

        Uses the cache if available.  Returns a ScoreExplanation with
        all individual factor breakdowns.

        Parameters
        ----------
        task:
            The task to score.
        context:
            Pre-computed graph data for the scoring factors.

        Returns
        -------
        ScoreExplanation
            Full scoring explanation including factor breakdowns and
            final weighted score.
        """
        cached = self.cache.get(task.id)
        if cached is not None:
            return cached

        now = datetime.now(timezone.utc)
        tid = task.id

        # --- Factor 1: Timeline Urgency ---
        deadline = task.timeline.deadline
        effort_days = task.timeline.estimated_effort_days or 0.0
        if deadline is not None:
            # Ensure both datetimes are timezone-aware for comparison
            dl = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
            days_remaining = (dl - now).total_seconds() / 86400.0
        else:
            days_remaining = 999.0  # no deadline → effectively far out

        f1_raw = timeline_urgency(days_remaining, effort_days)
        f1_weighted = f1_raw * self.w1

        # --- Factor 2: Dependency Weight ---
        direct_deps = context.task_direct_dependents.get(tid, 0)
        trans_deps = context.task_transitive_dependents.get(tid, 0)
        f2_raw = dependency_weight(direct_deps, trans_deps)
        f2_weighted = f2_raw * self.w2

        # --- Factor 3: Critical Path ---
        goal_priority = context.task_goal_priority.get(tid, GoalPriority.P3)
        f3_raw = critical_path_score(task.on_critical_path, goal_priority)
        f3_weighted = f3_raw * self.w3

        # --- Factor 4: Blocker Score ---
        blocker_type = context.task_blocker_type.get(tid, "NONE")
        f4_raw = blocker_score(blocker_type)
        f4_weighted = f4_raw * self.w4

        # --- Factor 5: Human Override ---
        modifiers: list[ScoreModifier] = []
        f5_raw: float = 0.0
        if task.override.is_overridden and task.override.override_type is not None:
            override_val = human_override_score(task.override.override_type)
            if override_val is None:
                # Snoozed — excluded from queue; score 0.
                f5_raw = 0.0
            else:
                f5_raw = override_val
        f5_weighted = f5_raw * self.w5

        # --- Factor 6: Resource Risk ---
        reliability = context.task_resource_reliability.get(tid, 0.8)
        load = context.task_resource_load_factor.get(tid, 0.0)
        signals = context.task_resource_risk_signals.get(tid, 0.0)
        f6_raw = resource_risk(reliability, load, signals)
        f6_weighted = f6_raw * self.w6

        # --- Factor 7: Constraint Pressure ---
        constraints = context.task_constraints.get(tid, [])
        f7_raw = constraint_pressure(constraints)
        f7_weighted = f7_raw * self.w7

        # --- Final Score ---
        # Weighted sum of all 7 factors.  Weights sum to 1.0 so the baseline
        # score range is [0, 1.0], but individual factors can exceed 1.0
        # (e.g. timeline_urgency reaches 1.2 when overdue with negative slack),
        # meaning final_score can occasionally exceed 1.0 before multipliers.
        final_score = (
            f1_weighted
            + f2_weighted
            + f3_weighted
            + f4_weighted
            + f5_weighted
            + f6_weighted
            + f7_weighted
        )

        # Chain urgency rollup applied later by score_all; stored here from
        # task.scoring if it was pre-set.
        chain_rollup = task.scoring.chain_urgency_rollup
        if chain_rollup > 0.0:
            modifiers.append(
                ScoreModifier(
                    modifier_type="chain_urgency_rollup",
                    multiplier=1.0,
                    plain_english=(
                        f"Urgency rollup from downstream chain tasks: "
                        f"effective urgency raised to {chain_rollup:.3f}"
                    ),
                )
            )

        # Build factor list.
        factors = [
            ScoreFactor(
                factor_name="timeline_urgency",
                raw_score=f1_raw,
                weight=self.w1,
                weighted_score=f1_weighted,
                plain_english=_timeline_plain_english(days_remaining, effort_days, f1_raw),
            ),
            ScoreFactor(
                factor_name="dependency_weight",
                raw_score=f2_raw,
                weight=self.w2,
                weighted_score=f2_weighted,
                plain_english=(
                    f"{direct_deps} direct and {trans_deps} transitive downstream dependents"
                ),
            ),
            ScoreFactor(
                factor_name="critical_path",
                raw_score=f3_raw,
                weight=self.w3,
                weighted_score=f3_weighted,
                plain_english=(
                    f"{'On' if task.on_critical_path else 'Off'} critical path "
                    f"(goal priority: {goal_priority.value if hasattr(goal_priority, 'value') else goal_priority})"
                ),
            ),
            ScoreFactor(
                factor_name="blocker",
                raw_score=f4_raw,
                weight=self.w4,
                weighted_score=f4_weighted,
                plain_english=f"Blocking edge strength: {blocker_type}",
            ),
            ScoreFactor(
                factor_name="human_override",
                raw_score=f5_raw,
                weight=self.w5,
                weighted_score=f5_weighted,
                plain_english=(
                    f"Human override: {task.override.override_type.value if task.override.override_type else 'none'}"
                ),
            ),
            ScoreFactor(
                factor_name="resource_risk",
                raw_score=f6_raw,
                weight=self.w6,
                weighted_score=f6_weighted,
                plain_english=(
                    f"Resource reliability={reliability:.2f}, load={load:.2f}, "
                    f"risk_signals={signals:.2f}"
                ),
            ),
            ScoreFactor(
                factor_name="constraint_pressure",
                raw_score=f7_raw,
                weight=self.w7,
                weighted_score=f7_weighted,
                plain_english=f"{len(constraints)} active constraints, pressure={f7_raw:.3f}",
            ),
        ]

        summary = _build_summary(task, final_score, factors)

        explanation = ScoreExplanation(
            node_id=tid,
            scored_at=now,
            final_score=final_score,
            rank=0,  # rank assigned by score_all
            factors=factors,
            modifiers=modifiers,
            summary=summary,
        )

        self.cache.set(tid, explanation)
        return explanation

    # ------------------------------------------------------------------
    # Batch scoring
    # ------------------------------------------------------------------

    async def score_all(
        self,
        tasks: list[TaskNode],
        context: ScoringContext,
    ) -> list[ActionQueueEntry]:
        """Score all tasks, apply topology modifiers, return sorted queue.

        Excludes:
        - COMPLETE / CANCELLED tasks
        - BLOCKED tasks (score suppressed per PRD — blocker is elevated instead)
        - SNOOZED tasks whose override type is SNOOZE

        Parameters
        ----------
        tasks:
            All candidate tasks.
        context:
            Pre-computed scoring context.

        Returns
        -------
        list[ActionQueueEntry]
            Sorted (descending by final_score) ActionQueueEntry list with
            rank assigned.
        """
        from graphclaw.scoring.action_queue import build_action_queue
        from graphclaw.scoring.topology import apply_sequential_suppression, urgency_rollup

        # Filter out terminal / excluded states.
        scoreable = [
            t
            for t in tasks
            if t.state not in (TaskState.COMPLETE, TaskState.CANCELLED, TaskState.BLOCKED)
            and not _is_snoozed(t)
        ]

        # Compute topology suppression if graph_repo is available.
        suppressed: dict[str, bool] = {t.id: False for t in scoreable}
        rollup_scores: dict[str, float] = {t.id: 0.0 for t in scoreable}

        if context.graph_repo is not None:
            suppressed = await apply_sequential_suppression(scoreable, context.graph_repo)
            rollup_scores = await urgency_rollup(scoreable, context.graph_repo)

        # Apply rollup to task scoring blocks before scoring.
        for task in scoreable:
            rollup = rollup_scores.get(task.id, 0.0)
            if rollup > task.scoring.timeline_urgency:
                task.scoring.chain_urgency_rollup = rollup

        # Score each task (uses cache).
        scored: list[tuple[TaskNode, ScoreExplanation]] = []
        for task in scoreable:
            if suppressed.get(task.id, False):
                continue
            explanation = self.score_task(task, context)
            scored.append((task, explanation))

        # Sort descending by final_score.
        scored.sort(key=lambda x: x[1].final_score, reverse=True)

        # Assign ranks and build ActionQueueEntries.
        ranked_explanations: list[ScoreExplanation] = []
        for rank, (_, expl) in enumerate(scored, start=1):
            expl.rank = rank
            # Update cached version with rank.
            self.cache.set(expl.node_id, expl)
            ranked_explanations.append(expl)

        tasks_by_id = {t.id: t for t in scoreable}
        queue = build_action_queue([(tasks_by_id[e.node_id], e) for e in ranked_explanations])
        return queue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_snoozed(task: TaskNode) -> bool:
    """Return True if the task has a SNOOZE override (excluded from queue)."""
    if task.state == TaskState.SNOOZED:
        return True
    if task.override.is_overridden and task.override.override_type == OverrideType.SNOOZE:
        return True
    return False


def _timeline_plain_english(days_remaining: float, effort_days: float, raw: float) -> str:
    if days_remaining <= 0:
        return f"Task is overdue by {abs(days_remaining):.1f} days"
    slack = days_remaining - effort_days
    if slack < 0:
        return (
            f"Deadline in {days_remaining:.1f} days but {effort_days:.1f} days of effort "
            "remain — negative slack"
        )
    return (
        f"Deadline in {days_remaining:.1f} days, {effort_days:.1f} days effort, "
        f"{slack:.1f} days slack (urgency={raw:.3f})"
    )


def _build_summary(
    task: TaskNode,
    final_score: float,
    factors: list[ScoreFactor],
) -> str:
    top_factor = max(factors, key=lambda f: f.weighted_score)
    return (
        f"Task '{task.title}' scored {final_score:.3f}. "
        f"Top factor: {top_factor.factor_name} "
        f"(weighted {top_factor.weighted_score:.3f}). "
        f"State: {task.state.value}."
    )


__all__ = ["ScoringEngine", "ScoringContext"]
