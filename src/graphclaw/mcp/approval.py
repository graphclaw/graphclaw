"""graphclaw.mcp.approval — GatedApprovalService: APPROVAL task creation for GATED tools.

Description
-----------
Provides ``GatedApprovalService``, which bridges the MCP trust-tier system and
the GraphClaw task graph.  When a GATED MCP tool call is requested, this service
creates an APPROVAL ``TaskNode`` that the owning user must act on before the
tool call proceeds.

Design Patterns
---------------
- Polling: ``wait_for_approval`` polls the graph on a configurable interval
  rather than relying on push events, keeping the service backend-agnostic.
- Delegation pattern: Reuses the same ``ApprovalMetadata`` and ``TaskNode``
  construction used by ``DelegationService`` for consistency.

Public API
----------
- GatedApprovalService: Creates and polls APPROVAL tasks for GATED MCP tool calls.

Dependencies
------------
- graphclaw.agent.delegation: _extract_initials (reused helper).
- graphclaw.db.base: GraphStore.
- graphclaw.mcp.client: MCPApprovalTimeoutError (imported here to avoid circular).
- graphclaw.models.base: generate_task_id, utcnow.
- graphclaw.models.enums: TaskState, TaskType.
- graphclaw.models.nodes: TaskNode.
- graphclaw.models.type_metadata: ApprovalMetadata.
"""

from __future__ import annotations

import asyncio
import json
import logging

from graphclaw.agent.delegation import _extract_initials
from graphclaw.db.base import GraphStore
from graphclaw.models.base import generate_task_id, utcnow
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.nodes import TaskNode
from graphclaw.models.type_metadata import ApprovalMetadata

logger = logging.getLogger(__name__)

# Avoid circular import: MCPApprovalTimeoutError is defined in client.py
# We import it here only when needed inside wait_for_approval.


class GatedApprovalService:
    """Creates APPROVAL tasks for GATED MCP tool calls and polls for resolution.

    Parameters
    ----------
    graph_store:
        A concrete ``GraphStore`` implementation for all node CRUD operations.
    """

    def __init__(self, graph_store: GraphStore) -> None:
        self._store = graph_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        user_id: str,
        tool_name: str,
        server_name: str,
        arguments: dict,
    ) -> str:
        """Create an APPROVAL ``TaskNode`` for a GATED MCP tool call.

        The task encodes the tool name and truncated argument JSON in its
        ``ApprovalMetadata.approval_criteria`` field so the user can see what
        they are approving.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` who must approve the call.
        tool_name:
            MCP tool name being requested.
        server_name:
            Human-readable name of the MCP server.
        arguments:
            Tool arguments (serialised into the approval criteria).

        Returns
        -------
        str
            The ``TSK-…-APR`` ID of the newly created APPROVAL task.
        """
        now = utcnow()
        initials = _extract_initials(user_id)
        task_id = generate_task_id(initials, TaskType.APPROVAL)

        # Build a truncated JSON summary of the arguments for display
        try:
            args_json = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            args_json = str(arguments)
        criteria_payload = f"tool={tool_name}; args={args_json}"
        approval_criteria = criteria_payload[:500]

        approval_metadata = ApprovalMetadata(
            approver_id=user_id,
            approval_criteria=approval_criteria,
            max_wait_days=1,
            escalation_action="CANCEL",
        )

        task = TaskNode(
            id=task_id,
            task_type=TaskType.APPROVAL,
            title=f"Approve MCP tool: {tool_name} on {server_name}",
            description=(
                f"The agent wants to call the MCP tool '{tool_name}' on server "
                f"'{server_name}'. Review the arguments below and approve or deny.\n\n"
                f"Arguments: {approval_criteria}"
            ),
            created_by=user_id,
            owned_by=user_id,
            assigned_to=user_id,
            state=TaskState.PENDING,
            type_metadata=approval_metadata,
            created_at=now,
            updated_at=now,
            version=0,
        )

        await self._store.create_node(task)
        logger.info(
            "mcp.approval.request_approval",
            extra={
                "task_id": task_id,
                "user_id": user_id,
                "tool_name": tool_name,
                "server_name": server_name,
            },
        )
        return task_id

    async def wait_for_approval(
        self,
        approval_task_id: str,
        timeout_seconds: int = 3600,
        poll_interval_seconds: int = 5,
    ) -> bool:
        """Poll the graph until the APPROVAL task is resolved.

        The polling loop checks the task state at *poll_interval_seconds*
        intervals until:

        - ``TaskState.COMPLETE``   → returns ``True`` (approved).
        - ``TaskState.CANCELLED``  → returns ``False`` (denied).
        - *timeout_seconds* elapsed → raises ``MCPApprovalTimeoutError``.

        Parameters
        ----------
        approval_task_id:
            The ``TSK-…-APR`` task ID to poll.
        timeout_seconds:
            Maximum number of seconds to wait (default: 3600 = 1 hour).
        poll_interval_seconds:
            Seconds between graph polls (default: 5).

        Returns
        -------
        bool
            ``True`` if approved, ``False`` if denied/cancelled.

        Raises
        ------
        MCPApprovalTimeoutError
            If the approval task is not resolved within *timeout_seconds*.
        """
        from graphclaw.mcp.client import MCPApprovalTimeoutError  # avoid circular

        elapsed = 0.0
        while elapsed < timeout_seconds:
            raw = await self._store.get_node(approval_task_id)
            if raw is not None:
                state_value = raw.get("state")
                if state_value == TaskState.COMPLETE.value:
                    logger.info(
                        "mcp.approval.approved",
                        extra={"task_id": approval_task_id},
                    )
                    return True
                if state_value in (
                    TaskState.CANCELLED.value,
                    TaskState.BLOCKED.value,
                ):
                    logger.info(
                        "mcp.approval.denied",
                        extra={"task_id": approval_task_id, "state": state_value},
                    )
                    return False

            await asyncio.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds

        raise MCPApprovalTimeoutError(
            f"Approval task '{approval_task_id}' was not resolved within "
            f"{timeout_seconds} seconds."
        )

    async def get_pending_approvals(self, user_id: str) -> list[dict]:
        """List all unresolved APPROVAL tasks for *user_id*.

        Returns tasks in ``PENDING`` or ``IN_PROGRESS`` state where the
        assigned user is *user_id*.

        Parameters
        ----------
        user_id:
            The ``USER-{id}`` whose approval queue to query.

        Returns
        -------
        list[dict]
            Raw node dicts for matching APPROVAL tasks.
        """
        pending = await self._store.list_nodes(
            label="TaskNode",
            filters={
                "task_type": TaskType.APPROVAL.value,
                "assigned_to": user_id,
                "state": TaskState.PENDING.value,
            },
        )
        in_progress = await self._store.list_nodes(
            label="TaskNode",
            filters={
                "task_type": TaskType.APPROVAL.value,
                "assigned_to": user_id,
                "state": TaskState.IN_PROGRESS.value,
            },
        )
        return list(pending) + list(in_progress)


__all__ = ["GatedApprovalService"]
