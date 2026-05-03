"""tests.test_api.test_org_lifecycle — Unit tests for org archive endpoints (FR-DEL-009).

Tests cover (AC1 + AC2):
- archive_org: 404 if org missing, 409 if already archived.
- archive_org: sets archived_at/purge_after on org node.
- archive_org: archives workspace nodes (AC2).
- archive_org: does NOT archive UserNodes (AC1 verified via mock calls).
- archive_org: members_untouched count is correct.
- archive_org: writes audit entry.
- cancel_org_archive: 409 if not archived, clears fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from graphclaw.api.admin.org_lifecycle import (
    CancelOrgArchiveRequest,
    OrgArchiveRequest,
    archive_org,
    cancel_org_archive,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(archived_at=None, members=None):
    org = MagicMock()
    org.id = "ORG-test"
    org.archived_at = archived_at
    org.members = members or []
    return org


def _make_workspace(ws_id: str, archived_at=None):
    ws = MagicMock()
    ws.id = ws_id
    ws.archived_at = archived_at
    return ws


def _make_store(org=None, workspaces=None):
    store = MagicMock()
    store.get_node = AsyncMock(return_value=org)
    store.update_node = AsyncMock()
    store.list_nodes = AsyncMock(return_value=workspaces or [])
    return store


def _make_storage():
    storage = MagicMock()
    storage.read = AsyncMock(side_effect=FileNotFoundError)
    storage.write = AsyncMock()
    return storage


def _make_member(user_id: str):
    m = MagicMock()
    m.user_id = user_id
    return m


# ---------------------------------------------------------------------------
# archive_org
# ---------------------------------------------------------------------------


class TestArchiveOrg:
    async def test_raises_404_if_org_missing(self) -> None:
        store = _make_store(org=None)
        with pytest.raises(HTTPException) as exc:
            await archive_org(OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage())
        assert exc.value.status_code == 404

    async def test_raises_409_if_already_archived(self) -> None:
        org = _make_org(archived_at=datetime.now(UTC))
        store = _make_store(org=org)
        with pytest.raises(HTTPException) as exc:
            await archive_org(OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage())
        assert exc.value.status_code == 409

    async def test_sets_archived_at_on_org(self) -> None:
        org = _make_org()
        store = _make_store(org=org)
        await archive_org(OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage())
        # First call to update_node is the org itself
        first_call = store.update_node.call_args_list[0]
        assert first_call[0][0] == "ORG-x"
        updates = first_call[0][1]
        assert updates["archived_at"] is not None
        assert updates["purge_after"] is not None

    async def test_archives_workspaces(self) -> None:
        org = _make_org()
        ws1 = _make_workspace("WS-001")
        ws2 = _make_workspace("WS-002")
        store = _make_store(org=org, workspaces=[ws1, ws2])
        result = await archive_org(
            OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
        )
        assert result.workspaces_archived == 2

    async def test_skips_already_archived_workspaces(self) -> None:
        org = _make_org()
        ws_active = _make_workspace("WS-001", archived_at=None)
        ws_archived = _make_workspace("WS-002", archived_at=datetime.now(UTC))
        store = _make_store(org=org, workspaces=[ws_active, ws_archived])
        result = await archive_org(
            OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
        )
        assert result.workspaces_archived == 1  # only the un-archived one

    async def test_does_not_touch_user_nodes(self) -> None:
        """AC1: UserNodes must never be updated during org archive."""
        members = [_make_member("USER-001"), _make_member("USER-002")]
        org = _make_org(members=members)
        store = _make_store(org=org)
        result = await archive_org(
            OrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
        )
        # Confirm update_node was never called with a USER- id
        for call in store.update_node.call_args_list:
            node_id = call[0][0]
            assert not node_id.startswith("USER-"), (
                f"update_node called on UserNode {node_id} — violates AC1"
            )
        assert result.members_untouched == 2

    async def test_writes_audit_entry(self) -> None:
        org = _make_org()
        storage = _make_storage()
        store = _make_store(org=org)
        await archive_org(OrgArchiveRequest(org_id="ORG-x"), "admin", store, storage)
        storage.write.assert_called()
        written = storage.write.call_args[0][1].decode()
        assert "org_archived" in written

    async def test_returns_org_id_in_response(self) -> None:
        org = _make_org()
        store = _make_store(org=org)
        result = await archive_org(
            OrgArchiveRequest(org_id="ORG-test"), "admin", store, _make_storage()
        )
        assert result.org_id == "ORG-test"
        assert result.ok is True


# ---------------------------------------------------------------------------
# cancel_org_archive
# ---------------------------------------------------------------------------


class TestCancelOrgArchive:
    async def test_raises_409_if_not_archived(self) -> None:
        org = _make_org(archived_at=None)
        store = _make_store(org=org)
        with pytest.raises(HTTPException) as exc:
            await cancel_org_archive(
                CancelOrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 409

    async def test_raises_404_if_missing(self) -> None:
        store = _make_store(org=None)
        with pytest.raises(HTTPException) as exc:
            await cancel_org_archive(
                CancelOrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
            )
        assert exc.value.status_code == 404

    async def test_clears_archive_fields(self) -> None:
        org = _make_org(archived_at=datetime.now(UTC))
        store = _make_store(org=org)
        await cancel_org_archive(
            CancelOrgArchiveRequest(org_id="ORG-x"), "admin", store, _make_storage()
        )
        updates = store.update_node.call_args[0][1]
        assert updates["archived_at"] is None
        assert updates["purge_after"] is None

    async def test_writes_audit_entry(self) -> None:
        org = _make_org(archived_at=datetime.now(UTC))
        storage = _make_storage()
        store = _make_store(org=org)
        await cancel_org_archive(
            CancelOrgArchiveRequest(org_id="ORG-x"), "admin", store, storage
        )
        storage.write.assert_called()
        written = storage.write.call_args[0][1].decode()
        assert "org_archive_cancelled" in written
