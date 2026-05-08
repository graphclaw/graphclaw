# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.tools.archive — Archive tool handlers (FR-DEL-002).

Description
-----------
Provides ``archive_task``, ``archive_resource``, and ``archive_goal`` handler
functions that the ``AgentOrchestrator`` calls when the LLM invokes the
corresponding archive_* tool names.

Design
------
Archiving a node is a two-step operation:
  1. Write lifecycle fields (``archived_at``, ``archived_by``, ``archive_reason``,
     ``link_status``) on the original node via the **admin_principal** store.
  2. Create a ``TombstoneNode`` vertex pointing at the original node, with an
     optional ``redirect_to`` for successor nodes.

These operations MUST use the ``admin_principal`` store because ``agent_principal``
is blocked from writing lifecycle fields (W0-PR4 guard in
``AgeGraphStore.update_node``).

The handlers return a standardised ``ArchiveResult`` dict so the LLM gets
consistent, auditable confirmation.

Public API
----------
- ArchiveError: Exception raised when an archive operation cannot complete.
- archive_task: Archive a TaskNode.
- archive_resource: Archive a ResourceNode.
- archive_goal: Archive a GoalNode.

Dependencies
------------
- graphclaw.db.base: GraphStore.
- graphclaw.models.nodes: TombstoneNode.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.db.base import GraphStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ArchiveError(Exception):
    """Raised when an archive operation cannot proceed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _write_archive_fields(
    node_id: str,
    archived_by: str,
    reason: str,
    redirect_to: str | None,
    admin_store: GraphStore,
) -> None:
    """Update lifecycle fields on *node_id* using admin_principal store."""
    updates: dict = {
        "archived_at": _now_iso(),
        "archived_by": archived_by,
        "archive_reason": reason,
        "link_status": "redirected" if redirect_to else "archived",
    }
    await admin_store.update_node(node_id, updates)


async def _create_tombstone(
    archived_node_id: str,
    redirect_to: str | None,
    reason: str,
    admin_store: GraphStore,
) -> str:
    """Create a TombstoneNode and return its id."""
    from graphclaw.models.nodes import TombstoneNode  # noqa: PLC0415

    tombstone = TombstoneNode(
        archived_node_id=archived_node_id,
        redirect_to=redirect_to,
        reason=reason,
    )
    created = await admin_store.create_node(tombstone)
    # create_node returns the full props dict; fall back to tombstone.id.
    if isinstance(created, dict):
        return created.get("id", tombstone.id)
    return tombstone.id


# ---------------------------------------------------------------------------
# Public handlers
# ---------------------------------------------------------------------------


async def archive_task(
    task_id: str,
    archived_by: str,
    reason: str,
    redirect_to: str | None,
    admin_store: GraphStore,
) -> dict:
    """Archive a TaskNode.

    Parameters
    ----------
    task_id:
        TSK-* node ID to archive.
    archived_by:
        User ID or agent ID initiating the archive.
    reason:
        Human-readable rationale (stored in ``archive_reason`` field).
    redirect_to:
        Optional TSK-* / GOAL-* replacement node; sets ``link_status = "redirected"``.
    admin_store:
        A ``GraphStore`` opened with admin_principal (required to write
        lifecycle fields).

    Returns
    -------
    dict
        ``{status, task_id, tombstone_id, redirect_to, archived_at}``

    Raises
    ------
    ArchiveError:
        When the task node is not found or the archive write fails.
    """
    node = await admin_store.get_node(task_id, include_archived=True)
    if node is None:
        raise ArchiveError(f"Task not found: {task_id!r}")

    if node.get("archived_at"):
        raise ArchiveError(f"Task {task_id!r} is already archived")

    archived_at = _now_iso()
    updates: dict = {
        "archived_at": archived_at,
        "archived_by": archived_by,
        "archive_reason": reason,
        "link_status": "redirected" if redirect_to else "archived",
    }
    await admin_store.update_node(task_id, updates)

    tombstone_id = await _create_tombstone(task_id, redirect_to, reason, admin_store)

    logger.info(
        "archive_task.completed",
        extra={
            "task_id": task_id,
            "tombstone_id": tombstone_id,
            "archived_by": archived_by,
            "redirect_to": redirect_to,
        },
    )
    return {
        "status": "archived",
        "task_id": task_id,
        "tombstone_id": tombstone_id,
        "redirect_to": redirect_to,
        "archived_at": archived_at,
    }


async def archive_resource(
    resource_id: str,
    archived_by: str,
    reason: str,
    redirect_to: str | None,
    admin_store: GraphStore,
) -> dict:
    """Archive a ResourceNode.

    Parameters
    ----------
    resource_id:
        RES-* node ID to archive.
    archived_by:
        User ID or agent ID initiating the archive.
    reason:
        Human-readable rationale.
    redirect_to:
        Optional RES-* replacement node.
    admin_store:
        Admin-principal GraphStore.

    Returns
    -------
    dict
        ``{status, resource_id, tombstone_id, redirect_to, archived_at}``

    Raises
    ------
    ArchiveError:
        When the resource node is not found or already archived.
    """
    node = await admin_store.get_node(resource_id, include_archived=True)
    if node is None:
        raise ArchiveError(f"Resource not found: {resource_id!r}")
    if node.get("archived_at"):
        raise ArchiveError(f"Resource {resource_id!r} is already archived")

    archived_at = _now_iso()
    updates: dict = {
        "archived_at": archived_at,
        "archived_by": archived_by,
        "archive_reason": reason,
        "link_status": "redirected" if redirect_to else "archived",
    }
    await admin_store.update_node(resource_id, updates)
    tombstone_id = await _create_tombstone(resource_id, redirect_to, reason, admin_store)

    logger.info(
        "archive_resource.completed",
        extra={
            "resource_id": resource_id,
            "tombstone_id": tombstone_id,
            "archived_by": archived_by,
        },
    )
    return {
        "status": "archived",
        "resource_id": resource_id,
        "tombstone_id": tombstone_id,
        "redirect_to": redirect_to,
        "archived_at": archived_at,
    }


async def archive_goal(
    goal_id: str,
    archived_by: str,
    reason: str,
    redirect_to: str | None,
    admin_store: GraphStore,
) -> dict:
    """Archive a GoalNode.

    Parameters
    ----------
    goal_id:
        GOAL-* node ID to archive.
    archived_by:
        User ID or agent ID initiating the archive.
    reason:
        Human-readable rationale.
    redirect_to:
        Optional GOAL-* replacement node.
    admin_store:
        Admin-principal GraphStore.

    Returns
    -------
    dict
        ``{status, goal_id, tombstone_id, redirect_to, archived_at}``

    Raises
    ------
    ArchiveError:
        When the goal node is not found or already archived.
    """
    node = await admin_store.get_node(goal_id, include_archived=True)
    if node is None:
        raise ArchiveError(f"Goal not found: {goal_id!r}")
    if node.get("archived_at"):
        raise ArchiveError(f"Goal {goal_id!r} is already archived")

    archived_at = _now_iso()
    updates: dict = {
        "archived_at": archived_at,
        "archived_by": archived_by,
        "archive_reason": reason,
        "link_status": "redirected" if redirect_to else "archived",
    }
    await admin_store.update_node(goal_id, updates)
    tombstone_id = await _create_tombstone(goal_id, redirect_to, reason, admin_store)

    logger.info(
        "archive_goal.completed",
        extra={
            "goal_id": goal_id,
            "tombstone_id": tombstone_id,
            "archived_by": archived_by,
        },
    )
    return {
        "status": "archived",
        "goal_id": goal_id,
        "tombstone_id": tombstone_id,
        "redirect_to": redirect_to,
        "archived_at": archived_at,
    }
