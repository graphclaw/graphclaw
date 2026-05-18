# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.catalog — AgentCatalog: agent discovery from MinIO manifests.

Description
-----------
Provides ``AgentCatalog``, which reads ``manifest.json`` files from both the
system agent directory (``system/agents/``) and user-specific agent directories
(``{user_id}/agents/``).  It exposes:

- A compact one-line-per-agent catalog string for injection into the system prompt.
- Full manifest detail for the ``list_available_agents`` tool response.
- A lookup helper used by ``_tool_delegate_to_agent()`` to resolve whether an
  agent is system-level or user-level.

Agent Manifest Schema (manifest.json)
--------------------------------------
{
  "agent_id":    "comms",
  "name":        "Communications Agent",
  "type":        "system" | "user",
  "description": "…",
  "capabilities": ["email_read", "telegram_read"],
  "invocation":  "async" | "sync",
  "tool_hint":   "…"     // shown in compact catalog
}

Caching Design
--------------
Two-tier caching is used to eliminate redundant MinIO reads on every agent cycle.

Tier 1 — in-process TTL (system manifests):
  System agents are seeded by admins and only change on deployment.  The full
  list is cached on the ``AgentCatalog`` instance with a 30-minute TTL using
  ``time.monotonic()``.  No external dependency.  Lost on restart (acceptable).

Tier 2 — Redis (user manifests):
  User agents are scoped per-user and can be created or deleted between sessions.
  Cached at key ``graphclaw:catalog:manifests:{user_id}`` with a 10-minute TTL.
  Gracefully degrades to a live MinIO read when Redis is unavailable.
  Call ``invalidate_user_catalog(user_id)`` from create/delete agent endpoints
  to evict the key immediately on mutation.

Public API
----------
- AgentCatalog: Discovers and caches agent manifests.
- AgentCatalog.get_compact_catalog: Compact string for the system prompt.
- AgentCatalog.list_all: Full manifest list, optionally filtered by capability.
- AgentCatalog.resolve_source: Return "system" | "user" for a given agent_id.
- AgentCatalog.invalidate_user_catalog: Evict Redis cache for a user's agents.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, StoragePaths.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache constants
# ---------------------------------------------------------------------------

# System manifests — in-process TTL (30 min).  Only changes on deployment.
_SYSTEM_MANIFESTS_TTL: float = 1800.0

# User manifests — Redis TTL (10 min).  Can change between sessions.
_USER_CATALOG_TTL: int = 600
_USER_CATALOG_KEY_PREFIX = "graphclaw:catalog:manifests:"


class AgentCatalog:
    """Discovers system and user agents from MinIO manifest files.

    Parameters
    ----------
    storage_client:
        Storage backend for reading manifest JSON files.
    redis_client:
        Optional async Redis client.  When provided, user-agent manifests are
        cached in Redis with a 10-minute TTL.  When ``None``, user manifests
        are read from MinIO on every call (graceful degradation).
    """

    def __init__(
        self,
        storage_client: StorageClient,
        redis_client: Any | None = None,
    ) -> None:
        self._storage = storage_client
        self._redis = redis_client

        # Tier 1: in-process TTL cache for system manifests
        self._system_manifests: list[dict[str, Any]] | None = None
        self._system_manifests_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_compact_catalog(self, user_id: str) -> str:
        """Return a compact catalog string (~100 tokens) for the system prompt.

        Format::

            ## Available Agents
            - comms [system]: Reads email, Telegram, WhatsApp — delegate for comms queries
            - my-research [user]: Searches the web and summarises findings
            To delegate: load_tool_set("delegation"), then call delegate_to_agent
        """
        manifests = await self._load_all_manifests(user_id)
        if not manifests:
            return ""

        lines = ["## Available Agents"]
        for m in manifests:
            agent_id = m.get("agent_id", "?")
            agent_type = m.get("type", "user")
            hint = m.get("tool_hint") or m.get("description", "")
            lines.append(f"- {agent_id} [{agent_type}]: {hint}")
        lines.append('To delegate: load_tool_set("delegation"), then call delegate_to_agent')
        return "\n".join(lines)

    async def list_all(
        self,
        user_id: str,
        capability_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return full manifest list for all agents visible to *user_id*.

        Parameters
        ----------
        user_id:
            The requesting user's ID.
        capability_filter:
            Optional capability string — only return agents whose ``capabilities``
            list contains this value.
        """
        manifests = await self._load_all_manifests(user_id)
        if capability_filter:
            manifests = [m for m in manifests if capability_filter in m.get("capabilities", [])]
        return manifests

    async def resolve_source(self, user_id: str, agent_id: str) -> str:  # noqa: ARG002
        """Return ``"system"`` if *agent_id* is a system agent, else ``"user"``.

        Checks the cached system manifest list first to avoid a redundant
        MinIO read.  Falls back to a direct storage probe on cache miss.
        """
        # Fast path: check in-process system manifest cache
        if self._system_manifests is not None:
            ids = {m.get("agent_id") for m in self._system_manifests}
            return "system" if agent_id in ids else "user"

        # Slow path: direct storage probe (populates cache as a side effect)
        system_path = StoragePaths.system_agent_manifest(agent_id)
        try:
            await self._storage.read(system_path)
            return "system"
        except FileNotFoundError:
            return "user"
        except Exception as exc:
            logger.warning(
                "catalog.resolve_source.error",
                extra={"agent_id": agent_id, "error": str(exc)},
            )
            return "user"

    async def invalidate_user_catalog(self, user_id: str) -> None:
        """Evict the Redis cache entry for *user_id*'s agent manifests.

        Call this from the create-agent and delete-agent API endpoints so that
        the next chat turn picks up the updated manifest list immediately.
        Does nothing when Redis is unavailable.
        """
        if self._redis is None:
            return
        key = f"{_USER_CATALOG_KEY_PREFIX}{user_id}"
        try:
            await self._redis.delete(key)
            logger.debug("catalog.invalidate_user_catalog", extra={"user_id": user_id})
        except Exception as exc:
            logger.warning(
                "catalog.invalidate_user_catalog.error",
                extra={"user_id": user_id, "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_all_manifests(self, user_id: str) -> list[dict[str, Any]]:
        """Load manifests from system/agents/ (Tier 1) and {user_id}/agents/ (Tier 2)."""
        system = await self._get_system_manifests()
        user = await self._get_user_manifests(user_id)
        return system + user

    # --- Tier 1: in-process TTL for system manifests ---

    async def _get_system_manifests(self) -> list[dict[str, Any]]:
        """Return system agent manifests, refreshing from MinIO after TTL expires."""
        now = time.monotonic()
        if (
            self._system_manifests is not None
            and now - self._system_manifests_at < _SYSTEM_MANIFESTS_TTL
        ):
            return self._system_manifests

        manifests = await self._load_manifests_from_prefix(
            StoragePaths.system_agents_prefix(),
            expected_type="system",
        )
        self._system_manifests = manifests
        self._system_manifests_at = now
        logger.debug(
            "catalog.system_manifests.refreshed",
            extra={"count": len(manifests)},
        )
        return manifests

    # --- Tier 2: Redis cache for user manifests ---

    async def _get_user_manifests(self, user_id: str) -> list[dict[str, Any]]:
        """Return user agent manifests from Redis, falling back to MinIO."""
        key = f"{_USER_CATALOG_KEY_PREFIX}{user_id}"

        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning(
                    "catalog.user_manifests.redis_get_failed",
                    extra={"user_id": user_id, "error": str(exc)},
                )

        manifests = await self._load_manifests_from_prefix(
            StoragePaths.agents_prefix(user_id),
            expected_type="user",
        )

        if self._redis is not None:
            try:
                await self._redis.setex(key, _USER_CATALOG_TTL, json.dumps(manifests))
            except Exception as exc:
                logger.warning(
                    "catalog.user_manifests.redis_set_failed",
                    extra={"user_id": user_id, "error": str(exc)},
                )

        return manifests

    async def _load_manifests_from_prefix(
        self,
        prefix: str,
        expected_type: str,
    ) -> list[dict[str, Any]]:
        """Load all ``manifest.json`` files under *prefix*."""
        try:
            keys = await self._storage.list_objects(prefix)
        except Exception as exc:
            logger.warning(
                "catalog.list_failed",
                extra={"prefix": prefix, "error": str(exc)},
            )
            return []

        manifests = []
        for key in keys:
            if not key.endswith("manifest.json"):
                continue
            try:
                raw = await self._storage.read(key)
                manifest = json.loads(raw.decode())
                # Ensure type field is consistent with directory location
                manifest.setdefault("type", expected_type)
                manifests.append(manifest)
            except Exception as exc:
                logger.warning(
                    "catalog.manifest_load_failed",
                    extra={"key": key, "error": str(exc)},
                )

        return manifests


__all__ = ["AgentCatalog"]
