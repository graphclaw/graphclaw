# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.distillation.outbox — Idempotent distillation outbox (FR-RES-001).

Description
-----------
Distillation writes (intelligence lines, working memory notes) go through this
outbox for idempotency and retry semantics.  The outbox table
(``distillation_outbox``, migration 0022) stores pending writes with a
UNIQUE constraint on ``(message_id, target_node_id, target_type)`` to prevent
duplicates regardless of retry count.

Design Patterns
---------------
- Outbox pattern: All distillation writes enqueued here; a worker processes them.
- Idempotency key: ``(message_id, target_node_id, target_type)`` prevents double-writes.
- Graceful degradation: Enqueue failure is logged but does not block the chat reply.

Public API
----------
- DistillationWrite: Payload model.
- DistillationOutbox: Async outbox for enqueue + pending query.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Payload model
# ---------------------------------------------------------------------------


@dataclass
class DistillationWrite:
    """A pending distillation write.

    Attributes
    ----------
    message_id:
        Idempotency key — usually the chat turn's message_id or session_id.
    target_node_id:
        Node whose intelligence or working memory gets the write.
    target_type:
        ``"intelligence"`` | ``"memory_note"``.
    payload:
        Data to write (e.g. ``{"line": "[2026-05-03] cockpit | in | …"}``).
    id:
        Auto-generated UUID row key.
    created_at:
        When the entry was enqueued.
    processed_at:
        Set when the write succeeds.
    retry_count:
        Number of retry attempts made so far.
    error_detail:
        Last error string if any retry failed.
    """

    message_id: str
    target_node_id: str
    target_type: str  # "intelligence" | "memory_note"
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: datetime | None = None
    retry_count: int = 0
    error_detail: str | None = None


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


class DistillationOutbox:
    """Idempotent distillation write outbox (FR-RES-001).

    Parameters
    ----------
    pool:
        Async DB pool with ``execute`` / ``fetch`` (asyncpg-style).
        When ``None``, operates in-memory only (test mode).
    """

    def __init__(self, pool: Any | None = None) -> None:
        self._pool = pool
        self._memory: list[DistillationWrite] = []

    async def enqueue(self, write: DistillationWrite) -> bool:
        """Enqueue a distillation write.

        Returns ``True`` when newly inserted; ``False`` when duplicate (idempotent).
        Enqueue failure is caught and logged — must never block a chat reply.
        """
        self._memory.append(write)
        if self._pool is None:
            return True
        import json  # noqa: PLC0415

        sql = """
            INSERT INTO distillation_outbox
                (id, message_id, target_node_id, target_type, payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id, target_node_id, target_type) DO NOTHING
            RETURNING id
        """
        try:
            rows = await self._pool.fetch(
                sql,
                write.id,
                write.message_id,
                write.target_node_id,
                write.target_type,
                json.dumps(write.payload),
                write.created_at,
            )
            return bool(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("distillation_outbox.enqueue_failed: %s", exc)
            return True  # In-memory copy preserved

    async def list_pending(self, limit: int = 100) -> list[DistillationWrite]:
        """Return pending (unprocessed) entries, oldest first."""
        if self._pool is None:
            return [w for w in self._memory if w.processed_at is None]

        sql = """
            SELECT id, message_id, target_node_id, target_type, payload,
                   created_at, processed_at, retry_count, error_detail
            FROM distillation_outbox
            WHERE processed_at IS NULL
            ORDER BY created_at ASC
            LIMIT %s
        """
        try:
            rows = await self._pool.fetch(sql, limit)
            return [self._row_to_write(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("distillation_outbox.list_pending_failed: %s", exc)
            return []

    async def mark_processed(self, entry_id: str) -> None:
        """Mark an entry as successfully processed."""
        now = datetime.now(timezone.utc)
        # Update in-memory
        for w in self._memory:
            if w.id == entry_id:
                w.processed_at = now
                break

        if self._pool is None:
            return
        sql = "UPDATE distillation_outbox SET processed_at = %s WHERE id = %s"
        try:
            await self._pool.execute(sql, now, entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("distillation_outbox.mark_processed_failed: %s", exc)

    async def mark_failed(self, entry_id: str, error: str) -> None:
        """Increment retry_count and record the last error."""
        for w in self._memory:
            if w.id == entry_id:
                w.retry_count += 1
                w.error_detail = error
                break

        if self._pool is None:
            return
        sql = """
            UPDATE distillation_outbox
               SET retry_count  = retry_count + 1,
                   error_detail = %s
             WHERE id = %s
        """
        try:
            await self._pool.execute(sql, error, entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("distillation_outbox.mark_failed_failed: %s", exc)

    @staticmethod
    def _row_to_write(row: Any) -> DistillationWrite:
        import json as _json  # noqa: PLC0415

        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}

        return DistillationWrite(
            id=str(row["id"]),
            message_id=row["message_id"],
            target_node_id=row["target_node_id"],
            target_type=row["target_type"],
            payload=payload,
            created_at=row["created_at"],
            processed_at=row.get("processed_at"),
            retry_count=row.get("retry_count", 0),
            error_detail=row.get("error_detail"),
        )
