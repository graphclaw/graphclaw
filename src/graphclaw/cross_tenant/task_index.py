"""graphclaw.cross_tenant.task_index — Org task index read/write API (FR-XT-001).

Description
-----------
Manages the ``org_task_index`` Postgres table — a per-org denormalised index
of tasks with their assignees' ``linked_user_ids``.  Used by
``list_external_assignments_for_me`` (FR-XT-002) and the assignee-side
briefing extension (FR-XT-004).

Design Patterns
---------------
- Repository: ``OrgTaskIndex`` is a thin async CRUD repo over the Postgres table.
- Mandatory ACL: ``list_for_assignee`` requires ``caller_user_id`` and
  ``caller_org_ids`` — never returns data from orgs the caller doesn't belong to.

Public API
----------
- OrgTaskIndexEntry: Row DTO.
- OrgTaskIndex: Async repo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class OrgTaskIndexEntry:
    """A row from the ``org_task_index`` table."""

    task_id: str
    owner_user_id: str
    org_id: str
    workspace_id: str | None
    assignee_linked_user_ids: list[str]
    state: str
    deadline: datetime | None
    last_activity_at: datetime | None
    summary_text: str
    archived_at: datetime | None = None


# ---------------------------------------------------------------------------
# OrgTaskIndex
# ---------------------------------------------------------------------------


class OrgTaskIndex:
    """Org task index repository (FR-XT-001).

    Parameters
    ----------
    pool:
        Async DB pool (asyncpg-style: ``fetch``, ``execute``).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Write path (updated by event consumer on task mutations)
    # ------------------------------------------------------------------

    async def upsert(self, entry: OrgTaskIndexEntry) -> None:
        """Insert or update a task index entry."""
        if self._pool is None:
            return
        sql = """
            INSERT INTO org_task_index
                (task_id, owner_user_id, org_id, workspace_id,
                 assignee_linked_user_ids, state, deadline,
                 last_activity_at, summary_text, archived_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, NOW()),%s,%s)
            ON CONFLICT (task_id) DO UPDATE SET
                owner_user_id            = EXCLUDED.owner_user_id,
                org_id                   = EXCLUDED.org_id,
                workspace_id             = EXCLUDED.workspace_id,
                assignee_linked_user_ids = EXCLUDED.assignee_linked_user_ids,
                state                    = EXCLUDED.state,
                deadline                 = EXCLUDED.deadline,
                last_activity_at         = COALESCE(EXCLUDED.last_activity_at, NOW()),
                summary_text             = EXCLUDED.summary_text,
                archived_at              = EXCLUDED.archived_at
        """
        try:
            await self._pool.execute(
                sql,
                entry.task_id,
                entry.owner_user_id,
                entry.org_id,
                entry.workspace_id,
                entry.assignee_linked_user_ids,
                entry.state,
                entry.deadline,
                entry.last_activity_at,
                entry.summary_text,
                entry.archived_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("org_task_index.upsert_failed: %s", exc)

    async def set_archived(self, task_id: str, archived_at: datetime) -> None:
        """Mark a task as archived in the index."""
        if self._pool is None:
            return
        sql = "UPDATE org_task_index SET archived_at = %s WHERE task_id = %s"
        try:
            await self._pool.execute(sql, archived_at, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("org_task_index.set_archived_failed: %s", exc)

    # ------------------------------------------------------------------
    # Read path (FR-XT-002)
    # ------------------------------------------------------------------

    async def list_for_assignee(
        self,
        assignee_user_id: str,
        caller_org_ids: list[str],
        *,
        state_filter: str | None = None,
        deadline_before: datetime | None = None,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[OrgTaskIndexEntry]:
        """Return tasks where *assignee_user_id* is an assignee, scoped to *caller_org_ids*.

        This is the ACL-enforced query for FR-XT-002.

        Parameters
        ----------
        assignee_user_id:
            The platform user whose linked_user_id must appear in assignee_linked_user_ids.
        caller_org_ids:
            Orgs the caller is a member of — cross-org entries never returned (NFR-004).
        state_filter:
            Optional state value to filter by.
        deadline_before:
            Optional deadline upper bound.
        workspace_id:
            Optional workspace scope.
        limit:
            Max results.
        """
        if not caller_org_ids or not assignee_user_id:
            return []
        if self._pool is None:
            return []

        placeholders = ", ".join("%s" for _ in caller_org_ids)
        params: list[Any] = [assignee_user_id]
        params.extend(caller_org_ids)

        extra_conditions = ""

        if state_filter:
            extra_conditions += " AND state = %s"
            params.append(state_filter)

        if deadline_before:
            extra_conditions += " AND deadline < %s"
            params.append(deadline_before)

        if workspace_id:
            extra_conditions += " AND workspace_id = %s"
            params.append(workspace_id)

        sql = f"""
            SELECT *
            FROM org_task_index
            WHERE %s = ANY(assignee_linked_user_ids)
              AND org_id IN ({placeholders})
              AND archived_at IS NULL
              {extra_conditions}
            ORDER BY last_activity_at DESC NULLS LAST
            LIMIT {int(limit)}
        """
        try:
            rows = await self._pool.fetch(sql, *params)
            return [self._row_to_entry(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("org_task_index.list_for_assignee_failed: %s", exc)
            return []

    @staticmethod
    def _row_to_entry(row: Any) -> OrgTaskIndexEntry:
        return OrgTaskIndexEntry(
            task_id=row["task_id"],
            owner_user_id=row["owner_user_id"],
            org_id=row["org_id"],
            workspace_id=row.get("workspace_id"),
            assignee_linked_user_ids=list(row.get("assignee_linked_user_ids") or []),
            state=row["state"],
            deadline=row.get("deadline"),
            last_activity_at=row.get("last_activity_at"),
            summary_text=row.get("summary_text") or "",
            archived_at=row.get("archived_at"),
        )
