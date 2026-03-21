"""tests.test_connectors.test_notion_import — Tests for NotionImportConnector.

Description
-----------
Tests for NotionImportConnector:
- list_projects (POST /v1/search) returns a list of database dicts.
- fetch_items (POST /v1/databases/{id}/query) returns an ImportBatch with
  correct item mapping from the Notion page format.
- to_task_nodes produces dicts with the required TaskNode fields.

The Notion REST API is mocked via unittest.mock — no real network calls are made.

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
from graphclaw.connectors.import_.notion.adapter import NotionImportConnector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_CREDS: dict[str, str] = {
    "api_key": "secret_test_notion_token_abc",
    "database_id": "db-default-001",
}

_NOTION_SEARCH_RESPONSE: dict[str, Any] = {
    "results": [
        {
            "id": "db-001",
            "object": "database",
            "title": [{"plain_text": "Tasks", "type": "text"}],
        },
        {
            "id": "db-002",
            "object": "database",
            "title": [{"plain_text": "Backlog", "type": "text"}],
        },
    ]
}

_NOTION_PAGE_FULL: dict[str, Any] = {
    "id": "page-001",
    "object": "page",
    "url": "https://notion.so/page-001",
    "properties": {
        "Name": {
            "title": [{"plain_text": "Implement dark mode", "type": "text"}]
        },
        "Notes": {
            "rich_text": [{"plain_text": "Add a toggle to settings page.", "type": "text"}]
        },
        "Status": {
            "select": {"name": "In Progress"},
        },
        "Priority": {
            "select": {"name": "High"},
        },
        "Due": {
            "date": {"start": "2025-06-15"},
        },
        "Assignee": {
            "people": [{"id": "user-001", "name": "Bob"}],
        },
        "Tags": {
            "multi_select": [{"name": "ui"}, {"name": "design"}],
        },
    },
}

_NOTION_PAGE_MINIMAL: dict[str, Any] = {
    "id": "page-002",
    "object": "page",
    "url": "https://notion.so/page-002",
    "properties": {
        "Title": {
            "title": [{"plain_text": "Quick fix", "type": "text"}]
        },
    },
}

_NOTION_QUERY_RESPONSE: dict[str, Any] = {
    "results": [_NOTION_PAGE_FULL, _NOTION_PAGE_MINIMAL],
    "has_more": False,
    "next_cursor": None,
}

_NOTION_QUERY_PAGED_RESPONSE: dict[str, Any] = {
    "results": [_NOTION_PAGE_FULL],
    "has_more": True,
    "next_cursor": "cursor-notion-xyz",
}


def _make_config() -> ConnectorConfig:
    return ConnectorConfig(connector_type="notion", credentials=_CREDS)


def _make_connector() -> NotionImportConnector:
    """Create a NotionImportConnector with an injected mock httpx client."""
    connector = NotionImportConnector(_make_config())
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


class TestNotionListProjects:
    """Tests for NotionImportConnector.list_projects (uses POST /v1/search)."""

    @pytest.mark.asyncio
    async def test_list_projects_returns_list(self) -> None:
        """list_projects should return a list."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_SEARCH_RESPONSE)
        )
        projects = await connector.list_projects()
        assert isinstance(projects, list)

    @pytest.mark.asyncio
    async def test_list_projects_count(self) -> None:
        """list_projects should return one dict per database in the response."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_SEARCH_RESPONSE)
        )
        projects = await connector.list_projects()
        assert len(projects) == 2

    @pytest.mark.asyncio
    async def test_list_projects_required_keys(self) -> None:
        """Each project dict must contain id, name, and description."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_SEARCH_RESPONSE)
        )
        projects = await connector.list_projects()
        for proj in projects:
            assert "id" in proj
            assert "name" in proj
            assert "description" in proj

    @pytest.mark.asyncio
    async def test_list_projects_maps_ids(self) -> None:
        """The 'id' field should come from the Notion database 'id' field."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_SEARCH_RESPONSE)
        )
        projects = await connector.list_projects()
        ids = [p["id"] for p in projects]
        assert "db-001" in ids
        assert "db-002" in ids

    @pytest.mark.asyncio
    async def test_list_projects_uses_post_search(self) -> None:
        """list_projects should call POST (not GET) on /search."""
        connector = _make_connector()
        post_mock = AsyncMock(return_value=_make_mock_response(_NOTION_SEARCH_RESPONSE))
        connector._client.post = post_mock
        await connector.list_projects()
        post_mock.assert_called_once()


# ---------------------------------------------------------------------------
# fetch_items
# ---------------------------------------------------------------------------


class TestNotionFetchItems:
    """Tests for NotionImportConnector.fetch_items with mocked responses."""

    @pytest.mark.asyncio
    async def test_fetch_items_returns_import_batch(self) -> None:
        """fetch_items should return an ImportBatch."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert isinstance(batch, ImportBatch)

    @pytest.mark.asyncio
    async def test_fetch_items_item_count(self) -> None:
        """All pages in 'results' should be converted to ImportItems."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert len(batch.items) == 2

    @pytest.mark.asyncio
    async def test_fetch_items_source_system(self) -> None:
        """ImportBatch.source_system should be 'notion'."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.source_system == "notion"

    @pytest.mark.asyncio
    async def test_fetch_items_project_id(self) -> None:
        """ImportBatch.project_id should match the database ID queried."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.project_id == "db-001"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_title(self) -> None:
        """The first item's title should come from the 'Name' title property."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.items[0].title == "Implement dark mode"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_external_id(self) -> None:
        """external_id should come from the Notion page 'id' field."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.items[0].external_id == "page-001"

    @pytest.mark.asyncio
    async def test_fetch_items_maps_status_in_progress(self) -> None:
        """An 'In Progress' Notion page status should map to 'in_progress'."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.items[0].status == "in_progress"

    @pytest.mark.asyncio
    async def test_fetch_items_has_more_with_cursor(self) -> None:
        """has_more should be True when Notion returns next_cursor."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_PAGED_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.has_more is True
        assert batch.next_cursor == "cursor-notion-xyz"

    @pytest.mark.asyncio
    async def test_fetch_items_no_more_when_complete(self) -> None:
        """has_more should be False when has_more is False in the response."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        assert batch.has_more is False
        assert batch.next_cursor is None

    @pytest.mark.asyncio
    async def test_fetch_items_all_items_are_import_items(self) -> None:
        """Every element in batch.items should be an ImportItem."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=_make_mock_response(_NOTION_QUERY_RESPONSE)
        )
        batch = await connector.fetch_items("db-001")
        for item in batch.items:
            assert isinstance(item, ImportItem)

    @pytest.mark.asyncio
    async def test_fetch_items_fallback_to_default_database_id(self) -> None:
        """When project_id is empty, the connector's database_id should be used."""
        connector = _make_connector()
        post_mock = AsyncMock(return_value=_make_mock_response(_NOTION_QUERY_RESPONSE))
        connector._client.post = post_mock

        batch = await connector.fetch_items("")
        # The batch project_id should fall back to the configured database_id
        assert batch.project_id == "db-default-001"


# ---------------------------------------------------------------------------
# to_task_nodes
# ---------------------------------------------------------------------------


class TestNotionToTaskNodes:
    """Tests for NotionImportConnector.to_task_nodes."""

    def _make_item(self, **kwargs: Any) -> ImportItem:
        defaults: dict[str, Any] = dict(
            external_id="page-001",
            title="Implement dark mode",
            description="Add a toggle to settings page.",
            status="in_progress",
            priority="high",
            due_date=datetime(2025, 6, 15, tzinfo=timezone.utc),
            assignee="Bob",
            labels=["ui", "design"],
            url="https://notion.so/page-001",
            source_system="notion",
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
    async def test_to_task_nodes_required_fields(self) -> None:
        """Each dict must have title, description, task_type, and status."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item()])
        node = result[0]
        for key in ("title", "description", "task_type", "status"):
            assert key in node, f"Missing required key: {key}"

    @pytest.mark.asyncio
    async def test_to_task_nodes_title_mapped(self) -> None:
        """The title in the output dict should match ImportItem.title."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(title="My Notion Page")])
        assert result[0]["title"] == "My Notion Page"

    @pytest.mark.asyncio
    async def test_to_task_nodes_status_mapped(self) -> None:
        """The status should carry through from ImportItem."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(status="done")])
        assert result[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_to_task_nodes_source_system_notion(self) -> None:
        """source_system should be 'notion'."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item()])
        assert result[0]["source_system"] == "notion"

    @pytest.mark.asyncio
    async def test_to_task_nodes_external_id_preserved(self) -> None:
        """external_id should be present in the output dict."""
        connector = _make_connector()
        result = await connector.to_task_nodes([self._make_item(external_id="page-999")])
        assert result[0]["external_id"] == "page-999"

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
            self._make_item(title="Page A", external_id="p-1"),
            self._make_item(title="Page B", external_id="p-2"),
            self._make_item(title="Page C", external_id="p-3"),
        ]
        result = await connector.to_task_nodes(items)
        assert len(result) == 3
        assert result[0]["title"] == "Page A"
        assert result[1]["title"] == "Page B"
        assert result[2]["title"] == "Page C"
