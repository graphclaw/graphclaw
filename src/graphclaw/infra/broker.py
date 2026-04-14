"""graphclaw.infra.broker — MessageBroker ABC and RedisMessageBroker implementation.

Description
-----------
Defines the ``MessageBroker`` abstract interface for publishing and consuming
messages from named queues, along with a ``RedisMessageBroker`` concrete
implementation backed by ``redis.asyncio``.  The broker uses Redis list
operations (``LPUSH`` / ``BRPOP``) as a simple, reliable FIFO queue.

Queue name constants are provided as module-level strings so callers
never hardcode raw queue names.

Design Patterns
---------------
- Abstract Base Class: ``MessageBroker`` contract makes it trivial to swap
  Redis for SQS, BullMQ, or Pub/Sub without touching calling code.
- Async Generator: ``consume()`` is an ``AsyncIterator`` that yields messages
  indefinitely, using ``BRPOP`` with a timeout to avoid busy-waiting while
  remaining responsive to cancellation.

Public API
----------
- MessageBroker: ABC with publish, consume, acknowledge, close.
- RedisMessageBroker: redis.asyncio-backed implementation.
- INBOUND_MESSAGES: Queue name for inbound channel messages.
- TRIGGER_EVENTS: Queue name for task trigger events.
- SKILL_JOBS: Queue name for skill execution jobs.
- STATUS_UPDATES: Queue name for task status change events.
- OUTBOUND_MESSAGES: Queue name for outbound channel messages.
- AGENT_JOBS: Queue name for sub-agent delegation jobs.
- AGENT_UPDATES: Queue name for typed sub-agent progress/completion events.

Dependencies
------------
- abc: ABC, abstractmethod.
- asyncio: used for queue concurrency primitives.
- redis.asyncio: async Redis client (install: redis[hiredis]).
- collections.abc: AsyncIterator type.

Notes
-----
Redis does not have explicit message acknowledgement for list-based queues.
``acknowledge`` is a no-op — once ``BRPOP`` returns a message it is
considered consumed.  For at-least-once delivery guarantees, a future
implementation could use Redis Streams (XACK).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

# ---------------------------------------------------------------------------
# Queue name constants
# ---------------------------------------------------------------------------

INBOUND_MESSAGES: str = "inbound_messages"
TRIGGER_EVENTS: str = "trigger_events"
SKILL_JOBS: str = "skill_jobs"
STATUS_UPDATES: str = "status_updates"
OUTBOUND_MESSAGES: str = "outbound_messages"
# Phase 5 — Sub-agent orchestration queues
AGENT_JOBS: str = "agent_jobs"
AGENT_UPDATES: str = "agent_updates"


class MessageBroker(ABC):
    """Abstract interface for message queue backends."""

    @abstractmethod
    async def publish(self, queue: str, message: str) -> None:
        """Enqueue *message* on *queue*.

        Args:
            queue: Name of the target queue.
            message: Serialised message payload (typically JSON).
        """

    @abstractmethod
    async def consume(self, queue: str) -> AsyncIterator[str]:
        """Yield messages from *queue* indefinitely.

        Args:
            queue: Name of the source queue.

        Yields:
            Raw message payloads (strings) as they arrive.
        """

    @abstractmethod
    async def acknowledge(self, queue: str, message_id: str) -> None:
        """Acknowledge successful processing of *message_id* on *queue*.

        For backends that require explicit acknowledgement (e.g. SQS).
        No-op for Redis list-based queues.

        Args:
            queue: Name of the queue.
            message_id: Backend-specific message identifier.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying connection and release resources."""


class RedisMessageBroker(MessageBroker):
    """Message broker backed by Redis list operations.

    Uses ``LPUSH`` to publish and blocking ``BRPOP`` to consume.

    Args:
        url: Redis connection URL (default ``"redis://localhost:6379"``).
    """

    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._url = url
        self._redis: object | None = None  # lazy init

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_redis(self) -> object:
        """Return (or lazily create) the async Redis client."""
        if self._redis is None:
            import redis.asyncio as aioredis  # local import for optional dep

            self._redis = await aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    # ------------------------------------------------------------------
    # MessageBroker interface
    # ------------------------------------------------------------------

    async def publish(self, queue: str, message: str) -> None:
        """Push *message* onto the left end of Redis list *queue*."""
        r = await self._get_redis()
        await r.lpush(queue, message)

    async def consume(self, queue: str) -> AsyncIterator[str]:
        """Yield messages from *queue* using blocking BRPOP (timeout=5s).

        The generator runs forever; callers should break or cancel the
        surrounding task to stop consumption.
        """
        r = await self._get_redis()
        while True:
            result = await r.brpop(queue, timeout=5)
            if result is not None:
                # brpop returns (queue_name, value)
                _, value = result
                yield value

    async def acknowledge(self, queue: str, message_id: str) -> None:
        """No-op: Redis list consumption is implicitly acknowledged."""

    async def close(self) -> None:
        """Close the Redis connection if one was opened."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
