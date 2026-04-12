"""graphclaw.api.mcp_registry — MCP server CRUD endpoints.

Description
-----------
Provides REST endpoints for registering and managing MCP servers in the user's
personal MCP Registry.  Server metadata is persisted as ``MCPServerNode``
vertices in the graph database.

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
- MCPRegistry delegation: All node CRUD is routed through ``MCPRegistry``,
  which manages ``MCPServerNode`` vertices and ``GRANTS_ACCESS_TO_MCP`` edges.
- OfficialMCPRegistry search: The ``/search`` endpoint queries the live
  registry at ``registry.modelcontextprotocol.io`` via ``OfficialMCPRegistry``.
  It degrades gracefully to an empty list if the upstream is unreachable.
- Ownership validation: ``get_mcp_server``, ``update_mcp_server``, and
  ``delete_mcp_server`` verify ownership via the MCPRegistry's per-user edge
  before allowing modifications.
- TrustTier guard: ``MCPRegistry.update_trust`` enforces the BLOCKED → AUTO
  escalation guard (must go through GATED first).
- MCP-prefixed IDs: Server IDs follow ``MCP-[\w-]+`` matching the
  ``MCPServerNode`` validator.

Public API
----------
- router: ``APIRouter`` for /mcp-servers routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, MCPRegistryDep.
- graphclaw.mcp.official_registry: OfficialMCPRegistry.
- graphclaw.models.enums: MCPTransport, TrustTier.
- graphclaw.models.nodes: MCPServerNode.
- graphclaw.models.base: generate_mcp_server_id.
- fastapi: APIRouter, HTTPException, Query, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, GraphStoreDep, MCPRegistryDep
from graphclaw.mcp.official_registry import OfficialMCPRegistry
from graphclaw.models.base import utcnow
from graphclaw.models.enums import MCPTransport, TrustTier
from graphclaw.models.nodes import MCPServerNode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-servers", tags=["app-api"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


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
    publisher: str = ""
    version: str = ""
    official: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[MCPServerEntry],
    status_code=status.HTTP_200_OK,
    summary="List registered MCP servers",
    description="Return all MCP servers registered for the authenticated user (enabled and disabled).",
)
async def list_mcp_servers(
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> list[MCPServerEntry]:
    """List all MCP servers for the authenticated user."""
    servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
    return [_node_to_entry(s) for s in servers]


@router.post(
    "",
    response_model=MCPServerEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new MCP server",
    description="Register an MCP server in the authenticated user's personal registry.",
)
async def register_mcp_server(
    body: MCPServerRegisterRequest,
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> MCPServerEntry:
    """Register a new MCP server for the authenticated user."""
    try:
        transport = MCPTransport(body.transport)
    except ValueError:
        valid = [t.value for t in MCPTransport]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid transport '{body.transport}'. Valid values: {valid}",
        )

    try:
        trust_tier = TrustTier(body.trust_tier)
    except ValueError:
        valid_tiers = [t.value for t in TrustTier]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid trust_tier '{body.trust_tier}'. Valid values: {valid_tiers}",
        )

    now = utcnow()
    server_id = f"MCP-{uuid4().hex[:12]}"
    node = MCPServerNode(
        id=server_id,
        name=body.name,
        transport=transport,
        endpoint_url=body.endpoint_url,
        trust_tier=trust_tier,
        scope=list(body.scope),
        enabled=True,
        created_at=now,
        updated_at=now,
        version=0,
    )
    await mcp_registry.register(user_id, node)
    logger.info("mcp-servers: registered '%s' (%s) for user_id=%s", body.name, server_id, user_id)
    return _node_to_entry(node)


@router.get(
    "/search",
    response_model=list[MCPRegistrySearchResult],
    status_code=status.HTTP_200_OK,
    summary="Search the official MCP registry",
    description=(
        "Search for available MCP servers in the official registry at "
        "registry.modelcontextprotocol.io.  Returns an empty list if the "
        "upstream registry is unreachable."
    ),
)
async def search_mcp_registry(
    user_id: CurrentUserDep,  # noqa: ARG001 — required for auth
    q: str = Query(default="", description="Search query string"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum results to return"),
) -> list[MCPRegistrySearchResult]:
    """Search the official MCP registry."""
    try:
        async with OfficialMCPRegistry() as reg:
            listings = await reg.search(query=q, limit=limit)
        return [
            MCPRegistrySearchResult(
                name=li.name,
                description=li.description,
                transport=li.transport,
                publisher=getattr(li, "publisher", ""),
                version=getattr(li, "version", ""),
                official=True,
            )
            for li in listings
        ]
    except Exception as exc:
        logger.warning("mcp-servers: official registry search failed: %s", exc)
        return []


@router.get(
    "/{server_id}",
    response_model=MCPServerEntry,
    status_code=status.HTTP_200_OK,
    summary="Get a specific MCP server",
    description="Return the details of a registered MCP server by its ID.",
)
async def get_mcp_server(
    server_id: str,
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> MCPServerEntry:
    """Return a specific MCP server belonging to the authenticated user."""
    node = await mcp_registry.get(server_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server '{server_id}' not found"
        )

    # Verify ownership via the user's edge list
    user_servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
    if not any(s.id == server_id for s in user_servers):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server '{server_id}' not found"
        )

    return _node_to_entry(node)


@router.patch(
    "/{server_id}",
    response_model=MCPServerEntry,
    status_code=status.HTTP_200_OK,
    summary="Update MCP server trust tier or enabled status",
    description=(
        "Partially update a registered MCP server's trust tier or enabled flag.  "
        "Note: BLOCKED servers cannot be promoted directly to AUTO — set to GATED first."
    ),
)
async def update_mcp_server(
    server_id: str,
    body: MCPServerPatchRequest,
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> MCPServerEntry:
    """Update trust tier or enabled status for an MCP server."""
    # Verify ownership
    user_servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
    if not any(s.id == server_id for s in user_servers):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server '{server_id}' not found"
        )

    if body.trust_tier is not None:
        try:
            new_tier = TrustTier(body.trust_tier)
        except ValueError:
            valid_tiers = [t.value for t in TrustTier]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid trust_tier '{body.trust_tier}'. Valid values: {valid_tiers}",
            )
        try:
            node = await mcp_registry.update_trust(server_id, new_tier)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    else:
        node = await mcp_registry.get(server_id)

    if body.enabled is not None and node is not None:
        if body.enabled:
            await mcp_registry.enable(server_id)
        else:
            await mcp_registry.disable(server_id)
        node = await mcp_registry.get(server_id)

    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server '{server_id}' not found"
        )

    logger.debug("mcp-servers: updated '%s' for user_id=%s", server_id, user_id)
    return _node_to_entry(node)


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deregister an MCP server",
    description=(
        "Remove a registered MCP server from the user's personal registry.  "
        "If the server has a secret_ref, the associated credential is also deleted."
    ),
)
async def delete_mcp_server(
    server_id: str,
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> None:
    """Deregister an MCP server from the authenticated user's registry."""
    # Verify ownership
    user_servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
    if not any(s.id == server_id for s in user_servers):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server '{server_id}' not found"
        )

    await mcp_registry.deregister(server_id)
    logger.info("mcp-servers: deregistered '%s' for user_id=%s", server_id, user_id)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _node_to_entry(node: MCPServerNode) -> MCPServerEntry:
    """Map an ``MCPServerNode`` to an ``MCPServerEntry`` response."""
    return MCPServerEntry(
        server_id=node.id,
        name=node.name,
        transport=node.transport.value if hasattr(node.transport, "value") else str(node.transport),
        endpoint_url=node.endpoint_url,
        trust_tier=node.trust_tier.value
        if hasattr(node.trust_tier, "value")
        else str(node.trust_tier),
        scope=list(node.scope),
        enabled=node.enabled,
    )


# ---------------------------------------------------------------------------
# Wave 5 — Tools listing and MCP approvals
# Note: These routes live on a *separate* router to avoid prefix collision with
# /mcp-servers/{server_id} vs /mcp-approvals (different prefix).
# ---------------------------------------------------------------------------

mcp_approvals_router = APIRouter(prefix="/mcp-approvals", tags=["app-api"])


class MCPToolOut(BaseModel):
    """A single tool exposed by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict = {}
    server_id: str


class MCPApprovalOut(BaseModel):
    """A pending MCP tool-call approval task."""

    task_id: str
    task_type: str = "APPROVAL"
    state: str
    assigned_to: str
    title: str = ""
    created_at: str | None = None


@router.get(
    "/{server_id}/tools",
    response_model=list[MCPToolOut],
    status_code=status.HTTP_200_OK,
    summary="List MCP server tools",
    description=(
        "Return the tools advertised by the registered MCP server.  Attempts "
        "a live connection and degrades gracefully to an empty list if the "
        "server is unreachable."
    ),
)
async def list_mcp_server_tools(
    server_id: str,
    user_id: CurrentUserDep,
    mcp_registry: MCPRegistryDep,
) -> list[MCPToolOut]:
    """List tools from the specified MCP server; gracefully handles failures."""
    # Verify ownership / existence
    user_servers = await mcp_registry.list_for_user(user_id, enabled_only=False)
    server_node = next((s for s in user_servers if s.id == server_id), None)
    if server_node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found",
        )

    # Attempt live connection to list tools
    try:
        from graphclaw.mcp.client import MCPClient

        client = MCPClient()
        await client.connect(server_node)
        try:
            tools = await client.list_tools()
        finally:
            await client.disconnect()
        return [
            MCPToolOut(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                server_id=t.server_id,
            )
            for t in tools
        ]
    except Exception as exc:
        logger.warning(
            "mcp-servers: tools list failed for server_id=%s: %s",
            server_id,
            exc,
        )
        return []


@mcp_approvals_router.get(
    "",
    response_model=list[MCPApprovalOut],
    status_code=status.HTTP_200_OK,
    summary="List pending MCP approvals",
    description=("Return all pending MCP tool-call approval tasks for the authenticated user."),
)
async def list_mcp_approvals(
    user_id: CurrentUserDep,
    graph_store: GraphStoreDep,
) -> list[MCPApprovalOut]:
    """List APPROVAL tasks for MCP tool calls pending user review."""
    from graphclaw.mcp.approval import GatedApprovalService

    service = GatedApprovalService(graph_store=graph_store)
    try:
        raw_tasks = await service.get_pending_approvals(user_id)
    except Exception as exc:
        logger.warning("mcp-approvals: fetch failed: %s", exc)
        return []

    return [
        MCPApprovalOut(
            task_id=t.get("id", ""),
            state=t.get("state", "PENDING"),
            assigned_to=t.get("assigned_to", user_id),
            title=t.get("title", ""),
            created_at=str(t["created_at"]) if t.get("created_at") else None,
        )
        for t in raw_tasks
    ]
