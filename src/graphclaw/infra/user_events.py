"""graphclaw.infra.user_events — Per-user UI event publisher abstraction.

Description
-----------
Provides ``UserEventPublisher``, an ABC for delivering ``AgentRunEvent``
objects to browser clients in real time.  The concrete backends are:

- ``RedisUserEventPublisher`` — publishes to the ``graphclaw:events:{user_id}``
  Redis pub/sub channel, picked up by the existing SSE endpoint in
  ``graphclaw.api.events``.
- ``InMemoryUserEventPublisher`` — accumulates events in a plain Python list;
  used by integration tests that need to assert on emitted events without a
  live Redis instance.
- ``NullUserEventPublisher`` — silent no-op for environments without Redis or
  where transparency events are not required.

Design Patterns
---------------
- Abstract Base Class: ``UserEventPublisher`` defines a two-method contract;
  all callers depend only on the ABC.
- Strategy: The concrete backend is chosen at startup (in
  ``graphclaw.gateway.app``) and injected via ``AgentLoop``'s constructor so
  the loop itself never imports Redis.

Public API
----------
- UserEventPublisher: ABC — ``publish(user_id, event)``, ``close()``.
- RedisUserEventPublisher: Redis pub/sub backend.
- InMemoryUserEventPublisher: In-process list backend for testing.
- NullUserEventPublisher: No-op backend.

Dependencies
------------
- graphclaw.agent.run_events: AgentRunEvent.
- json: event serialisation (stdlib).
- abc: ABC, abstractmethod (stdlib).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from graphclaw.agent.run_events import AgentRunEvent

logger = logging.getLogger(__name__)

_REDIS_CHANNEL_PREFIX = "graphclaw:events:"


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class UserEventPublisher(ABC):
    """Deliver agent run-trace events to the browser client of a specific user.

    Implementations must be safe to call from multiple async coroutines
    concurrently (per the contract of AsyncIO, not thread-safe).
    """

    @abstractmethod
    async def publish(self, user_id: str, event: AgentRunEvent) -> None:
        """Publish a single event for ``user_id``.

        Implementations must not raise on transient delivery failures —
        they should log and swallow so the agent run is never blocked by
        an event delivery error.

        Parameters
        ----------
        user_id:
            Target user; events are scoped per-user.
        event:
            The structured run-trace event to deliver.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the publisher (connections, pools)."""


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------


class RedisUserEventPublisher(UserEventPublisher):
    """Publish ``AgentRunEvent`` objects to Redis pub/sub.

    The channel name ``graphclaw:events:{user_id}`` matches the subscription
    used by the SSE endpoint in ``graphclaw.api.events`` so the cockpit
    browser client receives the events via the existing long-poll stream.

    Parameters
    ----------
    redis:
        An initialised ``redis.asyncio.Redis`` client instance.  The caller
        is responsible for its lifecycle; ``close()`` does *not* close the
        underlying Redis connection.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def publish(self, user_id: str, event: AgentRunEvent) -> None:
        channel = f"{_REDIS_CHANNEL_PREFIX}{user_id}"
        try:
            payload = json.dumps(
                {
                    "event": event.event_type,
                    "data": event.model_dump(mode="json"),
                },
                default=str,
            )
            await self._redis.publish(channel, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "user_events: publish failed for user_id=%s event_type=%s: %s",
                user_id,
                event.event_type,
                exc,
            )

    async def close(self) -> None:
        # Redis client lifecycle managed by the caller (app lifespan)
        pass


# ---------------------------------------------------------------------------
# In-memory backend (for integration tests)
# ---------------------------------------------------------------------------


class InMemoryUserEventPublisher(UserEventPublisher):
    """Accumulate published events in a Python list.

    Useful for integration tests that need to assert on the exact sequence
    of events emitted during a run without a live Redis instance.

    Attributes
    ----------
    events:
        Ordered list of ``AgentRunEvent`` objects in publication order,
        across all user_ids.  Use ``events_for(user_id)`` for filtering.
    """

    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def events_for(self, user_id: str) -> list[AgentRunEvent]:
        """Return only events published for ``user_id``."""
        return [e for e in self.events if e.user_id == user_id]

    async def publish(self, user_id: str, event: AgentRunEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Null backend
# ---------------------------------------------------------------------------


class NullUserEventPublisher(UserEventPublisher):
    """No-op publisher for environments without Redis or when events are disabled."""

    async def publish(self, user_id: str, event: AgentRunEvent) -> None:
        pass

    async def close(self) -> None:
        pass
