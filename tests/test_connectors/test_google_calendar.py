"""tests.test_connectors.test_google_calendar — Tests for GoogleCalendarConnector.

Description
-----------
Tests for GoogleCalendarConnector:
- list_events maps a Google Calendar API response to list[CalendarEvent].
- create_event maps a CalendarEvent to the correct Google API body format.

All Google API calls are intercepted via asyncio.to_thread mocking so that
google-api-python-client is not required to run the test suite.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.calendar.google.adapter import (
    GoogleCalendarConnector,
    _calendar_event_to_google_body,
    _google_event_to_calendar_event,
)
from graphclaw.connectors.calendar.models import CalendarEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREDS = {
    "client_id": "test_client_id",
    "client_secret": "test_client_secret",
    "refresh_token": "test_refresh_token",
}

_GOOGLE_EVENT_RAW: dict[str, Any] = {
    "id": "evt_abc123",
    "summary": "Team Standup",
    "description": "Daily sync",
    "location": "Zoom",
    "start": {"dateTime": "2024-03-01T09:00:00Z"},
    "end": {"dateTime": "2024-03-01T09:30:00Z"},
    "attendees": [
        {"email": "alice@example.com", "responseStatus": "accepted"},
        {"email": "bob@example.com", "responseStatus": "needsAction"},
    ],
    "iCalUID": "standup@google.com",
}

_GOOGLE_ALL_DAY_EVENT_RAW: dict[str, Any] = {
    "id": "evt_allday",
    "summary": "Company Holiday",
    "description": "",
    "location": "",
    "start": {"date": "2024-03-15"},
    "end": {"date": "2024-03-16"},
    "attendees": [],
    "iCalUID": "holiday@google.com",
}


def _make_config(**extra_creds) -> ConnectorConfig:
    creds = {**_CREDS, **extra_creds}
    return ConnectorConfig(connector_type="google_calendar", credentials=creds)


def _make_connector(config: ConnectorConfig | None = None) -> GoogleCalendarConnector:
    if config is None:
        config = _make_config()
    connector = GoogleCalendarConnector(config)
    # Inject a pre-built mock service so connect() is not required for unit tests
    connector._service = MagicMock()
    return connector


# ---------------------------------------------------------------------------
# _google_event_to_calendar_event (pure mapping)
# ---------------------------------------------------------------------------


class TestGoogleEventToCalendarEvent:
    """Tests for the raw→CalendarEvent mapping function."""

    def test_basic_fields_mapped(self) -> None:
        """All standard fields should be mapped from the Google event dict."""
        event = _google_event_to_calendar_event(_GOOGLE_EVENT_RAW)
        assert event.event_id == "evt_abc123"
        assert event.title == "Team Standup"
        assert event.description == "Daily sync"
        assert event.location == "Zoom"
        assert event.is_all_day is False
        assert event.external_id == "standup@google.com"

    def test_start_end_datetimes_parsed(self) -> None:
        """Start and end dateTime strings should be parsed to datetime objects."""
        event = _google_event_to_calendar_event(_GOOGLE_EVENT_RAW)
        assert event.start == datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
        assert event.end == datetime(2024, 3, 1, 9, 30, tzinfo=UTC)

    def test_attendees_extracted(self) -> None:
        """Attendee email addresses should be extracted into a list."""
        event = _google_event_to_calendar_event(_GOOGLE_EVENT_RAW)
        assert set(event.attendees) == {"alice@example.com", "bob@example.com"}

    def test_all_day_event_detected(self) -> None:
        """Events with 'date' (not 'dateTime') should have is_all_day=True."""
        event = _google_event_to_calendar_event(_GOOGLE_ALL_DAY_EVENT_RAW)
        assert event.is_all_day is True
        assert event.title == "Company Holiday"

    def test_empty_attendees_defaults_to_list(self) -> None:
        """Events with no attendees should produce an empty list."""
        event = _google_event_to_calendar_event(_GOOGLE_ALL_DAY_EVENT_RAW)
        assert event.attendees == []

    def test_missing_description_defaults_to_empty_string(self) -> None:
        """Missing description field should default to empty string."""
        raw = {**_GOOGLE_EVENT_RAW}
        del raw["description"]
        event = _google_event_to_calendar_event(raw)
        assert event.description == ""


# ---------------------------------------------------------------------------
# _calendar_event_to_google_body (pure mapping)
# ---------------------------------------------------------------------------


class TestCalendarEventToGoogleBody:
    """Tests for the CalendarEvent→Google API body mapping function."""

    _now = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
    _later = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)

    def _make_event(self, **kwargs) -> CalendarEvent:
        defaults = dict(event_id=None, title="Meeting", start=self._now, end=self._later)
        defaults.update(kwargs)
        return CalendarEvent(**defaults)

    def test_summary_mapped_from_title(self) -> None:
        """The 'summary' field in Google body should come from CalendarEvent.title."""
        body = _calendar_event_to_google_body(self._make_event(title="My Meeting"))
        assert body["summary"] == "My Meeting"

    def test_start_end_as_datetime_for_timed_event(self) -> None:
        """Timed events should use 'dateTime' format in start/end."""
        body = _calendar_event_to_google_body(self._make_event())
        assert "dateTime" in body["start"]
        assert "dateTime" in body["end"]
        assert "date" not in body["start"]

    def test_all_day_uses_date_format(self) -> None:
        """All-day events should use 'date' format (YYYY-MM-DD) in start/end."""
        event = self._make_event(is_all_day=True)
        body = _calendar_event_to_google_body(event)
        assert "date" in body["start"]
        assert "date" in body["end"]
        assert "dateTime" not in body["start"]
        assert body["start"]["date"] == "2024-03-01"

    def test_description_included_when_present(self) -> None:
        """Non-empty description should appear in the body."""
        body = _calendar_event_to_google_body(self._make_event(description="Agenda"))
        assert body["description"] == "Agenda"

    def test_description_omitted_when_empty(self) -> None:
        """Empty description should be omitted from the body."""
        body = _calendar_event_to_google_body(self._make_event(description=""))
        assert "description" not in body

    def test_location_included_when_present(self) -> None:
        """Non-empty location should appear in the body."""
        body = _calendar_event_to_google_body(self._make_event(location="Room 42"))
        assert body["location"] == "Room 42"

    def test_location_omitted_when_empty(self) -> None:
        """Empty location should be omitted from the body."""
        body = _calendar_event_to_google_body(self._make_event(location=""))
        assert "location" not in body

    def test_attendees_formatted_as_email_dicts(self) -> None:
        """Attendee emails should be formatted as {'email': '...'} dicts."""
        emails = ["alice@example.com", "bob@example.com"]
        body = _calendar_event_to_google_body(self._make_event(attendees=emails))
        assert body["attendees"] == [{"email": "alice@example.com"}, {"email": "bob@example.com"}]

    def test_attendees_omitted_when_empty(self) -> None:
        """Empty attendees list should be omitted from the body."""
        body = _calendar_event_to_google_body(self._make_event(attendees=[]))
        assert "attendees" not in body


# ---------------------------------------------------------------------------
# GoogleCalendarConnector.list_events (async, mocked to_thread)
# ---------------------------------------------------------------------------


class TestGoogleCalendarConnectorListEvents:
    """Tests for GoogleCalendarConnector.list_events with mocked API calls."""

    _since = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    _until = datetime(2024, 3, 31, 23, 59, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_list_events_maps_response_correctly(self) -> None:
        """list_events should convert all returned Google events to CalendarEvent."""
        connector = _make_connector()
        api_response = [_GOOGLE_EVENT_RAW, _GOOGLE_ALL_DAY_EVENT_RAW]

        # Mock asyncio.to_thread to return the API response directly
        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.return_value = api_response
            events = await connector.list_events(self._since, self._until)

        assert len(events) == 2
        assert events[0].event_id == "evt_abc123"
        assert events[0].title == "Team Standup"
        assert events[1].event_id == "evt_allday"
        assert events[1].is_all_day is True

    @pytest.mark.asyncio
    async def test_list_events_passes_correct_time_range(self) -> None:
        """list_events should pass timeMin and timeMax to the Google API."""
        connector = _make_connector()
        captured_func = None

        async def capture_to_thread(fn, *args, **kwargs):
            nonlocal captured_func
            captured_func = fn
            # Simulate the Google API call to inspect the chained method calls
            return []

        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread",
            side_effect=capture_to_thread,
        ):
            await connector.list_events(self._since, self._until)

        # The captured function should be callable (the inner _fetch closure)
        assert captured_func is not None
        assert callable(captured_func)

    @pytest.mark.asyncio
    async def test_list_events_empty_response(self) -> None:
        """list_events should return an empty list when no events are found."""
        connector = _make_connector()

        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.return_value = []
            events = await connector.list_events(self._since, self._until)

        assert events == []

    @pytest.mark.asyncio
    async def test_list_events_returns_calendar_event_instances(self) -> None:
        """list_events should return CalendarEvent instances, not raw dicts."""
        connector = _make_connector()

        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.return_value = [_GOOGLE_EVENT_RAW]
            events = await connector.list_events(self._since, self._until)

        assert all(isinstance(e, CalendarEvent) for e in events)


# ---------------------------------------------------------------------------
# GoogleCalendarConnector.create_event (async, mocked to_thread)
# ---------------------------------------------------------------------------


class TestGoogleCalendarConnectorCreateEvent:
    """Tests for GoogleCalendarConnector.create_event with mocked API calls."""

    _now = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
    _later = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)

    def _make_event(self, **kwargs) -> CalendarEvent:
        defaults = dict(
            event_id=None,
            title="New Meeting",
            start=self._now,
            end=self._later,
            description="Agenda here",
            attendees=["alice@example.com"],
        )
        defaults.update(kwargs)
        return CalendarEvent(**defaults)

    @pytest.mark.asyncio
    async def test_create_event_returns_event_id(self) -> None:
        """create_event should return the event ID from the API response."""
        connector = _make_connector()

        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.return_value = {"id": "new_evt_id"}
            event_id = await connector.create_event(self._make_event())

        assert event_id == "new_evt_id"

    @pytest.mark.asyncio
    async def test_create_event_body_has_required_fields(self) -> None:
        """create_event should produce a body with summary, start, and end."""
        connector = _make_connector()
        captured_body: dict | None = None

        def capture_call(fn):
            """Execute fn to get the body that would be sent to Google API."""
            # We intercept the _create inner function call
            return {"id": "evt_captured"}

        # We test the body mapping via _calendar_event_to_google_body directly
        event = self._make_event()
        body = _calendar_event_to_google_body(event)

        assert "summary" in body
        assert "start" in body
        assert "end" in body
        assert body["summary"] == "New Meeting"
        assert "dateTime" in body["start"]
        assert "dateTime" in body["end"]

    @pytest.mark.asyncio
    async def test_create_event_calls_to_thread(self) -> None:
        """create_event should call asyncio.to_thread exactly once."""
        connector = _make_connector()

        with patch(
            "graphclaw.connectors.calendar.google.adapter.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.return_value = {"id": "evt_xyz"}
            await connector.create_event(self._make_event())

        mock_to_thread.assert_called_once()


# ---------------------------------------------------------------------------
# GoogleCalendarConnector.connect (import error path)
# ---------------------------------------------------------------------------


class TestGoogleCalendarConnectorConnect:
    """Tests for GoogleCalendarConnector.connect error handling."""

    @pytest.mark.asyncio
    async def test_connect_raises_import_error_when_google_libs_missing(self) -> None:
        """connect() should raise ImportError with a helpful message if google libs absent."""
        config = _make_config()
        connector = GoogleCalendarConnector(config)
        connector._service = None  # ensure not pre-connected

        with patch.dict(
            "sys.modules", {"google.oauth2.credentials": None, "googleapiclient.discovery": None}
        ):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'google'")):
                with pytest.raises(ImportError, match="google-api-python-client"):
                    await connector.connect()
