"""graphclaw.triggers.briefing — BriefingGenerator: 5-section daily briefing from graph state.

Description
-----------
``BriefingGenerator`` consumes a snapshot of task and goal dicts and produces a
structured ``DailyBriefing`` with five labelled sections:

1. **CRITICAL** — Tasks that need immediate attention (BLOCKED/DELAYED on the
   critical path, or past their deadline).  Capped at 3 items.
2. **INFERENCES** — Agent-observed patterns such as systemic blocking or
   widespread delays.
3. **COMPLETED** — Tasks whose state is COMPLETE.
4. **AHEAD OF CURVE** — ACTIVE/IN_PROGRESS tasks with a score above 0.8.
5. **DEFERRED** — Snoozed tasks and P3 tasks still in PENDING state.

Design Patterns
---------------
- Strategy (private section builders): Each section is built by a focused
  private method, making it easy to extend or replace individual sections
  without touching the public ``generate`` method.
- Dependency Injection: The optional ``AsyncLogger`` is injected at
  construction time, keeping the class testable without a real logger.

Public API
----------
- BriefingGenerator.__init__: Construct with an optional AsyncLogger.
- BriefingGenerator.generate: Async method; returns a ``DailyBriefing``.

Dependencies
------------
- datetime: datetime (type annotation only; instances supplied by callers).
- graphclaw.infra.logger: AsyncLogger, generate_session_id.
- graphclaw.models.base: utcnow.
- graphclaw.triggers.models: BriefingSection, DailyBriefing.

Notes
-----
Task dicts are expected to carry the following optional keys:
    id, title, state, priority, deadline, score, is_critical_path.
Missing keys are handled gracefully via ``dict.get`` with safe defaults.
The ``deadline`` value, when present, must be a timezone-aware ``datetime``
comparable to ``utcnow()``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphclaw.infra.logger import AsyncLogger, generate_session_id
from graphclaw.models.base import utcnow
from graphclaw.triggers.models import BriefingSection, DailyBriefing


class BriefingGenerator:
    """Generates the 5-section daily briefing from a graph-state snapshot.

    Usage::

        generator = BriefingGenerator(logger=logger)
        briefing = await generator.generate(tasks=tasks, goals=goals)
    """

    CRITICAL_MAX: int = 3

    def __init__(self, logger: AsyncLogger | None = None) -> None:
        self._logger = logger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        tasks: list[dict],
        goals: list[dict] | None = None,
    ) -> DailyBriefing:
        """Generate a daily briefing from the current task/goal state.

        Args:
            tasks: List of task dicts.  Expected keys (all optional): ``id``,
                ``title``, ``state``, ``priority``, ``deadline`` (datetime),
                ``score`` (float), ``is_critical_path`` (bool).
            goals: Optional list of goal dicts (reserved for future sections).

        Returns:
            A populated ``DailyBriefing`` instance.
        """
        session_id = generate_session_id()
        now = utcnow()

        critical = self._build_critical_section(tasks, now)
        inferences = self._build_inferences_section(tasks)
        completed = self._build_completed_section(tasks)
        ahead = self._build_ahead_section(tasks)
        deferred = self._build_deferred_section(tasks)

        if self._logger is not None:
            self._logger.log(
                "INFO",
                "briefing.generated",
                session_id,
                critical_count=len(critical.items),
                completed_count=len(completed.items),
            )

        return DailyBriefing(
            generated_at=now,
            session_id=session_id,
            critical=critical,
            inferences=inferences,
            completed=completed,
            ahead_of_curve=ahead,
            deferred=deferred,
        )

    # ------------------------------------------------------------------
    # Private section builders
    # ------------------------------------------------------------------

    def _build_critical_section(
        self,
        tasks: list[dict],
        now: datetime,
    ) -> BriefingSection:
        """CRITICAL: Tasks that need immediate attention.  Max 3 items.

        Includes:
        - BLOCKED or DELAYED tasks on the critical path.
        - Tasks whose deadline is strictly before *now* (overdue).
        """
        items: list[str] = []
        for t in tasks:
            state = t.get("state", "")
            title = t.get("title", "Untitled")
            tid = t.get("id", "")
            if t.get("is_critical_path") and state in ("BLOCKED", "DELAYED"):
                items.append(f"[{tid}] {title} — {state}")
            elif t.get("deadline") is not None and t["deadline"] < now:
                items.append(f"[{tid}] {title} — OVERDUE")
        return BriefingSection(
            title="CRITICAL",
            items=items[: self.CRITICAL_MAX],
            max_items=self.CRITICAL_MAX,
        )

    def _build_inferences_section(self, tasks: list[dict]) -> BriefingSection:
        """INFERENCES: Agent observations about task progress patterns."""
        items: list[str] = []

        blocked = [t for t in tasks if t.get("state") == "BLOCKED"]
        if len(blocked) > 2:
            items.append(
                f"{len(blocked)} tasks currently blocked — may indicate systemic dependency issue"
            )

        delayed = [t for t in tasks if t.get("state") == "DELAYED"]
        if delayed:
            items.append(f"{len(delayed)} tasks marked delayed")

        return BriefingSection(title="INFERENCES", items=items)

    def _build_completed_section(self, tasks: list[dict]) -> BriefingSection:
        """COMPLETED: Recently completed tasks (up to 10)."""
        items: list[str] = []
        for t in tasks:
            if t.get("state") == "COMPLETE":
                tid = t.get("id", "")
                title = t.get("title", "Untitled")
                items.append(f"[{tid}] {title}")
        return BriefingSection(title="COMPLETED", items=items[:10])

    def _build_ahead_section(self, tasks: list[dict]) -> BriefingSection:
        """AHEAD OF CURVE: ACTIVE/IN_PROGRESS tasks with score > 0.8."""
        items: list[str] = []
        for t in tasks:
            score = t.get("score")
            if (
                score is not None
                and score > 0.8
                and t.get("state")
                in (
                    "ACTIVE",
                    "IN_PROGRESS",
                )
            ):
                tid = t.get("id", "")
                title = t.get("title", "Untitled")
                items.append(f"[{tid}] {title} — score {score:.2f}")
        return BriefingSection(title="AHEAD OF CURVE", items=items[:10])

    def _build_deferred_section(self, tasks: list[dict]) -> BriefingSection:
        """DEFERRED: Snoozed tasks and P3 PENDING tasks."""
        items: list[str] = []
        for t in tasks:
            state = t.get("state", "")
            tid = t.get("id", "")
            title = t.get("title", "Untitled")
            if state == "SNOOZED":
                items.append(f"[{tid}] {title} — snoozed")
            elif t.get("priority") == "P3" and state == "PENDING":
                items.append(f"[{tid}] {title} — P3 pending")
        return BriefingSection(title="DEFERRED", items=items[:10])
