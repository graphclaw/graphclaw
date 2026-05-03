"""graphclaw.agent.tools.external_assignments — Cross-tenant assignment tools (FR-XT-002).

Description
-----------
Provides agent-callable tools for assignee-side visibility into tasks created
by other org members:
- ``list_external_assignments_for_me``: List tasks I'm assigned to by others.
- ``get_external_task_summary``: Redacted summary of a single external task.

Both tools enforce org-scoped ACL (FR-XT-003): no cross-org data leakage.

Public API
----------
- EXTERNAL_ASSIGNMENT_TOOLS: list[dict] compatible with tool_registry.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def list_external_assignments_for_me(
    caller_user_id: str,
    caller_org_ids: list[str] | None = None,
    state_filter: str | None = None,
    deadline_before: str | None = None,
    workspace_id: str | None = None,
    task_index: Any = None,
    **_: Any,
) -> dict:
    """List tasks assigned to me by other org members (FR-XT-002).

    Returns redacted summaries only — full task body not returned (FR-XT-002 AC2).

    Parameters
    ----------
    caller_user_id:
        Calling user's platform user ID.
    caller_org_ids:
        Orgs the caller belongs to (ACL scope, FR-XT-003).
    state_filter:
        Optional task state filter.
    deadline_before:
        ISO-8601 deadline upper bound.
    workspace_id:
        Optional workspace scope.
    task_index:
        ``OrgTaskIndex`` instance.
    """
    if not caller_org_ids:
        return {"assignments": [], "error": "no_org_ids_provided"}

    if task_index is None:
        return {"assignments": [], "error": "task_index_not_configured"}

    try:
        from datetime import datetime  # noqa: PLC0415

        deadline_dt = None
        if deadline_before:
            try:
                deadline_dt = datetime.fromisoformat(deadline_before)
            except ValueError:
                pass

        entries = await task_index.list_for_assignee(
            assignee_user_id=caller_user_id,
            caller_org_ids=caller_org_ids,
            state_filter=state_filter,
            deadline_before=deadline_dt,
            workspace_id=workspace_id,
        )

        assignments = [
            {
                "task_id": e.task_id,
                "owner_user_id": e.owner_user_id,
                "org_id": e.org_id,
                "state": e.state,
                "deadline": e.deadline.isoformat() if e.deadline else None,
                "last_activity_at": e.last_activity_at.isoformat() if e.last_activity_at else None,
                "summary_text": e.summary_text,
            }
            for e in entries
        ]
        return {"assignments": assignments}
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_external_assignments_for_me failed: %s", exc)
        return {"assignments": [], "error": str(exc)}


async def get_external_task_summary(
    task_id: str,
    caller_user_id: str,
    caller_org_ids: list[str] | None = None,
    task_index: Any = None,
    **_: Any,
) -> dict:
    """Return a redacted summary of a single external task (FR-XT-002 AC2).

    Full task body is NOT returned — only: title/summary, deadline, owner display, state, last activity.
    """
    if not caller_org_ids:
        return {"error": "no_org_ids_provided"}

    if task_index is None:
        return {"error": "task_index_not_configured"}

    try:
        entries = await task_index.list_for_assignee(
            assignee_user_id=caller_user_id,
            caller_org_ids=caller_org_ids,
        )
        for entry in entries:
            if entry.task_id == task_id:
                return {
                    "task_id": entry.task_id,
                    "summary": entry.summary_text,
                    "state": entry.state,
                    "deadline": entry.deadline.isoformat() if entry.deadline else None,
                    "owner_user_id": entry.owner_user_id,
                    "last_activity_at": entry.last_activity_at.isoformat()
                    if entry.last_activity_at
                    else None,
                }
        return {"error": "task_not_found_or_not_authorized", "task_id": task_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_external_task_summary failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool registry entries
# ---------------------------------------------------------------------------

EXTERNAL_ASSIGNMENT_TOOLS: list[dict] = [
    {
        "name": "list_external_assignments_for_me",
        "description": (
            "List tasks assigned to me by other users in shared orgs. "
            "Returns redacted summaries only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state_filter": {
                    "type": "string",
                    "description": "Filter by task state (optional).",
                },
                "deadline_before": {
                    "type": "string",
                    "description": "ISO-8601 deadline upper bound.",
                },
                "workspace_id": {"type": "string"},
            },
            "required": [],
        },
        "fn": list_external_assignments_for_me,
    },
    {
        "name": "get_external_task_summary",
        "description": "Get a redacted summary of a specific external task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        "fn": get_external_task_summary,
    },
]
