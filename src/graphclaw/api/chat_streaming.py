# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.chat_streaming — Helpers for the streaming chat endpoint.

Description
-----------
Provides ``sse_frame`` for SSE formatting and ``stream_chat_run`` as the
async generator that bridges ``MainOrchestrator.process_chat_message_stream`` to an
HTTP ``text/event-stream`` response body.

After the run completes the helper persists both the user message and the
final assistant text to the JSON chat history in MinIO so that the regular
``GET /app/v1/chat/messages`` endpoint remains consistent.

Design Patterns
---------------
- Generator pipeline: ``stream_chat_run`` is an async generator that consumes
  the upstream ``AgentRunEvent`` stream and yields formatted SSE frame strings.
- Separation of concerns: SSE framing logic is isolated here so ``chat.py``
  stays thin.

Public API
----------
- sse_frame: Format one SSE event frame string.
- stream_chat_run: Async generator → SSE frame strings.

Dependencies
------------
- graphclaw.agent.main_orchestrator: MainOrchestrator (TYPE_CHECKING).
- graphclaw.agent.run_events: RunEventType (for terminal detection).
- graphclaw.api.chat: _load_history, _save_history, _HISTORY_MAX, ChatMessage.
- fastapi.responses: StreamingResponse (not imported here, used by caller).
- datetime: UTC timestamps (stdlib).
- json: serialisation (stdlib).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from graphclaw.agent.run_events import RunEventType

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from graphclaw.agent.main_orchestrator import MainOrchestrator
    from graphclaw.infra.storage import StorageClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------


def sse_frame(event_type: str, data: dict[str, Any]) -> str:
    """Return a properly terminated SSE frame string.

    Parameters
    ----------
    event_type:
        The SSE ``event:`` field (e.g. ``"assistant.delta"``).
    data:
        JSON-serialisable payload dict.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


def sse_comment(text: str = "keepalive") -> str:
    """Return an SSE comment frame (invisible to EventSource, keeps connection alive)."""
    return f": {text}\n\n"


# ---------------------------------------------------------------------------
# Main streaming helper
# ---------------------------------------------------------------------------


async def stream_chat_run(
    agent_loop: MainOrchestrator,
    storage_client: StorageClient,
    user_id: str,
    text: str,
    history: list[dict[str, Any]],
    session_id: str,
    org_id: str = "default",
) -> AsyncGenerator[str, None]:
    """Async generator that streams SSE frames for one chat run.

    Yields formatted SSE frame strings.  After the run ends with a terminal
    event (``run.completed`` or ``run.failed``), the user message and the
    assembled assistant reply are persisted to MinIO chat history.

    Parameters
    ----------
    agent_loop:
        Configured ``AgentLoop`` instance.
    storage_client:
        ``StorageClient`` for persisting chat history.
    user_id:
        Authenticated user identifier.
    text:
        Incoming user message text.
    history:
        Current chat history list (will be mutated and saved after run).
    session_id:
        Conversation session identifier.
    """
    from graphclaw.api.chat import ChatMessage, _save_history  # noqa: PLC0415

    # Build user message entry for history
    now_str = datetime.now(timezone.utc).isoformat()
    msg_index = len(history)
    user_msg = ChatMessage(
        message_id=f"msg-{msg_index:06d}-u",
        role="user",
        content=text,
        timestamp=now_str,
    )
    history.append(user_msg.model_dump())

    accumulated_content = ""
    terminal_event_type: str | None = None

    _TERMINAL = {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED}

    try:
        async for event in agent_loop.process_chat_message_stream(
            user_id=user_id,
            text=text,
            conversation_history=history[:-1],  # exclude the just-added user msg
            session_id=session_id,
            org_id=org_id,
        ):
            # Accumulate assistant text
            if event.event_type == RunEventType.ASSISTANT_DELTA:
                payload = event.payload
                if hasattr(payload, "delta"):
                    accumulated_content += payload.delta  # type: ignore[union-attr]

            if event.event_type in _TERMINAL:
                terminal_event_type = event.event_type

            # Yield SSE frame
            yield sse_frame(event.event_type, event.model_dump(mode="json"))

    except Exception as exc:  # noqa: BLE001
        logger.exception("stream_chat_run: error for user_id=%s", user_id)
        error_payload = {
            "error_class": type(exc).__name__,
            "error_message": str(exc)[:200],
        }
        yield sse_frame("run.failed", error_payload)

    finally:
        # Persist to history regardless of success/failure
        if accumulated_content:
            agent_msg = ChatMessage(
                message_id=f"msg-{msg_index:06d}-a",
                role="agent",
                content=accumulated_content,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            history.append(agent_msg.model_dump())
            try:
                await _save_history(user_id, storage_client, history)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stream_chat_run: history save failed for user_id=%s: %s", user_id, exc
                )
