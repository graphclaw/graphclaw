"""tests.test_agent.test_delegation — Unit tests for DelegationService.

Description
-----------
Tests for ``DelegationService.delegate_task``, ``revoke_delegation``, helper
``_extract_initials``, and result/error types.  All graph store calls are
mocked via ``AsyncMock``.

Design Patterns
---------------
- Arrange/Act/Assert: Each test sets up a mock store, calls the service, and
  asserts side-effects.
- Helper: ``make_task_dict`` builds a minimal serialised TaskNode dict so that
  ``TaskNode.model_validate`` succeeds in the service.

Dependencies
------------
- pytest, pytest-asyncio: Test runner with async support.
- unittest.mock: AsyncMock.
- graphclaw.agent.delegation: DelegationService, DelegationResult, DelegationError,
  _extract_initials.
- graphclaw.models.enums: TaskState, TaskType, EdgeType.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, call

from graphclaw.agent.delegation import (
    DelegationError,
    DelegationResult,
    DelegationService,
    _extract_initials,
)
from graphclaw.models.enums import EdgeType, TaskState, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task_dict(
    task_id: str = "TSK-AB-0001-ATM",
    owned_by: str = "USER-alice",
    title: str = "Test Task",
) -> dict:
    """Build a minimal serialised TaskNode dict for use in mock get_node returns."""
    return {
        "id": task_id,
        "task_type": TaskType.ATOMIC,
        "title": title,
        "description": "",
        "created_by": owned_by,
        "owned_by": owned_by,
        "assigned_to": None,
        "state": TaskState.PENDING,
        "type_metadata": None,
        "version": 0,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }


def _make_store(task_dict=None):
    store = AsyncMock()
    store.get_node = AsyncMock(return_value=task_dict)
    store.create_node = AsyncMock(return_value={})
    store.create_edge = AsyncMock(return_value={})
    store.update_node = AsyncMock(return_value={})
    store.list_nodes = AsyncMock(return_value=[])
    return store


# ---------------------------------------------------------------------------
# _extract_initials helper
# ---------------------------------------------------------------------------


class TestExtractInitials:
    def test_typical_user_id(self):
        assert _extract_initials("USER-abc123") == "AB"

    def test_no_alpha_chars_returns_xx(self):
        assert _extract_initials("USER-123") == "XX"

    def test_single_alpha_char_returns_x_suffix(self):
        assert _extract_initials("USER-x") == "XX"

    def test_two_alpha_chars(self):
        assert _extract_initials("USER-mn") == "MN"

    def test_mixed_alpha_and_digits(self):
        result = _extract_initials("USER-a1b2c3")
        assert result == "AB"

    def test_without_user_prefix(self):
        # should still work — strips prefix conditionally
        result = _extract_initials("alice")
        assert result == "AL"


# ---------------------------------------------------------------------------
# delegate_task — precondition failures
# ---------------------------------------------------------------------------


class TestDelegatePreconditions:
    @pytest.mark.asyncio
    async def test_raises_delegation_error_when_task_not_found(self):
        store = _make_store(task_dict=None)
        svc = DelegationService(store)

        with pytest.raises(DelegationError, match="not found"):
            await svc.delegate_task(
                "TSK-AB-0001-ATM",
                from_user_id="USER-alice",
                to_user_id="USER-bob",
            )

    @pytest.mark.asyncio
    async def test_raises_delegation_error_when_not_owner(self):
        task = make_task_dict(owned_by="USER-alice")
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        with pytest.raises(DelegationError, match="not the owner"):
            await svc.delegate_task(
                "TSK-AB-0001-ATM",
                from_user_id="USER-carol",   # not the owner
                to_user_id="USER-bob",
            )


# ---------------------------------------------------------------------------
# delegate_task — visibility grant creation
# ---------------------------------------------------------------------------


class TestDelegateVisibilityGrant:
    @pytest.mark.asyncio
    async def test_with_grant_creates_visibility_grant_node(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=True,
        )

        # create_node called at least once for the VisibilityGrantNode
        assert store.create_node.called
        assert result.visibility_grant_id is not None
        assert result.visibility_grant_id.startswith("GRANT-")

    @pytest.mark.asyncio
    async def test_with_grant_creates_grants_access_to_edge(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=True,
        )

        edge_types_used = [
            str(c.kwargs.get("edge_type") or c.args[2])
            for c in store.create_edge.call_args_list
        ]
        assert any("GRANTS_ACCESS_TO" in et for et in edge_types_used)

    @pytest.mark.asyncio
    async def test_without_grant_skips_grant_creation(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
        )

        assert result.visibility_grant_id is None
        # create_node should NOT have been called (no grant, no approval)
        store.create_node.assert_not_called()


# ---------------------------------------------------------------------------
# delegate_task — approval task creation
# ---------------------------------------------------------------------------


class TestDelegateApprovalTask:
    @pytest.mark.asyncio
    async def test_with_approval_creates_approval_task_node(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
            require_approval=True,
        )

        assert result.approval_task_id is not None
        assert "APR" in result.approval_task_id

    @pytest.mark.asyncio
    async def test_with_approval_creates_spawned_from_edge(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
            require_approval=True,
        )

        edge_types_used = [
            str(c.kwargs.get("edge_type") or c.args[2])
            for c in store.create_edge.call_args_list
        ]
        assert any("SPAWNED_FROM" in et for et in edge_types_used)

    @pytest.mark.asyncio
    async def test_without_approval_does_not_create_approval_task(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
            require_approval=False,
        )

        assert result.approval_task_id is None


# ---------------------------------------------------------------------------
# delegate_task — result fields
# ---------------------------------------------------------------------------


class TestDelegateTaskResult:
    @pytest.mark.asyncio
    async def test_result_has_correct_task_id(self):
        task = make_task_dict(task_id="TSK-AB-0001-ATM")
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
        )

        assert result.task_id == "TSK-AB-0001-ATM"

    @pytest.mark.asyncio
    async def test_result_has_correct_delegated_to_user_id(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
        )

        assert result.delegated_to_user_id == "USER-bob"

    @pytest.mark.asyncio
    async def test_result_is_delegation_result_type(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        result = await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
        )

        assert isinstance(result, DelegationResult)

    @pytest.mark.asyncio
    async def test_update_node_called_to_reassign_task(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        await svc.delegate_task(
            "TSK-AB-0001-ATM",
            from_user_id="USER-alice",
            to_user_id="USER-bob",
            create_visibility_grant=False,
        )

        store.update_node.assert_called()
        update_args = store.update_node.call_args[0]
        # First arg is task_id, second is updates dict
        assert update_args[0] == "TSK-AB-0001-ATM"
        assert update_args[1].get("assigned_to") == "USER-bob"


# ---------------------------------------------------------------------------
# revoke_delegation
# ---------------------------------------------------------------------------


class TestRevokeDelegation:
    @pytest.mark.asyncio
    async def test_revoke_calls_update_node_to_clear_assignment(self):
        task = make_task_dict()
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        await svc.revoke_delegation("TSK-AB-0001-ATM", from_user_id="USER-alice")

        store.update_node.assert_called()
        update_args = store.update_node.call_args_list[0][0]
        assert update_args[0] == "TSK-AB-0001-ATM"
        assert update_args[1].get("assigned_to") is None

    @pytest.mark.asyncio
    async def test_revoke_raises_delegation_error_when_task_not_found(self):
        store = _make_store(task_dict=None)
        svc = DelegationService(store)

        with pytest.raises(DelegationError, match="not found"):
            await svc.revoke_delegation("TSK-AB-0001-ATM", from_user_id="USER-alice")

    @pytest.mark.asyncio
    async def test_revoke_raises_delegation_error_when_not_owner(self):
        task = make_task_dict(owned_by="USER-alice")
        store = _make_store(task_dict=task)
        svc = DelegationService(store)

        with pytest.raises(DelegationError, match="not the owner"):
            await svc.revoke_delegation("TSK-AB-0001-ATM", from_user_id="USER-carol")
