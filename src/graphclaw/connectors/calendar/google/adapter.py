"""graphclaw.connectors.calendar.google.adapter — Google Calendar connector.

Description
-----------
Implements ``CalendarConnector`` for Google Calendar using the
``google-api-python-client`` library.  All blocking Google API calls are
wrapped with ``asyncio.to_thread`` to avoid blocking the event loop.

Authentication uses OAuth 2.0 refresh-token flow via
``google.oauth2.credentials.Credentials``.

Design Patterns
---------------
- Adapter: Translates between the GraphClaw CalendarEvent model and the
  Google Calendar API v3 event resource format.
- Thread Offloading: ``asyncio.to_thread`` makes synchronous SDK calls
  safe to use from async code.

Public API
----------
- GoogleCalendarConnector: CalendarConnector for Google Calendar.

Dependencies
------------
- google-api-python-client: ``googleapiclient.discovery.build`` (optional).
- google-auth: ``google.oauth2.credentials.Credentials`` (optional).
- asyncio: to_thread for blocking call offloading.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.calendar.base import CalendarConnector
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _parse_datetime(dt_str: str | None, date_str: str | None) -> datetime:
    """Parse a Google Calendar dateTime or date string into a datetime."""
    if dt_str:
        # RFC 3339 format, e.g. "2024-01-15T10:00:00Z" or "2024-01-15T10:00:00+05:30"
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    if date_str:
        # All-day event: "2024-01-15"
        from datetime import date  # noqa: PLC0415, timezone

        d = date.fromisoformat(date_str)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    raise ValueError("Both dateTime and date are None in Google Calendar response")


def _google_event_to_calendar_event(raw: dict) -> CalendarEvent:
    """Map a Google Calendar API event resource to a ``CalendarEvent``."""
    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})
    is_all_day = "date" in start_raw and "dateTime" not in start_raw

    attendees = [a.get("email", "") for a in raw.get("attendees", []) if a.get("email")]

    return CalendarEvent(
        event_id=raw.get("id"),
        title=raw.get("summary", ""),
        start=_parse_datetime(start_raw.get("dateTime"), start_raw.get("date")),
        end=_parse_datetime(end_raw.get("dateTime"), end_raw.get("date")),
        description=raw.get("description", ""),
        location=raw.get("location", ""),
        attendees=attendees,
        is_all_day=is_all_day,
        external_id=raw.get("iCalUID"),
    )


def _calendar_event_to_google_body(event: CalendarEvent) -> dict:
    """Map a ``CalendarEvent`` to a Google Calendar API event resource body."""
    if event.is_all_day:
        start: dict[str, Any] = {"date": event.start.strftime("%Y-%m-%d")}
        end: dict[str, Any] = {"date": event.end.strftime("%Y-%m-%d")}
    else:
        start = {"dateTime": event.start.isoformat(), "timeZone": "UTC"}
        end = {"dateTime": event.end.isoformat(), "timeZone": "UTC"}

    body: dict[str, Any] = {
        "summary": event.title,
        "start": start,
        "end": end,
    }
    if event.description:
        body["description"] = event.description
    if event.location:
        body["location"] = event.location
    if event.attendees:
        body["attendees"] = [{"email": email} for email in event.attendees]
    return body


class GoogleCalendarConnector(CalendarConnector):
    """Google Calendar connector using the Google API Python client library.

    Requires ``google-api-python-client`` and ``google-auth`` to be installed::

        pip install google-api-python-client google-auth

    Args:
        config: ``ConnectorConfig`` with ``connector_type="google_calendar"``
            and the following ``credentials`` keys:

            - ``client_id``: OAuth2 client ID.
            - ``client_secret``: OAuth2 client secret.
            - ``refresh_token``: OAuth2 refresh token.
            - ``token_uri``: Token endpoint URI
              (default: ``"https://oauth2.googleapis.com/token"``).
    """

    connector_type = "google_calendar"

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._service: Any = None

    async def connect(self) -> None:
        """Build the Google Calendar service object."""
        try:
            from google.oauth2.credentials import Credentials  # noqa: PLC0415
            from googleapiclient.discovery import build  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client and google-auth are required for "
                "GoogleCalendarConnector. Install with: "
                "pip install google-api-python-client google-auth"
            ) from exc

        creds_data = self._config.credentials
        token_uri = creds_data.get("token_uri", "https://oauth2.googleapis.com/token")

        creds = Credentials(
            token=None,
            refresh_token=creds_data["refresh_token"],
            client_id=creds_data["client_id"],
            client_secret=creds_data["client_secret"],
            token_uri=token_uri,
            scopes=_SCOPES,
        )

        def _build() -> Any:
            return build("calendar", "v3", credentials=creds, cache_discovery=False)

        self._service = await asyncio.to_thread(_build)
        logger.info("GoogleCalendarConnector connected")

    async def disconnect(self) -> None:
        """Release the service object (no persistent connection to close)."""
        self._service = None
        logger.info("GoogleCalendarConnector disconnected")

    async def health_check(self) -> bool:
        """Return True if the calendar list endpoint responds successfully."""
        try:

            def _check() -> bool:
                result = self._service.calendarList().list(maxResults=1).execute()
                return "items" in result or "kind" in result

            return await asyncio.to_thread(_check)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GoogleCalendarConnector health_check failed: %s", exc)
            return False

    async def list_events(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[CalendarEvent]:
        """List all events in the given time window from the specified calendar."""
        time_min = since.isoformat() if since.tzinfo else since.replace(tzinfo=timezone.utc).isoformat()
        time_max = until.isoformat() if until.tzinfo else until.replace(tzinfo=timezone.utc).isoformat()

        def _fetch() -> list[dict]:
            events_result = (
                self._service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return events_result.get("items", [])

        raw_events = await asyncio.to_thread(_fetch)
        return [_google_event_to_calendar_event(e) for e in raw_events]

    async def get_event(self, event_id: str) -> CalendarEvent:
        """Fetch a single event by ID from the primary calendar."""

        def _fetch() -> dict:
            return self._service.events().get(calendarId="primary", eventId=event_id).execute()

        try:
            raw = await asyncio.to_thread(_fetch)
        except Exception as exc:  # noqa: BLE001
            raise KeyError(f"Google Calendar event {event_id!r} not found") from exc
        return _google_event_to_calendar_event(raw)

    async def create_event(
        self,
        event: CalendarEvent,
        calendar_id: str = "primary",
    ) -> str:
        """Create a new event and return its platform-assigned ID."""
        body = _calendar_event_to_google_body(event)

        def _create() -> dict:
            return self._service.events().insert(calendarId=calendar_id, body=body).execute()

        result = await asyncio.to_thread(_create)
        return result["id"]

    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent:
        """Apply a partial update to an existing event."""

        def _patch() -> dict:
            return (
                self._service.events()
                .patch(calendarId="primary", eventId=event_id, body=patch)
                .execute()
            )

        try:
            raw = await asyncio.to_thread(_patch)
        except Exception as exc:  # noqa: BLE001
            raise KeyError(f"Google Calendar event {event_id!r} not found") from exc
        return _google_event_to_calendar_event(raw)

    async def delete_event(self, event_id: str) -> None:
        """Delete a calendar event by ID."""

        def _delete() -> None:
            self._service.events().delete(calendarId="primary", eventId=event_id).execute()

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:  # noqa: BLE001
            raise KeyError(f"Google Calendar event {event_id!r} not found") from exc

    async def check_free_busy(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[FreeBusySlot]:
        """Query the free/busy information for the given time range."""
        time_min = since.isoformat() if since.tzinfo else since.replace(tzinfo=timezone.utc).isoformat()
        time_max = until.isoformat() if until.tzinfo else until.replace(tzinfo=timezone.utc).isoformat()

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}],
        }

        def _query() -> dict:
            return self._service.freebusy().query(body=body).execute()

        result = await asyncio.to_thread(_query)
        cal_data = result.get("calendars", {}).get(calendar_id, {})
        busy_periods = cal_data.get("busy", [])

        slots: list[FreeBusySlot] = []
        for period in busy_periods:
            start_str = period["start"].replace("Z", "+00:00")
            end_str = period["end"].replace("Z", "+00:00")
            slots.append(
                FreeBusySlot(
                    start=datetime.fromisoformat(start_str),
                    end=datetime.fromisoformat(end_str),
                    status="busy",
                )
            )
        return slots


__all__ = [
    "GoogleCalendarConnector",
]
