# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
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
``{user_id}/chat/history.json``.  Each entry records the human message,
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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_HISTORY_MAX = 200  # keep only the last N messages
_LLM_NOT_CONFIGURED_MESSAGE = (
    "LLM is not configured for this environment. "
    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY (or configure cloud secrets) and restart the service."
)

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _history_path(user_id: str) -> str:
    return StoragePaths.chat_history(user_id)


async def _load_history(user_id: str, storage_client) -> list[dict[str, Any]]:
    try:
        raw = await storage_client.read(_history_path(user_id))
        return json.loads(raw.decode())
    except Exception as exc:  # noqa: BLE001 — treat any storage failure as empty history
        logger.warning("chat: failed to load history for user_id=%s: %s", user_id, exc)
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


class ChatRuntimeResponse(BaseModel):
    """Runtime LLM metadata used by the chat orchestrator."""

    provider: str = "unknown"
    model: str = "unknown"
    connected: bool = False


def _provider_from_llm_client(llm_client: Any | None) -> str:
    """Infer provider name from the configured LLM client instance."""
    if llm_client is None:
        return "unavailable"

    class_name = llm_client.__class__.__name__.lower()
    if "anthropic" in class_name:
        return "anthropic"
    if "openai" in class_name:
        return "openai"
    if "litellm" in class_name:
        return "litellm"

    normalized = class_name.removesuffix("llmclient")
    return normalized or "unknown"


def _model_from_llm_client(llm_client: Any | None) -> str:
    """Best-effort model extraction from known LLM client implementations."""
    if llm_client is None:
        return "unknown"

    value = getattr(llm_client, "_default_model", None)
    if isinstance(value, str) and value.strip():
        return value
    return "unknown"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/runtime",
    response_model=ChatRuntimeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get chat runtime LLM metadata",
    description=(
        "Return the active LLM provider/model currently bound to the chat "
        "orchestrator in this process."
    ),
)
async def get_chat_runtime(request: Request) -> ChatRuntimeResponse:
    """Return runtime LLM metadata for the chat orchestrator."""
    agent_loop = getattr(request.app.state, "agent_loop", None)
    llm_client = getattr(agent_loop, "llm_client", None) if agent_loop is not None else None

    return ChatRuntimeResponse(
        provider=_provider_from_llm_client(llm_client),
        model=_model_from_llm_client(llm_client),
        connected=llm_client is not None,
    )


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
    # Without a cursor, return the most recent `limit` messages.
    # With a cursor (integer offset from the start), return from that offset — used
    # to page backwards through older messages.
    if cursor and cursor.isdigit():
        start = int(cursor)
    else:
        start = max(0, len(history) - limit)
    page = history[start : start + limit]
    prev_cursor = str(max(0, start - limit)) if start > 0 else None
    return ChatHistoryResponse(
        messages=[ChatMessage(**m) for m in page],
        next_cursor=prev_cursor,
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

    # Generate agent response — pass loaded history and session_id
    session_id = f"ses-{msg_index:06d}"
    agent_text = await _generate_agent_response(
        request,
        user_id,
        body.content,
        history=history[:-1],  # exclude the current user message; user_text carries it
        session_id=session_id,
    )
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


async def _generate_agent_response(
    request: Request,
    user_id: str,
    user_text: str,
    history: list | None = None,
    session_id: str | None = None,
) -> str:
    """Try to get an AI response from AgentLoop; fall back to a placeholder."""
    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is not None and hasattr(agent_loop, "process_chat_message"):
        if getattr(agent_loop, "llm_client", None) is None:
            return _LLM_NOT_CONFIGURED_MESSAGE
        try:
            org_id: str = getattr(request.state, "org_id", None) or request.headers.get(
                "X-Org-Id", "default"
            )
            return await agent_loop.process_chat_message(
                user_id,
                user_text,
                conversation_history=history,
                session_id=session_id,
                org_id=org_id,
            )
        except Exception as exc:
            logger.warning("chat: agent_loop.process_chat_message failed: %s", exc)

    # Graceful fallback — agent not yet wired or unavailable
    return (
        "I received your message. The agent loop is not yet connected in this "
        "environment. Once the backend is fully initialised I will be able to "
        "analyse your task graph and respond here."
    )


@router.post(
    "/messages/stream",
    status_code=status.HTTP_200_OK,
    summary="Send a chat message and stream transparency events",
    description=(
        "Send a natural-language message and receive a ``text/event-stream`` "
        "response.  Each SSE frame carries one ``AgentRunEvent`` (delta text, "
        "tool calls, plan steps, etc.).  The run always ends with a "
        "``run.completed`` or ``run.failed`` terminal event.  Chat history is "
        "persisted after the terminal event."
    ),
)
async def send_chat_message_stream(
    body: ChatMessageRequest,
    request: Request,
    user_id: CurrentUserDep,
    storage_client: StorageClientDep,
) -> StreamingResponse:
    """Stream agent transparency events for one chat turn."""
    history = await _load_history(user_id, storage_client)
    msg_index = len(history)
    session_id = f"ses-{msg_index:06d}-stream"

    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is None or not hasattr(agent_loop, "process_chat_message_stream"):
        # Fallback: emit a single run.failed SSE frame
        import json as _json  # noqa: PLC0415

        payload = _json.dumps(
            {
                "event_type": "run.failed",
                "payload": {
                    "schema_version": "1.0",
                    "error_class": "NotInitialised",
                    "error_message": "Agent loop is not available in this environment.",
                    "duration_ms": 0,
                },
            }
        )
        fallback = f"event: run.failed\ndata: {payload}\n\n"
        return StreamingResponse(
            iter([fallback]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if getattr(agent_loop, "llm_client", None) is None:
        import json as _json  # noqa: PLC0415

        payload = _json.dumps(
            {
                "event_type": "run.failed",
                "payload": {
                    "schema_version": "1.0",
                    "error_class": "LLMNotConfigured",
                    "error_message": _LLM_NOT_CONFIGURED_MESSAGE,
                    "duration_ms": 0,
                },
            }
        )
        fallback = f"event: run.failed\ndata: {payload}\n\n"
        return StreamingResponse(
            iter([fallback]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    from graphclaw.api.chat_streaming import stream_chat_run  # noqa: PLC0415

    org_id_stream: str = getattr(request.state, "org_id", None) or request.headers.get(
        "X-Org-Id", "default"
    )
    generator = stream_chat_run(
        agent_loop=agent_loop,
        storage_client=storage_client,
        user_id=user_id,
        text=body.content,
        history=history,
        session_id=session_id,
        org_id=org_id_stream,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
