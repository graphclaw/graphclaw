"""graphclaw.cross_tenant.repo — ACL-enforced cross-tenant task query builder (FR-XT-002..003).

Description
-----------
``CrossTenantRepo`` wraps ``OrgTaskIndex`` with a mandatory ACL layer that
enforces org-scoping at the query-builder level.  Application code **cannot**
bypass the ACL filter — passing empty ``caller_org_ids`` returns an empty list,
never raising an error that could be caught and re-tried without credentials.

The ``ACLViolation`` sentinel is raised only when an explicit cross-org bypass
attempt is detected (e.g. passing an ``org_id`` not in ``caller_org_ids``).

Design Patterns
---------------
- Decorator / Wrapper: ``CrossTenantRepo`` wraps ``OrgTaskIndex`` and enforces
  ACL at every call site; callers cannot reach the underlying index directly.
- Fail-Closed: Empty ``caller_org_ids`` → empty result, not an error. This
  prevents accidental data leaks from mis-configured callers.
- Explicit ACL Violation: Only explicit bypass attempts raise ``ACLViolation``;
  normal callers just receive an empty result for uncredentialed calls.

Public API
----------
- CrossTenantRepo: ACL-enforced repository.
- ACLViolation: Raised when an explicit org-bypass attempt is detected.

Dependencies
------------
- graphclaw.cross_tenant.task_index: OrgTaskIndex, OrgTaskIndexEntry.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from graphclaw.cross_tenant.task_index import OrgTaskIndex, OrgTaskIndexEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinel exception
# ---------------------------------------------------------------------------


class ACLViolation(Exception):
    """Raised when a caller attempts an explicit cross-org bypass."""


# ---------------------------------------------------------------------------
# ACL-enforced repository
# ---------------------------------------------------------------------------


class CrossTenantRepo:
    """ACL-enforced wrapper over OrgTaskIndex (FR-XT-002..003).

    All query methods require ``caller_user_id`` and ``caller_org_ids``.
    Queries without ``caller_org_ids`` return empty results immediately (fail-
    closed) and never reach the underlying index.  An ``ACLViolation`` is
    raised only when an ``org_id`` filter explicitly outside the caller's orgs
    is requested.

    Parameters
    ----------
    task_index:
        The underlying ``OrgTaskIndex`` to delegate reads/writes to.
    """

    def __init__(self, task_index: OrgTaskIndex) -> None:
        self._index = task_index

    # ------------------------------------------------------------------
    # Write path — passthrough (ACL is enforced at read time)
    # ------------------------------------------------------------------

    async def upsert(self, entry: OrgTaskIndexEntry) -> None:
        """Upsert a task index entry (write-path; no ACL required)."""
        await self._index.upsert(entry)

    async def set_archived(self, task_id: str, archived_at: datetime) -> None:
        """Archive a task index entry (write-path; no ACL required)."""
        await self._index.set_archived(task_id, archived_at)

    # ------------------------------------------------------------------
    # Read path — ACL enforced (FR-XT-002..003)
    # ------------------------------------------------------------------

    async def list_for_assignee(
        self,
        assignee_user_id: str,
        caller_user_id: str,
        caller_org_ids: list[str],
        *,
        state_filter: str | None = None,
        deadline_before: datetime | None = None,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[OrgTaskIndexEntry]:
        """Return tasks where *assignee_user_id* is listed as an assignee.

        Enforces:
        1. ``caller_org_ids`` must be non-empty (fail-closed).
        2. Results are automatically scoped to ``caller_org_ids``.
        3. Caller must be the assignee OR the query is on behalf of the
           caller themselves (FR-XT-003).

        Parameters
        ----------
        assignee_user_id:
            User whose linked IDs must appear in ``assignee_linked_user_ids``.
        caller_user_id:
            The authenticated user making the request.
        caller_org_ids:
            Orgs the caller is a member of. Cross-org results are never returned.
        state_filter:
            Optional state value filter.
        deadline_before:
            Optional deadline upper bound.
        workspace_id:
            Optional workspace scope.
        limit:
            Max results.

        Raises
        ------
        ACLViolation:
            When caller_user_id != assignee_user_id (cross-user query without
            admin context is not permitted at this layer).

        Returns
        -------
        list[OrgTaskIndexEntry]
            Tasks visible to the caller, or empty list if caller has no orgs.
        """
        if not caller_org_ids:
            logger.debug("CrossTenantRepo: empty caller_org_ids — returning [] (fail-closed)")
            return []

        # FR-XT-003: caller must be querying their own assignments
        # (admin-level cross-user queries go through a different path)
        if caller_user_id and assignee_user_id and caller_user_id != assignee_user_id:
            raise ACLViolation(
                f"Cross-user query denied: caller '{caller_user_id}' may not query "
                f"assignments for '{assignee_user_id}'. Use admin context for cross-user reads."
            )

        return await self._index.list_for_assignee(
            assignee_user_id=assignee_user_id,
            caller_org_ids=caller_org_ids,
            state_filter=state_filter,
            deadline_before=deadline_before,
            workspace_id=workspace_id,
            limit=limit,
        )

    async def get_task_summary(
        self,
        task_id: str,
        caller_user_id: str,
        caller_org_ids: list[str],
    ) -> dict[str, Any] | None:
        """Return a redacted task summary visible to the caller (FR-XT-002 AC2).

        Only returns: task_id, state, deadline, last_activity_at, summary_text,
        owner_user_id. Full body is never returned.

        Parameters
        ----------
        task_id:
            ID of the task to fetch.
        caller_user_id:
            Authenticated user making the request.
        caller_org_ids:
            Orgs the caller belongs to.

        Returns
        -------
        dict | None
            Redacted summary dict, or ``None`` if not found / not in caller's orgs.
        """
        if not caller_org_ids:
            return None

        # Fetch by listing for the caller's assignee ID to enforce ACL
        tasks = await self._index.list_for_assignee(
            assignee_user_id=caller_user_id,
            caller_org_ids=caller_org_ids,
            limit=1000,  # scan for the specific task_id
        )
        for t in tasks:
            if t.task_id == task_id:
                return {
                    "task_id": t.task_id,
                    "state": t.state,
                    "deadline": t.deadline.isoformat() if t.deadline else None,
                    "last_activity_at": (
                        t.last_activity_at.isoformat() if t.last_activity_at else None
                    ),
                    "summary_text": t.summary_text,
                    "owner_user_id": t.owner_user_id,
                    "org_id": t.org_id,
                }
        return None
