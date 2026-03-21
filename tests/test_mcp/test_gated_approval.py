"""tests.test_mcp.test_gated_approval — Unit tests for GatedApprovalService.

Description
-----------
Tests ``GatedApprovalService.request_approval``, ``wait_for_approval``, and
``get_pending_approvals`` using a mock ``GraphStore``.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock.
- graphclaw.mcp.approval: GatedApprovalService.
- graphclaw.mcp.client: MCPApprovalTimeoutError.
- graphclaw.models.enums: TaskState, TaskType.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from graphclaw.mcp.approval import GatedApprovalService
from graphclaw.mcp.client import MCPApprovalTimeoutError
from graphclaw.models.enums import TaskState, TaskType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store() -> AsyncMock:
    store = AsyncMock()
    store.create_node = AsyncMock(return_value={})
    store.get_node = AsyncMock(return_value=None)
    store.list_nodes = AsyncMock(return_value=[])
    return store


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------


class TestGatedApprovalServiceRequestApproval:
    @pytest.mark.asyncio
    async def test_creates_approval_task_node(self):
        store = make_store()
        svc = GatedApprovalService(store)

        task_id = await svc.request_approval(
            user_id="USER-alice",
            tool_name="create_event",
            server_name="Google Calendar",
            arguments={"title": "Team meeting"},
        )

        store.create_node.assert_awaited_once()
        created_node = store.create_node.call_args.args[0]

        assert created_node.task_type == TaskType.APPROVAL
        assert "create_event" in created_node.title
        assert "Google Calendar" in created_node.title

    @pytest.mark.asyncio
    async def test_approval_metadata_has_correct_fields(self):
        store = make_store()
        svc = GatedApprovalService(store)

        await svc.request_approval(
            user_id="USER-bob",
            tool_name="delete_file",
            server_name="FS Server",
            arguments={"path": "/tmp/test.txt"},
        )

        created_node = store.create_node.call_args.args[0]
        meta = created_node.type_metadata

        assert meta.approver_id == "USER-bob"
        assert meta.max_wait_days == 1
        assert meta.escalation_action == "CANCEL"
        assert "delete_file" in meta.approval_criteria
        assert len(meta.approval_criteria) <= 500

    @pytest.mark.asyncio
    async def test_returns_valid_task_id(self):
        store = make_store()
        svc = GatedApprovalService(store)

        task_id = await svc.request_approval(
            user_id="USER-carol",
            tool_name="send_email",
            server_name="Email Server",
            arguments={},
        )

        assert task_id.startswith("TSK-")
        assert task_id.endswith("-APR")

    @pytest.mark.asyncio
    async def test_arguments_truncated_in_criteria(self):
        """Approval criteria must not exceed 500 chars even with large arguments."""
        store = make_store()
        svc = GatedApprovalService(store)

        large_args = {"data": "x" * 1000}
        await svc.request_approval(
            user_id="USER-alice",
            tool_name="write_data",
            server_name="DB Server",
            arguments=large_args,
        )

        created_node = store.create_node.call_args.args[0]
        assert len(created_node.type_metadata.approval_criteria) <= 500


# ---------------------------------------------------------------------------
# wait_for_approval
# ---------------------------------------------------------------------------


class TestGatedApprovalServiceWaitForApproval:
    @pytest.mark.asyncio
    async def test_returns_true_when_task_reaches_complete(self):
        store = make_store()
        store.get_node = AsyncMock(return_value={"state": TaskState.COMPLETE.value})

        svc = GatedApprovalService(store)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc.wait_for_approval(
                "TSK-AL-0001-APR",
                timeout_seconds=60,
                poll_interval_seconds=1,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_task_reaches_cancelled(self):
        store = make_store()
        store.get_node = AsyncMock(return_value={"state": TaskState.CANCELLED.value})

        svc = GatedApprovalService(store)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc.wait_for_approval(
                "TSK-AL-0001-APR",
                timeout_seconds=60,
                poll_interval_seconds=1,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_raises_timeout_error_when_time_exceeded(self):
        """When the task never resolves, MCPApprovalTimeoutError is raised."""
        store = make_store()
        # Always return PENDING — never resolved
        store.get_node = AsyncMock(return_value={"state": TaskState.PENDING.value})

        svc = GatedApprovalService(store)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MCPApprovalTimeoutError, match="TSK-AL-0001-APR"):
                await svc.wait_for_approval(
                    "TSK-AL-0001-APR",
                    timeout_seconds=3,  # short timeout
                    poll_interval_seconds=2,
                )

    @pytest.mark.asyncio
    async def test_polls_until_resolved(self):
        """Service polls multiple times before reaching COMPLETE."""
        store = make_store()
        call_count = 0

        async def get_node_side_effect(node_id):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"state": TaskState.PENDING.value}
            return {"state": TaskState.COMPLETE.value}

        store.get_node = AsyncMock(side_effect=get_node_side_effect)

        svc = GatedApprovalService(store)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc.wait_for_approval(
                "TSK-AL-0001-APR",
                timeout_seconds=60,
                poll_interval_seconds=1,
            )

        assert result is True
        assert call_count == 3


# ---------------------------------------------------------------------------
# get_pending_approvals
# ---------------------------------------------------------------------------


class TestGatedApprovalServiceGetPendingApprovals:
    @pytest.mark.asyncio
    async def test_returns_pending_and_in_progress_tasks(self):
        pending_task = {"id": "TSK-AL-0001-APR", "state": "PENDING"}
        in_progress_task = {"id": "TSK-AL-0002-APR", "state": "IN_PROGRESS"}

        store = make_store()
        store.list_nodes = AsyncMock(side_effect=[[pending_task], [in_progress_task]])

        svc = GatedApprovalService(store)
        results = await svc.get_pending_approvals("USER-alice")

        assert len(results) == 2
        ids = [r["id"] for r in results]
        assert "TSK-AL-0001-APR" in ids
        assert "TSK-AL-0002-APR" in ids

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_pending(self):
        store = make_store()
        store.list_nodes = AsyncMock(return_value=[])

        svc = GatedApprovalService(store)
        results = await svc.get_pending_approvals("USER-nobody")
        assert results == []
