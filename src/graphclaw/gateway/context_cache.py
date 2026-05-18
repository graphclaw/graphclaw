# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.context_cache — Redis-backed conversation context cache.

Description
-----------
Stores recent message history per session so that follow-up messages in the
same conversation carry relevant context into the agent reasoning loop.

Each session key maps to a capped list of serialised message dicts. The list
is stored as a Redis list with a TTL (default 24 h) so idle sessions expire
automatically.

Design Patterns
---------------
- Facade: Hides Redis list operations behind a simple append/fetch interface.
- Graceful degradation: All methods silently no-op when Redis is unavailable
  (``_redis`` is ``None``), so the gateway continues functioning without context.

Public API
----------
- ConversationContextCache: Main cache class.
  - ``append(session_id, message_dict, ttl_seconds)`` — Add a message to session history.
  - ``get_context(session_id)`` — Retrieve recent messages for a session.
  - ``clear(session_id)`` — Delete all messages for a session.
  - ``from_env()`` classmethod — Build from REDIS_URL env var.

Dependencies
------------
- redis.asyncio: Async Redis client (install: redis[hiredis]).
- json: stdlib serialisation.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Key prefix to namespace all context cache keys in Redis
_KEY_PREFIX = "graphclaw:ctx:"

# Default number of messages to retain per session
_DEFAULT_MAX_MESSAGES = 20

# Default TTL (24 hours in seconds)
_DEFAULT_TTL_SECONDS = 86_400


class ConversationContextCache:
    """Redis-backed cache for per-session conversation history."""

    def __init__(self, redis_client: Any | None = None) -> None:
        """
        Args:
            redis_client: An async Redis client (``redis.asyncio.Redis``).
                If ``None``, the cache operates in no-op mode.
        """
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def from_env(cls) -> ConversationContextCache:
        """Create a cache from the ``REDIS_URL`` environment variable.

        Returns a no-op instance (``redis_client=None``) if the env var is
        absent or if the ``redis`` package is not installed.
        """
        url = os.environ.get("REDIS_URL", "")
        if not url:
            logger.info("ConversationContextCache: REDIS_URL not set, running in no-op mode")
            return cls(redis_client=None)
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            client = aioredis.from_url(url, decode_responses=True)
            logger.info("ConversationContextCache: connected to Redis at %s", url)
            return cls(redis_client=client)
        except ImportError:
            logger.warning(
                "ConversationContextCache: redis package not installed, running in no-op mode"
            )
            return cls(redis_client=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConversationContextCache: Redis connection failed: %s", exc)
            return cls(redis_client=None)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def append(
        self,
        session_id: str,
        message: dict[str, Any],
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """Append a message dict to the session's context list.

        Trims the list to ``max_messages`` (oldest messages are discarded)
        and resets the TTL on every append.

        Args:
            session_id: Unique session identifier (e.g. ``"SES-uuid4"``).
            message: Serialisable dict representing the message (e.g. InboundMessage.model_dump()).
            max_messages: Maximum number of messages to retain.
            ttl_seconds: Seconds until the key expires if not updated.
        """
        if self._redis is None:
            return
        try:
            key = f"{_KEY_PREFIX}{session_id}"
            serialised = json.dumps(message, default=str)
            pipe = self._redis.pipeline()
            pipe.rpush(key, serialised)
            pipe.ltrim(key, -max_messages, -1)
            pipe.expire(key, ttl_seconds)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConversationContextCache.append failed: %s", exc)

    async def get_context(
        self,
        session_id: str,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
    ) -> list[dict[str, Any]]:
        """Retrieve recent messages for a session.

        Args:
            session_id: Unique session identifier.
            max_messages: Maximum number of messages to return (most recent).

        Returns:
            List of message dicts, oldest first. Empty list on cache miss or error.
        """
        if self._redis is None:
            return []
        try:
            key = f"{_KEY_PREFIX}{session_id}"
            raw_items = await self._redis.lrange(key, -max_messages, -1)
            return [json.loads(item) for item in raw_items]
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConversationContextCache.get_context failed: %s", exc)
            return []

    async def clear(self, session_id: str) -> None:
        """Delete all context for a session.

        Args:
            session_id: Unique session identifier.
        """
        if self._redis is None:
            return
        try:
            key = f"{_KEY_PREFIX}{session_id}"
            await self._redis.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConversationContextCache.clear failed: %s", exc)

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ConversationContextCache.close failed: %s", exc)
