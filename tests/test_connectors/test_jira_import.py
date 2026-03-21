"""tests.test_connectors.test_jira_import — Tests for JiraImportConnector.

Description
-----------
Tests for JiraImportConnector:
- fetch_items returns ImportBatch with correct item mapping from Jira REST response.
- to_task_nodes produces dicts with all required TaskNode fields.
- Pagination (has_more / next_cursor) works correctly.
- list_projects returns correctly mapped project dicts.

The Jira API is mocked via unittest.mock — httpx is not required at test time.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.import_.jira.adapter import (
    JiraImportConnector,
    _normalise_priority,
    _normalise_status,
    _raw_issue_to_import_item,
)
from graphclaw.connectors.import_.models import ImportBatch, ImportItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER_URL = "https://myorg.atlassian.net"
_CREDS = {
    "server_url": _SERVER_URL,
    "username": "user@example.com",
    "api_token": "test_token_abc",
}

_JIRA_ISSUE_RAW: dict[str, Any] = {
    "id": "10001",
    "key": "PROJ-1",
    "fields": {
        "summary": "Fix login bug",
        "description": "Users cannot log in on mobile",
        "status": {"name": "In Progress", "statusCategory": {"name": "In Progress"}},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Alice Smith", "name": "alice"},
        "duedate": "2024-04-01",
        "labels": ["mobile", "auth"],
        "components": [{"name": "frontend"}],
        "issuetype": {"name": "Bug"},
    },
}

_JIRA_ISSUE_MINIMAL: dict[str, Any] = {
    "id": "10002",
    "key": "PROJ-2",
    "fields": {
        "summary": "Add dark mode",
        "description": None,
        "status": {"name": "To Do"},
        "priority": None,
        "assignee": None,
        "duedate": None,
        "labels": [],
        "components": [],
        "issuetype": {"name": "Story"},
    },
}

_JIRA_PROJECTS_RESPONSE = [
    {"id": "10001", "key": "PROJ", "name": "My Project", "description": "Main project"},
    {"id": "10002", "key": "INFRA", "name": "Infrastructure", "description": ""},
]

_JIRA_SEARCH_RESPONSE: dict[str, Any] = {
    "issues": [_JIRA_ISSUE_RAW, _JIRA_ISSUE_MINIMAL],
    "total": 50,
    "startAt": 0,
    "maxResults": 2,
}


def _make_config() -> ConnectorConfig:
    return ConnectorConfig(connector_type="jira", credentials=_CREDS)


def _make_connector() -> JiraImportConnector:
    connector = JiraImportConnector(_make_config())
    # Inject a mock httpx client
    connector._client = MagicMock()
    return connector


# ---------------------------------------------------------------------------
# Priority and status normalisation (pure functions)
# ---------------------------------------------------------------------------


class TestNormalisePriority:
    def test_high_maps_to_high(self) -> None:
        assert _normalise_priority("High") == "high"

    def test_highest_maps_to_critical(self) -> None:
        assert _normalise_priority("Highest") == "critical"

    def test_medium_maps_to_medium(self) -> None:
        assert _normalise_priority("Medium") == "medium"

    def test_low_maps_to_low(self) -> None:
        assert _normalise_priority("Low") == "low"

    def test_none_defaults_to_medium(self) -> None:
        assert _normalise_priority(None) == "medium"

    def test_unknown_defaults_to_medium(self) -> None:
        assert _normalise_priority("XYZ Priority") == "medium"

    def test_case_insensitive(self) -> None:
        assert _normalise_priority("HIGH") == "high"
        assert _normalise_priority("high") == "high"


class TestNormaliseStatus:
    def test_in_progress_maps(self) -> None:
        assert _normalise_status("In Progress") == "in_progress"

    def test_to_do_maps_to_open(self) -> None:
        assert _normalise_status("To Do") == "open"

    def test_done_maps_to_done(self) -> None:
        assert _normalise_status("Done") == "done"

    def test_closed_maps_to_done(self) -> None:
        assert _normalise_status("Closed") == "done"

    def test_none_defaults_to_open(self) -> None:
        assert _normalise_status(None) == "open"

    def test_unknown_defaults_to_open(self) -> None:
        assert _normalise_status("Unknown State XYZ") == "open"


# ---------------------------------------------------------------------------
# _raw_issue_to_import_item (pure mapping)
# ---------------------------------------------------------------------------


class TestRawIssueToImportItem:
    """Tests for the Jira issue → ImportItem mapping function."""

    def test_basic_fields_mapped(self) -> None:
        """All standard fields should be mapped from the Jira issue dict."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_RAW, _SERVER_URL)
        assert item.external_id == "10001"
        assert item.title == "Fix login bug"
        assert item.description == "Users cannot log in on mobile"
        assert item.status == "in_progress"
        assert item.priority == "high"
        assert item.assignee == "Alice Smith"
        assert item.source_system == "jira"

    def test_due_date_parsed(self) -> None:
        """Due date string should be parsed into a datetime."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_RAW, _SERVER_URL)
        assert item.due_date == datetime(2024, 4, 1, tzinfo=UTC)

    def test_labels_include_labels_and_components(self) -> None:
        """Both Jira labels and component names should appear in ImportItem.labels."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_RAW, _SERVER_URL)
        assert "mobile" in item.labels
        assert "auth" in item.labels
        assert "frontend" in item.labels

    def test_url_uses_browse_path(self) -> None:
        """The URL should be the Jira browse URL for the issue key."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_RAW, _SERVER_URL)
        assert item.url == f"{_SERVER_URL}/browse/PROJ-1"

    def test_raw_preserved(self) -> None:
        """The raw Jira issue dict should be stored in ImportItem.raw."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_RAW, _SERVER_URL)
        assert item.raw is _JIRA_ISSUE_RAW

    def test_minimal_issue_defaults(self) -> None:
        """Fields with None values should get safe defaults."""
        item = _raw_issue_to_import_item(_JIRA_ISSUE_MINIMAL, _SERVER_URL)
        assert item.description == ""
        assert item.priority == "medium"
        assert item.assignee is None
        assert item.due_date is None
        assert item.labels == []


# ---------------------------------------------------------------------------
# JiraImportConnector.fetch_items (async, mocked httpx)
# ---------------------------------------------------------------------------


class TestJiraImportConnectorFetchItems:
    """Tests for JiraImportConnector.fetch_items with mocked httpx responses."""

    def _make_mock_response(self, data: dict, status_code: int = 200) -> MagicMock:
        """Create a mock httpx response object."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.is_success = status_code < 400
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()  # no-op for success
        return resp

    @pytest.mark.asyncio
    async def test_fetch_items_returns_import_batch(self) -> None:
        """fetch_items should return an ImportBatch instance."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        assert isinstance(batch, ImportBatch)

    @pytest.mark.asyncio
    async def test_fetch_items_maps_items_correctly(self) -> None:
        """Each issue in the response should be mapped to an ImportItem."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        assert len(batch.items) == 2
        assert batch.items[0].external_id == "10001"
        assert batch.items[0].title == "Fix login bug"
        assert batch.items[1].external_id == "10002"
        assert batch.items[1].title == "Add dark mode"

    @pytest.mark.asyncio
    async def test_fetch_items_sets_source_system(self) -> None:
        """ImportBatch.source_system should be 'jira'."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        assert batch.source_system == "jira"

    @pytest.mark.asyncio
    async def test_fetch_items_sets_project_id(self) -> None:
        """ImportBatch.project_id should match the requested project."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        assert batch.project_id == "PROJ"

    @pytest.mark.asyncio
    async def test_fetch_items_has_more_when_more_results(self) -> None:
        """has_more should be True when total > returned items."""
        connector = _make_connector()
        # 50 total, only 2 returned → has_more = True
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        assert batch.has_more is True
        assert batch.next_cursor == "2"

    @pytest.mark.asyncio
    async def test_fetch_items_no_more_when_all_returned(self) -> None:
        """has_more should be False when all results fit in one page."""
        connector = _make_connector()
        all_in_one = {**_JIRA_SEARCH_RESPONSE, "total": 2}  # total == returned
        connector._client.post = AsyncMock(return_value=self._make_mock_response(all_in_one))

        batch = await connector.fetch_items("PROJ")
        assert batch.has_more is False
        assert batch.next_cursor is None

    @pytest.mark.asyncio
    async def test_fetch_items_with_cursor_sets_start_at(self) -> None:
        """Providing a cursor should set startAt in the JQL payload."""
        connector = _make_connector()
        post_mock = AsyncMock(
            return_value=self._make_mock_response({**_JIRA_SEARCH_RESPONSE, "total": 2})
        )
        connector._client.post = post_mock

        await connector.fetch_items("PROJ", cursor="10")

        call_args = post_mock.call_args
        payload = call_args[1].get("json") or call_args[0][1]
        assert payload["startAt"] == 10

    @pytest.mark.asyncio
    async def test_fetch_items_all_items_are_import_items(self) -> None:
        """All items in the batch should be ImportItem instances."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        batch = await connector.fetch_items("PROJ")
        for item in batch.items:
            assert isinstance(item, ImportItem)

    @pytest.mark.asyncio
    async def test_fetch_items_includes_fetched_at_timestamp(self) -> None:
        """ImportBatch.fetched_at should be a recent UTC datetime."""
        connector = _make_connector()
        connector._client.post = AsyncMock(
            return_value=self._make_mock_response(_JIRA_SEARCH_RESPONSE)
        )

        before = datetime.now(tz=UTC)
        batch = await connector.fetch_items("PROJ")
        after = datetime.now(tz=UTC)

        assert before <= batch.fetched_at <= after


# ---------------------------------------------------------------------------
# JiraImportConnector.to_task_nodes
# ---------------------------------------------------------------------------


class TestJiraImportConnectorToTaskNodes:
    """Tests for JiraImportConnector.to_task_nodes."""

    def _make_item(self, **kwargs) -> ImportItem:
        defaults = dict(
            external_id="PROJ-1",
            title="Fix login bug",
            description="Users cannot log in",
            status="in_progress",
            priority="high",
            due_date=datetime(2024, 4, 1, tzinfo=UTC),
            assignee="Alice Smith",
            labels=["mobile", "auth"],
            url=f"{_SERVER_URL}/browse/PROJ-1",
            source_system="jira",
        )
        defaults.update(kwargs)
        return ImportItem(**defaults)

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self) -> None:
        """to_task_nodes should return a list of dicts."""
        connector = _make_connector()
        items = [self._make_item()]
        result = await connector.to_task_nodes(items)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    @pytest.mark.asyncio
    async def test_required_fields_present(self) -> None:
        """Each dict must have: title, description, task_type, status, due_date."""
        connector = _make_connector()
        items = [self._make_item()]
        result = await connector.to_task_nodes(items)
        node = result[0]

        required_keys = {"title", "description", "task_type", "status", "due_date"}
        for key in required_keys:
            assert key in node, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_title_and_description_mapped(self) -> None:
        """title and description should come from ImportItem."""
        connector = _make_connector()
        item = self._make_item(title="My Task", description="Do the thing")
        result = await connector.to_task_nodes([item])
        assert result[0]["title"] == "My Task"
        assert result[0]["description"] == "Do the thing"

    @pytest.mark.asyncio
    async def test_status_mapped(self) -> None:
        """status should be passed through from ImportItem."""
        connector = _make_connector()
        item = self._make_item(status="done")
        result = await connector.to_task_nodes([item])
        assert result[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_due_date_preserved(self) -> None:
        """due_date should be preserved (including None)."""
        connector = _make_connector()
        due = datetime(2024, 6, 1, tzinfo=UTC)
        item = self._make_item(due_date=due)
        result = await connector.to_task_nodes([item])
        assert result[0]["due_date"] == due

    @pytest.mark.asyncio
    async def test_due_date_none_preserved(self) -> None:
        """due_date=None should produce a None value in the output dict."""
        connector = _make_connector()
        item = self._make_item(due_date=None)
        result = await connector.to_task_nodes([item])
        assert result[0]["due_date"] is None

    @pytest.mark.asyncio
    async def test_empty_items_returns_empty_list(self) -> None:
        """to_task_nodes([]) should return []."""
        connector = _make_connector()
        result = await connector.to_task_nodes([])
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_items_all_converted(self) -> None:
        """All items should be converted with correct ordering."""
        connector = _make_connector()
        items = [
            self._make_item(title="Task A", external_id="PROJ-1"),
            self._make_item(title="Task B", external_id="PROJ-2"),
            self._make_item(title="Task C", external_id="PROJ-3"),
        ]
        result = await connector.to_task_nodes(items)
        assert len(result) == 3
        assert result[0]["title"] == "Task A"
        assert result[1]["title"] == "Task B"
        assert result[2]["title"] == "Task C"


# ---------------------------------------------------------------------------
# JiraImportConnector.list_projects (async, mocked httpx)
# ---------------------------------------------------------------------------


class TestJiraImportConnectorListProjects:
    """Tests for JiraImportConnector.list_projects."""

    def _make_mock_response(self, data: Any, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.is_success = status_code < 400
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_list_projects_returns_list(self) -> None:
        """list_projects should return a list of project dicts."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=self._make_mock_response(_JIRA_PROJECTS_RESPONSE)
        )

        projects = await connector.list_projects()
        assert isinstance(projects, list)

    @pytest.mark.asyncio
    async def test_list_projects_has_required_keys(self) -> None:
        """Each project dict must have id, name, and description keys."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=self._make_mock_response(_JIRA_PROJECTS_RESPONSE)
        )

        projects = await connector.list_projects()
        for project in projects:
            assert "id" in project
            assert "name" in project
            assert "description" in project

    @pytest.mark.asyncio
    async def test_list_projects_maps_names(self) -> None:
        """Project names should be mapped from the Jira response."""
        connector = _make_connector()
        connector._client.get = AsyncMock(
            return_value=self._make_mock_response(_JIRA_PROJECTS_RESPONSE)
        )

        projects = await connector.list_projects()
        names = [p["name"] for p in projects]
        assert "My Project" in names
        assert "Infrastructure" in names
