"""graphclaw.triggers.engine — TriggerEngine: scheduled and event-driven trigger orchestration.

Description
-----------
``TriggerEngine`` is the main entry point for the trigger subsystem.  It runs two
concurrent async loops:

1. **Scheduled trigger loop** — wakes every 60 seconds, queries the
   ``TriggerScheduler`` for due triggers, converts each to a ``TriggerEvent``,
   and publishes it to the ``TRIGGER_EVENTS`` queue via the ``MessageBroker``.

2. **Event consumer loop** — consumes raw messages from the ``INBOUND_MESSAGES``
   queue and converts each to an ``INBOUND`` ``TriggerEvent`` for downstream
   processing by the agent loop.

Both loops run as ``asyncio.Task`` objects managed by ``start()`` / ``stop()``.

Design Patterns
---------------
- Background Tasks: Both loops are launched as fire-and-forget asyncio Tasks so
  the engine is non-blocking from the caller's perspective.
- Idempotency Guard: An in-memory LRU-bounded set deduplicates events that carry
  an ``idempotency_key``, preventing double-firing of daily cron triggers across
  restarts.
- Dependency Injection: ``MessageBroker`` and ``TriggerScheduler`` are injected
  at construction time, keeping the engine testable with mock collaborators.

Public API
----------
- TriggerEngine.__init__: Construct with a MessageBroker and TriggerScheduler.
- TriggerEngine.start: Launch background loops.
- TriggerEngine.stop: Gracefully cancel and await background loops.
- TriggerEngine.fire_on_demand: Dispatch an ON_DEMAND trigger immediately.

Dependencies
------------
- asyncio: Task management and sleep.
- uuid: Trigger event ID generation.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES, TRIGGER_EVENTS.
- graphclaw.models.base: utcnow.
- graphclaw.triggers.models: TriggerEvent, TriggerType.
- graphclaw.triggers.scheduler: TriggerScheduler.

Notes
-----
The ``_seen_keys`` set is bounded at 10 000 entries.  When the limit is exceeded,
the oldest 5 000 entries (by insertion order, approximated by list conversion)
are removed.  In production this should be replaced with a proper LRU cache or
a Redis SET with TTL.
"""

from __future__ import annotations

import asyncio
import uuid

from graphclaw.infra.broker import INBOUND_MESSAGES, TRIGGER_EVENTS, MessageBroker
from graphclaw.models.base import utcnow
from graphclaw.triggers.models import TriggerEvent, TriggerType
from graphclaw.triggers.scheduler import TriggerScheduler

_SCHEDULED_LOOP_INTERVAL = 60  # seconds
_SEEN_KEYS_MAX = 10_000
_SEEN_KEYS_TRIM_TO = 5_000


class TriggerEngine:
    """Main trigger engine: runs scheduled trigger loop and event consumer loop.

    Usage::

        engine = TriggerEngine(broker=broker, scheduler=scheduler)
        await engine.start()
        # … runtime …
        await engine.stop()
    """

    def __init__(self, broker: MessageBroker, scheduler: TriggerScheduler) -> None:
        self._broker = broker
        self._scheduler = scheduler
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._seen_keys: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start both loops as background asyncio Tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._scheduled_trigger_loop()),
            asyncio.create_task(self._event_consumer_loop()),
        ]

    async def stop(self) -> None:
        """Gracefully stop both loops.

        Cancels all background tasks and awaits their completion, suppressing
        ``CancelledError`` so callers receive a clean return.
        """
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------------
    # Public trigger helpers
    # ------------------------------------------------------------------

    async def fire_on_demand(
        self,
        user_id: str,
        payload: dict | None = None,
    ) -> TriggerEvent:
        """Dispatch an ON_DEMAND trigger immediately.

        Args:
            user_id: The user on whose behalf the trigger is fired.
            payload: Optional additional data included in the event payload.

        Returns:
            The ``TriggerEvent`` that was published.
        """
        event = TriggerEvent(
            trigger_id=f"TRIG-{uuid.uuid4()}",
            trigger_type=TriggerType.ON_DEMAND,
            user_id=user_id,
            payload=payload or {},
            created_at=utcnow(),
        )
        await self._dispatch(event)
        return event

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    async def _scheduled_trigger_loop(self) -> None:
        """Check for due triggers every 60 seconds and dispatch events."""
        while self._running:
            now = utcnow()
            due = self._scheduler.get_due_triggers(now)
            for config in due:
                event = TriggerEvent(
                    trigger_id=f"TRIG-{uuid.uuid4()}",
                    trigger_type=config.trigger_type,
                    user_id=config.user_id,
                    payload=config.payload_template,
                    created_at=now,
                    idempotency_key=f"{config.trigger_id}:{now.date()}",
                )
                await self._dispatch(event)
                self._scheduler.advance(config.trigger_id, now)
            await asyncio.sleep(_SCHEDULED_LOOP_INTERVAL)

    async def _event_consumer_loop(self) -> None:
        """Consume raw inbound messages and convert them to INBOUND trigger events."""
        async for message in self._broker.consume(INBOUND_MESSAGES):
            event = TriggerEvent(
                trigger_id=f"TRIG-{uuid.uuid4()}",
                trigger_type=TriggerType.INBOUND,
                user_id="",  # Resolved downstream by the agent loop
                payload={"raw_message": message},
                created_at=utcnow(),
            )
            await self._dispatch(event)

    # ------------------------------------------------------------------
    # Dispatch with idempotency
    # ------------------------------------------------------------------

    async def _dispatch(self, event: TriggerEvent) -> bool:
        """Publish a trigger event to the TRIGGER_EVENTS queue.

        Skips events whose ``idempotency_key`` has already been seen in this
        session.  After publishing, trims the ``_seen_keys`` set if it exceeds
        the maximum size.

        Args:
            event: The TriggerEvent to publish.

        Returns:
            ``True`` if the event was published; ``False`` if it was deduplicated.
        """
        if event.idempotency_key and event.idempotency_key in self._seen_keys:
            return False

        if event.idempotency_key:
            self._seen_keys.add(event.idempotency_key)
            if len(self._seen_keys) > _SEEN_KEYS_MAX:
                # Remove the oldest half (approximation via list conversion).
                to_remove = list(self._seen_keys)[:_SEEN_KEYS_TRIM_TO]
                for k in to_remove:
                    self._seen_keys.discard(k)

        await self._broker.publish(TRIGGER_EVENTS, event.model_dump_json())
        return True
