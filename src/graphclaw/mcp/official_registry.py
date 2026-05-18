# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.mcp.official_registry — OfficialMCPRegistry: search the public MCP server index.

Description
-----------
Wraps the official MCP server registry REST API at
``https://registry.modelcontextprotocol.io/v0.1/`` to allow agents to
discover publicly available MCP servers by keyword.  Handles cursor-based
pagination transparently.

Design Patterns
---------------
- Context Manager: The underlying ``httpx.AsyncClient`` is managed via
  ``async with OfficialMCPRegistry()`` for proper connection cleanup.
- Lazy Pagination: ``search()`` fetches additional pages only when the
  accumulated result count is below *limit* and a ``nextCursor`` is present.

Public API
----------
- OfficialMCPRegistry: Searches the official MCP registry.

Dependencies
------------
- graphclaw.mcp.models: MCPServerListing, MCPServerVersion.
- httpx: Async HTTP client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from graphclaw.mcp.models import MCPServerListing, MCPServerVersion


class OfficialMCPRegistry:
    """Wraps the official MCP server registry at registry.modelcontextprotocol.io.

    API reference: https://registry.modelcontextprotocol.io/v0.1/

    Examples
    --------
    ::

        async with OfficialMCPRegistry() as reg:
            servers = await reg.search("google calendar", limit=5)
    """

    BASE_URL = "https://registry.modelcontextprotocol.io/v0.1"

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15.0,
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str = "",
        limit: int = 10,
        updated_since: datetime | None = None,
    ) -> list[MCPServerListing]:
        """Search the official registry for MCP servers matching *query*.

        Fetches up to *limit* results, following ``nextCursor`` pagination
        until the limit is satisfied or no further pages exist.

        Parameters
        ----------
        query:
            Free-text search string (empty string returns all servers).
        limit:
            Maximum number of results to return.
        updated_since:
            Optional ISO 8601 datetime; only servers updated after this time
            are returned.

        Returns
        -------
        list[MCPServerListing]
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if query:
            params["search"] = query
        if updated_since is not None:
            params["updatedSince"] = updated_since.isoformat()

        results: list[MCPServerListing] = []
        cursor: str | None = None

        while len(results) < limit:
            if cursor:
                params["cursor"] = cursor
            elif "cursor" in params:
                del params["cursor"]

            response = await self._http.get(f"{self.BASE_URL}/servers", params=params)
            response.raise_for_status()
            payload = response.json()

            servers_raw: list[dict] = payload.get("servers", [])
            for raw in servers_raw:
                if len(results) >= limit:
                    break
                listing = self._parse_listing(raw)
                if listing is not None:
                    results.append(listing)

            cursor = payload.get("nextCursor")
            if not cursor:
                break

        return results[:limit]

    async def get_versions(self, server_name: str) -> list[MCPServerVersion]:
        """Return all available versions of *server_name*, sorted descending by release date.

        Parameters
        ----------
        server_name:
            The qualified server name as it appears in the registry
            (e.g. ``"io.github.example/my-server"``).

        Returns
        -------
        list[MCPServerVersion]
            Versions sorted by ``released_at`` descending (newest first).
        """
        response = await self._http.get(f"{self.BASE_URL}/servers/{server_name}/versions")
        response.raise_for_status()
        payload = response.json()

        versions: list[MCPServerVersion] = []
        for raw in payload.get("versions", []):
            released_raw = raw.get("releasedAt") or raw.get("released_at")
            released_at: datetime | None = None
            if released_raw:
                try:
                    released_at = datetime.fromisoformat(released_raw)
                except ValueError:
                    pass
            versions.append(
                MCPServerVersion(
                    server_name=server_name,
                    version=raw.get("version", ""),
                    released_at=released_at,
                    changelog=raw.get("changelog", ""),
                )
            )

        # Sort newest first; None released_at sorts to end
        versions.sort(
            key=lambda v: v.released_at or datetime.min,
            reverse=True,
        )
        return versions

    async def get_latest(self, server_name: str) -> MCPServerVersion:
        """Return the most recent version of *server_name*.

        Parameters
        ----------
        server_name:
            Qualified server name in the registry.

        Returns
        -------
        MCPServerVersion
            The version with the most recent ``released_at``.

        Raises
        ------
        ValueError
            If no versions are available for the given server name.
        """
        versions = await self.get_versions(server_name)
        if not versions:
            raise ValueError(
                f"No versions found for MCP server '{server_name}' in the official registry."
            )
        return versions[0]

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> OfficialMCPRegistry:
        return self

    async def __aexit__(self, *_) -> None:  # noqa: ANN002
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_listing(raw: dict) -> MCPServerListing | None:
        """Map a raw registry API dict to an ``MCPServerListing``.

        Returns ``None`` when mandatory fields are absent.
        """
        name = raw.get("name") or raw.get("id")
        if not name:
            return None

        # The registry may use different key names; try common variants.
        publisher = raw.get("publisher") or raw.get("author") or raw.get("vendor") or ""
        version = raw.get("version") or raw.get("latestVersion") or raw.get("latest_version") or ""
        transport_raw = (
            raw.get("transport")
            or raw.get("defaultTransport")
            or raw.get("default_transport")
            or "http"
        )
        # Normalise to lowercase string
        transport = transport_raw.lower() if isinstance(transport_raw, str) else "http"

        registry_url = (
            raw.get("url")
            or raw.get("registryUrl")
            or raw.get("registry_url")
            or f"https://registry.modelcontextprotocol.io/v0.1/servers/{name}"
        )

        tags_raw = raw.get("tags") or raw.get("categories") or []
        tags = list(tags_raw) if isinstance(tags_raw, list) else []

        install_command = raw.get("installCommand") or raw.get("install_command")

        return MCPServerListing(
            name=name,
            description=raw.get("description", ""),
            publisher=publisher,
            version=version,
            transport=transport,
            tags=tags,
            registry_url=registry_url,
            install_command=install_command,
        )


__all__ = ["OfficialMCPRegistry"]
