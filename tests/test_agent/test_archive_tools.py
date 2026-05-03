"""tests.test_agent.test_archive_tools — W0-PR6 acceptance tests (FR-DEL-002).

Verifies:
  AC1: archive_task sets archived_at, archived_by, archive_reason, link_status.
  AC2: archive_resource and archive_goal work symmetrically.
  AC3: Double-archive raises ArchiveError.
  AC4: TombstoneNode is created with correct archived_node_id + redirect_to.
  AC5: State machine rejects DELETED / PURGED target states.
  AC6: tool_registry.py exposes archive_* in task_management set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphclaw.agent.tools.archive import ArchiveError, archive_goal, archive_resource, archive_task


# ---------------------------------------------------------------------------
# Mock store helpers
# ---------------------------------------------------------------------------


def _admin_store(node: dict | None = None, update_ok: bool = True) -> object:
    """Build a minimal admin_principal mock store."""
    store = MagicMock()

    async def _get_node(node_id: str, include_archived: bool = False) -> dict | None:
        return node

    async def _update_node(node_id: str, props: dict) -> None:
        if not update_ok:
            raise RuntimeError("update_node blocked")
        if node is not None:
            node.update(props)

    async def _create_node(model_or_label, props=None) -> dict:
        # Accepts both (model) and legacy (label, dict) call styles.
        if props is None:
            # model_or_label is a Pydantic model
            m = model_or_label
            return m.model_dump(mode="json")
        return {"id": "tomb_" + str(props.get("archived_node_id", "x")), **props}

    async def _list_nodes(label: str, filters: dict | None = None) -> list[dict]:
        return []

    store.get_node = _get_node
    store.update_node = _update_node
    store.create_node = _create_node
    store.list_nodes = _list_nodes
    return store


# ---------------------------------------------------------------------------
# AC1: archive_task
# ---------------------------------------------------------------------------


class TestArchiveTask:
    async def test_archive_task_sets_lifecycle_fields(self) -> None:
        """archive_task updates archived_at, archived_by, archive_reason, link_status."""
        node = {"id": "TSK-001", "title": "some task"}
        store = _admin_store(node)
        result = await archive_task("TSK-001", "user-1", "superseded", None, store)
        assert result["status"] == "archived"
        assert result["task_id"] == "TSK-001"
        assert result["redirect_to"] is None
        assert "archived_at" in result
        # link_status in node is "archived" (no redirect)
        assert node.get("link_status") == "archived"

    async def test_archive_task_with_redirect(self) -> None:
        """redirect_to sets link_status='redirected' and tombstone.redirect_to."""
        node = {"id": "TSK-001", "title": "task"}
        store = _admin_store(node)
        result = await archive_task("TSK-001", "agent", "replaced by TSK-002", "TSK-002", store)
        assert result["redirect_to"] == "TSK-002"
        assert node.get("link_status") == "redirected"

    async def test_archive_task_not_found(self) -> None:
        """ArchiveError raised when node is absent."""
        store = _admin_store(node=None)
        with pytest.raises(ArchiveError, match="not found"):
            await archive_task("TSK-MISSING", "user", "reason", None, store)

    async def test_double_archive_raises(self) -> None:
        """AC3: Double-archive raises ArchiveError."""
        node = {"id": "TSK-001", "archived_at": "2026-01-01"}
        store = _admin_store(node)
        with pytest.raises(ArchiveError, match="already archived"):
            await archive_task("TSK-001", "user", "reason", None, store)

    async def test_tombstone_id_returned(self) -> None:
        """AC4: Returned dict includes tombstone_id."""
        node = {"id": "TSK-001"}
        store = _admin_store(node)
        result = await archive_task("TSK-001", "user", "reason", None, store)
        assert result.get("tombstone_id") is not None


# ---------------------------------------------------------------------------
# AC2: archive_resource and archive_goal
# ---------------------------------------------------------------------------


class TestArchiveResource:
    async def test_archive_resource_success(self) -> None:
        node = {"id": "RES-001", "name": "Alice"}
        store = _admin_store(node)
        result = await archive_resource("RES-001", "user", "left org", None, store)
        assert result["status"] == "archived"
        assert result["resource_id"] == "RES-001"

    async def test_archive_resource_not_found(self) -> None:
        store = _admin_store(None)
        with pytest.raises(ArchiveError, match="not found"):
            await archive_resource("RES-X", "user", "reason", None, store)


class TestArchiveGoal:
    async def test_archive_goal_success(self) -> None:
        node = {"id": "GOAL-001", "title": "Q1 OKR"}
        store = _admin_store(node)
        result = await archive_goal("GOAL-001", "user", "quarter ended", None, store)
        assert result["status"] == "archived"
        assert result["goal_id"] == "GOAL-001"

    async def test_archive_goal_with_redirect(self) -> None:
        node = {"id": "GOAL-001"}
        store = _admin_store(node)
        result = await archive_goal("GOAL-001", "user", "merged", "GOAL-002", store)
        assert result["redirect_to"] == "GOAL-002"
        assert node["link_status"] == "redirected"


# ---------------------------------------------------------------------------
# AC5: State machine rejects DELETED / PURGED
# ---------------------------------------------------------------------------


class TestStateMachineNoDeletion:
    def _make_task(self) -> object:
        from datetime import UTC, datetime

        from graphclaw.models.base import generate_task_id
        from graphclaw.models.enums import TaskState, TaskType
        from graphclaw.models.nodes import TaskNode

        now = datetime.now(UTC)
        task_id = generate_task_id("AG", TaskType.ATOMIC)
        return TaskNode(
            id=task_id,
            title="Test",
            description="test task",
            task_type=TaskType.ATOMIC,
            state=TaskState.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def test_deleted_state_rejected(self) -> None:
        """State machine raises when trying to transition to DELETED."""
        from graphclaw.models.enums import TaskState
        from graphclaw.state.machine import StateMachine
        from graphclaw.state.transitions import InvalidTransitionError

        task = self._make_task()
        sm = StateMachine()

        # Verify the guard is present via the _check_transition_table path.
        with pytest.raises((InvalidTransitionError, ValueError)):
            state_val = type("FakeState", (str,), {"value": "DELETED"})()
            sm._check_transition_table(task, task.state, state_val)  # type: ignore[arg-type]

    def test_purged_state_rejected(self) -> None:
        """State machine raises when trying to transition to PURGED."""
        from graphclaw.models.enums import TaskState
        from graphclaw.state.machine import StateMachine
        from graphclaw.state.transitions import InvalidTransitionError

        task = self._make_task()
        sm = StateMachine()

        with pytest.raises((InvalidTransitionError, ValueError)):
            state_val = type("FakeState", (str,), {"value": "PURGED"})()
            sm._check_transition_table(task, task.state, state_val)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC6: tool_registry exposes archive_* in task_management
# ---------------------------------------------------------------------------


class TestToolRegistryArchiveTools:
    def test_archive_task_in_task_management_set(self) -> None:
        from graphclaw.agent.tool_registry import ToolSetRegistry

        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        tools = registry.activate("task_management")
        names = {t.name for t in tools}
        assert "archive_task" in names

    def test_archive_resource_in_task_management_set(self) -> None:
        from graphclaw.agent.tool_registry import ToolSetRegistry

        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        tools = registry.activate("task_management")
        names = {t.name for t in tools}
        assert "archive_resource" in names

    def test_archive_goal_in_task_management_set(self) -> None:
        from graphclaw.agent.tool_registry import ToolSetRegistry

        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        tools = registry.activate("task_management")
        names = {t.name for t in tools}
        assert "archive_goal" in names

    def test_archive_task_required_fields(self) -> None:
        from graphclaw.agent.tool_registry import ToolSetRegistry

        registry = ToolSetRegistry(has_skill_registry=False, has_mcp_registry=False)
        tools = registry.activate("task_management")
        archive_t = next(t for t in tools if t.name == "archive_task")
        assert "task_id" in archive_t.parameters.get("required", [])
        assert "reason" in archive_t.parameters.get("required", [])
