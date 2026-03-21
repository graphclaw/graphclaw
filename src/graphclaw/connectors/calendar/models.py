"""graphclaw.connectors.calendar.models — Data models for calendar connectors.

Description
-----------
Defines the shared data transfer objects used by all calendar connector
implementations: ``CalendarEvent`` (a single calendar entry) and
``FreeBusySlot`` (a time range with a free/busy status).  Both are frozen
dataclasses so they are safe to cache and compare by value.

Public API
----------
- CalendarEvent: Immutable dataclass representing a calendar event.
- FreeBusySlot: Immutable dataclass representing a free/busy time range.

Dependencies
------------
- dataclasses: dataclass, field.
- datetime: datetime.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CalendarEvent:
    """A single calendar event.

    Attributes
    ----------
    event_id:
        Platform-assigned event identifier (``None`` before creation).
    title:
        Human-readable event title / subject.
    start:
        Event start time (timezone-aware recommended).
    end:
        Event end time (timezone-aware recommended).
    description:
        Optional long-form description or body text.
    location:
        Optional physical or virtual meeting location.
    attendees:
        List of attendee email addresses.
    is_all_day:
        ``True`` if this is an all-day event (time portion is ignored).
    external_id:
        Optional identifier from the originating system (e.g. iCal UID).
    """

    event_id: str | None
    title: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    is_all_day: bool = False
    external_id: str | None = None


@dataclass(frozen=True)
class FreeBusySlot:
    """A time range with an associated free/busy status.

    Attributes
    ----------
    start:
        Start of the time slot.
    end:
        End of the time slot.
    status:
        One of ``"busy"``, ``"free"``, or ``"tentative"``.
    """

    start: datetime
    end: datetime
    status: str  # "busy" | "free" | "tentative"


__all__ = [
    "CalendarEvent",
    "FreeBusySlot",
]
