"""graphclaw.mcp.registry — MCPRegistry: CRUD for MCPServerNode records.

Description
-----------
Provides ``MCPRegistry``, a service that persists and retrieves
``MCPServerNode`` vertices in the graph database.  Each registered server is
owned by a user via a ``GRANTS_ACCESS_TO_MCP`` edge from the ``UserNode`` to
the ``MCPServerNode``.

Design Patterns
---------------
- Service Object: ``MCPRegistry`` depends on the ``GraphStore`` ABC so any
  backend (AGE, in-memory fake) can be substituted without changing call sites.
- Optional SecretsClient: The registry accepts an optional ``SecretsClient``
  so credential cleanup can be performed in ``deregister()`` when available.

Public API
----------
- MCPRegistry: Manages MCPServerNode persistence in the graph.

Dependencies
------------
- graphclaw.db.base: GraphStore.
- graphclaw.models.base: utcnow.
- graphclaw.models.enums: EdgeType, TrustTier.
- graphclaw.models.nodes: MCPServerNode.
"""

from __future__ import annotations

import logging

from graphclaw.db.base import GraphStore
from graphclaw.models.base import utcnow
from graphclaw.models.enums import EdgeType, TrustTier
from graphclaw.models.nodes import MCPServerNode

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Manages MCPServerNode persistence in the property graph.

    Parameters
    ----------
    graph_store:
        A concrete ``GraphStore`` implementation for all node/edge CRUD.
    secrets_client:
        Optional secrets client used to delete credentials in ``deregister()``.
    """

    def __init__(self, graph_store: GraphStore, secrets_client=None) -> None:
        self._store = graph_store
        self._secrets = secrets_client

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    async def register(self, user_id: str, node: MCPServerNode) -> MCPServerNode:
        """Persist *node* to the graph and link it to the owning user.

        Creates the ``MCPServerNode`` vertex and a directed
        ``GRANTS_ACCESS_TO_MCP`` edge from *user_id* → *node.id*.

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
        await self._store.create_node(node)
        await self._store.create_edge(
            source_id=user_id,
            target_id=node.id,
            edge_type=EdgeType.GRANTS_ACCESS_TO_MCP,
            properties={"registered_at": node.registered_at.isoformat()},
        )
        logger.info(
            "mcp.registry.register",
            extra={"user_id": user_id, "server_id": node.id, "name": node.name},
        )
        return node

    async def get(self, server_id: str) -> MCPServerNode | None:
        """Return the ``MCPServerNode`` for *server_id*, or ``None`` if not found.

        Parameters
        ----------
        server_id:
            An ``MCP-{short_uuid}`` identifier.
        """
        raw = await self._store.get_node(server_id)
        if raw is None:
            return None
        return MCPServerNode.model_validate(raw)

    async def list_for_user(self, user_id: str, enabled_only: bool = True) -> list[MCPServerNode]:
        """Return all registered MCP servers belonging to *user_id*.

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
        # Traverse GRANTS_ACCESS_TO_MCP edges from the user
        edges = await self._store.get_edges(
            user_id,
            direction="out",
            edge_type=EdgeType.GRANTS_ACCESS_TO_MCP,
        )
        results: list[MCPServerNode] = []
        for edge in edges:
            target_id = edge.get("target_id") or edge.get("to") or edge.get("end_id")
            if not target_id:
                continue
            raw = await self._store.get_node(target_id)
            if raw is None:
                continue
            try:
                server = MCPServerNode.model_validate(raw)
            except Exception:
                logger.warning(
                    "mcp.registry.list_for_user.invalid_node",
                    extra={"node_id": target_id},
                )
                continue
            if enabled_only and not server.enabled:
                continue
            results.append(server)
        return results

    async def update_trust(self, server_id: str, trust_tier: TrustTier) -> MCPServerNode:
        """Change the trust tier of a registered MCP server.

        Parameters
        ----------
        server_id:
            Target server ID.
        trust_tier:
            The new ``TrustTier`` to apply.

        Returns
        -------
        MCPServerNode
            The updated node.

        Raises
        ------
        ValueError
            If the server is not found, or if attempting to promote a
            ``BLOCKED`` server to ``AUTO`` without an explicit override flag
            (this guard prevents accidental re-enabling of blocked servers).
        """
        existing = await self.get(server_id)
        if existing is None:
            raise ValueError(f"MCP server '{server_id}' not found.")

        if existing.trust_tier == TrustTier.BLOCKED and trust_tier == TrustTier.AUTO:
            raise ValueError(
                f"Cannot promote BLOCKED server '{server_id}' directly to AUTO. "
                "Set trust_tier to GATED first, then promote to AUTO."
            )

        now = utcnow()
        raw = await self._store.update_node(
            server_id,
            {"trust_tier": trust_tier.value, "updated_at": now.isoformat()},
        )
        return MCPServerNode.model_validate(raw)

    async def update_last_used(self, server_id: str) -> None:
        """Record the current timezone.utc timestamp as ``last_used_at`` for *server_id*.

        Parameters
        ----------
        server_id:
            Target server ID.
        """
        now = utcnow()
        await self._store.update_node(
            server_id,
            {"last_used_at": now.isoformat(), "updated_at": now.isoformat()},
        )

    async def enable(self, server_id: str) -> None:
        """Set ``enabled=True`` on *server_id*.

        Parameters
        ----------
        server_id:
            Target server ID.
        """
        now = utcnow()
        await self._store.update_node(
            server_id,
            {"enabled": True, "updated_at": now.isoformat()},
        )

    async def disable(self, server_id: str) -> None:
        """Set ``enabled=False`` on *server_id*.

        Parameters
        ----------
        server_id:
            Target server ID.
        """
        now = utcnow()
        await self._store.update_node(
            server_id,
            {"enabled": False, "updated_at": now.isoformat()},
        )

    async def deregister(self, server_id: str) -> None:
        """Delete *server_id* from the graph and its associated credential secret.

        If a ``SecretsClient`` was provided at construction time and the server
        has a ``secret_ref``, the secret is deleted before the node is removed.

        Parameters
        ----------
        server_id:
            Target server ID.
        """
        server = await self.get(server_id)
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

        await self._store.delete_node(server_id)
        logger.info("mcp.registry.deregister", extra={"server_id": server_id})

    async def find_by_scope(self, user_id: str, scope: str) -> list[MCPServerNode]:
        """Return enabled servers for *user_id* whose scope list contains *scope*.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` whose servers to search.
        scope:
            A capability scope string (e.g. ``"calendar:read"``).

        Returns
        -------
        list[MCPServerNode]
            Enabled servers where ``scope in server.scope``.
        """
        all_servers = await self.list_for_user(user_id, enabled_only=True)
        return [s for s in all_servers if scope in s.scope]


__all__ = ["MCPRegistry"]
