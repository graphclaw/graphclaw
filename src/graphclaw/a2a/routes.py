# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.a2a.routes — FastAPI routers for A2A key management and task updates.

Description
-----------
Provides two routers that together form the A2A REST API surface:

``a2a_router`` — Agent management endpoints under ``/api/v1/a2a/``:

  POST   /api/v1/a2a/agents                 — Register a new agent, return key once.
  POST   /api/v1/a2a/agents/{key_id}/rotate — Rotate a key, return new key once.
  DELETE /api/v1/a2a/agents/{key_id}        — Revoke a key.
  GET    /api/v1/a2a/agents                 — List all registered agents (no secrets).

``task_update_router`` — Inbound task-update endpoint (no ``/a2a`` prefix, per PRD):

  POST   /api/v1/task-update                — Main A2A inbound endpoint.

The task-update endpoint is on a separate router with no prefix so that agents
POST to the canonical ``/api/v1/task-update`` path matching the PRD.

Authentication
--------------
- ``/api/v1/a2a/agents`` management routes require a platform JWT Bearer token
  (standard ``Authorization: Bearer <token>`` header via ``require_auth``).
- ``POST /api/v1/task-update`` requires the ``X-Agent-Api-Key`` header
  (via ``require_a2a_auth``), which maps the key to a ``user_id``.

Request size guard
------------------
``A2A_MAX_REQUEST_SIZE_KB`` environment variable sets the maximum body size for
``POST /api/v1/task-update`` (default: 512 KB).  Requests exceeding this limit
are rejected with HTTP 413.

Design Patterns
---------------
- Two-router split: Separating management routes from the inbound endpoint lets
  them carry different authentication schemes and prefixes cleanly.
- 202 Accepted for inbound: Task updates are published to the broker queue and
  processed asynchronously; callers should not assume synchronous completion.
- One-time key disclosure: Plaintext keys are returned only at registration or
  rotation and are never logged or re-transmitted.

Public API
----------
- a2a_router: ``APIRouter`` for agent key management.
- task_update_router: ``APIRouter`` for inbound task updates.

Dependencies
------------
- graphclaw.a2a.middleware: get_a2a_key_manager, require_a2a_auth.
- graphclaw.a2a.models: A2ARegistration, A2AUpdatePayload, A2AKeyRotateResponse.
- graphclaw.a2a.key_manager: A2AKeyManager.
- graphclaw.auth.middleware: get_current_user_id (platform JWT auth).
- graphclaw.gateway.deps: get_broker.
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.infra.broker: INBOUND_MESSAGES.
- fastapi: APIRouter, Depends, HTTPException, Request, status (third-party).
- datetime, json, logging, os, uuid: stdlib.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from graphclaw.a2a.key_manager import A2AKeyManager
from graphclaw.a2a.middleware import get_a2a_key_manager, require_a2a_auth
from graphclaw.a2a.models import (
    A2AKeyRotateResponse,
    A2ARegistration,
    A2AUpdatePayload,
)
from graphclaw.auth.middleware import get_current_user_id
from graphclaw.gateway.deps import get_broker
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_DEFAULT_MAX_REQUEST_KB = 512


def _max_request_bytes() -> int:
    """Return the configured maximum request body size in bytes."""
    raw = os.environ.get("A2A_MAX_REQUEST_SIZE_KB", str(_DEFAULT_MAX_REQUEST_KB))
    try:
        return int(raw) * 1024
    except ValueError:
        return _DEFAULT_MAX_REQUEST_KB * 1024


# ── Routers ────────────────────────────────────────────────────────────────────

a2a_router = APIRouter(prefix="/api/v1/a2a", tags=["a2a"])
task_update_router = APIRouter(tags=["a2a"])


# ── Agent management routes ────────────────────────────────────────────────────


@a2a_router.post(
    "/agents",
    status_code=201,
    summary="Register a new A2A agent",
    description=(
        "Creates a new agent registration and issues an API key in ``wg_agent_*`` "
        "format.  The plaintext key is returned **once** in this response — it is "
        "never stored or re-transmitted.  Store it securely immediately."
    ),
)
async def register_agent(
    registration: A2ARegistration,
    user_id: str = Depends(get_current_user_id),
    key_manager: A2AKeyManager = Depends(get_a2a_key_manager),
) -> dict[str, Any]:
    """Register a new agent and return its one-time API key.

    Parameters
    ----------
    registration:
        ``A2ARegistration`` payload from the request body.
    user_id:
        Platform user ID extracted from the Bearer access token.
    key_manager:
        Singleton ``A2AKeyManager`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"key_id": str, "plaintext_key": str, "agent_name": str}``.
        The ``plaintext_key`` is shown once and must be stored by the caller.

    Raises
    ------
    HTTPException(500):
        If the graph store is unavailable or the node cannot be created.
    """
    try:
        key_ref, plaintext = await key_manager.register_agent(user_id, registration)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "a2a/register_agent: failed for user_id=%s agent_name=%s: %s",
            user_id,
            registration.agent_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register agent — see server logs",
        ) from exc

    logger.info(
        "a2a/register_agent: registered key_id=%s agent_name=%s user_id=%s",
        key_ref.key_id,
        registration.agent_name,
        user_id,
    )
    return {
        "key_id": key_ref.key_id,
        "plaintext_key": plaintext,
        "agent_name": key_ref.agent_name,
    }


@a2a_router.post(
    "/agents/{key_id}/rotate",
    response_model=A2AKeyRotateResponse,
    summary="Rotate an A2A agent key",
    description=(
        "Generates a new API key for the specified agent registration and "
        "immediately invalidates the previous key.  The new plaintext key is "
        "returned **once** — store it securely."
    ),
)
async def rotate_agent_key(
    key_id: str,
    user_id: str = Depends(get_current_user_id),
    key_manager: A2AKeyManager = Depends(get_a2a_key_manager),
) -> A2AKeyRotateResponse:
    """Rotate the API key for an existing agent registration.

    Parameters
    ----------
    key_id:
        Resource node ID of the agent whose key should be rotated.
    user_id:
        Platform user ID extracted from the Bearer access token.
    key_manager:
        Singleton ``A2AKeyManager`` injected via ``Depends``.

    Returns
    -------
    A2AKeyRotateResponse:
        ``{"key_id", "plaintext_key", "rotated_at"}``.

    Raises
    ------
    HTTPException(404):
        If the agent key_id does not exist or does not belong to *user_id*.
    HTTPException(500):
        If the graph store update fails.
    """
    try:
        new_plaintext, _ = await key_manager.rotate_key(user_id, key_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("a2a/rotate_key: failed for key_id=%s user_id=%s: %s", key_id, user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rotate agent key — see server logs",
        ) from exc

    rotated_at = datetime.now(timezone.utc)
    logger.info("a2a/rotate_key: rotated key_id=%s user_id=%s", key_id, user_id)
    return A2AKeyRotateResponse(
        key_id=key_id,
        plaintext_key=new_plaintext,
        rotated_at=rotated_at,
    )


@a2a_router.delete(
    "/agents/{key_id}",
    status_code=204,
    summary="Revoke an A2A agent key",
    description=(
        "Clears the API key hash from the agent registration, immediately "
        "preventing any further authentication with that key.  The agent "
        "registration node is retained but can no longer authenticate requests."
    ),
)
async def revoke_agent_key(
    key_id: str,
    user_id: str = Depends(get_current_user_id),
    key_manager: A2AKeyManager = Depends(get_a2a_key_manager),
) -> None:
    """Revoke the API key for an agent registration.

    Parameters
    ----------
    key_id:
        Resource node ID of the agent whose key should be revoked.
    user_id:
        Platform user ID extracted from the Bearer access token.
    key_manager:
        Singleton ``A2AKeyManager`` injected via ``Depends``.

    Returns
    -------
    None:
        HTTP 204 No Content on success.

    Raises
    ------
    HTTPException(404):
        If the agent key_id does not exist or does not belong to *user_id*.
    HTTPException(500):
        If the graph store update fails.
    """
    try:
        await key_manager.revoke_key(user_id, key_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("a2a/revoke_key: failed for key_id=%s user_id=%s: %s", key_id, user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke agent key — see server logs",
        ) from exc

    logger.info("a2a/revoke_key: revoked key_id=%s user_id=%s", key_id, user_id)


@a2a_router.get(
    "/agents",
    summary="List registered A2A agents",
    description=(
        "Returns all active agent registrations for the authenticated user.  "
        "Only agents with an active (non-revoked) key are included.  "
        "Plaintext keys are never returned."
    ),
)
async def list_agents(
    user_id: str = Depends(get_current_user_id),
    key_manager: A2AKeyManager = Depends(get_a2a_key_manager),
) -> dict[str, Any]:
    """List all registered agents for the authenticated user.

    Parameters
    ----------
    user_id:
        Platform user ID extracted from the Bearer access token.
    key_manager:
        Singleton ``A2AKeyManager`` injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"agents": list[dict]}``.  Each dict contains
        ``key_id``, ``agent_name``, ``created_at``, ``resource_node_id``
        (no plaintext keys).
    """
    refs = await key_manager.list_agents(user_id)
    agents = [
        {
            "key_id": ref.key_id,
            "agent_name": ref.agent_name,
            "created_at": ref.created_at.isoformat(),
            "resource_node_id": ref.resource_node_id,
        }
        for ref in refs
    ]
    return {"agents": agents}


# ── Task-update inbound route ──────────────────────────────────────────────────


@task_update_router.post(
    "/api/v1/task-update",
    status_code=202,
    summary="A2A inbound task update",
    description=(
        "Main inbound endpoint for Agent-to-Agent task updates.  "
        "Authenticated via ``X-Agent-Api-Key`` header.  "
        "Body must be a JSON-RPC 2.0 envelope (``A2AUpdatePayload``).  "
        "The ``params`` dict is forwarded to the agent loop via the broker queue.  "
        "Returns HTTP 202 immediately; processing is asynchronous.  "
        "Requests exceeding ``A2A_MAX_REQUEST_SIZE_KB`` (default 512 KB) "
        "are rejected with HTTP 413."
    ),
)
async def task_update(
    request: Request,
    payload: A2AUpdatePayload,
    user_id: str = Depends(require_a2a_auth),
    broker: MessageBroker = Depends(get_broker),
) -> dict[str, str]:
    """Accept an A2A task update and publish it to the broker queue.

    Parameters
    ----------
    request:
        The raw FastAPI ``Request`` — used to check Content-Length for the
        size guard.
    payload:
        Parsed ``A2AUpdatePayload`` JSON-RPC 2.0 envelope.
    user_id:
        Platform user ID of the key owner, resolved by ``require_a2a_auth``.
    broker:
        ``MessageBroker`` singleton injected via ``Depends``.

    Returns
    -------
    dict:
        ``{"status": "accepted", "message_id": str}``.

    Raises
    ------
    HTTPException(413):
        If the request body exceeds ``A2A_MAX_REQUEST_SIZE_KB``.
    HTTPException(403):
        If the ``X-Agent-Api-Key`` header is missing or invalid (raised by
        ``require_a2a_auth`` before this handler runs).
    """
    # ── Request size guard ────────────────────────────────────────────────────
    content_length = request.headers.get("content-length")
    max_bytes = _max_request_bytes()
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(f"Request body exceeds maximum allowed size of {max_bytes // 1024} KB"),
                )
        except ValueError:
            pass  # Non-integer Content-Length — let the body parse naturally

    # ── Normalize to InboundMessage ───────────────────────────────────────────
    message_id = str(uuid.uuid4())
    session_id = f"SES-{uuid.uuid4()}"

    inbound = InboundMessage(
        message_id=message_id,
        channel="a2a",
        sender=user_id,
        subject=payload.method,
        body=json.dumps(payload.params),
        received_at=datetime.now(timezone.utc),
        session_id=session_id,
        raw_headers={
            "x-jsonrpc-method": payload.method,
            **({"x-jsonrpc-id": payload.id} if payload.id else {}),
        },
    )

    # ── Publish to broker ─────────────────────────────────────────────────────
    await broker.publish(INBOUND_MESSAGES, inbound.model_dump_json())

    logger.info(
        "a2a/task-update: published message_id=%s method=%s user_id=%s session_id=%s",
        message_id,
        payload.method,
        user_id,
        session_id,
        extra={
            "message_id": message_id,
            "channel": "a2a",
            "session_id": session_id,
            "method": payload.method,
        },
    )

    return {"status": "accepted", "message_id": message_id}
