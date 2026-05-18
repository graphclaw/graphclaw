# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_connectors.test_asana_import — Tests for AsanaImportConnector.

Description
-----------
Tests for AsanaImportConnector:
- list_projects returns a list of project dicts from the Asana API.
- fetch_items returns an ImportBatch with correct item mapping.
- to_task_nodes produces dicts with the required TaskNode fields.

The Asana REST API is mocked via unittest.mock — no real network calls are made.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.import_.asana.adapter import AsanaImportConnector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_CREDS: dict[str, str] = {
    "access_token": "test_asana_token_abc",
    "workspace_gid": "12345678",
}

_ASANA_PROJECTS_RESPONSE: dict[str, Any] = {
    "data": [
        {"gid": "proj-001", "name": "Alpha Project", "notes": "Main project"},
        {"gid": "proj-002", "name": "Beta Project", "notes": ""},
    ]
}

_ASANA_TASK_FULL: dict[str, Any] = {
    "gid": "task-001",
    "name": "Fix the login screen",
    "notes": "Users report that the login fails on iOS.",
    "completed": False,
    "due_on": "2025-05-01",
    "assignee": {"gid": "user-1", "name": "Alice"},
    "tags": [{"gid": "tag-1", "name": "bug"}, {"gid": "tag-2", "name": "ios"}],
    "memberships": [],
    "permalink_url": "https://app.asana.com/0/proj-001/task-001",
    "custom_fields": [],
    "modified_at": "2025-04-01T12:00:00Z",
}

_ASANA_TASK_COMPLETED: dict[str, Any] = {
    "gid": "task-002",
    "name": "Deploy v2",
    "notes": "",
    "completed": True,
    "due_on": None,
    "assignee": None,
    "tags": [],
    "memberships": [],
    "permalink_url": "https://app.asana.com/0/proj-001/task-002",
    "custom_fields": [],
    "modified_at": "2025-04-02T09:00:00Z",
}

_ASANA_TASKS_RESPONSE: dict[str, Any] = {
    "data": [_ASANA_TASK_FULL, _ASANA_TASK_COMPLETED],
    "next_page": None,
}

_ASANA_TASKS_PAGED_RESPONSE: dict[str, Any] = {
    "data": [_ASANA_TASK_FULL],
    "next_page": {"offset": "cursor-abc", "path": "/tasks?offset=cursor-abc"},
}


def _make_config() -> ConnectorConfig:
    return ConnectorConfig(connector_type="asana", credentials=_CREDS)


def _make_connector() -> AsanaImportConnector:
    """Create an AsanaImportConnector with an injected mock httpx client."""
    connector = AsanaImportConnector(_make_config())
    connector._client = MagicMock()
    return connector


def _make_mock_response(data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = status_code < 400
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


class TestAsanaListProjects:
    """Tests for AsanaImportConnector.list_projects."""

    @pytest.mark.asyncio
    async def test_list_projects_returns_list(self) -> None:
        """list_projects should return a list."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_PROJECTS_RESPONSE)
        )
        projects = await connector.list_projects()
        assert isinstance(projects, list)

    @pytest.mark.asyncio
    async def test_list_projects_count(self) -> None:
        """list_projects should return one dict per project in the response."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_PROJECTS_RESPONSE)
        )
        projects = await connector.list_projects()
        assert len(projects) == 2

    @pytest.mark.asyncio
    async def test_list_projects_required_keys(self) -> None:
        """Each project dict must have id, name, and description."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_PROJECTS_RESPONSE)
        )
        projects = await connector.list_projects()
        for proj in projects:
            assert "id" in proj
            assert "name" in proj
            assert "description" in proj

    @pytest.mark.asyncio
    async def test_list_projects_maps_gid_to_id(self) -> None:
        """The 'id' field should come from the Asana 'gid' field."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_PROJECTS_RESPONSE)
        )
        projects = await connector.list_projects()
        ids = [p["id"] for p in projects]
        assert "proj-001" in ids
        assert "proj-002" in ids

    @pytest.mark.asyncio
    async def test_list_projects_maps_names(self) -> None:
        """Project names should match the Asana response."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_PROJECTS_RESPONSE)
        )
        projects = await connector.list_projects()
        names = [p["name"] for p in projects]
        assert "Alpha Project" in names
        assert "Beta Project" in names


# ---------------------------------------------------------------------------
# fetch_items
# ---------------------------------------------------------------------------


class TestAsanaFetchItems:
    """Tests for AsanaImportConnector.fetch_items with mocked httpx responses."""

    @pytest.mark.asyncio
    async def test_fetch_items_returns_import_batch(self) -> None:
        """fetch_items should return an ImportBatch."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert isinstance(batch, ImportBatch)

    @pytest.mark.asyncio
    async def test_fetch_items_item_count(self) -> None:
        """All tasks in the response data array should be returned as ImportItems."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert len(batch.items) == 2

    @pytest.mark.asyncio
    async def test_fetch_items_source_system(self) -> None:
        """ImportBatch.source_system should be 'asana'."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.source_system == "asana"

    @pytest.mark.asyncio
    async def test_fetch_items_project_id(self) -> None:
        """ImportBatch.project_id should match the requested project."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.project_id == "proj-001"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_title(self) -> None:
        """The first item's title should come from the Asana task 'name' field."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.items[0].title == "Fix the login screen"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_external_id(self) -> None:
        """The external_id should come from the Asana task 'gid' field."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.items[0].external_id == "task-001"

    @pytest.mark.asyncio
    async def test_fetch_items_open_status_for_incomplete(self) -> None:
        """An uncompleted Asana task should have status='open'."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.items[0].status == "open"

    @pytest.mark.asyncio
    async def test_fetch_items_done_status_for_completed(self) -> None:
        """A completed Asana task should have status='done'."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.items[1].status == "done"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_tags_to_labels(self) -> None:
        """Asana task tags should be mapped to ImportItem.labels."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert "bug" in batch.items[0].labels
        assert "ios" in batch.items[0].labels

    @pytest.mark.asyncio
    async def test_fetch_items_has_more_with_pagination(self) -> None:
        """has_more should be True when next_page offset is present."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=_make_mock_response(_ASANA_TASKS_PAGED_RESPONSE)
        )
        batch = await connector.fetch_items("proj-001")
        assert batch.has_more is True
        assert batch.next_cursor == "cursor-abc"

    @pytest.mark.asyncio
    async def test_fetch_items_no_more_without_pagination(self) -> None:
        """has_more should be False when next_page is None."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        assert batch.has_more is False
        assert batch.next_cursor is None

    @pytest.mark.asyncio
    async def test_fetch_items_all_items_are_import_items(self) -> None:
        """All items in the batch should be ImportItem instances."""
        connector = _make_connector()
        connector._client.get = AsyncMock(return_value=_make_mock_response(_ASANA_TASKS_RESPONSE))
        batch = await connector.fetch_items("proj-001")
        for item in batch.items:
            assert isinstance(item, ImportItem)


# ---------------------------------------------------------------------------
# to_task_nodes
# ---------------------------------------------------------------------------


class TestAsanaToTaskNodes:
    """Tests for AsanaImportConnector.to_task_nodes."""

    def _make_item(self, **kwargs: Any) -> ImportItem:
        defaults: dict[str, Any] = dict(
            external_id="task-001",
            title="Fix the login screen",
            description="Users report that the login fails on iOS.",
            status="open",
            priority="medium",
            due_date=datetime(2025, 5, 1, tzinfo=timezone.utc),
            assignee="Alice",
            labels=["bug", "ios"],
            url="https://app.asana.com/0/proj-001/task-001",
            source_system="asana",
        )
        defaults.update(kwargs)
        return ImportItem(**defaults)

    @pytest.mark.asyncio
    async def test_to_task_nodes_returns_list_of_dicts(self) -> None:
        """to_task_nodes should return a list of dicts."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item()])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    @pytest.mark.asyncio
    async def test_to_task_nodes_title_field(self) -> None:
        """The 'title' field should be mapped from ImportItem.title."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(title="My Asana Task")])
        assert result[0]["title"] == "My Asana Task"

    @pytest.mark.asyncio
    async def test_to_task_nodes_task_type_present(self) -> None:
        """Each dict must include a 'task_type' key."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item()])
        assert "task_type" in result[0]

    @pytest.mark.asyncio
    async def test_to_task_nodes_status_mapped(self) -> None:
        """The status field should carry through from ImportItem."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(status="done")])
        assert result[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_to_task_nodes_source_system_asana(self) -> None:
        """source_system should be 'asana'."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item()])
        assert result[0]["source_system"] == "asana"

    @pytest.mark.asyncio
    async def test_to_task_nodes_external_id_preserved(self) -> None:
        """external_id should be present in the output dict."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(external_id="task-999")])
        assert result[0]["external_id"] == "task-999"

    @pytest.mark.asyncio
    async def test_to_task_nodes_empty_input(self) -> None:
        """to_task_nodes([]) should return an empty list."""
        connector = _make_connector()
        result = await connector.to_task_nodes([])
        assert result == []

    @pytest.mark.asyncio
    async def test_to_task_nodes_multiple_items(self) -> None:
        """All items should be converted preserving order."""
        connector = _make_connector()
        items = [
            self._make_item(title="Task A", external_id="t-1"),
            self._make_item(title="Task B", external_id="t-2"),
        ]
        result = await connector.to_task_nodes(items)
        assert len(result) == 2
        assert result[0]["title"] == "Task A"
        assert result[1]["title"] == "Task B"
