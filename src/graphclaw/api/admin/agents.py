# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.agents — Multi-agent admin REST endpoints (FR-AM-001).

Routes
------
GET    /app/v1/admin/agents            — list agent ResourceNodes for the authenticated user
POST   /app/v1/admin/agents            — create a new agent ResourceNode
PATCH  /app/v1/admin/agents/{agent_id} — rename an agent
DELETE /app/v1/admin/agents/{agent_id} — archive (soft-delete) an agent

All endpoints require ADMIN role.

Design Patterns
---------------
- Repository: agent nodes persisted in graph store.
- Factory: agent IDs generated via canonical generate_resource_id helper.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, CallerContextDep, GraphStoreDep
from graphclaw.models.enums import LinkStatus, ResourceType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/agents", tags=["admin-api"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_agent_id(user_id: str, display_name: str) -> str:
    """Generate a deterministic but unique agent resource ID."""
    import hashlib  # noqa: PLC0415

    suffix = hashlib.sha256(
        f"{user_id}:{display_name}:{_utcnow().isoformat()}".encode(),
    ).hexdigest()[:8]
    slug = display_name.lower().replace(" ", "-")[:12]
    return f"RES-agent-{slug}-{suffix}"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateAgentRequest(BaseModel):
    """Body for POST /admin/agents."""

    display_name: str
    contact: str | None = None
    timezone: str | None = None


class RenameAgentRequest(BaseModel):
    """Body for PATCH /admin/agents/{agent_id}."""

    display_name: str


class AgentOut(BaseModel):
    """Serialised agent ResourceNode summary."""

    id: str
    display_name: str
    contact: str | None
    link_status: str
    owner_user_id: str
    created_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[AgentOut],
    status_code=status.HTTP_200_OK,
    summary="List agents",
    description="List all agent ResourceNodes owned by the authenticated admin user.",
)
async def list_agents(
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
) -> list[AgentOut]:
    """Return AI agent ResourceNodes for *admin_user_id*."""
    nodes = await graph_store.list_nodes("Resource", {"resource_type": ResourceType.AI_AGENT.value})
    results: list[AgentOut] = []
    for node in nodes:
        owner = node.get("linked_user_id") or node.get("owned_by") or ""
        if owner != admin_user_id:
            continue
        results.append(
            AgentOut(
                id=node["id"],
                display_name=node.get("name", ""),
                contact=node.get("contact"),
                link_status=node.get("link_status", LinkStatus.ACTIVE.value),
                owner_user_id=owner,
                created_at=str(node.get("created_at", "")),
            )
        )
    return results


@router.post(
    "",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create agent",
    description="Create a new AI agent ResourceNode for the authenticated admin user.",
)
async def create_agent(
    body: CreateAgentRequest,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
    caller_context: CallerContextDep,
) -> AgentOut:
    """Create a new agent ResourceNode owned by *admin_user_id*."""
    from graphclaw.models.nodes import ResourceNode  # noqa: PLC0415

    agent_id = _generate_agent_id(admin_user_id, body.display_name)
    node = ResourceNode(
        id=agent_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
        resource_type=ResourceType.AI_AGENT,
        name=body.display_name,
        contact=body.contact,
        timezone=body.timezone,
        linked_user_id=admin_user_id,
        link_status=LinkStatus.ACTIVE,
    )

    try:
        created: dict[str, Any] = await graph_store.create_node(node, caller_context=caller_context)
    except Exception as exc:
        logger.error("admin/agents: failed to create agent for user %s: %s", admin_user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create agent node.",
        ) from exc

    logger.info("admin/agents: created agent %s for user %s", agent_id, admin_user_id)
    return AgentOut(
        id=created.get("id", agent_id),
        display_name=created.get("name", body.display_name),
        contact=created.get("contact"),
        link_status=created.get("link_status", LinkStatus.ACTIVE.value),
        owner_user_id=admin_user_id,
        created_at=str(created.get("created_at", _utcnow())),
    )


@router.patch(
    "/{agent_id}",
    response_model=AgentOut,
    status_code=status.HTTP_200_OK,
    summary="Rename agent",
    description="Update the display name of an agent ResourceNode.",
)
async def rename_agent(
    agent_id: str,
    body: RenameAgentRequest,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
) -> AgentOut:
    """Rename *agent_id*."""
    existing = await graph_store.get_node(agent_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{agent_id}' not found"
        )
    owner = existing.get("linked_user_id") or existing.get("owned_by") or ""
    if owner != admin_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{admin_user_id}' does not own agent '{agent_id}'",
        )

    updated = await graph_store.update_node(agent_id, {"name": body.display_name})
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rename agent.",
        )

    logger.info("admin/agents: renamed %s → '%s' by %s", agent_id, body.display_name, admin_user_id)
    return AgentOut(
        id=updated.get("id", agent_id),
        display_name=updated.get("name", body.display_name),
        contact=updated.get("contact"),
        link_status=updated.get("link_status", LinkStatus.ACTIVE.value),
        owner_user_id=admin_user_id,
        created_at=str(updated.get("created_at", "")),
    )


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive agent",
    description="Soft-archive an agent ResourceNode (sets link_status=ARCHIVED, does not delete).",
)
async def archive_agent(
    agent_id: str,
    admin_user_id: AdminUserDep,
    graph_store: GraphStoreDep,
) -> None:
    """Archive *agent_id* by setting link_status to ARCHIVED."""
    existing = await graph_store.get_node(agent_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{agent_id}' not found"
        )
    owner = existing.get("linked_user_id") or existing.get("owned_by") or ""
    if owner != admin_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{admin_user_id}' does not own agent '{agent_id}'",
        )

    await graph_store.update_node(agent_id, {"link_status": LinkStatus.ARCHIVED.value})
    logger.info("admin/agents: archived agent %s by user %s", agent_id, admin_user_id)
