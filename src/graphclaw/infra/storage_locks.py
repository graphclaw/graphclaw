# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.infra.storage_locks — Optimistic storage-level locks (FR-RES-003).

Description
-----------
Prevents concurrent writes to the same MinIO object path by using an advisory
lock file (``{path}.lock``).  Callers must acquire the lock before writing and
release it after.  Lock files have a TTL; stale locks are broken automatically.

This is a lightweight cooperative locking scheme, not a distributed mutex.
Use for low-contention paths (distillation writes, profile updates).

Design Patterns
---------------
- Advisory lock file: Write a ``{path}.lock`` sentinel before writing.
- TTL-based expiry: Lock files older than ``ttl_seconds`` are considered stale.
- Context manager: ``async with storage_locks.lock(path): ...`` pattern.

Public API
----------
- StorageLock: Asynchonous advisory lock.
- StorageLock.acquire(path): Write lock file.
- StorageLock.release(path): Delete lock file.
- StorageLock.lock(path): Async context manager.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 60
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_MAX_WAIT_SECONDS = 30


class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired within the timeout."""


class StorageLock:
    """Advisory per-path lock backed by MinIO/S3 lock files (FR-RES-003).

    Parameters
    ----------
    storage:
        ``StorageClient`` instance.
    owner_id:
        Identifier of the lock owner (e.g. agent_id, worker_id).
    ttl_seconds:
        Seconds after which a stale lock is broken.
    poll_interval:
        Seconds between lock-check polls.
    max_wait_seconds:
        How long to wait before raising ``LockAcquisitionError``.
    """

    def __init__(
        self,
        storage: Any,
        *,
        owner_id: str = "worker",
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> None:
        self._storage = storage
        self._owner_id = owner_id
        self._ttl = ttl_seconds
        self._poll = poll_interval
        self._max_wait = max_wait_seconds

    def _lock_path(self, path: str) -> str:
        return f"{path}.lock"

    def _lock_payload(self) -> bytes:
        data = {
            "owner": self._owner_id,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(data).encode("utf-8")

    async def _is_stale(self, lock_path: str) -> bool:
        """Return True if the lock file is older than ttl_seconds."""
        try:
            raw = await self._storage.read(lock_path)
            data = json.loads(raw)
            acquired_at_str = data.get("acquired_at", "")
            acquired_at = datetime.fromisoformat(acquired_at_str)
            age = (datetime.now(timezone.utc) - acquired_at).total_seconds()
            return age > self._ttl
        except Exception:  # noqa: BLE001
            # If we can't read the lock, treat it as non-stale (conservative)
            return False

    async def acquire(self, path: str) -> None:
        """Acquire the lock for *path*.

        Polls until the lock is free or stale, then writes the lock file.
        Raises ``LockAcquisitionError`` if ``max_wait_seconds`` elapses.
        """
        lock_path = self._lock_path(path)
        elapsed = 0.0

        while True:
            lock_exists = await self._storage.exists(lock_path)
            if not lock_exists:
                break
            if await self._is_stale(lock_path):
                logger.info("storage_lock.breaking_stale_lock: %s", lock_path)
                await self._storage.delete(lock_path)
                break
            if elapsed >= self._max_wait:
                raise LockAcquisitionError(
                    f"Could not acquire lock for {path!r} within {self._max_wait}s"
                )
            await asyncio.sleep(self._poll)
            elapsed += self._poll

        await self._storage.write(lock_path, self._lock_payload(), "application/json")

    async def release(self, path: str) -> None:
        """Release (delete) the lock for *path*.  Swallows errors."""
        lock_path = self._lock_path(path)
        try:
            await self._storage.delete(lock_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("storage_lock.release_failed: %s", exc)

    @asynccontextmanager
    async def lock(self, path: str) -> AsyncGenerator[None, None]:
        """Async context manager that acquires and releases the lock.

        Usage::

            async with storage_lock.lock("user-1/agents/main/profile.md"):
                await storage.write(...)
        """
        await self.acquire(path)
        try:
            yield
        finally:
            await self.release(path)
