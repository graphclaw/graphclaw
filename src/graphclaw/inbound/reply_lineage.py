"""graphclaw.inbound.reply_lineage — Reply thread lineage tracker (FR-RES-002).

Description
-----------
Tracks the chain of inbound/outbound messages for a task conversation, enabling
accurate reply-key matching and thread reconstruction.  Uses the ``reply_keys``
Postgres table (migration 0017) as the persistent store.

Design Patterns
---------------
- Repository: ``ReplyLineageTracker`` is a thin async repo over ``reply_keys``.
- Graceful degradation: Any DB failure returns None/[] — never breaks message flow.

Public API
----------
- LineageRecord: A single message's lineage metadata.
- ReplyLineageTracker: Track and query reply threads.
- ReplyLineageTracker.record(message_id, task_id, direction, parent_id): Store.
- ReplyLineageTracker.find_task_id(message_id): Reverse-lookup by message_id.
- ReplyLineageTracker.get_thread(task_id, limit): Get ordered thread for a task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class LineageRecord:
    """A single message's lineage metadata."""

    message_id: str
    task_id: str
    direction: str  # "inbound" | "outbound"
    parent_message_id: str | None
    channel: str
    recorded_at: datetime


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class ReplyLineageTracker:
    """Persist and query reply-thread lineage (FR-RES-002).

    Parameters
    ----------
    pool:
        Async DB pool (asyncpg-style).
    """

    def __init__(self, pool: Any | None = None) -> None:
        self._pool = pool

    async def record(
        self,
        message_id: str,
        task_id: str,
        direction: str,
        *,
        parent_message_id: str | None = None,
        channel: str = "unknown",
    ) -> None:
        """Record a message's lineage.

        Duplicates (same ``message_id``) are silently ignored.
        """
        if self._pool is None:
            return
        sql = """
            INSERT INTO reply_keys
                (key, task_id, direction, parent_key, channel, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (key) DO NOTHING
        """
        try:
            await self._pool.execute(
                sql,
                message_id,
                task_id,
                direction,
                parent_message_id,
                channel,
                datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reply_lineage.record_failed: %s", exc)

    async def find_task_id(self, message_id: str) -> str | None:
        """Look up the task_id for a given reply ``message_id``.

        Returns ``None`` when not found (graceful degradation).
        """
        if self._pool is None:
            return None
        sql = "SELECT task_id FROM reply_keys WHERE key = $1"
        try:
            rows = await self._pool.fetch(sql, message_id)
            return rows[0]["task_id"] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("reply_lineage.find_task_id_failed: %s", exc)
            return None

    async def get_thread(self, task_id: str, *, limit: int = 100) -> list[LineageRecord]:
        """Return the ordered message thread for a task."""
        if self._pool is None:
            return []
        sql = """
            SELECT key, task_id, direction, parent_key, channel, created_at
            FROM reply_keys
            WHERE task_id = $1
            ORDER BY created_at ASC
            LIMIT $2
        """
        try:
            rows = await self._pool.fetch(sql, task_id, limit)
            return [
                LineageRecord(
                    message_id=r["key"],
                    task_id=r["task_id"],
                    direction=r.get("direction") or "unknown",
                    parent_message_id=r.get("parent_key"),
                    channel=r.get("channel") or "unknown",
                    recorded_at=r.get("created_at") or datetime.now(timezone.utc),
                )
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("reply_lineage.get_thread_failed: %s", exc)
            return []
