# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.connectors.calendar.outlook.adapter — Outlook Calendar connector.

Description
-----------
Implements ``CalendarConnector`` for Microsoft Outlook / Exchange via the
Microsoft Graph API using ``httpx.AsyncClient`` (no extra SDK required beyond
``httpx`` which is already a project dependency).

Supports both delegated access (access_token) and application auth
(client_id / client_secret / tenant_id) via the OAuth 2.0 client-credentials
flow.  When an ``access_token`` is provided directly it is used as-is; when
app credentials are provided a token is fetched on ``connect()``.

Design Patterns
---------------
- Adapter: Translates between the GraphClaw CalendarEvent model and the
  MS Graph calendar event JSON format.
- Async HTTP: Uses ``httpx.AsyncClient`` for all Graph API calls.

Public API
----------
- OutlookCalendarConnector: CalendarConnector for Outlook/Exchange via MS Graph.

Dependencies
------------
- httpx: Async HTTP client (required).

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from graphclaw.connectors.base import ConnectorConfig
from graphclaw.connectors.calendar.base import CalendarConnector
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _parse_graph_datetime(raw: dict | None) -> datetime:
    """Parse a Graph API dateTime object ``{"dateTime": "...", "timeZone": "..."}``."""
    if raw is None:
        raise ValueError("Null dateTime object from MS Graph")
    dt_str = raw.get("dateTime", "")
    # Graph returns ISO 8601 without timezone suffix when timezone is specified separately
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback: treat as timezone.utc
        dt = datetime.fromisoformat(dt_str.rstrip("Z")).replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _graph_event_to_calendar_event(raw: dict) -> CalendarEvent:
    """Map an MS Graph event resource to a ``CalendarEvent``."""
    attendees = [
        a.get("emailAddress", {}).get("address", "")
        for a in raw.get("attendees", [])
        if a.get("emailAddress", {}).get("address")
    ]
    is_all_day = raw.get("isAllDay", False)
    body_content = raw.get("body", {}).get("content", "")

    return CalendarEvent(
        event_id=raw.get("id"),
        title=raw.get("subject", ""),
        start=_parse_graph_datetime(raw.get("start")),
        end=_parse_graph_datetime(raw.get("end")),
        description=body_content,
        location=raw.get("location", {}).get("displayName", ""),
        attendees=attendees,
        is_all_day=is_all_day,
        external_id=raw.get("iCalUId"),
    )


def _calendar_event_to_graph_body(event: CalendarEvent) -> dict:
    """Map a ``CalendarEvent`` to an MS Graph create/update event body."""
    body: dict[str, Any] = {
        "subject": event.title,
        "start": {
            "dateTime": event.start.isoformat(),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": event.end.isoformat(),
            "timeZone": "UTC",
        },
        "isAllDay": event.is_all_day,
    }
    if event.description:
        body["body"] = {"contentType": "text", "content": event.description}
    if event.location:
        body["location"] = {"displayName": event.location}
    if event.attendees:
        body["attendees"] = [
            {
                "emailAddress": {"address": email},
                "type": "required",
            }
            for email in event.attendees
        ]
    return body


class OutlookCalendarConnector(CalendarConnector):
    """Outlook / Exchange calendar connector via the Microsoft Graph API.

    Uses ``httpx.AsyncClient`` for all HTTP calls — no MS SDK required.

    Args:
        config: ``ConnectorConfig`` with ``connector_type="outlook_calendar"``
            and one of the following ``credentials`` configurations:

            Delegated access (recommended for user calendars):

            - ``access_token``: A valid MS Graph access token.

            Application auth (for daemon / service-to-service access):

            - ``client_id``: Azure app registration client ID.
            - ``client_secret``: Azure app registration secret.
            - ``tenant_id``: Azure tenant ID.
    """

    connector_type = "outlook_calendar"

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._access_token: str = config.credentials.get("access_token", "")
        self._client: Any = None  # httpx.AsyncClient

    async def _fetch_app_token(self) -> str:
        """Obtain an access token via client-credentials flow."""
        import httpx  # noqa: PLC0415

        creds = self._config.credentials
        tenant_id = creds["tenant_id"]
        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        }
        async with httpx.AsyncClient() as tmp_client:
            resp = await tmp_client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def connect(self) -> None:
        """Initialise the httpx client, fetching an app token if needed."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "httpx is required for OutlookCalendarConnector. Install with: pip install httpx"
            ) from exc

        if not self._access_token:
            self._access_token = await self._fetch_app_token()

        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._access_token}"},
            base_url=_GRAPH_BASE,
        )
        logger.info("OutlookCalendarConnector connected")

    async def disconnect(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("OutlookCalendarConnector disconnected")

    async def health_check(self) -> bool:
        """Return True if the /me endpoint responds successfully."""
        try:
            resp = await self._client.get("/me")
            return resp.is_success
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutlookCalendarConnector health_check failed: %s", exc)
            return False

    async def list_events(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[CalendarEvent]:
        """List events from the MS Graph calendarView endpoint."""
        start_dt = (
            since.isoformat() if since.tzinfo else since.replace(tzinfo=timezone.utc).isoformat()
        )
        end_dt = (
            until.isoformat() if until.tzinfo else until.replace(tzinfo=timezone.utc).isoformat()
        )

        if calendar_id == "primary":
            url = "/me/calendarView"
        else:
            url = f"/me/calendars/{calendar_id}/calendarView"

        params = {"startDateTime": start_dt, "endDateTime": end_dt}
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        return [_graph_event_to_calendar_event(item) for item in items]

    async def get_event(self, event_id: str) -> CalendarEvent:
        """Fetch a single event by its Graph event ID."""
        resp = await self._client.get(f"/me/events/{event_id}")
        if resp.status_code == 404:
            raise KeyError(f"Outlook event {event_id!r} not found")
        resp.raise_for_status()
        return _graph_event_to_calendar_event(resp.json())

    async def create_event(
        self,
        event: CalendarEvent,
        calendar_id: str = "primary",
    ) -> str:
        """Create a new event and return its Graph-assigned ID."""
        if calendar_id == "primary":
            url = "/me/events"
        else:
            url = f"/me/calendars/{calendar_id}/events"

        body = _calendar_event_to_graph_body(event)
        resp = await self._client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()["id"]

    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent:
        """Apply a partial update (PATCH) to an existing event."""
        resp = await self._client.patch(f"/me/events/{event_id}", json=patch)
        if resp.status_code == 404:
            raise KeyError(f"Outlook event {event_id!r} not found")
        resp.raise_for_status()
        return _graph_event_to_calendar_event(resp.json())

    async def delete_event(self, event_id: str) -> None:
        """Delete an event by its Graph event ID."""
        resp = await self._client.delete(f"/me/events/{event_id}")
        if resp.status_code == 404:
            raise KeyError(f"Outlook event {event_id!r} not found")
        resp.raise_for_status()

    async def check_free_busy(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[FreeBusySlot]:
        """Query free/busy via the MS Graph getSchedule endpoint."""
        start_dt = (
            since.isoformat() if since.tzinfo else since.replace(tzinfo=timezone.utc).isoformat()
        )
        end_dt = (
            until.isoformat() if until.tzinfo else until.replace(tzinfo=timezone.utc).isoformat()
        )

        # getSchedule requires the user's email; fetch it from /me
        me_resp = await self._client.get("/me", params={"$select": "mail,userPrincipalName"})
        me_resp.raise_for_status()
        me_data = me_resp.json()
        email = me_data.get("mail") or me_data.get("userPrincipalName", "")

        body = {
            "schedules": [email],
            "startTime": {"dateTime": start_dt, "timeZone": "UTC"},
            "endTime": {"dateTime": end_dt, "timeZone": "UTC"},
        }
        resp = await self._client.post("/me/calendar/getSchedule", json=body)
        resp.raise_for_status()
        schedules = resp.json().get("value", [])

        slots: list[FreeBusySlot] = []
        for schedule in schedules:
            for item in schedule.get("scheduleItems", []):
                status_map = {
                    "busy": "busy",
                    "free": "free",
                    "tentative": "tentative",
                    "oof": "busy",
                    "workingElsewhere": "busy",
                }
                status = status_map.get(item.get("status", "free"), "busy")
                slots.append(
                    FreeBusySlot(
                        start=_parse_graph_datetime(item.get("start")),
                        end=_parse_graph_datetime(item.get("end")),
                        status=status,
                    )
                )
        return slots


__all__ = [
    "OutlookCalendarConnector",
]
