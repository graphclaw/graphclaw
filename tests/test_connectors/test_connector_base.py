# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_connectors.test_connector_base — Tests for the connectors framework.

Description
-----------
Tests for:
- ConnectorRegistry listing expected connector types.
- create_connector raising ValueError for unknown types.
- CalendarEvent frozen dataclass immutability.
- ImportBatch has_more and next_cursor fields.
- ConnectorConfig frozen dataclass behaviour.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from graphclaw.connectors.base import ConnectorABC, ConnectorConfig
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot
from graphclaw.connectors.factory import create_connector
from graphclaw.connectors.import_.models import ImportBatch, ImportItem
from graphclaw.connectors.registry import ConnectorRegistry, default_registry

# ---------------------------------------------------------------------------
# ConnectorRegistry
# ---------------------------------------------------------------------------


class TestConnectorRegistry:
    """Tests for the ConnectorRegistry class."""

    def test_list_types_contains_all_expected_types(self) -> None:
        """The default registry must include all five built-in connector types."""
        types = default_registry.list_types()
        expected = {"google_calendar", "outlook_calendar", "jira", "asana", "notion"}
        assert expected.issubset(set(types)), f"Missing connector types. Got: {types}"

    def test_list_types_returns_sorted_list(self) -> None:
        """list_types() should return types in sorted order."""
        types = default_registry.list_types()
        assert types == sorted(types)

    def test_get_known_type_returns_class(self) -> None:
        """get() should return the registered class for a known type."""
        from graphclaw.connectors.calendar.google.adapter import GoogleCalendarConnector

        cls = default_registry.get("google_calendar")
        assert cls is GoogleCalendarConnector

    def test_get_unknown_type_raises_value_error(self) -> None:
        """get() should raise ValueError for an unregistered type."""
        registry = ConnectorRegistry()
        with pytest.raises(ValueError, match="Unknown connector type"):
            registry.get("nonexistent_type")

    def test_register_custom_connector(self) -> None:
        """register() should add a new connector type to the registry."""

        class DummyConnector(ConnectorABC):
            connector_type = "dummy_test"

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def health_check(self) -> bool:
                return True

        registry = ConnectorRegistry()
        registry.register(DummyConnector)
        assert "dummy_test" in registry.list_types()
        assert registry.get("dummy_test") is DummyConnector

    def test_empty_registry_list_types_returns_empty(self) -> None:
        """A freshly created registry should have no types."""
        registry = ConnectorRegistry()
        assert registry.list_types() == []


# ---------------------------------------------------------------------------
# create_connector factory
# ---------------------------------------------------------------------------


class TestCreateConnector:
    """Tests for the create_connector factory function."""

    def test_raises_for_unknown_type(self) -> None:
        """create_connector should raise ValueError for unknown connector types."""
        config = ConnectorConfig(
            connector_type="unknown_xyz",
            credentials={},
        )
        with pytest.raises(ValueError, match="Unknown connector type"):
            create_connector("unknown_xyz", config)

    def test_creates_google_calendar_connector(self) -> None:
        """create_connector should return a GoogleCalendarConnector for 'google_calendar'."""
        from graphclaw.connectors.calendar.google.adapter import (
            GoogleCalendarConnector,  # noqa: PLC0415
        )

        config = ConnectorConfig(
            connector_type="google_calendar",
            credentials={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "refresh_token": "test_token",
            },
        )
        conn = create_connector("google_calendar", config)
        assert isinstance(conn, GoogleCalendarConnector)

    def test_creates_jira_connector(self) -> None:
        """create_connector should return a JiraImportConnector for 'jira'."""
        from graphclaw.connectors.import_.jira.adapter import JiraImportConnector  # noqa: PLC0415

        config = ConnectorConfig(
            connector_type="jira",
            credentials={
                "server_url": "https://example.atlassian.net",
                "username": "user@example.com",
                "api_token": "test_token",
            },
        )
        conn = create_connector("jira", config)
        assert isinstance(conn, JiraImportConnector)

    def test_creates_asana_connector(self) -> None:
        """create_connector should return an AsanaImportConnector for 'asana'."""
        from graphclaw.connectors.import_.asana.adapter import AsanaImportConnector  # noqa: PLC0415

        config = ConnectorConfig(
            connector_type="asana",
            credentials={"access_token": "test", "workspace_gid": "123"},
        )
        conn = create_connector("asana", config)
        assert isinstance(conn, AsanaImportConnector)

    def test_creates_notion_connector(self) -> None:
        """create_connector should return a NotionImportConnector for 'notion'."""
        from graphclaw.connectors.import_.notion.adapter import (
            NotionImportConnector,  # noqa: PLC0415
        )

        config = ConnectorConfig(
            connector_type="notion",
            credentials={"api_key": "secret_test", "database_id": "abc123"},
        )
        conn = create_connector("notion", config)
        assert isinstance(conn, NotionImportConnector)

    def test_error_message_lists_available_types(self) -> None:
        """ValueError message should list all available connector types."""
        config = ConnectorConfig(connector_type="bad", credentials={})
        with pytest.raises(ValueError) as exc_info:
            create_connector("bad", config)
        msg = str(exc_info.value)
        assert "jira" in msg
        assert "asana" in msg


# ---------------------------------------------------------------------------
# ConnectorConfig
# ---------------------------------------------------------------------------


class TestConnectorConfig:
    """Tests for ConnectorConfig frozen dataclass."""

    def test_frozen_prevents_mutation(self) -> None:
        """ConnectorConfig should be immutable (frozen=True)."""
        config = ConnectorConfig(
            connector_type="jira",
            credentials={"api_token": "test"},
        )
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            config.connector_type = "asana"  # type: ignore[misc]

    def test_default_options_empty_dict(self) -> None:
        """ConnectorConfig.options should default to an empty dict."""
        config = ConnectorConfig(connector_type="jira", credentials={})
        assert config.options == {}

    def test_equality_by_value(self) -> None:
        """Two ConnectorConfigs with the same data should be equal."""
        c1 = ConnectorConfig(connector_type="jira", credentials={"token": "x"})
        c2 = ConnectorConfig(connector_type="jira", credentials={"token": "x"})
        assert c1 == c2


# ---------------------------------------------------------------------------
# CalendarEvent
# ---------------------------------------------------------------------------


class TestCalendarEvent:
    """Tests for the CalendarEvent frozen dataclass."""

    _now = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
    _later = datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc)

    def _make_event(self, **kwargs) -> CalendarEvent:
        defaults = dict(
            event_id="evt-001",
            title="Test Event",
            start=self._now,
            end=self._later,
        )
        defaults.update(kwargs)
        return CalendarEvent(**defaults)

    def test_frozen_cannot_mutate_title(self) -> None:
        """CalendarEvent should be immutable (frozen=True)."""
        event = self._make_event()
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            event.title = "Modified"  # type: ignore[misc]

    def test_frozen_cannot_mutate_start(self) -> None:
        """CalendarEvent.start should be immutable."""
        event = self._make_event()
        new_dt = datetime(2024, 1, 20, 9, 0, tzinfo=timezone.utc)
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            event.start = new_dt  # type: ignore[misc]

    def test_defaults(self) -> None:
        """CalendarEvent optional fields should have sensible defaults."""
        event = self._make_event()
        assert event.description == ""
        assert event.location == ""
        assert event.attendees == []
        assert event.is_all_day is False
        assert event.external_id is None

    def test_equality_by_value(self) -> None:
        """Two CalendarEvents with the same data should be equal."""
        e1 = self._make_event()
        e2 = self._make_event()
        assert e1 == e2

    def test_attendees_list_preserved(self) -> None:
        """Attendees list should be stored exactly as provided."""
        attendees = ["alice@example.com", "bob@example.com"]
        event = self._make_event(attendees=attendees)
        assert event.attendees == attendees

    def test_all_day_event(self) -> None:
        """is_all_day flag should be stored correctly."""
        event = self._make_event(is_all_day=True)
        assert event.is_all_day is True


# ---------------------------------------------------------------------------
# FreeBusySlot
# ---------------------------------------------------------------------------


class TestFreeBusySlot:
    """Tests for the FreeBusySlot frozen dataclass."""

    def test_frozen(self) -> None:
        """FreeBusySlot should be immutable."""
        slot = FreeBusySlot(
            start=datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
            status="busy",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            slot.status = "free"  # type: ignore[misc]

    def test_status_values(self) -> None:
        """All three status values should be storable."""
        base = dict(
            start=datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
        )
        for status in ("busy", "free", "tentative"):
            slot = FreeBusySlot(status=status, **base)
            assert slot.status == status


# ---------------------------------------------------------------------------
# ImportItem
# ---------------------------------------------------------------------------


class TestImportItem:
    """Tests for the ImportItem frozen dataclass."""

    def test_frozen(self) -> None:
        """ImportItem should be immutable (frozen=True)."""
        item = ImportItem(external_id="JIRA-123", title="Fix bug")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
            item.title = "Changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        """ImportItem optional fields should have sensible defaults."""
        item = ImportItem(external_id="123", title="Test")
        assert item.description == ""
        assert item.status == "open"
        assert item.priority == "medium"
        assert item.due_date is None
        assert item.assignee is None
        assert item.labels == []
        assert item.url is None
        assert item.source_system == ""
        assert item.raw == {}


# ---------------------------------------------------------------------------
# ImportBatch
# ---------------------------------------------------------------------------


class TestImportBatch:
    """Tests for ImportBatch pagination fields."""

    def test_has_more_defaults_to_false(self) -> None:
        """ImportBatch.has_more should default to False."""
        batch = ImportBatch(
            items=[],
            source_system="jira",
            project_id="PROJ",
            fetched_at=datetime.now(tz=timezone.utc),
        )
        assert batch.has_more is False

    def test_next_cursor_defaults_to_none(self) -> None:
        """ImportBatch.next_cursor should default to None."""
        batch = ImportBatch(
            items=[],
            source_system="jira",
            project_id="PROJ",
            fetched_at=datetime.now(tz=timezone.utc),
        )
        assert batch.next_cursor is None

    def test_has_more_and_cursor_set_together(self) -> None:
        """When has_more=True, next_cursor should carry the pagination token."""
        batch = ImportBatch(
            items=[],
            source_system="jira",
            project_id="PROJ",
            fetched_at=datetime.now(tz=timezone.utc),
            next_cursor="100",
            has_more=True,
        )
        assert batch.has_more is True
        assert batch.next_cursor == "100"

    def test_items_list_accessible(self) -> None:
        """ImportBatch.items should store and return ImportItem instances."""
        item = ImportItem(external_id="1", title="Task one")
        batch = ImportBatch(
            items=[item],
            source_system="asana",
            project_id="proj-abc",
            fetched_at=datetime.now(tz=timezone.utc),
        )
        assert len(batch.items) == 1
        assert batch.items[0] is item

    def test_batch_is_mutable(self) -> None:
        """ImportBatch should be a regular (mutable) dataclass."""
        batch = ImportBatch(
            items=[],
            source_system="notion",
            project_id="db-xyz",
            fetched_at=datetime.now(tz=timezone.utc),
        )
        batch.has_more = True
        batch.next_cursor = "next-page-token"
        assert batch.has_more is True
        assert batch.next_cursor == "next-page-token"
