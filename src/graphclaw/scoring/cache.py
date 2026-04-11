"""graphclaw.scoring.cache — In-process score cache with invalidation triggers.

Description
-----------
Provides ``ScoreCache``, a lightweight in-process dict-based cache for
``ScoreExplanation`` objects.  The cache avoids redundant scoring work across a
single agent cycle and supports six named invalidation triggers from PRD Section 9.
Phase 0 uses a plain dict; later phases will swap in a Redis-backed variant
implementing the same interface.

Design Patterns
---------------
- Cache-Aside: The engine reads the cache before computing and writes after;
  the cache itself is passive and does not proactively expire entries.
- Observer (deferred): Invalidation methods are called by the state machine,
  the override handler, and the resource risk monitor when their data changes.

Public API
----------
- ScoreCache.get: Return the cached ScoreExplanation for a task, or None.
- ScoreCache.has: Return True if a valid cached score exists for a task.
- ScoreCache.set: Store or replace the score explanation for a task.
- ScoreCache.invalidate: Invalidate the score for a single task.
- ScoreCache.invalidate_upstream: Invalidate a dependent and its upstream tasks.
- ScoreCache.invalidate_by_resource: Invalidate all tasks assigned to a resource.
- ScoreCache.invalidate_all: Clear the entire cache (forced full rescore).
- ScoreCache.size: Number of currently cached scores.
- ScoreCache.last_full_rescore: Timestamp of the last full cache clear.

Dependencies
------------
- graphclaw.models.scoring: ScoreExplanation.

Notes
-----
This implementation is not thread-safe.  Phase 0 runs single-threaded async I/O,
so concurrent mutation is not a concern.  A Redis-backed implementation with
atomic operations will be needed for multi-worker deployments.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from graphclaw.models.base import utcnow
from graphclaw.models.scoring import ScoreExplanation

logger = logging.getLogger(__name__)


class ScoreCache:
    """In-process dict cache for ScoreExplanation objects.

    Invalidation triggers (call the corresponding method when the event occurs):
    1. Node state changes          → invalidate(task_id)
    2. Deadline crosses bracket    → invalidate(task_id)
    3. Dependent node state change → invalidate_upstream(dependent_id, upstream_ids)
    4. Human override applied      → invalidate(task_id)
    5. Resource risk signal change → invalidate_by_resource(resource_id, task_ids)
    6. Constraint pressure change  → invalidate(task_id)

    Forced full rescore:
    - invalidate_all()
    """

    def __init__(self) -> None:
        self._store: dict[str, ScoreExplanation] = {}
        self._last_full_rescore: datetime | None = None

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> ScoreExplanation | None:
        """Return the cached ScoreExplanation for *task_id*, or None if absent."""
        return self._store.get(task_id)

    def has(self, task_id: str) -> bool:
        """Return True if a valid cached score exists for *task_id*."""
        return task_id in self._store

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set(self, task_id: str, explanation: ScoreExplanation) -> None:
        """Store or replace the score explanation for *task_id*."""
        self._store[task_id] = explanation
        logger.debug("score_cache: stored score for %s", task_id)

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, task_id: str) -> None:
        """Invalidate the cached score for a single task.

        Trigger conditions: state change, deadline bracket crossing,
        human override applied/removed, constraint pressure change.
        """
        if task_id in self._store:
            del self._store[task_id]
            logger.debug("score_cache: invalidated %s", task_id)

    def invalidate_upstream(self, dependent_id: str, upstream_ids: list[str]) -> None:
        """Invalidate upstream tasks when a dependent node's state changes.

        When a downstream task's state changes, the dependency weight and
        blocker scores of all upstream nodes may change — they need rescoring.

        Parameters
        ----------
        dependent_id:
            The task whose state changed.
        upstream_ids:
            All upstream (blocker / dependency) task IDs to invalidate.
        """
        self.invalidate(dependent_id)
        for uid in upstream_ids:
            self.invalidate(uid)
        logger.debug(
            "score_cache: invalidated dependent %s and %d upstream nodes",
            dependent_id,
            len(upstream_ids),
        )

    def invalidate_by_resource(self, resource_id: str, task_ids: list[str]) -> None:
        """Invalidate all tasks assigned to *resource_id*.

        Triggered when a resource risk signal is added or removed.

        Parameters
        ----------
        resource_id:
            The resource whose risk profile changed.
        task_ids:
            IDs of tasks assigned to this resource.
        """
        for tid in task_ids:
            self.invalidate(tid)
        logger.debug(
            "score_cache: invalidated %d tasks for resource %s",
            len(task_ids),
            resource_id,
        )

    def invalidate_all(self) -> None:
        """Clear the entire cache (forced full rescore)."""
        count = len(self._store)
        self._store.clear()
        self._last_full_rescore = utcnow()
        logger.info("score_cache: full rescore — cleared %d entries", count)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of currently cached scores."""
        return len(self._store)

    @property
    def last_full_rescore(self) -> datetime | None:
        """Timestamp of the last full cache clear, or None if never done."""
        return self._last_full_rescore


__all__ = ["ScoreCache"]
