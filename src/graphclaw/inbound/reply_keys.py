# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.inbound.reply_keys — Dual-write reply-key substrate.

Description
-----------
Every outbound dispatch creates a reply-key that allows the inbound router to
correlate a future response back to the originating task and counterparty.

Two stores are maintained:
1. **Redis** — short-lived (7 days) lookup key used by the inbound router for
   real-time processing.  Key: ``checkin:{channel}:{thread_id}:{msg_id}``.
2. **Postgres reply_lineage table** — persistent (channel, thread_id) →
   (task_id, counterparty_id, user_id) row used for recovery when Redis has
   expired.

Design Patterns
---------------
- Dual-write with graceful degradation: Redis failure is non-fatal; Postgres is
  the persistent source of truth.
- Repository: ``ReplyKeyStore`` wraps both stores behind a single interface.

Public API
----------
- REDIS_REPLY_KEY_TTL_SECONDS: 7-day TTL for Redis keys.
- ReplyKeyRecord: Data class for reply-key records.
- ReplyKeyStore: Writes + reads reply keys across both stores.

Dependencies
------------
- asyncpg (Postgres for reply_lineage table).
- redis.asyncio (Redis for fast inbound routing).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

REDIS_REPLY_KEY_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# Key pattern: checkin:{channel}:{thread_id}:{msg_id}
# msg_id is the channel-specific message identifier (could be checkin_id as fallback)
_REDIS_KEY_PATTERN = "checkin:{channel}:{thread_id}:{msg_id}"


@dataclass
class ReplyKeyRecord:
    """Payload stored under a reply key."""

    task_id: str | None
    counterparty_id: str
    user_id: str
    channel: str
    thread_id: str
    checkin_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(
            {
                "task_id": self.task_id,
                "counterparty_id": self.counterparty_id,
                "user_id": self.user_id,
                "channel": self.channel,
                "thread_id": self.thread_id,
                "checkin_id": self.checkin_id,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> ReplyKeyRecord:
        parsed = json.loads(data)
        return cls(**parsed)


class ReplyKeyStore:
    """Dual-write reply-key store (Redis + Postgres).

    Parameters
    ----------
    redis:
        Optional ``redis.asyncio`` client.  When ``None``, Redis write is
        skipped with a warning.
    db_pool:
        Optional ``asyncpg`` connection pool.  When ``None``, Postgres write
        is skipped with a warning.
    """

    def __init__(
        self,
        redis: Any | None = None,
        db_pool: Any | None = None,
    ) -> None:
        self._redis = redis
        self._db_pool = db_pool

    @staticmethod
    def redis_key(channel: str, thread_id: str, msg_id: str) -> str:
        return f"checkin:{channel}:{thread_id}:{msg_id}"

    async def write(self, record: ReplyKeyRecord, msg_id: str) -> None:
        """Write reply-key to Redis (7d TTL) and Postgres reply_lineage.

        Parameters
        ----------
        record:
            The reply-key record to persist.
        msg_id:
            Channel-specific message identifier (e.g. Telegram message_id,
            email Message-ID).  Falls back to ``record.checkin_id`` if blank.
        """
        msg_id = msg_id or record.checkin_id

        # ── Redis write (non-fatal if unavailable) ────────────────────────────
        if self._redis is not None:
            key = self.redis_key(record.channel, record.thread_id, msg_id)
            try:
                await self._redis.set(key, record.to_json(), ex=REDIS_REPLY_KEY_TTL_SECONDS)
                logger.debug(
                    "ReplyKeyStore: Redis write OK key=%s",
                    key,
                    extra={"checkin_id": record.checkin_id},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ReplyKeyStore: Redis write failed: %s", exc)
        else:
            logger.debug("ReplyKeyStore: Redis not available — skipping Redis write")

        # ── Postgres write (non-fatal if unavailable) ────────────────────────
        if self._db_pool is not None:
            try:
                async with self._db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO reply_lineage
                            (channel, thread_id, task_id, counterparty_id,
                             user_id, checkin_id, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (channel, thread_id) DO UPDATE
                            SET task_id       = EXCLUDED.task_id,
                                counterparty_id = EXCLUDED.counterparty_id,
                                checkin_id    = EXCLUDED.checkin_id,
                                created_at    = EXCLUDED.created_at
                        """,
                        record.channel,
                        record.thread_id,
                        record.task_id,
                        record.counterparty_id,
                        record.user_id,
                        record.checkin_id,
                        record.created_at,
                    )
                    logger.debug(
                        "ReplyKeyStore: Postgres write OK thread=%s/%s",
                        record.channel,
                        record.thread_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ReplyKeyStore: Postgres write failed: %s", exc)
        else:
            logger.debug("ReplyKeyStore: DB pool not available — skipping Postgres write")

    async def read_from_redis(
        self, channel: str, thread_id: str, msg_id: str
    ) -> ReplyKeyRecord | None:
        """Look up a reply key in Redis.

        Returns ``None`` when the key has expired or Redis is unavailable.
        """
        if self._redis is None:
            return None
        key = self.redis_key(channel, thread_id, msg_id)
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            return ReplyKeyRecord.from_json(value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReplyKeyStore: Redis read failed: %s", exc)
            return None

    async def read_from_db(self, channel: str, thread_id: str) -> ReplyKeyRecord | None:
        """Look up a reply key in Postgres reply_lineage.

        Used as a fallback when Redis has expired.
        """
        if self._db_pool is None:
            return None
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT channel, thread_id, task_id, counterparty_id,
                           user_id, checkin_id, created_at::text
                    FROM reply_lineage
                    WHERE channel = $1 AND thread_id = $2
                    """,
                    channel,
                    thread_id,
                )
                if row is None:
                    return None
                return ReplyKeyRecord(
                    channel=row["channel"],
                    thread_id=row["thread_id"],
                    task_id=row["task_id"],
                    counterparty_id=row["counterparty_id"],
                    user_id=row["user_id"],
                    checkin_id=row["checkin_id"],
                    created_at=row["created_at"],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReplyKeyStore: Postgres read failed: %s", exc)
            return None
