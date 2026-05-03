"""graphclaw.workers.purge_worker — Scheduled purge worker (FR-DEL-005).

Runs under admin_principal.  On each tick, queries for nodes that are:
  - archived_at IS NOT NULL
  - purge_after <= now()
  - legal_hold IS NOT TRUE
  - purge_cancelled_at IS NULL

Then hard-archives each (removes from the active graph) and writes an audit
entry.  A heartbeat is emitted at the start and end of each run cycle.

Design notes
------------
- Advisory lock via Redis SETNX prevents concurrent worker invocations
  (idempotency / race protection from FR-DEL-005/§12.3).
- Each node deletion is wrapped in an independent try/except; a single failure
  does not abort the batch.
- Audit entry written AFTER deletion succeeds (not before — avoids phantom
  audit entries for skipped nodes).
- DLQ: nodes that fail to purge are logged at WARNING with full context; a
  separate alerting layer monitors for repeated failures.
- Pattern: Strategy (store backend) + Command (purge command per node).

Methods
-------
- PurgeWorker.run_once() -> PurgeResult
- PurgeWorker.run_forever(interval_seconds) — async loop (for cron container)

Dependencies
------------
- graphclaw.db.base: GraphStore (admin_principal).
- graphclaw.audit.immutable_log: AuditLog, AuditEventType.
- graphclaw.workers.heartbeat: WorkerHeartbeat.
- graphclaw.infra.storage: StorageClient.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from pydantic import BaseModel

from graphclaw.audit.immutable_log import AuditEventType, AuditLog
from graphclaw.workers.heartbeat import WorkerHeartbeat

logger = logging.getLogger(__name__)

_WORKER_NAME = "purge_worker"
_LOCK_KEY = "worker:lock:purge_worker"
_LOCK_TTL_SECONDS = 120  # 2-minute lock; worker should finish well within this


class PurgeResult(BaseModel):
    """Summary of one purge worker run cycle."""

    run_at: datetime
    candidates_found: int = 0
    purged_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    errors: list[str] = []


class PurgeWorker:
    """Hard-purge worker for nodes past their ``purge_after`` deadline.

    Parameters
    ----------
    store :
        GraphStore instance backed by admin_principal (must have DELETE grants).
    storage :
        StorageClient for audit log writes.
    redis :
        Optional Redis client for advisory locking + heartbeat.
    audit_actor_id :
        Actor ID written to audit entries (default: ``"system:purge_worker"``).
    """

    def __init__(
        self,
        store,  # GraphStore (admin_principal)
        storage,  # StorageClient
        redis=None,
        audit_actor_id: str = "system:purge_worker",
    ) -> None:
        self._store = store
        self._storage = storage
        self._redis = redis
        self._audit_actor_id = audit_actor_id
        self._heartbeat = WorkerHeartbeat(redis=redis)
        self._audit = AuditLog(storage)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_once(self) -> PurgeResult:
        """Execute one purge cycle.

        Acquires advisory lock, queries eligible nodes, purges each,
        writes audit entries, emits heartbeat, releases lock.
        """
        now = datetime.now(UTC)
        result = PurgeResult(run_at=now)

        if not await self._acquire_lock():
            logger.info("purge_worker: skipping run — lock held by another instance")
            return result

        try:
            await self._heartbeat.beat(_WORKER_NAME, metadata={"phase": "start"})
            await self._do_purge(result)
            await self._heartbeat.beat(
                _WORKER_NAME,
                metadata={
                    "phase": "complete",
                    "purged": result.purged_count,
                    "failed": result.failed_count,
                },
            )
        finally:
            await self._release_lock()

        return result

    async def run_forever(self, interval_seconds: int = 3600) -> None:  # pragma: no cover
        """Run purge cycles indefinitely (for use in cron container)."""
        while True:
            try:
                result = await self.run_once()
                logger.info(
                    "purge_worker: cycle complete purged=%d failed=%d",
                    result.purged_count,
                    result.failed_count,
                )
            except Exception:  # noqa: BLE001
                logger.exception("purge_worker: unexpected error in run cycle")
            await asyncio.sleep(interval_seconds)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _do_purge(self, result: PurgeResult) -> None:
        """Query + purge eligible nodes, updating *result* in place."""
        now = datetime.now(UTC)
        try:
            candidates = await self._store.list_nodes(
                label=None,  # all labels
                filters={
                    "archived_at__not_null": True,
                    "purge_after__lte": now,
                    "legal_hold": False,
                    "purge_cancelled_at__null": True,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("purge_worker: failed to query candidates")
            result.errors.append("candidate query failed")
            return

        result.candidates_found = len(candidates)

        for node in candidates:
            node_id = getattr(node, "id", None) or str(node)
            # Re-check guard conditions inside the per-node operation
            # (cancel-vs-purge race; FR-DEL-004/§6.2)
            if not self._is_eligible(node, now):
                result.skipped_count += 1
                continue
            try:
                await self._purge_node(node_id)
                result.purged_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("purge_worker: failed to purge node=%s: %s", node_id, exc)
                result.failed_count += 1
                result.errors.append(f"{node_id}: {exc}")

    @staticmethod
    def _is_eligible(node, now: datetime) -> bool:
        """Re-check eligibility immediately before purge (race guard)."""
        purge_after = getattr(node, "purge_after", None)
        legal_hold = getattr(node, "legal_hold", False)
        purge_cancelled_at = getattr(node, "purge_cancelled_at", None)
        if purge_after is None or purge_after > now:
            return False
        if legal_hold:
            return False
        if purge_cancelled_at is not None:
            return False
        return True

    async def _purge_node(self, node_id: str) -> None:
        """Hard-delete a single node and write the audit entry."""
        # The store's delete_node uses admin_principal which has DELETE grants.
        await self._store.delete_node(node_id)
        await self._audit.record(
            AuditEventType.PURGE_EXECUTED,
            actor_id=self._audit_actor_id,
            subject_id=node_id,
            metadata={"purge_worker_run": True},
        )
        logger.info("purge_worker: purged node=%s", node_id)

    async def _acquire_lock(self) -> bool:
        """Acquire advisory lock via Redis SETNX.  Returns True if acquired."""
        if self._redis is None:
            return True  # No Redis → single-instance mode, always proceed.
        try:
            acquired = await self._redis.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SECONDS)
            return bool(acquired)
        except Exception:  # noqa: BLE001
            logger.warning("purge_worker: Redis lock acquisition failed — proceeding without lock")
            return True

    async def _release_lock(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(_LOCK_KEY)
        except Exception:  # noqa: BLE001
            pass
