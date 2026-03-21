"""graphclaw.connectors.calendar.base — Abstract base class for calendar connectors.

Description
-----------
Defines ``CalendarConnector``, the abstract intermediate class between
``ConnectorABC`` and the concrete Google / Outlook adapters.  It adds the
calendar-specific CRUD methods (list, get, create, update, delete events) and
free/busy query, expressed in terms of the shared ``CalendarEvent`` and
``FreeBusySlot`` data models.

Design Patterns
---------------
- Abstract Base Class: extends ``ConnectorABC`` with domain-specific contract.
- Template Method: concrete adapters implement only the abstract methods; the
  context-manager lifecycle is inherited from ``ConnectorABC``.

Public API
----------
- CalendarConnector: ABC with list_events, get_event, create_event, update_event,
  delete_event, check_free_busy.

Dependencies
------------
- graphclaw.connectors.base: ConnectorABC.
- graphclaw.connectors.calendar.models: CalendarEvent, FreeBusySlot.

Author
------
GraphClaw Project — https://graphclaw.ai
License: Apache 2.0
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from graphclaw.connectors.base import ConnectorABC
from graphclaw.connectors.calendar.models import CalendarEvent, FreeBusySlot


class CalendarConnector(ConnectorABC):
    """Abstract calendar connector.

    Concrete subclasses (``GoogleCalendarConnector``, ``OutlookCalendarConnector``)
    implement these methods using their respective client libraries or HTTP APIs.
    All methods are async and must be called within an active connection context
    (i.e. after ``connect()`` has been awaited).
    """

    @abstractmethod
    async def list_events(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[CalendarEvent]:
        """Return all events in the given time range.

        Args:
            since: Start of the query window (inclusive).
            until: End of the query window (exclusive).
            calendar_id: Calendar identifier (default ``"primary"``).

        Returns:
            List of ``CalendarEvent`` instances sorted by start time.
        """

    @abstractmethod
    async def get_event(self, event_id: str) -> CalendarEvent:
        """Fetch a single event by its platform ID.

        Args:
            event_id: The platform-assigned event identifier.

        Returns:
            A ``CalendarEvent`` instance.

        Raises:
            KeyError: If no event with the given ID exists.
        """

    @abstractmethod
    async def create_event(
        self,
        event: CalendarEvent,
        calendar_id: str = "primary",
    ) -> str:
        """Create a new calendar event and return its platform-assigned ID.

        Args:
            event: The event to create.  ``event.event_id`` is ignored.
            calendar_id: Calendar identifier (default ``"primary"``).

        Returns:
            The platform-assigned event ID string.
        """

    @abstractmethod
    async def update_event(self, event_id: str, patch: dict) -> CalendarEvent:
        """Apply a partial update to an existing event.

        Args:
            event_id: The platform-assigned event identifier.
            patch: Mapping of field names to new values (partial update).
                Supported keys depend on the concrete implementation.

        Returns:
            The updated ``CalendarEvent``.

        Raises:
            KeyError: If no event with the given ID exists.
        """

    @abstractmethod
    async def delete_event(self, event_id: str) -> None:
        """Permanently delete a calendar event.

        Args:
            event_id: The platform-assigned event identifier.

        Raises:
            KeyError: If no event with the given ID exists.
        """

    @abstractmethod
    async def check_free_busy(
        self,
        since: datetime,
        until: datetime,
        calendar_id: str = "primary",
    ) -> list[FreeBusySlot]:
        """Return the free/busy schedule for the given time range.

        Args:
            since: Start of the query window.
            until: End of the query window.
            calendar_id: Calendar identifier (default ``"primary"``).

        Returns:
            List of ``FreeBusySlot`` instances covering the queried range.
        """


__all__ = [
    "CalendarConnector",
]
