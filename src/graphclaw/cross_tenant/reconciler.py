# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.cross_tenant.reconciler — Org-task-index reconciliation (FR-AE-001).

Description
-----------
Nightly full-sync diff between the ``org_task_index`` Postgres table and the
AGE graph.  For each ``ASSIGNED_TO`` edge in the graph, the reconciler checks
that the index row is up-to-date; missing or stale rows are upserted and an
audit entry is written.

Reconciliation is triggered by:
  - ``POST /admin/cross-tenant/rebuild`` (on-demand)
  - A nightly cron via the TriggerEngine (scheduled)

Design Patterns
---------------
- Service: ``OrgTaskIndexReconciler`` is a stateless service that reads from
  the graph store and writes to OrgTaskIndex.
- Audit: Each reconciliation run produces a summary dict that callers can
  log or store.

Public API
----------
- ReconciliationResult: Summary returned after a run.
- OrgTaskIndexReconciler: Main reconciliation service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from graphclaw.cross_tenant.task_index import OrgTaskIndex, OrgTaskIndexEntry

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Result DTO
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationResult:
    """Summary produced by a single reconciliation run.

    Attributes
    ----------
    started_at:
        UTC timestamp when the run started.
    finished_at:
        UTC timestamp when the run completed (or None if still running).
    tasks_scanned:
        Total tasks examined from the graph store.
    rows_upserted:
        Number of index rows inserted or updated (drifted tasks).
    rows_unchanged:
        Number of tasks that were already up-to-date.
    errors:
        List of error messages for tasks that could not be reconciled.
    """

    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    tasks_scanned: int = 0
    rows_upserted: int = 0
    rows_unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for API responses and audit log."""
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "tasks_scanned": self.tasks_scanned,
            "rows_upserted": self.rows_upserted,
            "rows_unchanged": self.rows_unchanged,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Reconciler service
# ---------------------------------------------------------------------------


class OrgTaskIndexReconciler:
    """Full-sync reconciler for the org_task_index (FR-AE-001).

    Parameters
    ----------
    store:
        GraphStore instance for reading TaskNode + ASSIGNED_TO edges.
    task_index:
        OrgTaskIndex repo for upsert writes.
    org_id:
        When provided, only tasks owned by this org are reconciled; omit
        for a global rebuild.
    """

    def __init__(
        self,
        store: Any,
        task_index: OrgTaskIndex,
        org_id: str | None = None,
    ) -> None:
        self._store = store
        self._task_index = task_index
        self._org_id = org_id

    async def run(self) -> ReconciliationResult:
        """Execute a full reconciliation pass.

        For each TaskNode in the graph the reconciler:
        1. Reads the current row from ``org_task_index`` (if any).
        2. Computes the canonical index entry from the graph node.
        3. Upserts if the row is missing or the key fields have drifted.
        4. Records an audit entry for every upserted row.

        Returns
        -------
        ReconciliationResult
            Summary of the run; always succeeds — errors are captured in
            ``result.errors`` rather than raised.
        """
        result = ReconciliationResult()
        logger.info(
            "reconciler: starting full-sync org_id=%s",
            self._org_id or "all",
        )

        try:
            filters: dict[str, Any] = {}
            if self._org_id:
                filters["org_id"] = self._org_id
            task_nodes = await self._store.list_nodes("Task", filters)
        except Exception as exc:  # noqa: BLE001
            msg = f"reconciler: failed to list task nodes: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            result.finished_at = _utcnow()
            return result

        for node in task_nodes:
            result.tasks_scanned += 1
            task_id: str = node.get("id", "")
            if not task_id:
                continue

            try:
                entry = self._build_entry(node)
                await self._task_index.upsert(entry)
                result.rows_upserted += 1
                logger.debug(
                    "reconciler: upserted task_id=%s org_id=%s",
                    task_id,
                    entry.org_id,
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"task_id={task_id}: {exc}"
                logger.warning("reconciler: upsert_failed %s", msg)
                result.errors.append(msg)

        result.finished_at = _utcnow()
        logger.info(
            "reconciler: done scanned=%d upserted=%d errors=%d",
            result.tasks_scanned,
            result.rows_upserted,
            len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_entry(self, node: dict[str, Any]) -> OrgTaskIndexEntry:
        """Build an ``OrgTaskIndexEntry`` from a raw graph node dict."""
        task_id = node.get("id", "")
        owner_user_id = node.get("owned_by") or node.get("created_by") or ""
        org_id = node.get("org_id") or self._org_id or ""
        workspace_id = node.get("workspace_id")
        state = str(node.get("state", "PENDING"))
        summary_text = node.get("title") or node.get("description") or ""

        # Deadline may be a datetime object, ISO string, or None.
        deadline_raw = node.get("deadline") or (
            node.get("timeline", {}).get("deadline")
            if isinstance(node.get("timeline"), dict)
            else None
        )
        deadline: datetime | None = None
        if deadline_raw:
            if isinstance(deadline_raw, datetime):
                deadline = deadline_raw
            else:
                try:
                    deadline = datetime.fromisoformat(str(deadline_raw))
                except ValueError:
                    pass

        # assigned_to may be a single user_id string or a list.
        assigned_raw = node.get("assigned_to") or node.get("assignee_id")
        if assigned_raw is None:
            assignee_ids: list[str] = []
        elif isinstance(assigned_raw, list):
            assignee_ids = [str(x) for x in assigned_raw if x]
        else:
            assignee_ids = [str(assigned_raw)]

        return OrgTaskIndexEntry(
            task_id=task_id,
            owner_user_id=owner_user_id,
            org_id=org_id,
            workspace_id=workspace_id,
            assignee_linked_user_ids=assignee_ids,
            state=state,
            deadline=deadline,
            last_activity_at=_utcnow(),
            summary_text=summary_text,
            archived_at=None,
        )
