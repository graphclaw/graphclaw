# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.notifications.service — NotificationService for CRUD on notifications table."""

from __future__ import annotations

import json
import logging
from typing import Any

from graphclaw.db.age.connection import get_connection

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 100


class NotificationService:
    """All notification persistence operations against the notifications table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def create(
        self,
        user_id: str,
        event_type: str,
        title: str,
        body: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new notification row; return the new UUID as a string."""
        meta_json = json.dumps(metadata or {})
        async with get_connection(self._pool) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO notifications (user_id, event_type, title, body, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING id::text
                """,
                (user_id, event_type, title, body, meta_json),
            )
            row = await cursor.fetchone()
        return row[0]  # type: ignore[index]

    async def list_for_user(
        self,
        user_id: str,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Return (items, unread_count, next_cursor). Keyset-paginated on created_at DESC."""
        limit = min(limit, _MAX_LIMIT)
        async with get_connection(self._pool) as conn:
            if cursor:
                cur = await conn.execute(
                    """
                    SELECT id::text, event_type, title, body, metadata,
                           is_read, read_at, created_at
                    FROM notifications
                    WHERE user_id = %s
                      AND dismissed_at IS NULL
                      AND created_at < %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, cursor, limit + 1),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT id::text, event_type, title, body, metadata,
                           is_read, read_at, created_at
                    FROM notifications
                    WHERE user_id = %s
                      AND dismissed_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit + 1),
                )
            rows = await cur.fetchall()

            unread_cur = await conn.execute(
                """
                SELECT COUNT(*) FROM notifications
                WHERE user_id = %s AND is_read = FALSE AND dismissed_at IS NULL
                """,
                (user_id,),
            )
            unread_row = await unread_cur.fetchone()

        has_more = len(rows) > limit
        page = rows[:limit]

        next_cursor: str | None = None
        if has_more:
            last_ts = page[-1][7]  # created_at column
            next_cursor = last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts)

        col_names = [
            "id",
            "event_type",
            "title",
            "body",
            "metadata",
            "is_read",
            "read_at",
            "created_at",
        ]
        items: list[dict[str, Any]] = []
        for row in page:
            item = dict(zip(col_names, row))
            if isinstance(item.get("metadata"), str):
                item["metadata"] = json.loads(item["metadata"])
            items.append(item)

        unread_count = int(unread_row[0]) if unread_row else 0
        return items, unread_count, next_cursor

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        """Mark one notification read. Returns True if a row was updated."""
        async with get_connection(self._pool) as conn:
            cur = await conn.execute(
                """
                UPDATE notifications
                SET is_read = TRUE, read_at = NOW()
                WHERE id = %s::uuid AND user_id = %s AND dismissed_at IS NULL
                """,
                (notification_id, user_id),
            )
        return cur.rowcount == 1

    async def mark_all_read(self, user_id: str) -> int:
        """Mark all unread notifications read; return count updated."""
        async with get_connection(self._pool) as conn:
            cur = await conn.execute(
                """
                UPDATE notifications
                SET is_read = TRUE, read_at = NOW()
                WHERE user_id = %s AND is_read = FALSE AND dismissed_at IS NULL
                """,
                (user_id,),
            )
        return cur.rowcount

    async def dismiss(self, notification_id: str, user_id: str) -> bool:
        """Soft-delete a notification. Returns True if a row was updated."""
        async with get_connection(self._pool) as conn:
            cur = await conn.execute(
                """
                UPDATE notifications
                SET dismissed_at = NOW()
                WHERE id = %s::uuid AND user_id = %s AND dismissed_at IS NULL
                """,
                (notification_id, user_id),
            )
        return cur.rowcount == 1

    async def unread_count(self, user_id: str) -> int:
        """Return the current unread notification count for a user."""
        async with get_connection(self._pool) as conn:
            cur = await conn.execute(
                """
                SELECT COUNT(*) FROM notifications
                WHERE user_id = %s AND is_read = FALSE AND dismissed_at IS NULL
                """,
                (user_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
