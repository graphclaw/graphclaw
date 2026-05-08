# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.admin.agent_channels — AgentChannelIdentity CRUD endpoints (FR-IN-003).

Routes
------
GET    /app/v1/admin/agent-channels           — list all channel identity entries
POST   /app/v1/admin/agent-channels           — create a new entry
PUT    /app/v1/admin/agent-channels/{channel}/{account_id} — replace an entry
DELETE /app/v1/admin/agent-channels/{channel}/{account_id} — deactivate entry

All endpoints require ADMIN role.
The in-memory registry (app.state.channel_registry) is hot-reloaded on every write.

Design Patterns
---------------
- Repository / In-process cache: writes go to graph store; registry is hot-reloaded.
- Graceful degradation: registry absent → 503.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from graphclaw.models.agent_channel_identity import AgentChannelIdentity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/agent-channels", tags=["admin-api"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AgentChannelIdentityIn(BaseModel):
    """Request body for create / update."""

    user_id: str
    agent_id: str
    channel: str
    account_id: str
    display_name: str = ""
    credentials_ref: str = ""
    active: bool = True
    owner_identities: list[str] = []


class AgentChannelIdentityOut(AgentChannelIdentityIn):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_registry(request: Request) -> Any:
    """Return the AgentChannelIdentityRegistry from app state or raise 503."""
    registry = getattr(request.app.state, "channel_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AgentChannelIdentityRegistry not initialised",
        )
    return registry


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[AgentChannelIdentityOut],
    summary="List all agent channel identities",
)
async def list_entries(request: Request) -> list[dict]:
    """Return all registered channel identity entries."""
    registry = _get_registry(request)
    return [e.model_dump() for e in registry.all_entries()]


@router.post(
    "",
    response_model=AgentChannelIdentityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent channel identity",
)
async def create_entry(body: AgentChannelIdentityIn, request: Request) -> dict:
    """Create a new channel identity mapping and hot-reload the registry."""
    registry = _get_registry(request)
    entry = AgentChannelIdentity(**body.model_dump())
    registry.add(entry)
    logger.info(
        "admin/agent-channels: created %s/%s → user=%s",
        entry.channel,
        entry.account_id,
        entry.user_id,
    )
    return entry.model_dump()


@router.put(
    "/{channel}/{account_id}",
    response_model=AgentChannelIdentityOut,
    summary="Replace an agent channel identity",
)
async def update_entry(
    channel: str,
    account_id: str,
    body: AgentChannelIdentityIn,
    request: Request,
) -> dict:
    """Replace an existing channel identity mapping."""
    registry = _get_registry(request)
    entry = AgentChannelIdentity(**body.model_dump())
    if entry.channel != channel or entry.account_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="channel and account_id in path must match body",
        )
    registry.add(entry)
    logger.info("admin/agent-channels: updated %s/%s → user=%s", channel, account_id, entry.user_id)
    return entry.model_dump()


@router.delete(
    "/{channel}/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate an agent channel identity",
)
async def delete_entry(channel: str, account_id: str, request: Request) -> None:
    """Deactivate (disable) a channel identity mapping.

    Follows the no-delete principle: entry is marked inactive, not removed.
    """
    registry = _get_registry(request)
    existing = await registry.lookup(channel=channel, account_id=account_id)
    if existing is None:
        # Try to find even disabled entries
        found = next(
            (
                e
                for e in registry.all_entries()
                if e.channel == channel and e.account_id == account_id
            ),
            None,
        )
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No entry for {channel}/{account_id}",
            )
        existing = found

    # Mark inactive (no hard delete)
    disabled = existing.model_copy(update={"active": False})
    registry.add(disabled)
    logger.info("admin/agent-channels: deactivated %s/%s", channel, account_id)
