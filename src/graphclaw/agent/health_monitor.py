# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.health_monitor — AgentHealthMonitor: heartbeat tracking and BLOCKED escalation.

Description
-----------
``AgentHealthMonitor`` tracks the last heartbeat timestamp for each running
sub-agent.  It runs a background polling loop and, when a sub-agent's heartbeat
goes stale beyond the configured timeout, transitions the associated task to
BLOCKED and triggers escalation via ``EscalationService``.

No retry is performed on timeout — the task is marked BLOCKED and surfaced to
the user/escalation service.  This prevents duplicate side effects from MCP
write operations or emails that the sub-agent may have already performed.

Design Patterns
---------------
- Background Task: Launched via ``start()``; cancelled cleanly via ``stop()``.
- Dependency Injection: StateMachine, EscalationService, and broker injected.
- Observer: Updated by ``AgentEventConsumer._consume_agent_updates_loop()``
  calling ``record_heartbeat()`` on each incoming ``AgentHeartbeatEvent``.

Public API
----------
- AgentHealthMonitor: Monitor class.
- AgentHealthMonitor.start: Launch the background polling loop.
- AgentHealthMonitor.stop: Cancel the background task.
- AgentHealthMonitor.record_heartbeat: Update last-seen timestamp for an agent.
- AgentHealthMonitor.get_agent_health: Return health status for an agent.

Dependencies
------------
- asyncio: Task, sleep.
- graphclaw.infra.broker: MessageBroker, AGENT_UPDATES.
- graphclaw.agent.sub_agent_runner: AgentUpdateEvent, AgentUpdateEventType.
- graphclaw.infra.logger: AsyncLogger (TYPE_CHECKING).
- graphclaw.state.machine: StateMachine (TYPE_CHECKING).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from graphclaw.agent.sub_agent_runner import AgentUpdateEvent, AgentUpdateEventType
from graphclaw.infra.broker import AGENT_UPDATES, MessageBroker
from graphclaw.models.base import utcnow

if TYPE_CHECKING:
    from graphclaw.state.machine import StateMachine

logger = logging.getLogger(__name__)

AgentHealth = Literal["HEALTHY", "STALE", "BLOCKED"]


@dataclass
class _AgentRecord:
    """Internal liveness record for one sub-agent."""

    agent_id: str
    task_id: str
    session_id: str
    last_heartbeat: datetime
    escalated: bool = False


class AgentHealthMonitor:
    """Monitors sub-agent heartbeats and escalates on timeout.

    Parameters
    ----------
    broker:
        MessageBroker for publishing BLOCKED events to AGENT_UPDATES.
    state_machine:
        StateMachine for transitioning timed-out tasks to BLOCKED.
    check_interval:
        Seconds between health-check sweeps (default 30).
    heartbeat_timeout:
        Seconds of silence after which a sub-agent is considered BLOCKED
        (default 300 — 5 minutes).
    async_logger:
        Optional AsyncLogger for structured audit events.
    """

    def __init__(
        self,
        broker: MessageBroker,
        state_machine: StateMachine | None = None,
        check_interval: int = 30,
        heartbeat_timeout: int = 300,
    ) -> None:
        self._broker = broker
        self._state_machine = state_machine
        self._check_interval = check_interval
        self._heartbeat_timeout = heartbeat_timeout

        self._records: dict[str, _AgentRecord] = {}  # agent_id → record
        self._monitor_task: asyncio.Task | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the background health-check loop."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._check_loop())
        logger.info(
            "AgentHealthMonitor: started (timeout=%ds, interval=%ds)",
            self._heartbeat_timeout,
            self._check_interval,
        )

    async def stop(self) -> None:
        """Cancel the background task."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("AgentHealthMonitor: stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_heartbeat(self, agent_id: str, task_id: str, session_id: str) -> None:
        """Update the last-seen heartbeat timestamp for a sub-agent.

        Called by ``AgentEventConsumer._consume_agent_updates_loop()`` on
        every ``AgentHeartbeatEvent`` and ``AgentTaskStartedEvent`` received.

        Args:
            agent_id: Sub-agent identifier.
            task_id: Task the agent is working on.
            session_id: Orchestration session ID.
        """
        self._records[agent_id] = _AgentRecord(
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            last_heartbeat=utcnow(),
        )

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from monitoring (called on completion or BLOCKED)."""
        self._records.pop(agent_id, None)

    def get_agent_health(self, agent_id: str) -> AgentHealth:
        """Return health status for an agent.

        Returns:
            ``"HEALTHY"`` — heartbeat within timeout window.
            ``"STALE"`` — heartbeat between 60s and timeout threshold.
            ``"BLOCKED"`` — heartbeat beyond timeout threshold.
            ``"HEALTHY"`` — agent not currently tracked (not running).
        """
        record = self._records.get(agent_id)
        if record is None:
            return "HEALTHY"
        age_seconds = (utcnow() - record.last_heartbeat).total_seconds()
        if age_seconds > self._heartbeat_timeout:
            return "BLOCKED"
        if age_seconds > 60:
            return "STALE"
        return "HEALTHY"

    # ------------------------------------------------------------------
    # Background check loop
    # ------------------------------------------------------------------

    async def _check_loop(self) -> None:
        """Periodically check all tracked agents for heartbeat staleness."""
        while self._running:
            await asyncio.sleep(self._check_interval)
            await self._check_timeouts()

    async def _check_timeouts(self) -> None:
        """Find stale agents and mark tasks BLOCKED with escalation."""
        now = utcnow()
        timed_out = [
            rec
            for rec in self._records.values()
            if not rec.escalated
            and (now - rec.last_heartbeat).total_seconds() > self._heartbeat_timeout
        ]

        for rec in timed_out:
            logger.warning(
                "AgentHealthMonitor: agent %s (task %s) heartbeat timeout — marking BLOCKED",
                rec.agent_id,
                rec.task_id,
            )
            rec.escalated = True

            # Transition task to BLOCKED via StateMachine
            if self._state_machine is not None:
                try:
                    await self._state_machine.transition(
                        node_id=rec.task_id,
                        to_state="BLOCKED",
                        changed_by="SYSTEM",
                        reason=f"Sub-agent '{rec.agent_id}' heartbeat timeout after {self._heartbeat_timeout}s",
                    )
                except Exception as exc:
                    logger.warning(
                        "AgentHealthMonitor: state transition failed for %s: %s", rec.task_id, exc
                    )

            # Publish BLOCKED event to AGENT_UPDATES
            blocked_event = AgentUpdateEvent(
                event_type=AgentUpdateEventType.BLOCKED,
                agent_id=rec.agent_id,
                task_id=rec.task_id,
                session_id=rec.session_id,
                message=f"Heartbeat timeout after {self._heartbeat_timeout}s — task marked BLOCKED",
            )
            try:
                await self._broker.publish(AGENT_UPDATES, blocked_event.model_dump_json())
            except Exception as exc:
                logger.warning("AgentHealthMonitor: failed to publish BLOCKED event: %s", exc)

            logger.info(
                "agent.task.blocked",
                extra={
                    "event_type": "agent.task.blocked",
                    "agent_id": rec.agent_id,
                    "task_id": rec.task_id,
                    "session_id": rec.session_id,
                    "reason": f"Heartbeat timeout ({self._heartbeat_timeout}s)",
                },
            )

            # Remove from active tracking after escalation
            self._records.pop(rec.agent_id, None)
