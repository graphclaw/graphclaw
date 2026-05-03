"""Tests for Wave 9 backend: FR-AM-001 multi-agent admin + FR-AE-001 reconciler.

Covers:
- OrgTaskIndexReconciler.run: upserts tasks from graph store
- OrgTaskIndexReconciler.run: handles empty task list
- OrgTaskIndexReconciler.run: captures per-task errors without crashing
- OrgTaskIndexReconciler._build_entry: maps graph node to OrgTaskIndexEntry
- ReconciliationResult.to_dict: produces JSON-safe dict
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.cross_tenant.task_index import OrgTaskIndex

# ---------------------------------------------------------------------------
# OrgTaskIndexReconciler
# ---------------------------------------------------------------------------


class TestOrgTaskIndexReconciler:
    """Reconciler runs a full-sync diff against AGE truth (FR-AE-001)."""

    @pytest.mark.asyncio
    async def test_empty_graph_no_upserts(self):
        """No task nodes → no upserts, result is clean."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        store.list_nodes = AsyncMock(return_value=[])
        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()

        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)
        result = await reconciler.run()

        assert result.tasks_scanned == 0
        assert result.rows_upserted == 0
        assert result.errors == []
        index.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_each_task(self):
        """One task node → one upsert call."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "TSK-001",
                    "owned_by": "u1",
                    "org_id": "org1",
                    "state": "PENDING",
                    "title": "Fix the bug",
                    "assigned_to": "u2",
                }
            ]
        )
        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()

        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)
        result = await reconciler.run()

        assert result.tasks_scanned == 1
        assert result.rows_upserted == 1
        assert result.errors == []
        index.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_per_task_errors_captured(self):
        """Upsert failure for one task is captured; does not abort the run."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        store.list_nodes = AsyncMock(
            return_value=[
                {
                    "id": "TSK-001",
                    "owned_by": "u1",
                    "org_id": "org1",
                    "state": "PENDING",
                    "title": "T1",
                },
                {
                    "id": "TSK-002",
                    "owned_by": "u2",
                    "org_id": "org1",
                    "state": "DONE",
                    "title": "T2",
                },
            ]
        )
        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock(side_effect=[RuntimeError("DB down"), None])

        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)
        result = await reconciler.run()

        assert result.tasks_scanned == 2
        assert result.rows_upserted == 1
        assert len(result.errors) == 1
        assert "TSK-001" in result.errors[0]

    @pytest.mark.asyncio
    async def test_store_list_failure_captured(self):
        """If list_nodes raises, run() returns an error result without crashing."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        store.list_nodes = AsyncMock(side_effect=RuntimeError("AGE down"))
        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()

        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)
        result = await reconciler.run()

        assert len(result.errors) == 1
        assert "AGE down" in result.errors[0]
        index.upsert.assert_not_called()

    def test_build_entry_maps_fields(self):
        """_build_entry maps graph node fields to OrgTaskIndexEntry correctly."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        index = MagicMock(spec=OrgTaskIndex)
        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)

        node = {
            "id": "TSK-999",
            "owned_by": "owner-1",
            "org_id": "org-99",
            "state": "IN_PROGRESS",
            "title": "My task title",
            "assigned_to": "assignee-1",
        }
        entry = reconciler._build_entry(node)

        assert entry.task_id == "TSK-999"
        assert entry.owner_user_id == "owner-1"
        assert entry.org_id == "org-99"
        assert entry.state == "IN_PROGRESS"
        assert entry.summary_text == "My task title"
        assert entry.assignee_linked_user_ids == ["assignee-1"]

    def test_build_entry_assignee_list(self):
        """_build_entry handles a list-typed assigned_to field."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        index = MagicMock(spec=OrgTaskIndex)
        reconciler = OrgTaskIndexReconciler(store=store, task_index=index)

        node = {
            "id": "TSK-1",
            "owned_by": "u",
            "org_id": "o",
            "state": "PENDING",
            "title": "T",
            "assigned_to": ["a1", "a2"],
        }
        entry = reconciler._build_entry(node)
        assert entry.assignee_linked_user_ids == ["a1", "a2"]

    def test_result_to_dict(self):
        """ReconciliationResult.to_dict produces a JSON-safe dict."""
        from graphclaw.cross_tenant.reconciler import ReconciliationResult

        result = ReconciliationResult(tasks_scanned=5, rows_upserted=3, rows_unchanged=2)
        result.finished_at = result.started_at  # set for test
        d = result.to_dict()

        assert d["tasks_scanned"] == 5
        assert d["rows_upserted"] == 3
        assert isinstance(d["started_at"], str)
        assert isinstance(d["finished_at"], str)

    @pytest.mark.asyncio
    async def test_org_scoped_rebuild_filters(self):
        """Org-scoped rebuild passes org_id filter to list_nodes."""
        from graphclaw.cross_tenant.reconciler import OrgTaskIndexReconciler

        store = MagicMock()
        store.list_nodes = AsyncMock(return_value=[])
        index = MagicMock(spec=OrgTaskIndex)
        index.upsert = AsyncMock()

        reconciler = OrgTaskIndexReconciler(store=store, task_index=index, org_id="org-specific")
        await reconciler.run()

        store.list_nodes.assert_awaited_once_with("Task", {"org_id": "org-specific"})


# ---------------------------------------------------------------------------
# LinkStatus.ARCHIVED enum value
# ---------------------------------------------------------------------------


class TestLinkStatusArchived:
    """FR-AM-001: agents can be archived via link_status."""

    def test_archived_value_exists(self):
        """LinkStatus.ARCHIVED is a valid enum member."""
        from graphclaw.models.enums import LinkStatus

        assert LinkStatus.ARCHIVED.value == "archived"

    def test_archived_is_string(self):
        """LinkStatus is a str enum."""
        from graphclaw.models.enums import LinkStatus

        assert isinstance(LinkStatus.ARCHIVED, str)
        assert LinkStatus.ARCHIVED == "archived"
