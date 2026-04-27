"""graphclaw.mcp.registry — MCPRegistry: per-user MCP server config in MinIO.

Description
-----------
Provides ``MCPRegistry``, a service that stores and retrieves ``MCPServerNode``
configs as JSON files in object storage (MinIO/S3).  Each server is stored at
``{user_id}/mcp/servers/{server_id}.json``, giving hard user-level isolation
at the storage prefix boundary with no graph traversal required.

Design Patterns
---------------
- Repository: ``MCPRegistry`` depends on the ``StorageClient`` ABC so any
  backend (S3, MinIO, GCS, local) can be substituted without changing call sites.
- Read-modify-write: Updates (trust tier, enabled, last_used_at) read the
  existing JSON, patch the relevant fields, and write back atomically enough
  for the low-concurrency dev/single-user case.

Public API
----------
- MCPRegistry: Manages MCPServerNode config persistence in object storage.
- MCPRegistry.register: Write a new server config.
- MCPRegistry.get: Read one server config by user + server ID.
- MCPRegistry.list_for_user: List all configs for a user.
- MCPRegistry.update_trust: Change the trust tier.
- MCPRegistry.update_last_used: Stamp last_used_at.
- MCPRegistry.enable / disable: Toggle the enabled flag.
- MCPRegistry.deregister: Delete the config file.
- MCPRegistry.find_by_scope: Filter servers by capability scope.

Dependencies
------------
- graphclaw.infra.storage: StorageClient ABC, StoragePaths.
- graphclaw.infra.secrets: SecretsClient (optional, for credential cleanup).
- graphclaw.models.base: utcnow.
- graphclaw.models.enums: TrustTier.
- graphclaw.models.nodes: MCPServerNode.
"""

from __future__ import annotations

import json
import logging

from graphclaw.infra.storage import StorageClient, StoragePaths
from graphclaw.models.base import utcnow
from graphclaw.models.enums import TrustTier
from graphclaw.models.nodes import MCPServerNode

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Manages MCPServerNode config persistence in object storage.

    Parameters
    ----------
    storage_client:
        A concrete ``StorageClient`` implementation for all read/write ops.
    secrets_client:
        Optional secrets client used to delete credentials in ``deregister()``.
    """

    def __init__(self, storage_client: StorageClient, secrets_client=None) -> None:
        self._storage = storage_client
        self._secrets = secrets_client

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    async def register(self, user_id: str, node: MCPServerNode) -> MCPServerNode:
        """Persist *node* as a JSON file under the user's MCP prefix.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` of the user registering the server.
        node:
            A fully constructed ``MCPServerNode`` ready for persistence.

        Returns
        -------
        MCPServerNode
            The registered node (same object; returned for convenience).
        """
        path = StoragePaths.mcp_server(user_id, node.id)
        data = node.model_dump(mode="json")
        await self._storage.write(
            path,
            json.dumps(data, default=str).encode(),
            content_type="application/json",
        )
        logger.info(
            "mcp.registry.register",
            extra={"user_id": user_id, "server_id": node.id, "server_name": node.name},
        )
        return node

    async def get(self, user_id: str, server_id: str) -> MCPServerNode | None:
        """Return the ``MCPServerNode`` for *server_id*, or ``None`` if not found.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` who owns the server.
        server_id:
            An ``MCP-{identifier}`` server ID.
        """
        path = StoragePaths.mcp_server(user_id, server_id)
        try:
            raw = await self._storage.read(path)
            return MCPServerNode.model_validate(json.loads(raw.decode()))
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning(
                "mcp.registry.get.failed",
                extra={"user_id": user_id, "server_id": server_id, "error": str(exc)},
            )
            return None

    async def list_for_user(self, user_id: str, enabled_only: bool = True) -> list[MCPServerNode]:
        """Return all registered MCP servers for *user_id*.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` whose servers to list.
        enabled_only:
            When ``True`` (default), only return servers where ``enabled=True``.

        Returns
        -------
        list[MCPServerNode]
            Servers registered by *user_id*, optionally filtered to enabled only.
        """
        prefix = StoragePaths.mcp_servers_prefix(user_id)
        try:
            keys = await self._storage.list_objects(prefix)
        except Exception as exc:
            logger.warning(
                "mcp.registry.list_for_user.list_failed",
                extra={"user_id": user_id, "error": str(exc)},
            )
            return []

        results: list[MCPServerNode] = []
        for key in keys:
            if not key.endswith(".json"):
                continue
            try:
                raw = await self._storage.read(key)
                server = MCPServerNode.model_validate(json.loads(raw.decode()))
            except Exception:
                logger.warning("mcp.registry.list_for_user.invalid_file", extra={"key": key})
                continue
            if enabled_only and not server.enabled:
                continue
            results.append(server)
        return results

    async def update_trust(
        self, user_id: str, server_id: str, trust_tier: TrustTier
    ) -> MCPServerNode:
        """Change the trust tier of a registered MCP server.

        Raises
        ------
        ValueError
            If the server is not found, or if promoting a BLOCKED server
            directly to AUTO (must go BLOCKED → GATED → AUTO).
        """
        existing = await self.get(user_id, server_id)
        if existing is None:
            raise ValueError(f"MCP server '{server_id}' not found for user '{user_id}'.")

        if existing.trust_tier == TrustTier.BLOCKED and trust_tier == TrustTier.AUTO:
            raise ValueError(
                f"Cannot promote BLOCKED server '{server_id}' directly to AUTO. "
                "Set trust_tier to GATED first, then promote to AUTO."
            )

        updated = existing.model_copy(update={"trust_tier": trust_tier, "updated_at": utcnow()})
        await self.register(user_id, updated)
        return updated

    async def update_last_used(self, user_id: str, server_id: str) -> None:
        """Stamp *last_used_at* on *server_id* with the current UTC time."""
        existing = await self.get(user_id, server_id)
        if existing is None:
            return
        updated = existing.model_copy(update={"last_used_at": utcnow(), "updated_at": utcnow()})
        await self.register(user_id, updated)

    async def enable(self, user_id: str, server_id: str) -> None:
        """Set ``enabled=True`` on *server_id*."""
        existing = await self.get(user_id, server_id)
        if existing is None:
            return
        await self.register(user_id, existing.model_copy(update={"enabled": True}))

    async def disable(self, user_id: str, server_id: str) -> None:
        """Set ``enabled=False`` on *server_id*."""
        existing = await self.get(user_id, server_id)
        if existing is None:
            return
        await self.register(user_id, existing.model_copy(update={"enabled": False}))

    async def deregister(self, user_id: str, server_id: str) -> None:
        """Delete the server config file and its associated credential secret.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` who owns the server.
        server_id:
            Target server ID.
        """
        server = await self.get(user_id, server_id)
        if server is None:
            logger.warning("mcp.registry.deregister.not_found", extra={"server_id": server_id})
            return

        if self._secrets is not None and server.secret_ref:
            try:
                await self._secrets.delete_secret(server.secret_ref)
            except Exception as exc:
                logger.warning(
                    "mcp.registry.deregister.secret_delete_failed",
                    extra={"server_id": server_id, "error": str(exc)},
                )

        path = StoragePaths.mcp_server(user_id, server_id)
        try:
            await self._storage.delete(path)
        except FileNotFoundError:
            pass
        logger.info("mcp.registry.deregister", extra={"user_id": user_id, "server_id": server_id})

    async def find_by_scope(self, user_id: str, scope: str) -> list[MCPServerNode]:
        """Return enabled servers for *user_id* whose scope list contains *scope*.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` whose servers to search.
        scope:
            A capability scope string (e.g. ``"calendar:read"``).
        """
        all_servers = await self.list_for_user(user_id, enabled_only=True)
        return [s for s in all_servers if scope in s.scope]


__all__ = ["MCPRegistry"]
