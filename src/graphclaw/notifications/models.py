# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.notifications.models — Pydantic schemas for the notification system."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationEventType:
    TASK_NEEDS_ATTENTION = "task.needs_attention"
    APPROVAL_PENDING = "approval.pending"
    BRIEFING_READY = "briefing.ready"
    AGENT_RUN_COMPLETED = "agent.run_completed"
    AGENT_RUN_FAILED = "agent.run_failed"
    INBOUND_UNROUTED = "inbound.unrouted"
    TASK_CASCADE_COMPLETE = "task.cascade_complete"


class NotificationItem(BaseModel):
    id: str
    event_type: str
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    next_cursor: str | None = None


class MarkReadResponse(BaseModel):
    id: str
    ok: bool = True


class ReadAllResponse(BaseModel):
    updated: int
    ok: bool = True


class DismissResponse(BaseModel):
    id: str
    ok: bool = True
