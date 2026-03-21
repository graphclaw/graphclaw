"""tests.test_agent.test_escalation — Unit tests for EscalationService.

Description
-----------
Tests for ``EscalationService.check_and_escalate`` and ``escalate_task``.
All graph store and delegation service calls are mocked via ``AsyncMock``.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up mocks, calls the service, and asserts
  the expected side-effects / return values.
- Helper ``make_approval_task_dict``: Creates an overdue or non-overdue APPROVAL
  task dict suitable for ``TaskNode.model_validate``.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock, MagicMock.
- graphclaw.agent.escalation: EscalationService, EscalationError, EscalationEvent.
- graphclaw.agent.delegation: DelegationResult (used to mock delegate_task return).
- graphclaw.models.enums: TaskState, TaskType.
- graphclaw.models.type_metadata: ApprovalMetadata.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from graphclaw.agent.delegation import DelegationResult
from graphclaw.agent.escalation import (
    EscalationError,
    EscalationEvent,
    EscalationService,
)
from graphclaw.models.enums import TaskState, TaskType
from graphclaw.models.type_metadata import ApprovalMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_approval_task_dict(
    action: str = "CANCEL",
    target: str | None = None,
    days_old: int = 30,
    max_wait: int = 7,
    state: TaskState = TaskState.PENDING,
) -> dict:
    """Build a minimal serialised APPROVAL TaskNode dict."""
    meta = ApprovalMetadata(
        approver_id="USER-bob",
        approval_criteria="Do it",
        max_wait_days=max_wait,
        escalation_action=action,
        escalation_target_user_id=target,
        delegated_by_user_id="USER-alice",
    )
    old_time = datetime.now(UTC) - timedelta(days=days_old)
    return {
        "id": "TSK-AB-0002-APR",
        "task_type": TaskType.APPROVAL,
        "title": "Approval task",
        "description": "",
        "created_by": "USER-alice",
        "owned_by": "USER-alice",
        "assigned_to": "USER-bob",
        "state": state,
        "type_metadata": meta.model_dump(),
        "version": 0,
        "created_at": old_time.isoformat(),
        "updated_at": old_time.isoformat(),
    }


def _make_service(task_dicts=None):
    """Return (EscalationService, mock_store, mock_delegation)."""
    store = AsyncMock()
    store.list_nodes = AsyncMock(return_value=task_dicts or [])
    store.get_node = AsyncMock(return_value=task_dicts[0] if task_dicts else None)
    store.update_node = AsyncMock(return_value={})

    delegation = AsyncMock()
    delegation.delegate_task = AsyncMock(
        return_value=DelegationResult(
            task_id="TSK-AB-0002-APR",
            delegated_to_user_id="USER-carol",
        )
    )

    svc = EscalationService(graph_store=store, delegation_service=delegation)
    return svc, store, delegation


# ---------------------------------------------------------------------------
# check_and_escalate — no tasks / not overdue
# ---------------------------------------------------------------------------


class TestCheckAndEscalateNoOp:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_approval_tasks(self):
        svc, store, _ = _make_service(task_dicts=[])
        events = await svc.check_and_escalate("USER-bob")
        assert events == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_task_not_overdue(self):
        # Create a task that is only 1 day old with max_wait=7 — not yet overdue
        task = make_approval_task_dict(action="CANCEL", days_old=1, max_wait=7)
        svc, store, _ = _make_service(task_dicts=[task])
        events = await svc.check_and_escalate("USER-bob")
        assert events == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_task_in_terminal_state(self):
        task = make_approval_task_dict(
            action="CANCEL", days_old=30, max_wait=7, state=TaskState.COMPLETE
        )
        svc, store, _ = _make_service(task_dicts=[task])
        # The task is overdue but already COMPLETE — should be skipped
        store.get_node = AsyncMock(return_value=task)
        events = await svc.check_and_escalate("USER-bob")
        assert events == []


# ---------------------------------------------------------------------------
# check_and_escalate — overdue tasks
# ---------------------------------------------------------------------------


class TestCheckAndEscalateOverdue:
    @pytest.mark.asyncio
    async def test_cancel_action_calls_update_node_with_cancelled_state(self):
        task = make_approval_task_dict(action="CANCEL", days_old=30, max_wait=7)
        svc, store, _ = _make_service(task_dicts=[task])
        # escalate_task calls get_node internally
        store.get_node = AsyncMock(return_value=task)

        events = await svc.check_and_escalate("USER-bob")

        assert len(events) == 1
        assert events[0].action_taken == "CANCELLED"

        # update_node must have been called with CANCELLED state
        update_calls = store.update_node.call_args_list
        assert any(TaskState.CANCELLED in str(c) or "CANCELLED" in str(c) for c in update_calls)

    @pytest.mark.asyncio
    async def test_auto_approve_action_calls_update_node_with_complete_state(self):
        task = make_approval_task_dict(action="AUTO_APPROVE", days_old=30, max_wait=7)
        svc, store, _ = _make_service(task_dicts=[task])
        store.get_node = AsyncMock(return_value=task)

        events = await svc.check_and_escalate("USER-bob")

        assert len(events) == 1
        assert events[0].action_taken == "AUTO_APPROVED"

        update_calls = store.update_node.call_args_list
        assert any(TaskState.COMPLETE in str(c) or "COMPLETE" in str(c) for c in update_calls)

    @pytest.mark.asyncio
    async def test_reassign_action_calls_delegation_service(self):
        task = make_approval_task_dict(
            action="REASSIGN", target="USER-carol", days_old=30, max_wait=7
        )
        svc, store, delegation = _make_service(task_dicts=[task])
        store.get_node = AsyncMock(return_value=task)

        events = await svc.check_and_escalate("USER-bob")

        assert len(events) == 1
        assert events[0].action_taken == "REASSIGNED"
        assert events[0].escalated_to_user_id == "USER-carol"
        delegation.delegate_task.assert_called()

    @pytest.mark.asyncio
    async def test_escalation_event_has_task_id(self):
        task = make_approval_task_dict(action="CANCEL", days_old=30, max_wait=7)
        svc, store, _ = _make_service(task_dicts=[task])
        store.get_node = AsyncMock(return_value=task)

        events = await svc.check_and_escalate("USER-bob")

        assert events[0].task_id == "TSK-AB-0002-APR"


# ---------------------------------------------------------------------------
# escalate_task — error cases
# ---------------------------------------------------------------------------


class TestEscalateTaskErrors:
    @pytest.mark.asyncio
    async def test_raises_escalation_error_when_task_not_found(self):
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=None)
        delegation = AsyncMock()

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        with pytest.raises(EscalationError, match="not found"):
            await svc.escalate_task(
                "TSK-AB-0002-APR",
                {"escalation_action": "CANCEL"},
            )

    @pytest.mark.asyncio
    async def test_raises_escalation_error_for_unknown_action(self):
        task = make_approval_task_dict(action="CANCEL", days_old=30, max_wait=7)
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=task)
        store.update_node = AsyncMock(return_value={})
        delegation = AsyncMock()

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        with pytest.raises(EscalationError, match="Unknown escalation_action"):
            await svc.escalate_task(
                "TSK-AB-0002-APR",
                {"escalation_action": "EXPLODE"},
            )

    @pytest.mark.asyncio
    async def test_reassign_without_target_raises_escalation_error(self):
        task = make_approval_task_dict(action="REASSIGN", target=None, days_old=30, max_wait=7)
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=task)
        delegation = AsyncMock()

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        with pytest.raises(EscalationError, match="escalation_target_user_id"):
            await svc.escalate_task(
                "TSK-AB-0002-APR",
                {"escalation_action": "REASSIGN", "escalation_target_user_id": None},
            )


# ---------------------------------------------------------------------------
# escalate_task — direct invocation (success paths)
# ---------------------------------------------------------------------------


class TestEscalateTaskDirect:
    @pytest.mark.asyncio
    async def test_cancel_returns_escalation_event_with_cancelled(self):
        task = make_approval_task_dict(action="CANCEL", days_old=30, max_wait=7)
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=task)
        store.update_node = AsyncMock(return_value={})
        delegation = AsyncMock()

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        event = await svc.escalate_task(
            "TSK-AB-0002-APR",
            {"escalation_action": "CANCEL"},
        )

        assert isinstance(event, EscalationEvent)
        assert event.action_taken == "CANCELLED"
        assert event.escalated_to_user_id is None

    @pytest.mark.asyncio
    async def test_auto_approve_returns_escalation_event_with_auto_approved(self):
        task = make_approval_task_dict(action="AUTO_APPROVE", days_old=30, max_wait=7)
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=task)
        store.update_node = AsyncMock(return_value={})
        delegation = AsyncMock()

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        event = await svc.escalate_task(
            "TSK-AB-0002-APR",
            {"escalation_action": "AUTO_APPROVE"},
        )

        assert event.action_taken == "AUTO_APPROVED"

    @pytest.mark.asyncio
    async def test_reassign_returns_escalation_event_with_reassigned(self):
        task = make_approval_task_dict(
            action="REASSIGN", target="USER-carol", days_old=30, max_wait=7
        )
        store = AsyncMock()
        store.get_node = AsyncMock(return_value=task)
        store.update_node = AsyncMock(return_value={})
        delegation = AsyncMock()
        delegation.delegate_task = AsyncMock(
            return_value=DelegationResult(
                task_id="TSK-AB-0002-APR",
                delegated_to_user_id="USER-carol",
            )
        )

        svc = EscalationService(graph_store=store, delegation_service=delegation)

        event = await svc.escalate_task(
            "TSK-AB-0002-APR",
            {
                "escalation_action": "REASSIGN",
                "escalation_target_user_id": "USER-carol",
                "max_wait_days": 7,
            },
        )

        assert event.action_taken == "REASSIGNED"
        assert event.escalated_to_user_id == "USER-carol"
