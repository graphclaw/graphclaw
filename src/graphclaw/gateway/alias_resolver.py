"""graphclaw.gateway.alias_resolver — Cross-channel identity alias resolver.

Description
-----------
Maps channel-specific sender identifiers (phone numbers, Telegram user IDs,
email addresses) to a canonical GraphClaw ``USER-{uuid}`` user ID.

The mapping is stored in Redis as a two-way hash:
- Forward: ``graphclaw:alias:{channel}:{sender_id}`` → ``user_id``
- Reverse: ``graphclaw:user_aliases:{user_id}`` → set of ``{channel}:{sender_id}``

This lets the agent find a user's existing tasks regardless of which channel
they're messaging from.

Design Patterns
---------------
- Facade: Hides Redis hash/set operations behind a simple resolve/register interface.
- Graceful degradation: All methods return ``None``/empty when Redis is unavailable,
  allowing the caller to fall back to creating a new session-only identity.

Public API
----------
- AliasResolver: Main resolver class.
  - ``resolve(channel, sender_id)`` — Look up user_id for a sender. Returns None if unknown.
  - ``register(channel, sender_id, user_id)`` — Create forward + reverse mapping.
  - ``get_aliases(user_id)`` — Get all channel:sender_id pairs for a user.
  - ``from_env()`` classmethod — Build from REDIS_URL env var.

Dependencies
------------
- redis.asyncio: Async Redis client (install: redis[hiredis]).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ALIAS_PREFIX = "graphclaw:alias:"
_USER_ALIASES_PREFIX = "graphclaw:user_aliases:"

# Alias mappings are permanent (no TTL) — user identities don't expire
# Reverse index (user → aliases) uses a Redis Set


class AliasResolver:
    """Cross-channel sender identity resolver backed by Redis."""

    def __init__(self, redis_client: Any | None = None) -> None:
        """
        Args:
            redis_client: An async Redis client (``redis.asyncio.Redis``).
                If ``None``, the resolver operates in no-op mode.
        """
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def from_env(cls) -> "AliasResolver":
        """Create a resolver from the ``REDIS_URL`` environment variable.

        Returns a no-op instance when Redis is unavailable.
        """
        url = os.environ.get("REDIS_URL", "")
        if not url:
            logger.info("AliasResolver: REDIS_URL not set, running in no-op mode")
            return cls(redis_client=None)
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            client = aioredis.from_url(url, decode_responses=True)
            logger.info("AliasResolver: connected to Redis at %s", url)
            return cls(redis_client=client)
        except ImportError:
            logger.warning("AliasResolver: redis package not installed, running in no-op mode")
            return cls(redis_client=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AliasResolver: Redis connection failed: %s", exc)
            return cls(redis_client=None)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def resolve(self, channel: str, sender_id: str) -> str | None:
        """Look up the canonical user_id for a channel-specific sender.

        Args:
            channel: Channel name (e.g. ``"whatsapp"``, ``"email"``, ``"telegram"``).
            sender_id: Channel-specific sender identifier (phone, email, Telegram ID).

        Returns:
            Canonical ``USER-{uuid}`` user ID, or ``None`` if not registered.
        """
        if self._redis is None:
            return None
        try:
            key = f"{_ALIAS_PREFIX}{channel}:{sender_id}"
            return await self._redis.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AliasResolver.resolve failed: %s", exc)
            return None

    async def register(
        self,
        channel: str,
        sender_id: str,
        user_id: str,
    ) -> None:
        """Create a mapping between a channel sender and a canonical user_id.

        Both forward (sender → user) and reverse (user → senders) mappings
        are stored atomically in a pipeline.

        Args:
            channel: Channel name (e.g. ``"whatsapp"``).
            sender_id: Channel-specific sender identifier.
            user_id: Canonical GraphClaw ``USER-{uuid}`` user ID.
        """
        if self._redis is None:
            return
        try:
            forward_key = f"{_ALIAS_PREFIX}{channel}:{sender_id}"
            reverse_key = f"{_USER_ALIASES_PREFIX}{user_id}"
            alias_member = f"{channel}:{sender_id}"

            pipe = self._redis.pipeline()
            pipe.set(forward_key, user_id)
            pipe.sadd(reverse_key, alias_member)
            await pipe.execute()
            logger.debug(
                "AliasResolver: registered %s:%s → %s", channel, sender_id, user_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AliasResolver.register failed: %s", exc)

    async def get_aliases(self, user_id: str) -> list[str]:
        """Return all ``{channel}:{sender_id}`` aliases registered for a user.

        Args:
            user_id: Canonical GraphClaw ``USER-{uuid}`` user ID.

        Returns:
            List of alias strings (e.g. ``["whatsapp:15551234567", "email:alice@example.com"]``).
            Empty list if no aliases are registered or Redis is unavailable.
        """
        if self._redis is None:
            return []
        try:
            reverse_key = f"{_USER_ALIASES_PREFIX}{user_id}"
            members = await self._redis.smembers(reverse_key)
            return sorted(members)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AliasResolver.get_aliases failed: %s", exc)
            return []

    async def deregister(self, channel: str, sender_id: str) -> None:
        """Remove the alias mapping for a channel sender.

        Args:
            channel: Channel name.
            sender_id: Channel-specific sender identifier.
        """
        if self._redis is None:
            return
        try:
            forward_key = f"{_ALIAS_PREFIX}{channel}:{sender_id}"
            user_id = await self._redis.get(forward_key)
            if user_id:
                reverse_key = f"{_USER_ALIASES_PREFIX}{user_id}"
                pipe = self._redis.pipeline()
                pipe.delete(forward_key)
                pipe.srem(reverse_key, f"{channel}:{sender_id}")
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AliasResolver.deregister failed: %s", exc)

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("AliasResolver.close failed: %s", exc)
