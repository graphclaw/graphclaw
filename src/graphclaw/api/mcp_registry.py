"""graphclaw.api.mcp_registry — MCP server CRUD endpoints.

Description
-----------
Provides REST endpoints for registering and managing MCP servers in the user's
personal MCP Registry.

Endpoints
---------
- ``GET    /app/v1/mcp-servers``               — List registered MCP servers.
- ``POST   /app/v1/mcp-servers``               — Register a new MCP server.
- ``GET    /app/v1/mcp-servers/search``        — Search the official MCP registry.
- ``GET    /app/v1/mcp-servers/{server_id}``   — Get a specific MCP server.
- ``PATCH  /app/v1/mcp-servers/{server_id}``   — Update trust tier or enabled status.
- ``DELETE /app/v1/mcp-servers/{server_id}``   — Deregister a server.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- Stub storage: A module-level dict simulates per-user MCP server registries
  until the graph store integration is implemented.
- MCP-prefixed IDs: Server IDs follow the ``MCP-<hex>`` pattern matching the
  ``MCPServerNode`` validator.

Public API
----------
- router: ``APIRouter`` for /mcp-servers routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- fastapi: APIRouter, Depends, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-servers", tags=["app-api"])

# ── Stub in-memory storage ─────────────────────────────────────────────────────

# user_id -> list of MCP server dicts
_mcp_servers: dict[str, list[dict[str, Any]]] = {}


# ── Request / Response models ──────────────────────────────────────────────────


class MCPServerEntry(BaseModel):
    """A registered MCP server."""

    server_id: str
    name: str
    transport: str = "http"
    endpoint_url: str | None = None
    trust_tier: str = "GATED"
    scope: list[str] = []
    enabled: bool = True


class MCPServerRegisterRequest(BaseModel):
    """Request body for POST /app/v1/mcp-servers."""

    name: str
    transport: str = "http"
    endpoint_url: str | None = None
    trust_tier: str = "GATED"
    scope: list[str] = []


class MCPServerPatchRequest(BaseModel):
    """Request body for PATCH /app/v1/mcp-servers/{server_id}."""

    trust_tier: str | None = None
    enabled: bool | None = None


class MCPRegistrySearchResult(BaseModel):
    """A result entry from the official MCP registry search."""

    name: str
    description: str
    transport: str
    official: bool = False


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[MCPServerEntry],
    status_code=status.HTTP_200_OK,
    summary="List registered MCP servers",
    description="Return all MCP servers registered for the authenticated user.",
)
async def list_mcp_servers(
    user_id: str = Depends(require_auth),
) -> list[MCPServerEntry]:
    servers = _mcp_servers.get(user_id, [])
    return [MCPServerEntry(**s) for s in servers]


@router.post(
    "",
    response_model=MCPServerEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new MCP server",
    description="Register an MCP server in the authenticated user's personal registry.",
)
async def register_mcp_server(
    body: MCPServerRegisterRequest,
    user_id: str = Depends(require_auth),
) -> MCPServerEntry:
    server_id = f"MCP-{uuid4().hex[:12]}"
    entry: dict[str, Any] = {
        "server_id": server_id,
        "name": body.name,
        "transport": body.transport,
        "endpoint_url": body.endpoint_url,
        "trust_tier": body.trust_tier,
        "scope": list(body.scope),
        "enabled": True,
    }
    _mcp_servers.setdefault(user_id, []).append(entry)
    logger.info(
        "mcp-servers: registered '%s' (%s) for user_id=%s",
        body.name,
        server_id,
        user_id,
    )
    return MCPServerEntry(**entry)


@router.get(
    "/search",
    response_model=list[MCPRegistrySearchResult],
    status_code=status.HTTP_200_OK,
    summary="Search the official MCP registry",
    description=(
        "Search for available MCP servers in the official registry by name "
        "or description.  Stub implementation returns an empty list until the "
        "official MCP registry client is integrated."
    ),
)
async def search_mcp_registry(
    q: str = Query(default="", description="Search query string"),
    user_id: str = Depends(require_auth),  # noqa: ARG001
) -> list[MCPRegistrySearchResult]:
    """Search the official MCP registry.

    Stub — full integration with the official registry will be added in a
    future phase.  Returns the three pre-built adapters when ``q`` is empty
    or matches.
    """
    prebuilt = [
        MCPRegistrySearchResult(
            name="google_calendar",
            description="Google Calendar API via OAuth2",
            transport="sse",
            official=True,
        ),
        MCPRegistrySearchResult(
            name="github",
            description="GitHub REST API v3 via Personal Access Token",
            transport="http",
            official=True,
        ),
        MCPRegistrySearchResult(
            name="slack",
            description="Slack API via Bot Token",
            transport="http",
            official=True,
        ),
    ]
    if q:
        prebuilt = [
            r for r in prebuilt if q.lower() in r.name.lower() or q.lower() in r.description.lower()
        ]
    return prebuilt


@router.get(
    "/{server_id}",
    response_model=MCPServerEntry,
    status_code=status.HTTP_200_OK,
    summary="Get a specific MCP server",
    description="Return the details of a registered MCP server by its ID.",
)
async def get_mcp_server(
    server_id: str,
    user_id: str = Depends(require_auth),
) -> MCPServerEntry:
    servers = _mcp_servers.get(user_id, [])
    for server in servers:
        if server.get("server_id") == server_id:
            return MCPServerEntry(**server)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"MCP server '{server_id}' not found",
    )


@router.patch(
    "/{server_id}",
    response_model=MCPServerEntry,
    status_code=status.HTTP_200_OK,
    summary="Update MCP server trust tier or enabled status",
    description="Partially update a registered MCP server's trust tier or enabled flag.",
)
async def update_mcp_server(
    server_id: str,
    body: MCPServerPatchRequest,
    user_id: str = Depends(require_auth),
) -> MCPServerEntry:
    servers = _mcp_servers.get(user_id, [])
    for server in servers:
        if server.get("server_id") == server_id:
            if body.trust_tier is not None:
                server["trust_tier"] = body.trust_tier
            if body.enabled is not None:
                server["enabled"] = body.enabled
            logger.debug("mcp-servers: updated '%s' for user_id=%s", server_id, user_id)
            return MCPServerEntry(**server)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"MCP server '{server_id}' not found",
    )


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deregister an MCP server",
    description="Remove a registered MCP server from the user's personal registry.",
)
async def delete_mcp_server(
    server_id: str,
    user_id: str = Depends(require_auth),
) -> None:
    servers = _mcp_servers.get(user_id, [])
    original_len = len(servers)
    servers[:] = [s for s in servers if s.get("server_id") != server_id]
    if len(servers) == original_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found",
        )
    logger.info("mcp-servers: deregistered '%s' for user_id=%s", server_id, user_id)
