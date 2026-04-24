"""graphclaw.inbound.processor — Main inbound message processing pipeline.

Description
-----------
``InboundProcessor`` orchestrates the full inbound update protocol: it
delegates resolution to ``TaskResolver``, delegates signal extraction to
``StatusExtractor``, determines what action to take based on the combined
result, optionally publishes a state-update event to the message broker,
and returns a fully populated ``InboundResult``.

Design Patterns
---------------
- Facade / Orchestrator: ``InboundProcessor`` wires together the resolver,
  extractor, broker, and logger sub-components. Callers interact only with
  ``process()`` and never need to coordinate those sub-components manually.
- Dependency Injection: All collaborators are supplied at construction time,
  enabling easy testing with mocks and flexible production configuration.
- Null Object: ``broker`` and ``logger`` are optional; their absence is handled
  gracefully with ``if`` guards, so the processor degrades without errors when
  running in a context without infrastructure dependencies.

Public API
----------
- InboundProcessor: Processes inbound messages through the full pipeline.
- InboundProcessor.process: Async entry point returning an InboundResult.

Dependencies
------------
- json: Serialise broker payloads to JSON strings.
- graphclaw.inbound.extractor: StatusExtractor.
- graphclaw.inbound.models: InboundResult, StatusSignal.
- graphclaw.inbound.resolver: TaskResolver.
- graphclaw.infra.broker: MessageBroker, STATUS_UPDATES.
- graphclaw.infra.logger: AsyncLogger.

Notes
-----
The broker message published on status updates follows the schema:
``{"task_id": str, "new_state": str, "signal": str, "confidence": str,
   "session_id": str}``.
Consumers on the STATUS_UPDATES queue are expected to handle state transitions
and any downstream cascade logic.
"""

from __future__ import annotations

import json

from graphclaw.inbound.extractor import StatusExtractor
from graphclaw.inbound.models import InboundResult, StatusSignal
from graphclaw.inbound.resolver import TaskResolver
from graphclaw.infra.broker import STATUS_UPDATES, MessageBroker
from graphclaw.infra.logger import AsyncLogger


class InboundProcessor:
    """Processes inbound messages through the full resolution + extraction pipeline.

    Workflow:
    1. Resolve the message to a graph task (via ``TaskResolver``).
    2. Extract the status signal from the message body (via ``StatusExtractor``).
    3. Determine action: publish state update, flag for follow-up, or no-op.
    4. Log the outcome if a logger is provided.
    5. Return a fully populated ``InboundResult``.

    Args:
        resolver:
            ``TaskResolver`` instance for mapping messages to tasks.
        extractor:
            ``StatusExtractor`` instance for deriving status signals.
        broker:
            Optional ``MessageBroker`` for publishing state-update events.
            When ``None``, state updates are determined but not dispatched.
        logger:
            Optional ``AsyncLogger`` for structured logging. When ``None``,
            logging is skipped silently.
    """

    def __init__(
        self,
        resolver: TaskResolver,
        extractor: StatusExtractor,
        broker: MessageBroker | None = None,
        logger: AsyncLogger | None = None,
    ) -> None:
        self._resolver = resolver
        self._extractor = extractor
        self._broker = broker
        self._logger = logger

    async def process(
        self,
        message_id: str,
        session_id: str,
        subject: str,
        body: str,
        channel: str,
        user_id: str | None = None,
    ) -> InboundResult:
        """Process a single inbound message through the full pipeline.

        Args:
            message_id: Unique identifier of the inbound message.
            session_id: Distributed tracing session ID (``SES-{uuid4}``).
            subject: Message subject line or title.
            body: Plain-text message body.
            channel: Originating channel (e.g. ``"email"``, ``"api"``).
            user_id: Optional user id used to scope candidate-node fallback.

        Returns:
            An ``InboundResult`` recording the resolution, extracted status,
            action taken, and whether human follow-up is needed.
        """
        # Step 1: Resolve to task.
        resolution = await self._resolver.resolve(body, subject, user_id=user_id)

        # Step 2: Extract status signal.
        status = self._extractor.extract(body)

        # Step 3: Determine and execute action.
        action = "no_action"
        followup_needed = False

        if resolution.task_id and status.signal != StatusSignal.UNKNOWN:
            if status.suggested_state is not None:
                if self._broker is not None:
                    update_payload = {
                        "task_id": resolution.task_id,
                        "new_state": status.suggested_state.value,
                        "signal": status.signal.value,
                        "confidence": status.confidence.value,
                        "session_id": session_id,
                    }
                    await self._broker.publish(STATUS_UPDATES, json.dumps(update_payload))
                action = "state_update_published"

            if status.signal in (StatusSignal.BLOCKED, StatusSignal.NEEDS_HELP):
                followup_needed = True

        elif not resolution.task_id:
            action = (
                "manual_match_required"
                if resolution.match_unavailable_reason is not None
                else "unmatched"
            )
            followup_needed = True  # Human routing required.

        # Step 4: Log the outcome.
        if self._logger is not None:
            self._logger.log(
                "INFO",
                "inbound.processed",
                session_id,
                message_id=message_id,
                task_id=resolution.task_id or "none",
                signal=status.signal.value,
                action=action,
                channel=channel,
            )

        # Step 5: Return the complete result.
        return InboundResult(
            message_id=message_id,
            session_id=session_id,
            resolution=resolution,
            status=status,
            action_taken=action,
            followup_needed=followup_needed,
        )
