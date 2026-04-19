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

Public API
----------
- AgentCatalog: Discovers and caches agent manifests.
- AgentCatalog.get_compact_catalog: Compact string for the system prompt.
- AgentCatalog.list_all: Full manifest list, optionally filtered by capability.
- AgentCatalog.resolve_source: Return "system" | "user" for a given agent_id.

Dependencies
------------
- graphclaw.infra.storage: StorageClient, StoragePaths.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from graphclaw.infra.storage import StorageClient, StoragePaths

logger = logging.getLogger(__name__)


class AgentCatalog:
    """Discovers system and user agents from MinIO manifest files.

    Parameters
    ----------
    storage_client:
        Storage backend for reading manifest JSON files.
    """

    def __init__(self, storage_client: StorageClient) -> None:
        self._storage = storage_client

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

    async def resolve_source(self, user_id: str, agent_id: str) -> str:
        """Return ``"system"`` if *agent_id* is a system agent, else ``"user"``.

        Checks ``system/agents/{agent_id}/manifest.json`` first.
        Falls back to ``"user"`` if not found.
        """
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_all_manifests(self, user_id: str) -> list[dict[str, Any]]:
        """Load manifests from system/agents/ and {user_id}/agents/ prefixes."""
        manifests: list[dict[str, Any]] = []

        # System agents
        system_manifests = await self._load_manifests_from_prefix(
            StoragePaths.system_agents_prefix(),
            expected_type="system",
        )
        manifests.extend(system_manifests)

        # User agents
        user_manifests = await self._load_manifests_from_prefix(
            StoragePaths.agents_prefix(user_id),
            expected_type="user",
        )
        manifests.extend(user_manifests)

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

        manifests: list[dict[str, Any]] = []
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
