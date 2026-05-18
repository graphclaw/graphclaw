# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.notifications.emit — Fire-and-forget notification + SSE publisher."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_CHANNEL_PREFIX = "graphclaw:events:"


async def emit_notification(
    pool: Any | None,
    redis: Any | None,
    user_id: str,
    event_type: str,
    title: str,
    body: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Create a notification row and publish a notification.new SSE event.

    Never raises — logs and swallows on any error so callers are never blocked.
    Safe to call with pool=None or redis=None (no-op for that leg).
    """
    from graphclaw.notifications.service import NotificationService  # noqa: PLC0415

    try:
        if pool is not None:
            svc = NotificationService(pool)
            await svc.create(user_id, event_type, title, body, metadata)
            unread = await svc.unread_count(user_id)
        else:
            unread = 0

        if redis is not None:
            channel = f"{_REDIS_CHANNEL_PREFIX}{user_id}"
            payload = json.dumps(
                {"event_type": "notification.new", "unread_count": unread},
                default=str,
            )
            await redis.publish(channel, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notifications.emit: failed user_id=%s event_type=%s: %s",
            user_id,
            event_type,
            exc,
        )
