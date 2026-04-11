"""graphclaw.api.chat — Conversational agent chat endpoints.

Description
-----------
Provides REST endpoints for the cockpit chat interface, allowing users to
send natural-language messages to the GraphClaw agent and retrieve conversation
history.

Endpoints
---------
- ``GET  /app/v1/chat/messages``    — Retrieve recent chat history.
- ``POST /app/v1/chat/messages``    — Send a message and receive an agent response.
- ``DELETE /app/v1/chat/messages``  — Clear the authenticated user's chat history.

All endpoints require a valid Bearer access token.

Storage layout
--------------
Chat history is persisted via ``StorageClient`` at
``agents/{user_id}/chat_history.json``.  Each entry records the human message,
the agent's response, and a UTC timestamp.

Agent integration
-----------------
When ``app.state.agent_loop`` is set, messages are forwarded to
``AgentLoop.process_chat_message(user_id, text)`` for AI-generated responses.
If the agent loop is not available (dev environments) or the call fails, a
graceful placeholder response is returned so the UI remains functional.

Design Patterns
---------------
- StorageClient persistence: History is a JSON array appended on each POST,
  capped at the last 200 messages to bound storage growth.
- Graceful degradation: Missing agent loop → informative placeholder response;
  no hard dependency on LLM availability.
- Cursor pagination: GET supports ``limit`` and ``cursor`` (integer offset)
  consistent with the rest of the cockpit API.

Public API
----------
- router: ``APIRouter`` for /chat routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep, StorageClientDep.
- fastapi: APIRouter, status (third-party).
- pydantic: BaseModel (third-party).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_HISTORY_MAX = 200  # keep only the last N messages

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _history_path(user_id: str) -> str:
    return f"agents/{user_id}/chat_history.json"


async def _load_history(user_id: str, storage_client) -> list[dict[str, Any]]:
    try:
        raw = await storage_client.read(_history_path(user_id))
        return json.loads(raw.decode())
    except FileNotFoundError:
        return []


async def _save_history(user_id: str, storage_client, history: list[dict[str, Any]]) -> None:
    raw = json.dumps(history[-_HISTORY_MAX:], default=str).encode()
    await storage_client.write(_history_path(user_id), raw, content_type="application/json")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single exchange in the chat history."""

    message_id: str
    role: str  # "user" | "agent"
    content: str
    timestamp: str


class ChatMessageRequest(BaseModel):
    """Request body for POST /app/v1/chat/messages."""

    content: str


class ChatHistoryResponse(BaseModel):
    """Paginated chat history response."""

    messages: list[ChatMessage]
    next_cursor: str | None = None


class ChatResponse(BaseModel):
    """Response to a sent message — contains both the echo and the agent reply."""

    user_message: ChatMessage
    agent_message: ChatMessage


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/messages",
    response_model=ChatHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get chat history",
    description=(
        "Return recent chat messages for the authenticated user, ordered "
        "oldest-first.  Supports cursor-based pagination."
    ),
)
async def get_chat_history(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
    limit: int = Query(default=50, ge=1, le=200, description="Maximum messages to return"),
    cursor: str | None = Query(default=None, description="Opaque pagination cursor"),
) -> ChatHistoryResponse:
    """Return paginated chat history for the authenticated user."""
    history = await _load_history(user_id, storage_client)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = history[start: start + limit]
    next_cursor = str(start + limit) if start + limit < len(history) else None
    return ChatHistoryResponse(
        messages=[ChatMessage(**m) for m in page],
        next_cursor=next_cursor,
    )


@router.post(
    "/messages",
    response_model=ChatResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a chat message",
    description=(
        "Send a natural-language message to the GraphClaw agent and receive a "
        "response.  The message and response are appended to the user's chat history."
    ),
)
async def send_chat_message(
    body: ChatMessageRequest,
    request: Request,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> ChatResponse:
    """Send a message and get an agent response."""
    now = datetime.now(timezone.utc).isoformat()
    history = await _load_history(user_id, storage_client)

    # Build user message entry
    msg_index = len(history)
    user_message = ChatMessage(
        message_id=f"msg-{msg_index:06d}-u",
        role="user",
        content=body.content,
        timestamp=now,
    )
    history.append(user_message.model_dump())

    # Generate agent response
    agent_text = await _generate_agent_response(request, user_id, body.content)
    agent_message = ChatMessage(
        message_id=f"msg-{msg_index:06d}-a",
        role="agent",
        content=agent_text,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    history.append(agent_message.model_dump())
    await _save_history(user_id, storage_client, history)

    return ChatResponse(user_message=user_message, agent_message=agent_message)


@router.delete(
    "/messages",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear chat history",
    description="Delete all chat messages for the authenticated user.",
)
async def clear_chat_history(
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> None:
    """Clear the authenticated user's chat history."""
    await storage_client.delete(_history_path(user_id))
    logger.info("chat: history cleared for user_id=%s", user_id)


# ---------------------------------------------------------------------------
# Agent response helper
# ---------------------------------------------------------------------------


async def _generate_agent_response(request: Request, user_id: str, user_text: str) -> str:
    """Try to get an AI response from AgentLoop; fall back to a placeholder."""
    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is not None and hasattr(agent_loop, "process_chat_message"):
        try:
            return await agent_loop.process_chat_message(user_id, user_text)
        except Exception as exc:
            logger.warning("chat: agent_loop.process_chat_message failed: %s", exc)

    # Graceful fallback — agent not yet wired or unavailable
    return (
        "I received your message. The agent loop is not yet connected in this "
        "environment. Once the backend is fully initialised I will be able to "
        "analyse your task graph and respond here."
    )
