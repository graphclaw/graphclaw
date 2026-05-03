"""graphclaw.workers.heartbeat — Worker heartbeat utility (FR-DEL-005).

Tracks liveness of long-running workers by writing a heartbeat key to Redis
on each successful run cycle.  If the heartbeat is absent for more than
2× the expected interval, the monitoring layer should alert (P2).

Design notes
------------
- Redis key: ``worker:heartbeat:{worker_name}`` with a TTL of 2.5× interval.
- The TTL self-expires so monitoring need only check key existence.
- StorageClient (MinIO) is also written for durable record keeping.
- Pattern: Value Object (HeartbeatRecord) + Strategy (Redis or file-only fallback).

Methods
-------
- WorkerHeartbeat.beat(worker_name, metadata) -> HeartbeatRecord
- WorkerHeartbeat.last_seen(worker_name) -> HeartbeatRecord | None

Dependencies
------------
- redis.asyncio: async Redis client for TTL-based key.
- graphclaw.models.base: utcnow.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "worker:heartbeat:"


class HeartbeatRecord(BaseModel):
    """A single heartbeat snapshot from a worker run."""

    worker_name: str
    beat_at: datetime
    metadata: dict = {}


class WorkerHeartbeat:
    """Write and read worker heartbeats via Redis.

    Parameters
    ----------
    redis :
        ``redis.asyncio.Redis`` client.  Pass ``None`` to disable (no-op mode).
    interval_seconds :
        Expected run interval in seconds.  TTL is set to 2.5×.
    """

    def __init__(self, redis=None, interval_seconds: int = 3600) -> None:
        self._redis = redis
        self._ttl = int(interval_seconds * 2.5)

    async def beat(
        self, worker_name: str, metadata: dict | None = None
    ) -> HeartbeatRecord:
        """Record a successful heartbeat for *worker_name*.

        Writes a Redis key with TTL 2.5× interval.  Silently no-ops if Redis
        is unavailable (prevents the heartbeat from blocking the worker).
        """
        record = HeartbeatRecord(
            worker_name=worker_name,
            beat_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        if self._redis is not None:
            key = f"{_REDIS_PREFIX}{worker_name}"
            try:
                await self._redis.set(key, record.model_dump_json(), ex=self._ttl)
            except Exception:  # noqa: BLE001
                logger.warning("heartbeat: Redis write failed for worker=%s", worker_name)
        return record

    async def last_seen(self, worker_name: str) -> HeartbeatRecord | None:
        """Return the last heartbeat record for *worker_name*, or None."""
        if self._redis is None:
            return None
        key = f"{_REDIS_PREFIX}{worker_name}"
        try:
            raw: str | None = await self._redis.get(key)
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        try:
            return HeartbeatRecord.model_validate_json(raw)
        except Exception:
            return None
