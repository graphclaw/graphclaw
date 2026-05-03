"""graphclaw.triggers.follow_up — FollowUpTrigger service (FR-SCHED-001).

Description
-----------
Cron-driven trigger that selects tasks due for follow-up and invokes the
comms agent in ``trigger=follow_up_review`` mode for each candidate.

Follow-up candidate criteria (per owner user):
1. State in {WAITING, IN_PROGRESS}.
2. ``archived_at IS NULL``.
3. ``last_outbound_at`` is NULL **or** more than ``follow_up_days`` ago.

Design Patterns
---------------
- Strategy: ``FollowUpTrigger.run()`` is a single entry point; selection
  logic is encapsulated separately from agent invocation.
- Graceful degradation: per-candidate errors are caught and logged; a single
  failure does not abort the batch.

Public API
----------
- FollowUpTrigger: Main trigger service.
- FollowUpTriggerConfig: Configuration model.
- FollowUpCandidate: Data model for a follow-up candidate task.

Dependencies
------------
- graphclaw.agent.main_orchestrator: MainOrchestrator.
- graphclaw.infra.broker: MessageBroker (for publishing follow-up trigger events).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FollowUpTriggerConfig(BaseModel):
    """Configuration for the FollowUpTrigger.

    Attributes
    ----------
    user_id:
        Owner user ID to run the trigger for.
    agent_id:
        Agent ID to invoke (used as the ``session_id`` prefix).
    follow_up_days:
        Number of days after last outbound before a task is considered stale.
    max_candidates:
        Maximum number of candidates to process per run.
    interrupt_threshold:
        Minimum score the task needs to qualify for follow-up (0.0–1.0).
    """

    user_id: str
    agent_id: str = "main"
    follow_up_days: int = Field(default=3, ge=1, le=365)
    max_candidates: int = Field(default=10, ge=1, le=100)
    interrupt_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass
class FollowUpCandidate:
    """A task selected for follow-up review.

    Attributes
    ----------
    task_id:
        Task node ID.
    title:
        Human-readable task title.
    state:
        Current task state.
    last_outbound_at:
        ISO timestamp of most recent outbound message, or None.
    score:
        Latest priority score, or 0.0 if not scored.
    """

    task_id: str
    title: str
    state: str
    last_outbound_at: str | None
    score: float = 0.0


class FollowUpTrigger:
    """Cron-driven follow-up trigger (FR-SCHED-001).

    Parameters
    ----------
    graph_repo:
        Graph store with ``list_follow_up_candidates(user_id, cutoff_iso, ...)``
        method.
    comms_agent:
        ``MainOrchestrator`` instance for delivering the follow-up review.
    broker:
        Optional broker for publishing ``agent.trigger.follow_up`` events.
    config:
        ``FollowUpTriggerConfig`` with per-user settings.
    """

    def __init__(
        self,
        graph_repo: Any,
        comms_agent: Any,
        config: FollowUpTriggerConfig,
        broker: Any | None = None,
    ) -> None:
        self._repo = graph_repo
        self._agent = comms_agent
        self._config = config
        self._broker = broker

    async def run(self) -> int:
        """Execute one follow-up trigger cycle.

        1. Query candidate tasks.
        2. For each candidate, invoke comms agent with a synthetic trigger message.
        3. Log and return the number of candidates processed.

        Returns
        -------
        int
            Number of candidates processed (including failed ones).
        """
        cfg = self._config
        cutoff = (datetime.now(UTC) - timedelta(days=cfg.follow_up_days)).isoformat()

        candidates = await self._select_candidates(cutoff)
        logger.info(
            "follow_up_trigger.run",
            extra={
                "user_id": cfg.user_id,
                "candidate_count": len(candidates),
                "follow_up_days": cfg.follow_up_days,
            },
        )

        for candidate in candidates[: cfg.max_candidates]:
            await self._process_candidate(candidate)

        return min(len(candidates), cfg.max_candidates)

    async def _select_candidates(self, cutoff_iso: str) -> list[FollowUpCandidate]:
        """Query the graph store for follow-up candidates."""
        try:
            raw = await self._repo.list_follow_up_candidates(
                user_id=self._config.user_id,
                cutoff_iso=cutoff_iso,
                states=["WAITING", "IN_PROGRESS"],
                limit=self._config.max_candidates,
            )
            candidates = []
            for row in raw or []:
                candidates.append(
                    FollowUpCandidate(
                        task_id=row.get("task_id", ""),
                        title=row.get("title", ""),
                        state=row.get("state", ""),
                        last_outbound_at=row.get("last_outbound_at"),
                        score=float(row.get("score", 0.0)),
                    )
                )
            # Filter by interrupt threshold
            return [c for c in candidates if c.score >= self._config.interrupt_threshold]
        except Exception as exc:  # noqa: BLE001
            logger.warning("follow_up_trigger.candidate_query_failed: %s", exc)
            return []

    async def _process_candidate(self, candidate: FollowUpCandidate) -> None:
        """Invoke the comms agent for a single follow-up candidate."""
        import uuid  # noqa: PLC0415

        session_id = f"follow_up_{self._config.agent_id}_{uuid.uuid4().hex[:8]}"
        synthetic_message = (
            f"[TRIGGER: follow_up_review]\n"
            f"Task {candidate.task_id} ({candidate.title!r}) is in state {candidate.state} "
            f"and has had no outbound contact since "
            f"{candidate.last_outbound_at or 'never'}. "
            f"Please review the situation and determine if a follow-up message is warranted."
        )
        try:
            await self._agent.process_chat_message(
                user_id=self._config.user_id,
                text=synthetic_message,
                session_id=session_id,
                channel="trigger",
            )
            logger.debug(
                "follow_up_trigger.candidate_processed",
                extra={"task_id": candidate.task_id, "session_id": session_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "follow_up_trigger.candidate_failed: %s task=%s",
                exc,
                candidate.task_id,
            )
