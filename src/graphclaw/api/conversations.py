# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.api.conversations — Counterparty conversation REST endpoints.

Description
-----------
Provides REST endpoints for listing and reading per-counterparty conversation
threads stored in MinIO (FR-STORE-001, FR-UI-001).

Endpoints
---------
- ``GET  /app/v1/conversations``
    List all counterparties with whom the authenticated user has at least one
    thread.  Returns summary from the conversation index JSON.
- ``GET  /app/v1/conversations/{counterparty_id}``
    List all channels/threads for one counterparty.
- ``GET  /app/v1/conversations/{counterparty_id}/{channel}/{thread_id}``
    Read the JSONL messages in a single thread.  Returns messages newest-first
    when ``reverse=true`` (default) or oldest-first when ``reverse=false``.

All endpoints require a valid Bearer access token.  Users can only access
their own conversations (user_id scoped by JWT).

Design Patterns
---------------
- StorageClient reads: the index JSON is a single object; each thread is a
  JSONL file read as lines.
- Graceful degradation: missing index or missing thread file → empty list,
  not a 404.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import CurrentUserDep, StorageClientDep
from graphclaw.infra.storage import StoragePaths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CounterpartySummary(BaseModel):
    """One entry from the conversation index."""

    counterparty_id: str
    last_activity_at: str | None = None
    channels: list[str] = []
    thread_count: int = 0


class ThreadSummary(BaseModel):
    """A single channel + thread reference for a counterparty."""

    channel: str
    thread_id: str
    message_count: int = 0
    last_message_at: str | None = None


class ConversationMessage(BaseModel):
    """A single message in a conversation JSONL thread."""

    direction: str  # "out" | "in"
    role: str  # "agent" | "counterparty" | "system"
    content: str
    timestamp: str | None = None
    task_id: str | None = None
    channel: str | None = None
    counterparty_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_index(user_id: str, storage) -> dict[str, Any]:
    """Load the conversation index JSON; return {} when absent."""
    path = StoragePaths.conversation_index(user_id)
    try:
        raw = await storage.read(path)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        return json.loads(text)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("conversations: index missing or unreadable: %s", exc)
        return {}


async def _load_thread_messages(
    user_id: str,
    counterparty_id: str,
    channel: str,
    thread_id: str,
    storage,
) -> list[dict[str, Any]]:
    """Load JSONL thread; return [] when absent."""
    path = StoragePaths.conversation_thread(user_id, counterparty_id, channel, thread_id)
    try:
        raw = await storage.read(path)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        msgs = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return msgs
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "conversations: thread %s/%s/%s/%s unreadable: %s",
            user_id,
            counterparty_id,
            channel,
            thread_id,
            exc,
        )
        return []


async def _list_counterparty_threads(
    user_id: str, counterparty_id: str, storage
) -> list[ThreadSummary]:
    """List JSONL files under {user_id}/conversations/{counterparty_id}/."""
    prefix = StoragePaths.conversation_counterparty_dir(user_id, counterparty_id)
    try:
        keys: list[str] = await storage.list_objects(prefix)
    except Exception as exc:  # noqa: BLE001
        logger.debug("conversations: list_keys failed for %s: %s", prefix, exc)
        return []

    summaries: list[ThreadSummary] = []
    for key in keys:
        # Key format: {user_id}/conversations/{counterparty_id}/{channel}/{thread_id}.jsonl
        rel = key.removeprefix(prefix)
        parts = rel.rstrip("/").split("/")
        if len(parts) != 2:
            continue
        channel, thread_file = parts
        thread_id = thread_file.removesuffix(".jsonl")
        summaries.append(ThreadSummary(channel=channel, thread_id=thread_id))
    return summaries


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[CounterpartySummary],
    status_code=status.HTTP_200_OK,
    summary="List counterparties",
    description=(
        "Return all counterparties the authenticated user has conversation threads with. "
        "Reads the conversation index JSON from MinIO. (FR-UI-001)"
    ),
)
async def list_counterparties(
    user_id: CurrentUserDep,
    storage: StorageClientDep,
) -> list[CounterpartySummary]:
    """List counterparties from the conversation index."""
    index = await _load_index(user_id, storage)
    result: list[CounterpartySummary] = []
    for cp_id, info in index.items():
        if not isinstance(info, dict):
            continue
        result.append(
            CounterpartySummary(
                counterparty_id=cp_id,
                last_activity_at=info.get("last_activity_at"),
                channels=info.get("channels", []),
                thread_count=info.get("thread_count", 0),
            )
        )
    # Sort by last_activity_at descending (most recent first), nulls last.
    result.sort(key=lambda x: x.last_activity_at or "", reverse=True)
    return result


@router.get(
    "/{counterparty_id}",
    response_model=list[ThreadSummary],
    status_code=status.HTTP_200_OK,
    summary="List threads for a counterparty",
    description=("List all channel/thread references for one counterparty. (FR-UI-001)"),
)
async def list_threads(
    counterparty_id: str,
    user_id: CurrentUserDep,
    storage: StorageClientDep,
) -> list[ThreadSummary]:
    """List threads for counterparty_id."""
    return await _list_counterparty_threads(user_id, counterparty_id, storage)


@router.get(
    "/{counterparty_id}/{channel}/{thread_id}",
    response_model=list[ConversationMessage],
    status_code=status.HTTP_200_OK,
    summary="Read conversation thread",
    description=(
        "Return messages in a single counterparty thread. "
        "Set reverse=true (default) to get newest messages first. (FR-UI-001)"
    ),
)
async def read_thread(
    counterparty_id: str,
    channel: str,
    thread_id: str,
    user_id: CurrentUserDep,
    storage: StorageClientDep,
    reverse: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConversationMessage]:
    """Read messages from a JSONL conversation thread."""
    raw_msgs = await _load_thread_messages(user_id, counterparty_id, channel, thread_id, storage)
    if reverse:
        raw_msgs = list(reversed(raw_msgs))
    raw_msgs = raw_msgs[:limit]

    msgs: list[ConversationMessage] = []
    for m in raw_msgs:
        msgs.append(
            ConversationMessage(
                direction=m.get("direction", "out"),
                role=m.get("role", "agent"),
                content=m.get("content", m.get("text", "")),
                timestamp=m.get("timestamp") or m.get("sent_at"),
                task_id=m.get("task_id"),
                channel=m.get("channel", channel),
                counterparty_id=m.get("counterparty_id", counterparty_id),
            )
        )
    return msgs
