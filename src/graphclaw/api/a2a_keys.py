# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.a2a_keys — A2A agent key management endpoints.

Description
-----------
Provides REST endpoints for managing Agent-to-Agent (A2A) API keys for
registered external agents.  Keys are issued at registration or rotation and
disclosed in plaintext only once — they cannot be retrieved afterwards.

Endpoints
---------
- ``GET    /app/v1/a2a/agents``                — List registered A2A agents.
- ``POST   /app/v1/a2a/agents``                — Register a new agent.
- ``POST   /app/v1/a2a/agents/{key_id}/rotate`` — Rotate a key (one-time plaintext).
- ``DELETE /app/v1/a2a/agents/{key_id}``        — Revoke a key.

All endpoints require a valid Bearer access token.

Design Patterns
---------------
- One-time key disclosure: Plaintext keys are returned only at registration
  or rotation and are never logged or stored in plaintext.
- Stub storage: A module-level dict simulates key persistence until the
  ``A2AKeyManager`` graph store integration is connected here.

Public API
----------
- router: ``APIRouter`` for /a2a routes.

Dependencies
------------
- graphclaw.auth.middleware: require_auth.
- fastapi: APIRouter, Depends, HTTPException, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from graphclaw.auth.middleware import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["app-api"])

# ── Stub in-memory storage ─────────────────────────────────────────────────────

# user_id -> list of agent dicts (no plaintext keys stored)
_a2a_agents: dict[str, list[dict[str, Any]]] = {}


# ── Request / Response models ──────────────────────────────────────────────────


class A2AAgentEntry(BaseModel):
    """A registered A2A agent (no plaintext key)."""

    key_id: str
    agent_name: str
    description: str = ""
    revoked: bool = False


class A2AAgentRegisterRequest(BaseModel):
    """Request body for POST /app/v1/a2a/agents."""

    agent_name: str
    description: str = ""


class A2AAgentRegisterResponse(BaseModel):
    """Response for POST /app/v1/a2a/agents — plaintext key disclosed once."""

    key_id: str
    agent_name: str
    api_key: str  # plaintext — shown once only


class A2AKeyRotateResponse(BaseModel):
    """Response for POST /app/v1/a2a/agents/{key_id}/rotate."""

    key_id: str
    new_api_key: str  # plaintext — shown once only


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "/agents",
    response_model=list[A2AAgentEntry],
    status_code=status.HTTP_200_OK,
    summary="List registered A2A agents",
    description=(
        "Return all A2A agents registered by the authenticated user.  "
        "Plaintext keys are never returned after initial registration."
    ),
)
async def list_agents(
    user_id: str = Depends(require_auth),
) -> list[A2AAgentEntry]:
    agents = _a2a_agents.get(user_id, [])
    return [
        A2AAgentEntry(
            key_id=a["key_id"],
            agent_name=a["agent_name"],
            description=a.get("description", ""),
            revoked=a.get("revoked", False),
        )
        for a in agents
    ]


@router.post(
    "/agents",
    response_model=A2AAgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new A2A agent",
    description=(
        "Register a new agent and return its API key in plaintext.  "
        "The key is disclosed only once — store it securely."
    ),
)
async def register_agent(
    body: A2AAgentRegisterRequest,
    user_id: str = Depends(require_auth),
) -> A2AAgentRegisterResponse:
    key_id = f"A2A-{uuid4().hex[:12]}"
    plaintext_key = secrets.token_urlsafe(32)
    # Store only the key_id (not the plaintext) in the stub
    entry: dict[str, Any] = {
        "key_id": key_id,
        "agent_name": body.agent_name,
        "description": body.description,
        "revoked": False,
    }
    _a2a_agents.setdefault(user_id, []).append(entry)
    logger.info(
        "a2a: registered agent '%s' (%s) for user_id=%s",
        body.agent_name,
        key_id,
        user_id,
    )
    return A2AAgentRegisterResponse(
        key_id=key_id,
        agent_name=body.agent_name,
        api_key=plaintext_key,
    )


@router.post(
    "/agents/{key_id}/rotate",
    response_model=A2AKeyRotateResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate an agent key",
    description=(
        "Issue a new API key for the agent, invalidating the previous one.  "
        "The new key is returned in plaintext once only."
    ),
)
async def rotate_key(
    key_id: str,
    user_id: str = Depends(require_auth),
) -> A2AKeyRotateResponse:
    agents = _a2a_agents.get(user_id, [])
    for agent in agents:
        if agent.get("key_id") == key_id:
            if agent.get("revoked"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Agent key '{key_id}' has been revoked and cannot be rotated",
                )
            new_plaintext_key = secrets.token_urlsafe(32)
            logger.info("a2a: rotated key '%s' for user_id=%s", key_id, user_id)
            return A2AKeyRotateResponse(key_id=key_id, new_api_key=new_plaintext_key)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Agent key '{key_id}' not found",
    )


@router.delete(
    "/agents/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an agent key",
    description="Permanently revoke an A2A agent key, preventing further use.",
)
async def revoke_key(
    key_id: str,
    user_id: str = Depends(require_auth),
) -> None:
    agents = _a2a_agents.get(user_id, [])
    for agent in agents:
        if agent.get("key_id") == key_id:
            agent["revoked"] = True
            logger.info("a2a: revoked key '%s' for user_id=%s", key_id, user_id)
            return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Agent key '{key_id}' not found",
    )
