# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.event_consumer — AgentEventConsumer: processes TRIGGER_EVENTS for the agent.

Description
-----------
``AgentEventConsumer`` runs a long-lived background asyncio task that consumes
events from the ``TRIGGER_EVENTS`` broker queue and routes them to the correct
handler:

- **TIME_BASED / ON_DEMAND** events → run one scoring cycle, generate a briefing,
  and send it to the user via ``OutboundDispatcher``.
- **INBOUND** events → extract the raw inbound message, call
  ``InboundProcessor`` to resolve and update the matched task, then optionally
  send a confirmation reply to the sender via ``OutboundDispatcher``.
- **EVENT_BASED** events → run a scoring cycle and update internal state.

The consumer is designed to run inside the gateway/API service alongside the
``TriggerEngine`` so that the full agent loop (score → brief → reply) operates
on the same event loop as the channel adapters.

Design Patterns
---------------
- Background Task: Launched via ``start()``; cancelled cleanly via ``stop()``.
- Dependency Injection: All collaborators injected at construction time.
- Graceful Degradation: Malformed events and processing errors are logged and
  skipped; the consumer loop never stops for a single bad message.

Public API
----------
- AgentEventConsumer.start: Launch the background consumer task.
- AgentEventConsumer.stop: Gracefully cancel the background task.

Dependencies
------------
- graphclaw.agent.main_orchestrator: MainOrchestrator.
- graphclaw.agent.outbound: OutboundDispatcher.
- graphclaw.infra.broker: MessageBroker, TRIGGER_EVENTS.
- graphclaw.inbound.processor: InboundProcessor.
- graphclaw.triggers.models: TriggerEvent, TriggerType.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from graphclaw.agent.sub_agent_runner import AgentUpdateEvent, AgentUpdateEventType
from graphclaw.infra.broker import AGENT_UPDATES, TRIGGER_EVENTS, MessageBroker
from graphclaw.triggers.models import TriggerEvent, TriggerType

if TYPE_CHECKING:
    from graphclaw.agent.health_monitor import AgentHealthMonitor
    from graphclaw.agent.main_orchestrator import MainOrchestrator
    from graphclaw.agent.outbound import OutboundDispatcher
    from graphclaw.agent.result_collector import ResultCollector
    from graphclaw.cross_tenant.indexer import OrgTaskIndexer
    from graphclaw.identity.directory_indexer import DirectoryIndexer
    from graphclaw.infra.storage import StorageClient
    from graphclaw.infra.user_events import UserEventPublisher

logger = logging.getLogger(__name__)


class AgentEventConsumer:
    """Consumes TRIGGER_EVENTS and drives the agent loop accordingly.

    Parameters
    ----------
    broker:
        Message broker to consume from.
    agent_loop:
        AgentLoop instance for scoring and chat processing.
    dispatcher:
        OutboundDispatcher for sending proactive agent messages.
    user_channels:
        Mapping of user_id → list of channel descriptors
        (e.g. ``{"usr-001": [{"channel": "email", "to": "x@y.com"}]}``).
        Used to deliver briefings and replies to the correct user.
    """

    def __init__(
        self,
        broker: MessageBroker,
        agent_loop: MainOrchestrator,
        dispatcher: OutboundDispatcher,
        user_channels: dict[str, list[dict[str, Any]]] | None = None,
        default_user_id: str = "",
        storage: StorageClient | None = None,
        health_monitor: AgentHealthMonitor | None = None,
        result_collector: ResultCollector | None = None,
        directory_indexer: DirectoryIndexer | None = None,
        task_indexer: OrgTaskIndexer | None = None,
        event_publisher: UserEventPublisher | None = None,
    ) -> None:
        self._broker = broker
        self._loop = agent_loop
        self._dispatcher = dispatcher
        self._storage = storage
        self._event_publisher = event_publisher
        self._user_channels: dict[str, list[dict[str, Any]]] = user_channels or {}
        self._default_user_id: str = default_user_id
        self._task: asyncio.Task | None = None
        self._inbound_task: asyncio.Task | None = None
        self._agent_updates_task: asyncio.Task | None = None  # Phase 5
        self._running = False
        self._memory_lock: asyncio.Lock = asyncio.Lock()
        self._intelligence_agent: Any = None  # InboundIntelligenceAgent wired in start()
        self._health_monitor: AgentHealthMonitor | None = health_monitor
        self._result_collector: ResultCollector | None = result_collector
        self._directory_indexer: DirectoryIndexer | None = directory_indexer  # Wave 8
        self._task_indexer: OrgTaskIndexer | None = task_indexer  # Wave 8

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _graph_repo(self) -> Any:
        """Return graph repo via public orchestrator interface when available."""
        return getattr(self._loop, "graph_repo", None)

    def _llm_client(self) -> Any:
        """Return llm client via public orchestrator interface when available."""
        return getattr(self._loop, "llm_client", None)

    def _agent_id(self) -> str:
        """Return orchestrator agent id via public interface."""
        value = getattr(self._loop, "agent_id", None)
        return str(value) if value else "main"

    async def start(self) -> None:
        """Start the background consumer task."""
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        self._inbound_task = asyncio.create_task(self._consume_inbound_loop())
        self._agent_updates_task = asyncio.create_task(self._consume_agent_updates_loop())

        # Wire InboundIntelligenceAgent if LLM is available
        llm_client = self._llm_client()
        if llm_client is not None and self._storage is not None:
            from graphclaw.inbound.intelligence_agent import (  # noqa: PLC0415
                InboundIntelligenceAgent,
            )

            self._intelligence_agent = InboundIntelligenceAgent(
                llm=llm_client,
                graph_repo=self._graph_repo(),
                storage=self._storage,
                memory_lock=self._memory_lock,
            )

        # Wave 8 — Start directory + task indexers if provided
        if self._directory_indexer is not None:
            await self._directory_indexer.start()
        if self._task_indexer is not None:
            await self._task_indexer.start()

        logger.info("AgentEventConsumer: started")

    async def stop(self) -> None:
        """Gracefully cancel the background consumer task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._inbound_task is not None:
            self._inbound_task.cancel()
            try:
                await self._inbound_task
            except asyncio.CancelledError:
                pass
        if self._agent_updates_task is not None:
            self._agent_updates_task.cancel()
            try:
                await self._agent_updates_task
            except asyncio.CancelledError:
                pass
        # Wave 8 — Stop indexers
        if self._directory_indexer is not None:
            await self._directory_indexer.stop()
        if self._task_indexer is not None:
            await self._task_indexer.stop()
        logger.info("AgentEventConsumer: stopped")

    def register_user_channels(self, user_id: str, channels: list[dict[str, Any]]) -> None:
        """Register or update channel delivery targets for a user.

        Parameters
        ----------
        user_id:
            The user to register channels for.
        channels:
            List of ``{"channel": str, "to": str}`` descriptors.
        """
        self._user_channels[user_id] = channels

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Blocking loop — consume events from TRIGGER_EVENTS and process them."""
        async for raw_message in self._broker.consume(TRIGGER_EVENTS):
            if not self._running:
                break
            try:
                event = TriggerEvent.model_validate_json(raw_message)
                await self._handle_event(event)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "AgentEventConsumer: failed to process event: %s — raw: %.200s",
                    exc,
                    raw_message,
                )

    async def _handle_event(self, event: TriggerEvent) -> None:
        """Route an event to the appropriate handler function."""
        logger.debug(
            "AgentEventConsumer: handling %s event for user=%s",
            event.trigger_type,
            event.user_id,
        )

        if event.trigger_type in (TriggerType.TIME_BASED, TriggerType.ON_DEMAND):
            await self._handle_briefing_trigger(event)
        elif event.trigger_type == TriggerType.INBOUND:
            await self._handle_inbound_trigger(event)
        elif event.trigger_type == TriggerType.EVENT_BASED:
            await self._handle_event_based_trigger(event)
        else:
            logger.warning("AgentEventConsumer: unknown trigger type %s", event.trigger_type)

    async def _handle_briefing_trigger(self, event: TriggerEvent) -> None:
        """Run a scoring cycle, generate a briefing, and send it to the user."""
        user_id = event.user_id
        if not user_id:
            logger.debug("AgentEventConsumer: briefing trigger has no user_id — skipping")
            return

        try:
            trigger_source = "heartbeat"
            if event.trigger_type == TriggerType.ON_DEMAND:
                trigger_source = "on_demand"
            queue = await self._loop.run_cycle(user_id=user_id, trigger_source=trigger_source)
            briefing_text = await self._loop.generate_briefing(queue, top_n=5)
        except Exception as exc:  # noqa: BLE001
            logger.error("AgentEventConsumer: briefing generation failed: %s", exc)
            return

        channels = self._user_channels.get(user_id, [])
        if not channels:
            logger.info(
                "AgentEventConsumer: no channels registered for user %s — briefing not sent",
                user_id,
            )
            return

        subject = "Your GraphClaw Briefing"
        await self._dispatcher.broadcast(channels, subject=subject, body=briefing_text)
        logger.info(
            "AgentEventConsumer: briefing dispatched to %d channel(s) for user %s",
            len(channels),
            user_id,
        )

    async def _handle_inbound_trigger(self, event: TriggerEvent) -> None:
        """Process an inbound message via InboundProcessor; reply via OutboundDispatcher."""
        raw_message_str = event.payload.get("raw_message", "")
        if not raw_message_str:
            return

        # Deserialize the raw inbound message
        try:
            from graphclaw.gateway.schemas import InboundMessage  # noqa: PLC0415

            raw_data = (
                json.loads(raw_message_str) if isinstance(raw_message_str, str) else raw_message_str
            )
            inbound = InboundMessage.model_validate(raw_data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentEventConsumer: could not parse inbound message: %s", exc)
            return

        # Process via shared method
        await self._process_raw_inbound(inbound)

    async def _handle_event_based_trigger(self, event: TriggerEvent) -> None:
        """React to a graph event — run a scoring cycle to refresh priorities."""
        try:
            trigger_source = str(event.payload.get("trigger_source") or "property_change")
            await self._loop.run_cycle(user_id=event.user_id or None, trigger_source=trigger_source)
            logger.debug("AgentEventConsumer: event-based scoring cycle complete")
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentEventConsumer: event-based cycle failed: %s", exc)

    # ------------------------------------------------------------------
    # Direct INBOUND_MESSAGES consumer (local dev)
    # ------------------------------------------------------------------

    async def _consume_inbound_loop(self) -> None:
        """Directly consume INBOUND_MESSAGES queue, bypassing TriggerEngine (local dev)."""
        from graphclaw.gateway.schemas import InboundMessage  # noqa: PLC0415

        logger.info("AgentEventConsumer: inbound consume loop started")
        async for raw_message in self._broker.consume("inbound_messages"):
            if not self._running:
                break
            try:
                data = (
                    json.loads(raw_message)
                    if isinstance(raw_message, (str, bytes))
                    else raw_message
                )
                inbound = InboundMessage(**data)
            except Exception as exc:  # noqa: BLE001
                logger.error("AgentEventConsumer: failed to parse inbound message: %s", exc)
                continue
            try:
                await self._process_raw_inbound(inbound)
            except Exception as exc:  # noqa: BLE001
                logger.error("AgentEventConsumer: failed to process inbound message: %s", exc)
        logger.info("AgentEventConsumer: inbound consume loop stopped")

    # ------------------------------------------------------------------
    # Phase 5 — AGENT_UPDATES consumer (sub-agent orchestration)
    # ------------------------------------------------------------------

    async def _consume_agent_updates_loop(self) -> None:
        """Consume structured AgentUpdateEvents from AGENT_UPDATES queue.

        Routes events to the correct handler:
        - STARTED / HEARTBEAT → AgentHealthMonitor.record_heartbeat()
        - COMPLETED → ResultCollector.process_agent_result() + StateMachine
        - BLOCKED → EscalationService
        - DELEGATION_COMPLETE trigger → AgentLoop.process_chat_message()
        """
        logger.info("AgentEventConsumer: agent_updates consume loop started")
        async for raw_message in self._broker.consume(AGENT_UPDATES):
            if not self._running:
                break
            try:
                event = AgentUpdateEvent.model_validate_json(raw_message)
                await self._handle_agent_update(event)
            except Exception as exc:  # noqa: BLE001
                logger.error("AgentEventConsumer: failed to handle agent_updates message: %s", exc)
        logger.info("AgentEventConsumer: agent_updates consume loop stopped")

    async def _handle_agent_update(self, event: AgentUpdateEvent) -> None:
        """Route a single AgentUpdateEvent to the appropriate handler."""
        # Always update health monitor for STARTED, PROGRESS, HEARTBEAT events
        if self._health_monitor is not None and event.event_type in (
            AgentUpdateEventType.STARTED,
            AgentUpdateEventType.PROGRESS,
            AgentUpdateEventType.HEARTBEAT,
        ):
            self._health_monitor.record_heartbeat(
                agent_id=event.agent_id,
                task_id=event.task_id,
                session_id=event.session_id,
            )

        if event.event_type == AgentUpdateEventType.COMPLETED:
            await self._handle_agent_completed(event)

        elif event.event_type == AgentUpdateEventType.BLOCKED:
            await self._handle_agent_blocked(event)

    async def _handle_agent_completed(self, event: AgentUpdateEvent) -> None:
        """Handle sub-agent task completion: update task node and result collector."""
        # Remove from health monitor tracking
        if self._health_monitor is not None:
            self._health_monitor.remove_agent(event.agent_id)

        # Update task state via ResultCollector if available
        if self._result_collector is not None:
            try:
                await self._result_collector.process_agent_result(event)
            except Exception as exc:
                logger.warning(
                    "AgentEventConsumer: result_collector.process_agent_result failed: %s", exc
                )
        else:
            # Fallback: update task state directly
            try:
                repo = self._graph_repo()
                if repo and event.task_id:
                    import datetime as _dt

                    state = "NEEDS_REVIEW" if event.status == "COMPLETED" else "BLOCKED"
                    await repo.update_node(
                        event.task_id,
                        {
                            "state": state,
                            "intelligence": f"[{_dt.datetime.now(_dt.timezone.utc).date()}] "
                            f"Sub-agent '{event.agent_id}' completed | status={event.status} | "
                            f"{(event.message or '')[:150]}",
                        },
                    )
            except Exception as exc:
                logger.warning("AgentEventConsumer: direct task update failed: %s", exc)

        # Emit SSE event to frontend so user sees sub-agent completion in real-time
        if self._event_publisher is not None:
            try:
                from graphclaw.agent.run_events import (
                    AgentRunEvent,
                    RunEventType,
                    NotificationPayload,
                )

                # Extract user_id from session_id (format: ses-{user_id}-{timestamp})
                user_id = event.session_id.split("-")[1] if "-" in event.session_id else ""
                if user_id:
                    notification_event = AgentRunEvent(
                        event_type=RunEventType.NOTIFICATION,
                        session_id=event.session_id,
                        payload=NotificationPayload(
                            level="info",
                            message=f"Sub-agent '{event.agent_id}' completed task {event.task_id}",
                            details={
                                "task_id": event.task_id,
                                "agent_id": event.agent_id,
                                "status": event.status or "COMPLETED",
                                "duration_ms": event.duration_ms,
                                "batch_id": event.batch_id,
                            },
                        ),
                    )
                    await self._event_publisher.publish(user_id, notification_event)
            except Exception as exc:
                logger.debug("AgentEventConsumer: failed to emit SSE notification: %s", exc)

        logger.info(
            "AgentEventConsumer: sub-agent %s completed task %s (status=%s)",
            event.agent_id,
            event.task_id,
            event.status,
        )

    async def _handle_agent_blocked(self, event: AgentUpdateEvent) -> None:
        """Handle sub-agent BLOCKED event: escalate via EscalationService."""
        if self._health_monitor is not None:
            self._health_monitor.remove_agent(event.agent_id)

        try:
            repo = self._graph_repo()
            if repo and event.task_id:
                await repo.update_node(event.task_id, {"state": "BLOCKED"})
        except Exception as exc:
            logger.warning("AgentEventConsumer: task BLOCKED update failed: %s", exc)

        logger.warning(
            "AgentEventConsumer: sub-agent %s BLOCKED on task %s — reason: %s",
            event.agent_id,
            event.task_id,
            event.message,
        )

    async def _process_raw_inbound(self, inbound: Any) -> None:
        """Process an inbound message: resolve task, update intelligence, write inbox, optionally reply."""
        user_id = self._default_user_id
        agent_id = self._agent_id()

        # 1. Run InboundProcessor — resolves task, extracts signal
        from graphclaw.inbound.extractor import StatusExtractor  # noqa: PLC0415
        from graphclaw.inbound.processor import InboundProcessor  # noqa: PLC0415
        from graphclaw.inbound.resolver import TaskResolver  # noqa: PLC0415

        resolver = TaskResolver(graph_repo=self._graph_repo())
        extractor = StatusExtractor()
        processor = InboundProcessor(resolver, extractor, broker=self._broker)
        try:
            result = await processor.process(
                message_id=inbound.message_id,
                session_id=inbound.session_id,
                subject=inbound.subject or "",
                body=inbound.body or "",
                channel=inbound.channel,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("AgentEventConsumer: InboundProcessor failed: %s", exc)
            result = None

        # 2. Run InboundIntelligenceAgent — classify, summarize, route
        # Inbound messages are the comms agent's domain; write context to "comms"
        # not the main orchestrator so the Intelligence Hub displays correctly.
        if self._intelligence_agent is not None and result is not None:
            try:
                await self._intelligence_agent.process(
                    inbound=inbound,
                    resolution=result,
                    agent_id="comms",
                    user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("AgentEventConsumer: IntelligenceAgent failed: %s", exc)

        # 3. Write to MinIO inbox (archive + recent)
        await self._write_inbox_entries(inbound, result, user_id, agent_id)

        # 4. Handle unmatched — Betty asks user
        task_id = result.resolution.task_id if (result and result.resolution) else None
        if task_id is None and self._dispatcher is not None:
            await self._notify_user_unmatched(inbound, user_id, result)

        # 5. Use AgentLoop.process_chat_message to compose a reply
        try:
            reply_text = await self._loop.process_chat_message(
                user_id=user_id,
                text=inbound.body or "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentEventConsumer: chat processing failed for inbound: %s", exc)
            reply_text = None

        if reply_text:
            # Route reply back via same channel the message came in on
            channel_name = inbound.channel or "email"
            reply_subject = f"Re: {inbound.subject or 'Your message'}"
            if channel_name == "email":
                await self._dispatcher.send_email(
                    to=inbound.sender,
                    subject=reply_subject,
                    body=reply_text,
                )
                # Log outbound intelligence
                if task_id:
                    await self._append_outbound_intelligence(
                        task_id=task_id,
                        channel="email",
                        recipient=inbound.sender,
                        subject=reply_subject,
                    )
                    await self._store_checkin(
                        original_msg_id=inbound.message_id,
                        task_id=task_id,
                        outbound_body=reply_text,
                        channel="email",
                        recipient=inbound.sender,
                    )
            elif channel_name == "telegram":
                chat_id = (inbound.raw_headers or {}).get("tg_chat_id", "")
                if chat_id:
                    await self._dispatcher.send_telegram(chat_id=str(chat_id), text=reply_text)
                    # Log outbound intelligence
                    if task_id:
                        await self._append_outbound_intelligence(
                            task_id=task_id,
                            channel="telegram",
                            recipient=chat_id,
                            subject="(telegram message)",
                        )
                        await self._store_checkin(
                            original_msg_id=inbound.message_id,
                            task_id=task_id,
                            outbound_body=reply_text,
                            channel="telegram",
                            recipient=chat_id,
                        )

        # 6. Log the event
        logger.info(
            "AgentEventConsumer: inbound processed channel=%s task_id=%s signal=%s",
            inbound.channel,
            task_id,
            result.status.signal if (result and result.status) else "unknown",
        )

    # ------------------------------------------------------------------
    # Outbound intelligence logging (WS-P45-G)
    # ------------------------------------------------------------------

    async def _append_outbound_intelligence(
        self,
        task_id: str,
        channel: str,
        recipient: str,
        subject: str,
    ) -> None:
        """Append an outbound log entry to the task node's intelligence field."""
        if not task_id:
            return
        repo = self._graph_repo()
        if repo is None:
            return
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            log_line = f'[{today}] {channel} | outbound | Sent "{subject[:60]}" to {recipient}'
            existing = await repo.get_node_intelligence(task_id)
            new_text = log_line + "\n" + (existing or "")
            # Trim to 500 words
            words = new_text.split()
            if len(words) > 500:
                trimmed = " ".join(words[:500])
                new_text = trimmed + f"\n... {len(words) - 500} words archived"
            await repo.update_node_intelligence(task_id, new_text)
        except Exception as exc:  # noqa: BLE001
            logger.error("AgentEventConsumer: failed to append outbound intelligence: %s", exc)

    async def _store_checkin(
        self,
        original_msg_id: str,
        task_id: str,
        outbound_body: str,
        channel: str,
        recipient: str,
    ) -> None:
        """Create CheckinNode in graph and store Redis key for reply matching."""
        repo = self._graph_repo()
        agent_id = self._agent_id()
        if repo is None:
            return
        try:
            checkin_id = await repo.create_checkin_node(
                task_id=task_id,
                outbound_message=outbound_body,
                channel=channel,
                agent_id=agent_id,
                recipient=recipient,
            )
            # Store Redis key: checkin:{original_msg_id} → "{checkin_id}:{task_id}" TTL 7 days
            if self._broker is not None:
                key = f"checkin:{original_msg_id}"
                value = json.dumps({"checkin_id": checkin_id, "task_id": task_id})
                try:
                    if hasattr(self._broker, "set"):
                        await self._broker.set(key, value, ex=604800)  # type: ignore[attr-defined]
                    else:
                        logger.debug(
                            "AgentEventConsumer: broker has no set() — checkin not stored in Redis"
                        )
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.error("AgentEventConsumer: failed to create checkin node: %s", exc)

    # ------------------------------------------------------------------
    # Two-track inbox write (WS-P45-H)
    # ------------------------------------------------------------------

    async def _write_inbox_entries(
        self,
        inbound: Any,
        result: Any,
        user_id: str,
        agent_id: str,
    ) -> None:
        """Write archive (full) and recent (compact) inbox entries to MinIO."""
        if self._storage is None:
            return

        from graphclaw.infra.storage import StoragePaths  # noqa: PLC0415

        task_id = result.resolution.task_id if (result and result.resolution) else None
        signal = result.status.signal if (result and result.status) else None

        ts = (
            inbound.received_at.strftime("%Y%m%dT%H%M%SZ")
            if inbound.received_at
            else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        entry_name = f"{ts}-{inbound.message_id[:16]}.json"

        archive_path = StoragePaths.agent_inbox_archive(user_id, agent_id, entry_name)
        recent_path = StoragePaths.agent_inbox_recent(user_id, agent_id, entry_name)

        # Archive — full original
        archive_data = {
            "message_id": inbound.message_id,
            "sender": inbound.sender,
            "subject": inbound.subject,
            "body": inbound.body,
            "channel": inbound.channel,
            "received_at": inbound.received_at.isoformat() if inbound.received_at else None,
            "raw_headers": inbound.raw_headers,
            "task_id_matched": task_id,
            "signal": signal,
        }

        # Recent — compact
        body_summary = (inbound.body or "")[:150]
        recent_data = {
            "message_id": inbound.message_id,
            "sender": inbound.sender,
            "subject": inbound.subject,
            "body_summary": body_summary,
            "channel": inbound.channel,
            "received_at": inbound.received_at.isoformat() if inbound.received_at else None,
            "task_id_matched": task_id,
            "signal": signal,
            "archive_ref": archive_path,
        }

        try:
            await self._storage.write(
                archive_path, json.dumps(archive_data).encode(), content_type="application/json"
            )
            await self._storage.write(
                recent_path, json.dumps(recent_data).encode(), content_type="application/json"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentEventConsumer: failed to write inbox entries: %s", exc)

    async def _notify_user_unmatched(self, inbound: Any, user_id: str, result: Any | None) -> None:
        """For unmatched inbound, ask the user to manually resolve task mapping."""
        # Brief body summary — no LLM, just truncation
        summary = (inbound.body or "")[:200].strip()
        if not summary:
            return

        resolution = result.resolution if (result and getattr(result, "resolution", None)) else None
        unavailable_reason = (
            resolution.match_unavailable_reason
            if (resolution and hasattr(resolution, "match_unavailable_reason"))
            else None
        )
        candidate_lines: list[str] = []
        if resolution and hasattr(resolution, "candidate_nodes"):
            for candidate in (resolution.candidate_nodes or [])[:5]:
                state_part = f" [{candidate.state}]" if candidate.state else ""
                candidate_lines.append(f"- {candidate.node_id}: {candidate.title}{state_part}")

        unavailable_text = (
            "Automatic task matching is temporarily unavailable because the embedding "
            "service is unavailable. "
            if unavailable_reason == "embedding_service_unavailable"
            else (
                "Automatic task matching could not find a confident match. "
                if unavailable_reason == "low_embedding_confidence"
                else ""
            )
        )
        candidates_text = (
            "\n\nClosest candidate tasks:\n" + "\n".join(candidate_lines) if candidate_lines else ""
        )

        message = (
            f"I received a message from {inbound.sender} via {inbound.channel} "
            f"that I couldn't match to any active task.\n\n"
            f"{unavailable_text}"
            f'It says: "{summary}{"..." if len(inbound.body or "") > 200 else ""}"\n\n'
            f"Please reply with the matching task ID (or say create new task + title)."
            f"{candidates_text}"
        )

        # Look up user's preferred channel from loop config if available
        try:
            if self._dispatcher is not None and user_id:
                # Get user channels from user_channels dict
                channels = self._user_channels.get(user_id)
                if channels:
                    await self._dispatcher.broadcast(
                        channels, subject="Unmatched message received", body=message
                    )
                else:
                    logger.info(
                        "AgentEventConsumer: unmatched inbound from %s — no channels to notify user",
                        inbound.sender,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("AgentEventConsumer: failed to notify user of unmatched inbound: %s", exc)


__all__ = ["AgentEventConsumer"]
